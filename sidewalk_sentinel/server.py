"""Server HTTP (pustaka standar saja): melayani aplikasi web (PWA) dan API perangkat.

Mode LAN (``--lan``): server mendengarkan di semua antarmuka agar bisa dibuka dari HP; semua
permintaan wajib membawa cookie kunci acak yang dipasang saat tautan berkunci dibuka sekali.

Pemisahan data: perangkat hanya melihat datanya sendiri (dikenali dari kunci rahasia di cookie ``ss_dev``); pusat, yaitu
peramban di mesin server ini, melihat semua perangkat. Aturannya ada di ``access.py``; IP hanya penanda, bukan kunci.

Mode HP (``--hp``): satu perintah untuk memakai HP sebagai kamera. Bila cloudflared terpasang, server tetap hanya
di laptop (127.0.0.1) dan sebuah tunnel Cloudflare memberi alamat https publik; bila tidak, server berjalan di
LAN dengan HTTPS. Alamat HP berkunci itu ditampilkan sebagai kode QR di terminal dan di halaman ``/hp``.
"""
from __future__ import annotations

import hmac
import json
import logging
import mimetypes
import re
import secrets
import socket
import ssl
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from . import access, qr, tls, tunnel
from .cameras import MAX_CAMERAS, CameraManager
from .detector import (DEFAULT_SIDEWALK, DEFAULT_WEIGHTS, DetectorUnavailable, SidewalkSegmenter,
                       VehicleDetector)
from .config import Config
from .util import valid_id

log = logging.getLogger("sidewalk")
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
PUBLIC_PATHS = {"/manifest.webmanifest", "/icon.svg", "/icon-192.png", "/icon-512.png", "/apple-touch-icon.png"}
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("text/javascript", ".js")


def lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))       # tidak mengirim paket; hanya memilih antarmuka
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


class App:
    def __init__(self, data_dir: str | Path, token: str | None = None, retention_days: int = 30,
                 detector=None, sidewalk: SidewalkSegmenter | None = None):
        self.root = Path(data_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.token = token
        self.port = 0
        self.host = "127.0.0.1"
        self.lan = False
        self.https = False
        self.trust_proxy = False                  # benar bila server sendiri menjalankan tunnel (lihat access.client_ip)
        self._connect = {"mode": None, "state": "off", "url": None, "svg": None, "message": ""}
        self._connect_lock = threading.Lock()
        self.detector, self.sidewalk = detector, sidewalk
        self.cams = CameraManager(self.root, retention_days, detector=detector, sidewalk=sidewalk)
        self.cams.start()

    def set_connect(self, **kw) -> None:
        """Keadaan sambungan HP: mode (tunnel atau lan), state (off, memulai, siap, gagal), url berkunci, pesan."""
        with self._connect_lock:
            self._connect.update(kw)
            if kw.get("url"):
                self._connect["svg"] = qr.svg(qr.make(kw["url"]))

    def connect_info(self) -> dict:
        with self._connect_lock:
            return dict(self._connect)

    def close(self) -> None:
        self.cams.shutdown()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"SidewalkSentinel/{__version__}"
    app: App
    _extra: tuple | list = ()                      # header tambahan untuk satu jawaban (lihat end_headers)

    # ------------------------------------------------------------------- dasar
    def log_message(self, fmt, *args):  # tenang: hanya kesalahan yang dicatat
        if args and str(args[1]).startswith(("4", "5")):
            log.warning("%s %s", self.address_string(), fmt % args)

    def _send(self, status: int, body: bytes, ctype: str, headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, status: int = 200, headers: dict | None = None) -> None:
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8", headers)

    def _error(self, status: int, message: str, close: bool = False) -> None:
        if close:
            self.close_connection = True
        self._json({"error": message}, status, {"Connection": "close"} if close else None)

    def _drain(self) -> None:
        """Buang body yang tidak dibaca handler, agar koneksi keep-alive tidak kacau (desync)."""
        if getattr(self, "_body_read", True):
            return
        length = self.headers.get("Content-Length")
        if length and length.isdigit() and int(length) > 0:
            if int(length) > (1 << 20):
                self.close_connection = True
            else:
                self.rfile.read(int(length))
        elif self.headers.get("Transfer-Encoding"):
            self.close_connection = True
        self._body_read = True

    def _read_body(self, limit: int) -> bytes:
        self._body_read = True
        length = self.headers.get("Content-Length")
        if length is None or not length.isdigit():
            raise _HttpError(411, "Content-Length wajib ada", close=True)
        n = int(length)
        if n > limit:
            raise _HttpError(413, "isi permintaan terlalu besar", close=True)
        return self.rfile.read(n)

    def _read_json(self, limit: int = 256 * 1024) -> dict:
        try:
            data = json.loads(self._read_body(limit) or b"{}")
        except json.JSONDecodeError as e:
            raise _HttpError(400, f"JSON tidak valid: {e}") from e
        if not isinstance(data, dict):
            raise _HttpError(400, "JSON harus berupa objek")
        return data

    # ---------------------------------------------------------------- otorisasi
    def _cookie(self, name: str) -> str:
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return ""

    @staticmethod
    def _same(given: str, token: str) -> bool:
        """Perbandingan waktu-tetap. Lewat bytes agar karakter non-ASCII di tautan atau cookie hanya berarti salah, bukan galat."""
        return hmac.compare_digest(given.encode("utf-8", "replace"), token.encode("utf-8", "replace"))

    def _authorized(self) -> bool:
        token = self.app.token
        if token is None:
            return True
        return self._same(self._cookie("ss_key"), token)

    def end_headers(self) -> None:
        for k, v in self._extra:                      # Set-Cookie dari tautan berkunci, dipasang pada jawaban halaman itu sendiri
            self.send_header(k, v)
        self._extra = []
        self.send_header("Referrer-Policy", "no-referrer")     # kunci ada di alamat halaman: jangan ikut terkirim ke situs lain
        super().end_headers()

    def _client_ip(self) -> str:
        return access.client_ip(self.client_address[0], self.headers, self.app.trust_proxy)

    def _who(self):
        """('pusat', None), ('perangkat', kamera miliknya), atau ('tamu', None) bila belum punya perangkat terdaftar."""
        if access.is_center(self.client_address[0], self.headers):
            return "pusat", None
        cam = self.app.cams.by_key(self._cookie("ss_dev"))
        return ("perangkat", cam) if cam is not None else ("tamu", None)

    def _scope(self, who) -> list:
        """Perangkat yang boleh dilihat peminta: pusat semuanya, perangkat hanya miliknya, tamu tidak satu pun."""
        role, cam = who
        if role == "pusat":
            return self.app.cams.list()
        return [cam] if cam is not None else []

    def _device_cookie(self, key: str) -> str:
        """Kunci perangkat sebagai cookie: hanya untuk /api, tidak terbaca skrip, tidak ikut permintaan lintas situs."""
        cookie = f"ss_dev={key}; Path=/api; HttpOnly; SameSite=Strict; Max-Age=31536000"
        proxied_https = (self.app.trust_proxy and access.is_loopback(self.client_address[0])
                         and self.headers.get("X-Forwarded-Proto", "").lower() == "https")
        return cookie + ("; Secure" if self.app.https or proxied_https else "")

    # ----------------------------------------------------------------- dispatch
    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("HEAD")

    def do_POST(self):
        self._dispatch("POST")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        self._body_read = False
        self._extra: list[tuple[str, str]] = []
        try:
            self._dispatch_inner(method)
        finally:
            try:
                self._drain()
            except (OSError, ValueError):
                self.close_connection = True

    def _dispatch_inner(self, method: str) -> None:
        try:
            url = urlparse(self.path)
            path, query = url.path, parse_qs(url.query)

            # Tautan berkunci (?k=...) membuka halaman dan memasang cookie pada jawaban yang sama, tanpa mengalihkan. Kunci
            # sengaja tetap ada di alamat: pratinjau pemindai QR dan Safari punya wadah cookie yang terpisah, jadi bila
            # alamat dipindah antar browser (mis. "Buka di Safari") hanya alamat yang terbawa, dan cookie tidak.
            unlocked = False
            if (self.app.token is not None and path in ("/", "/hp") and method in ("GET", "HEAD") and "k" in query
                    and self._same(query["k"][0], self.app.token)):
                unlocked = True
                cookie = f"ss_key={self.app.token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000"
                if self.app.https:
                    cookie += "; Secure"
                self._extra.append(("Set-Cookie", cookie))

            if not unlocked and not self._authorized() and path not in PUBLIC_PATHS:
                if path.startswith("/api/"):
                    return self._error(403, "tidak diizinkan: buka tautan berkunci dari terminal")
                page = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
                        "<body style='background:#05060f;color:#f5f6ff;font:16px/1.5 system-ui;padding:2rem;max-width:34rem'>"
                        "<h2>Akses ditolak</h2>"
                        "<p>Alamat ini tidak memuat kunci yang berlaku. Kunci berganti setiap server dijalankan ulang.</p>"
                        "<p>Pindai kode QR yang baru di layar laptop (halaman <code>/hp</code>), atau buka tautan lengkap "
                        "yang dicetak di terminal.</p>"
                        "<p>Bila membuka dari pratinjau pemindai, pindahkan ke Safari dengan alamat lengkap yang sama "
                        "(termasuk <code>?k=...</code>).</p>")
                return self._send(403, page.encode(), "text/html; charset=utf-8")

            if path.startswith("/api/"):
                return self._api(method, path, query)
            if method not in ("GET", "HEAD"):
                return self._error(405, "metode tidak diizinkan")
            return self._static(path)
        except _HttpError as e:
            self._error(e.status, e.message, e.close)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True
        except Exception:      # noqa: BLE001
            log.exception("kesalahan tak terduga pada %s %s", method, self.path)
            self._error(500, "kesalahan internal server", close=True)

    # ------------------------------------------------------------------- statis
    def _static(self, path: str) -> None:
        rel = "index.html" if path in ("", "/") else "hp.html" if path == "/hp" else path.lstrip("/")
        target = (WEB_DIR / rel).resolve()
        if WEB_DIR.resolve() not in target.parents or not target.is_file():
            return self._error(404, "tidak ditemukan")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith(("javascript", "json", "xml")):
            ctype += "; charset=utf-8"
        self._send_file(target, ctype, cache="no-cache")

    def _send_file(self, path: Path, ctype: str, cache: str = "no-store", download: str | None = None) -> None:
        size = path.stat().st_size
        start, end, status = 0, size - 1, 200
        rng = self.headers.get("Range")
        if rng and size > 0:
            m = re.match(r"^bytes=(\d*)-(\d*)$", rng.strip())
            if m and (m.group(1) or m.group(2)):
                a, b = m.groups()
                if a == "":
                    start = max(0, size - int(b))
                else:
                    start = int(a)
                    end = min(size - 1, int(b)) if b else size - 1
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status = 206
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{download}"')
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(path, "rb") as fh:
            fh.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = fh.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _api(self, method: str, path: str, query: dict) -> None:
        app = self.app
        seg = path.strip("/").split("/")          # ['api', ...]
        n = len(seg)
        who = self._who()
        center = who[0] == "pusat"
        scope = self._scope(who)

        if method == "GET" and path == "/api/info":
            d = Config()
            return self._json({
                "name": "Sidewalk Sentinel", "version": __version__, "auth": app.token is not None,
                "role": "pusat" if center else "perangkat",
                "lan_url": f"http://{lan_ip()}:{app.port}/" if app.lan else None,
                "defaults": {"park_seconds": d.park_seconds, "conf": d.conf, "min_box_frac": d.min_box_frac},
                "detector": {**app.detector.status(), "sidewalk_model": bool(app.sidewalk and app.sidewalk.available)},
            })

        if method == "GET" and path == "/api/connect":
            if not center:
                return self._error(403, "hanya pusat (laptop yang menjalankan server) yang bisa membuka sambungan HP")
            c = app.connect_info()
            if c["state"] == "off":
                return self._json({"state": "off", "mode": None, "url": None, "svg": None, "message": "", "devices": []})
            devices = [{"name": cam.spec.name, "status": cam.status} for cam in app.cams.list() if cam.status != "menunggu"]
            return self._json({**c, "devices": devices})

        if path == "/api/detector/retry":
            if method != "POST":
                return self._error(405, "metode tidak diizinkan")
            app.detector.retry()
            return self._json(app.detector.status())

        if path == "/api/device":
            if method != "POST":
                return self._error(405, "metode tidak diizinkan")
            body = self._read_json()
            try:
                cam, created = app.cams.register(body.get("key"), body.get("name", ""))
            except ValueError as e:
                return self._error(400, str(e))
            app.cams.note_ip(cam, self._client_ip())
            return self._json(cam.info(admin=center), 201 if created else 200,
                              {"Set-Cookie": self._device_cookie(body["key"])})

        if n >= 2 and seg[1] == "cams":
            return self._cam_routes(method, seg, query, center, scope)

        if n == 2 and seg[1] == "events" and method == "GET":
            kind = query.get("kind", [None])[0]
            if kind not in (None, "parkir", "naik_trotoar"):
                return self._error(400, "kind harus parkir atau naik_trotoar")
            try:
                limit = max(1, min(200, int(query.get("limit", ["40"])[0])))
                offset = max(0, int(query.get("offset", ["0"])[0]))
                rows = app.cams.feed(limit, offset, kind, scope)
            except ValueError:
                return self._error(400, "parameter limit/offset tidak valid")
            return self._json({"events": rows})

        if n == 2 and seg[1] == "report" and method == "GET":
            camera = query.get("kamera", [None])[0]
            if camera is not None and not valid_id(camera):
                return self._error(400, "id perangkat tidak valid")
            try:
                return self._json(app.cams.report(query.get("periode", ["hari_ini"])[0], camera, cams=scope))
            except ValueError as e:
                return self._error(400, str(e))
            except KeyError:
                return self._error(404, "perangkat tidak ditemukan")

        if n == 2 and seg[1] == "events.csv" and method in ("GET", "HEAD"):
            return self._send(200, app.cams.csv(scope).encode("utf-8-sig"), "text/csv; charset=utf-8",
                              {"Content-Disposition": 'attachment; filename="sidewalk-kejadian.csv"'})

        return self._error(404, "endpoint tidak dikenal")

    def _cam_routes(self, method: str, seg: list[str], query: dict, center: bool, scope: list) -> None:
        mgr = self.app.cams
        n = len(seg)

        if n == 2:
            if method == "GET":
                return self._json({"cams": [c.info(admin=center) for c in scope], "max": MAX_CAMERAS,
                                   "now": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
            return self._error(405, "metode tidak diizinkan")

        cam = mgr.get(seg[2]) if valid_id(seg[2]) else None
        if cam is None or cam not in scope:                # perangkat orang lain dijawab sama seperti yang tidak ada
            return self._error(404, "perangkat tidak ditemukan")

        if n == 3:
            if method == "GET":
                return self._json(cam.info(admin=center))
            if method == "POST":
                try:
                    updated = mgr.update(cam.spec.id, self._read_json())
                except (ValueError, TypeError) as e:
                    return self._error(400, str(e))
                return self._json(updated.info(admin=center)) if updated else self._error(404, "perangkat tidak ditemukan")
            if method == "DELETE":
                return self._json({"deleted": mgr.delete(cam.spec.id)})
            return self._error(405, "metode tidak diizinkan")

        if n == 4 and seg[3] == "propose-zone" and method == "POST":
            try:
                return self._json(cam.propose_zone())
            except ValueError as e:
                return self._error(409, str(e))
            except DetectorUnavailable as e:
                return self._error(503, str(e))
            except Exception:                              # noqa: BLE001 - model gagal menjalankan inferensi
                log.exception("usulan zona gagal")
                return self._error(500, "Usulan zona gagal dibuat. Tandai zona secara manual.")

        if n == 4 and seg[3] == "frame" and method == "POST":
            body = self._read_body(3 * 1024 * 1024)
            mgr.note_ip(cam, self._client_ip())
            try:
                return self._json(cam.push(body))
            except ValueError as e:
                return self._error(400, str(e))

        if n == 4 and seg[3] == "events" and method == "GET":
            try:
                limit = max(1, min(200, int(query.get("limit", ["30"])[0])))
                if "after" in query:
                    rows = cam.events.after(int(query["after"][0]), limit)
                else:
                    before = int(query["before"][0]) if "before" in query else None
                    rows = cam.events.latest(limit, before, query.get("kind", [None])[0])
            except ValueError:
                return self._error(400, "parameter limit/after/before tidak valid")
            out = [{**r, "snapshot_url": f"/api/cams/{cam.spec.id}/{r['snapshot']}" if r["snapshot"] else None}
                   for r in rows]
            return self._json({"events": out, "seq": cam.events.seq})

        if n == 5 and seg[3] == "events" and method == "DELETE":
            if not re.fullmatch(r"\d{1,9}", seg[4]) or not cam.events.delete(int(seg[4])):
                return self._error(404, "kejadian tidak ditemukan")
            return self._json({"deleted": True, "no": int(seg[4])})

        if n == 5 and seg[3] == "events" and method in ("GET", "HEAD"):
            m = re.fullmatch(r"(\d{1,9})\.jpg", seg[4])
            f = cam.events.snapshot_path(int(m.group(1))) if m else None
            if f is None:
                return self._error(404, "bukti tidak ditemukan")
            return self._send_file(f, "image/jpeg", cache="private, max-age=3600")

        return self._error(404, "endpoint tidak dikenal")

class _HttpError(Exception):
    def __init__(self, status: int, message: str, close: bool = False):
        super().__init__(message)
        self.status, self.message, self.close = status, message, close


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        # Browser yang menolak sertifikat ditandatangani sendiri, atau klien yang memutus koneksi, bukan galat server.
        if isinstance(sys.exc_info()[1], (ssl.SSLError, ConnectionError)):
            log.debug("koneksi dari %s berakhir: %s", client_address[0], sys.exc_info()[1])
            return
        super().handle_error(request, client_address)


def make_server(host: str, port: int, data_dir: str | Path, lan: bool = False,
                token: str | None = None, retention_days: int = 30, https: bool = False,
                hosts: list[str] | None = None, detector=None, sidewalk: SidewalkSegmenter | None = None,
                weights: str = DEFAULT_WEIGHTS, sidewalk_weights: str | Path | None = DEFAULT_SIDEWALK,
                device: str | None = None) -> tuple[_Server, App]:
    if detector is None:                              # tes menyuntikkan detektor sendiri; pemakaian nyata memuat YOLO
        detector = VehicleDetector(weights, device)
        if sidewalk is None:
            sidewalk = SidewalkSegmenter(sidewalk_weights, device)
    app = App(data_dir, token=token, retention_days=retention_days, detector=detector, sidewalk=sidewalk)
    app.lan = lan
    try:
        ctx = None
        if https:
            ctx = tls.server_context(*tls.ensure_certificate(app.root / "tls", hosts or []))
        handler = type("BoundHandler", (Handler,), {"app": app})
        server = _Server((host, port), handler)
    except BaseException:
        app.close()                                  # jangan tinggalkan utas kamera bila server gagal dibuat
        raise
    if ctx is not None:
        # Jabat tangan TLS ditunda ke utas penangan (do_handshake_on_connect=False). Bila dilakukan saat accept(),
        # satu koneksi yang diam saja menahan semua koneksi lain.
        server.socket = ctx.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
        app.https = True
    app.host, app.port = host, server.server_address[1]
    return server, app


def _show_qr(url: str) -> None:
    """Cetak kode QR ke terminal. Terminal yang tidak bisa menampilkan blok Unicode tetap mendapat tautannya."""
    try:
        print("\n" + qr.terminal(qr.make(url)) + "\n")
        print("  Arahkan kamera HP ke kode di atas, lalu ketuk tautan yang muncul.")
    except (UnicodeEncodeError, ValueError):
        print("  (terminal ini tidak bisa menampilkan kode QR; buka halaman /hp di laptop)")
    print(f"  Tautan : {url}")
    print("  Jangan dibagikan: siapa pun yang punya tautan ini bisa membuka kamera Anda.\n")


def serve(host: str = "127.0.0.1", port: int = 8000, data_dir: str | Path = "data", lan: bool = False,
          retention_days: int = 30, https: bool = False, weights: str = DEFAULT_WEIGHTS,
          sidewalk_weights: str | Path | None = DEFAULT_SIDEWALK, device: str | None = None,
          hp: bool = False, open_browser: bool = False) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    exe = tunnel.find_cloudflared() if hp else None
    mode = ("tunnel" if exe else "lan") if hp else None
    if mode == "tunnel":
        https = False                                 # Cloudflare yang memegang HTTPS; cloudflared bicara HTTP ke server ini
    elif mode == "lan":
        https = True                                  # tanpa tunnel: HTTPS lokal agar kamera HP boleh menyala
    lan = lan or https                                # HTTPS dipakai agar HP bisa membuka kamera, jadi harus terjangkau dari LAN
    token = secrets.token_urlsafe(12) if (lan or mode == "tunnel") else None
    if lan:
        host = "0.0.0.0"
    ip = lan_ip()
    try:
        server, app = make_server(host, port, data_dir, lan=lan, token=token, retention_days=retention_days,
                                  https=https, hosts=[ip, "localhost"], weights=weights,
                                  sidewalk_weights=sidewalk_weights, device=device)
    except tls.TlsUnavailable as e:
        raise SystemExit(str(e))
    app.trust_proxy = mode == "tunnel"                # IP asli HP ada di header Cloudflare (hanya dari loopback)
    scheme = "https" if https else "http"
    bar = "=" * 62
    print(f"\n{bar}\n  SIDEWALK SENTINEL v{__version__}\n{bar}")
    page = "hp" if mode == "tunnel" else ""
    print(f"  Laptop : {scheme}://localhost:{app.port}/{page}" + (f"?k={token}" if token else ""))
    tun, opener = None, None
    if mode == "tunnel":
        print("  HP     : membuat tunnel Cloudflare, tunggu beberapa detik...")
        print("           (server hanya terbuka di laptop ini; HP masuk lewat tunnel dengan tautan berkunci)")
        app.set_connect(mode="tunnel", state="memulai", message="Membuat tunnel Cloudflare, tunggu beberapa detik.")

        def on_url(base: str) -> None:
            url = f"{base}/?k={token}"
            app.set_connect(state="siap", url=url, message="Siap. Scan kode ini dengan kamera HP.")
            _show_qr(url)

        def on_fail(message: str) -> None:
            app.set_connect(state="gagal", message=message)
            print(f"\n  Tunnel gagal: {message}\n")

        tun = tunnel.Tunnel(tunnel.command(exe, app.port), on_url, on_fail)
        tun.start()
        if open_browser:                              # halaman QR di laptop; dibatalkan bila server berhenti lebih dulu
            opener = threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{app.port}/hp?k={token}"))
            opener.daemon = True
            opener.start()
    elif mode == "lan":
        url = f"https://{ip}:{app.port}/?k={token}"
        app.set_connect(mode="lan", state="siap", url=url,
                        message="Siap. HP dan laptop harus di Wi-Fi yang sama; browser memberi peringatan sertifikat sekali.")
        print("  HP     : cloudflared tidak ditemukan, jadi memakai jaringan Wi-Fi (HP dan laptop harus satu Wi-Fi).")
        print("           Browser memberi peringatan sertifikat sekali: Chrome > Lanjutan > Lanjutkan ke situs;")
        print("           Safari > Tampilkan Detail > buka situs web ini.")
        print(f"           Lebih mudah dan tanpa peringatan: pasang cloudflared ({tunnel.INSTALL_HINT}), lalu jalankan ulang.")
        _show_qr(url)
    elif lan:
        print(f"  HP     : {scheme}://{ip}:{app.port}/?k={token}")
        print("           (HP dan laptop harus di Wi-Fi yang sama; tautan ini berkunci)")
        if https:
            print("  HTTPS  : sertifikat ditandatangani sendiri, jadi browser memberi peringatan sekali.")
            print("           Chrome: Lanjutan > Lanjutkan ke situs. Safari: Tampilkan Detail > buka situs web ini.")
            print("           Sesudah itu izinkan kamera; pemantauan mulai sendiri.")
        else:
            print("  Catatan: kamera HP butuh HTTPS. Jalankan ulang dengan --hp (atau --https) untuk memakainya.")
    side = "siap" if app.sidewalk and app.sidewalk.available else "tidak ditemukan (zona ditandai manual)"
    print(f"  Model  : kendaraan = {app.detector.weights}  |  trotoar = {side}")
    print(f"  Data   : {app.root}  (kejadian disimpan {retention_days} hari)")
    print(f"{bar}\n  Tekan Ctrl+C untuk berhenti.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBerhenti.")
    finally:
        if opener:
            opener.cancel()
        if tun:
            tun.stop()
        app.close()

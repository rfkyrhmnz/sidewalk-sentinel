"""Server HTTP (pustaka standar saja): melayani aplikasi web (PWA) dan API perangkat.

Mode LAN (``--lan``): server mendengarkan di semua antarmuka agar bisa dibuka dari HP; semua
permintaan wajib membawa cookie kunci acak yang dipasang saat tautan berkunci dibuka sekali.
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
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from . import tls
from .cameras import MAX_CAMERAS, CameraManager
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
    def __init__(self, data_dir: str | Path, token: str | None = None, retention_days: int = 30):
        self.root = Path(data_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.token = token
        self.port = 0
        self.host = "127.0.0.1"
        self.lan = False
        self.https = False
        self.cams = CameraManager(self.root, retention_days)
        self.cams.start()

    def close(self) -> None:
        self.cams.shutdown()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"SidewalkSentinel/{__version__}"
    app: App

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
    def _authorized(self) -> bool:
        token = self.app.token
        if token is None:
            return True
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "ss_key" and hmac.compare_digest(v, token):
                return True
        return False

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

            if self.app.token is not None and path == "/" and "k" in query:
                if hmac.compare_digest(query["k"][0], self.app.token):
                    cookie = f"ss_key={self.app.token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000"
                    if self.app.https:
                        cookie += "; Secure"
                    self.send_response(302)
                    self.send_header("Location", "/")
                    self.send_header("Set-Cookie", cookie)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return

            if not self._authorized() and path not in PUBLIC_PATHS:
                if path.startswith("/api/"):
                    return self._error(403, "tidak diizinkan: buka tautan berkunci dari terminal")
                page = ("<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
                        "<body style='background:#05070d;color:#e8f1ff;font:16px system-ui;padding:2rem'>"
                        "<h2>Akses ditolak</h2><p>Buka tautan berkunci yang dicetak di terminal "
                        "(<code>python main.py serve --lan</code>).</p>")
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
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
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

        if method == "GET" and path == "/api/info":
            d = Config()
            return self._json({
                "name": "Sidewalk Sentinel", "version": __version__, "auth": app.token is not None,
                "lan_url": f"http://{lan_ip()}:{app.port}/" if app.lan else None,
                "defaults": {"park_seconds": d.park_seconds, "min_area_frac": d.min_area_frac},
            })

        if path == "/api/device":
            if method != "POST":
                return self._error(405, "metode tidak diizinkan")
            body = self._read_json()
            try:
                cam, created = app.cams.register(body.get("key"), body.get("name", ""))
            except ValueError as e:
                return self._error(400, str(e))
            return self._json(cam.info(), 201 if created else 200)

        if n >= 2 and seg[1] == "cams":
            return self._cam_routes(method, seg, query)

        if n == 2 and seg[1] == "events" and method == "GET":
            kind = query.get("kind", [None])[0]
            if kind not in (None, "parkir", "naik_trotoar"):
                return self._error(400, "kind harus parkir atau naik_trotoar")
            try:
                limit = max(1, min(200, int(query.get("limit", ["40"])[0])))
                offset = max(0, int(query.get("offset", ["0"])[0]))
                rows = app.cams.feed(limit, offset, kind)
            except ValueError:
                return self._error(400, "parameter limit/offset tidak valid")
            return self._json({"events": rows})

        if n == 2 and seg[1] == "report" and method == "GET":
            camera = query.get("kamera", [None])[0]
            if camera is not None and not valid_id(camera):
                return self._error(400, "id perangkat tidak valid")
            try:
                return self._json(app.cams.report(query.get("periode", ["hari_ini"])[0], camera))
            except ValueError as e:
                return self._error(400, str(e))
            except KeyError:
                return self._error(404, "perangkat tidak ditemukan")

        if n == 2 and seg[1] == "events.csv" and method in ("GET", "HEAD"):
            return self._send(200, app.cams.csv().encode("utf-8-sig"), "text/csv; charset=utf-8",
                              {"Content-Disposition": 'attachment; filename="sidewalk-kejadian.csv"'})

        return self._error(404, "endpoint tidak dikenal")

    def _cam_routes(self, method: str, seg: list[str], query: dict) -> None:
        mgr = self.app.cams
        n = len(seg)

        if n == 2:
            if method == "GET":
                return self._json({"cams": [c.info() for c in mgr.list()], "max": MAX_CAMERAS,
                                   "now": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
            return self._error(405, "metode tidak diizinkan")

        cam = mgr.get(seg[2]) if valid_id(seg[2]) else None
        if cam is None:
            return self._error(404, "perangkat tidak ditemukan")

        if n == 3:
            if method == "GET":
                return self._json(cam.info())
            if method == "POST":
                try:
                    updated = mgr.update(cam.spec.id, self._read_json())
                except (ValueError, TypeError) as e:
                    return self._error(400, str(e))
                return self._json(updated.info()) if updated else self._error(404, "perangkat tidak ditemukan")
            if method == "DELETE":
                return self._json({"deleted": mgr.delete(cam.spec.id)})
            return self._error(405, "metode tidak diizinkan")

        if n == 4 and seg[3] == "recalibrate" and method == "POST":
            cam.recalibrate()
            return self._json({"ok": True})

        if n == 4 and seg[3] == "frame" and method == "POST":
            body = self._read_body(3 * 1024 * 1024)
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
                hosts: list[str] | None = None) -> tuple[_Server, App]:
    app = App(data_dir, token=token, retention_days=retention_days)
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


def serve(host: str = "127.0.0.1", port: int = 8000, data_dir: str | Path = "data", lan: bool = False,
          retention_days: int = 30, https: bool = False) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    lan = lan or https                                # HTTPS dipakai agar HP bisa membuka kamera, jadi harus terjangkau dari LAN
    token = secrets.token_urlsafe(12) if lan else None
    if lan:
        host = "0.0.0.0"
    ip = lan_ip()
    try:
        server, app = make_server(host, port, data_dir, lan=lan, token=token, retention_days=retention_days,
                                  https=https, hosts=[ip, "localhost"])
    except tls.TlsUnavailable as e:
        raise SystemExit(str(e))
    scheme = "https" if https else "http"
    bar = "=" * 62
    print(f"\n{bar}\n  SIDEWALK SENTINEL v{__version__}\n{bar}")
    print(f"  Laptop : {scheme}://localhost:{app.port}/" + (f"?k={token}" if token else ""))
    if lan:
        print(f"  HP     : {scheme}://{ip}:{app.port}/?k={token}")
        print("           (HP dan laptop harus di Wi-Fi yang sama; tautan ini berkunci)")
        if https:
            print("  HTTPS  : sertifikat ditandatangani sendiri, jadi browser memberi peringatan sekali.")
            print("           Chrome: Lanjutan > Lanjutkan ke situs. Safari: Tampilkan Detail > buka situs web ini.")
            print("           Sesudah itu izinkan kamera; pemantauan mulai sendiri.")
        else:
            print("  Catatan: kamera HP butuh HTTPS. Jalankan ulang dengan --https untuk memakainya.")
    print(f"  Data   : {app.root}  (kejadian disimpan {retention_days} hari)")
    print(f"{bar}\n  Tekan Ctrl+C untuk berhenti.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBerhenti.")
    finally:
        app.close()
        server.server_close()

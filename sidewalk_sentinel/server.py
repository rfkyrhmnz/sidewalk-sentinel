"""Server HTTP (pustaka standar saja) yang melayani UI web/PWA dan API analisis.

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
import sys
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from . import video as vid
from .cameras import MAX_CAMERAS, CameraManager, SourceError, parse_source
from .config import Config
from .jobs import (ALLOWED_EXT, JobManager, LiveManager, Store, Uploads, check_roi, new_id,
                   valid_id)
from .synthetic import ROI as DEMO_ROI
from .synthetic import default_scene, write_video

log = logging.getLogger("sidewalk")
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
MAX_UPLOAD = 600 * 1024 * 1024
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
        self.store = Store(data_dir)
        self.uploads = Uploads(self.store)
        self.jobs = JobManager(self.store, self.uploads)
        self.live = LiveManager(self.store)
        self.token = token
        self.port = 0
        self.host = "127.0.0.1"
        self.lan = False
        self.cams = CameraManager(self.store.root, self._resolve_source, retention_days)
        self.cams.start()

    def close(self) -> None:
        self.cams.shutdown()

    def _resolve_source(self, src):
        """Ubah sumber kamera menjadi yang dipahami OpenCV (jalur berkas, nomor perangkat, atau URL)."""
        if src.kind == "demo":
            return str(self.ensure_demo()["path"])
        if src.kind == "upload":
            item = self.uploads.get(src.value)
            if not item:
                raise SourceError("video unggahan tidak ditemukan (mungkin sudah dihapus)")
            return str(item["path"])
        if src.kind == "device":
            return int(src.value)
        return src.value

    def ensure_demo(self) -> dict:
        path = self.store.demo / "demo.mp4"
        if not path.exists():
            tmp = self.store.demo / "demo_tmp.mp4"
            write_video(default_scene(), str(tmp))
            tmp.replace(path)
        uid = "d" + "e" * 11
        item = self.uploads.get(uid) if uid in getattr(self.uploads, "_items", {}) else None
        if item is None:
            item = self.uploads.register(uid, path, "demo-sintetis.mp4", {"demo": True})
        return item


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

    # ---------------------------------------------------------------------- API
    def _api(self, method: str, path: str, query: dict) -> None:
        app = self.app
        seg = path.strip("/").split("/")          # ['api', ...]
        n = len(seg)

        if method == "GET" and path == "/api/info":
            defaults = Config().to_dict()
            return self._json({
                "name": "Sidewalk Sentinel", "version": __version__, "ffmpeg": vid.have_ffmpeg(),
                "max_upload_mb": MAX_UPLOAD // (1024 * 1024), "auth": app.token is not None,
                "lan_url": f"http://{lan_ip()}:{app.port}/" if app.lan else None,
                "defaults": {k: defaults[k] for k in (
                    "park_seconds", "diff_floor", "min_area_frac", "filter_pedestrians", "use_homomorphic",
                    "use_shadow_removal", "use_morphology", "use_clahe", "ref_update_rate", "ref_frames")},
            })

        if method == "POST" and path == "/api/demo":
            item = app.ensure_demo()
            return self._json({"upload_id": item["id"], "info": item["info"], "name": item["name"],
                               "roi": [list(p) for p in DEMO_ROI], "ref_time": 0.0, "demo": True})

        if method == "POST" and path == "/api/upload":
            return self._upload(query)

        if n == 3 and seg[1] == "uploads" and method == "GET":
            item = app.uploads.get(seg[2]) if valid_id(seg[2]) or seg[2] == "d" + "e" * 11 else None
            if not item:
                return self._error(404, "upload tidak ditemukan")
            return self._json({"upload_id": item["id"], "info": item["info"], "name": item["name"]})

        if n == 4 and seg[1] == "uploads" and seg[3] == "frame.jpg" and method in ("GET", "HEAD"):
            try:
                t = float(query.get("t", ["0"])[0])
                width = int(query.get("w", ["960"])[0])
            except ValueError:
                return self._error(400, "parameter t/w tidak valid")
            try:
                jpg = app.uploads.frame_jpeg(seg[2], max(0.0, t), max(160, min(1920, width)))
            except KeyError:
                return self._error(404, "upload tidak ditemukan")
            except vid.VideoError as e:
                return self._error(422, str(e))
            return self._send(200, jpg, "image/jpeg")

        if method == "POST" and path == "/api/analyze":
            body = self._read_json()
            try:
                roi = check_roi(body.get("roi"))
                cfg = Config.from_overrides(body.get("settings") or {})
                ref_time = max(0.0, float(body.get("ref_time", 0.0)))
                job = app.jobs.start(str(body.get("upload_id", "")), roi, cfg, ref_time,
                                     bool(body.get("save_video", True)))
            except KeyError:
                return self._error(404, "upload tidak ditemukan")
            except (ValueError, TypeError) as e:
                return self._error(400, str(e))
            return self._json({"job_id": job.id}, 202)

        if n == 2 and seg[1] == "jobs" and method == "GET":
            return self._json({"jobs": app.jobs.recent()})

        if n >= 3 and seg[1] == "jobs":
            return self._job_routes(method, seg, query)

        if method == "POST" and path == "/api/live":
            body = self._read_json()
            try:
                roi = check_roi(body.get("roi"))
                cfg = Config.from_overrides(body.get("settings") or {})
            except (ValueError, TypeError) as e:
                return self._error(400, str(e))
            sess = app.live.create(roi, cfg)
            return self._json({"sid": sess.id, "need": cfg.ref_frames}, 201)

        if n >= 3 and seg[1] == "live":
            return self._live_routes(method, seg)

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

        if n == 2 and seg[1] == "events.csv" and method in ("GET", "HEAD"):
            return self._send(200, app.cams.csv().encode("utf-8-sig"), "text/csv; charset=utf-8",
                              {"Content-Disposition": 'attachment; filename="sidewalk-kejadian.csv"'})

        return self._error(404, "endpoint tidak dikenal")

    # ------------------------------------------------------------------ unggahan
    def _upload(self, query: dict) -> None:
        self._body_read = True
        length = self.headers.get("Content-Length")
        if length is None or not length.isdigit() or int(length) == 0:
            raise _HttpError(411, "Content-Length wajib ada dan tidak nol", close=True)
        size = int(length)
        if size > MAX_UPLOAD:
            raise _HttpError(413, f"video melebihi batas {MAX_UPLOAD // (1024 * 1024)} MB", close=True)
        name = Path(query.get("name", ["video.mp4"])[0]).name[:80] or "video.mp4"
        ext = Path(name).suffix.lower()
        if ext not in ALLOWED_EXT:
            ext = ".mp4"
        uid = new_id()
        folder = self.app.store.uploads / uid
        folder.mkdir(parents=True)
        dest = folder / f"video{ext}"
        remaining = size
        with open(dest, "wb") as fh:
            while remaining > 0:
                chunk = self.rfile.read(min(1 << 20, remaining))
                if not chunk:
                    break
                fh.write(chunk)
                remaining -= len(chunk)
        if remaining:
            self.close_connection = True
            dest.unlink(missing_ok=True)
            return self._error(400, "unggahan terputus sebelum selesai", close=True)
        try:
            item = self.app.uploads.register(uid, dest, name)
        except vid.VideoError as e:
            for p in folder.iterdir():
                p.unlink(missing_ok=True)
            folder.rmdir()
            return self._error(422, str(e))
        self._json({"upload_id": uid, "info": item["info"], "name": name}, 201)

    # ---------------------------------------------------------------------- job
    def _job_routes(self, method: str, seg: list[str], query: dict) -> None:
        jid = seg[2]
        if not valid_id(jid):
            return self._error(404, "job tidak ditemukan")
        app = self.app
        n = len(seg)

        if n == 3 and method == "GET":
            pub = app.jobs.public(jid)
            return self._json(pub) if pub else self._error(404, "job tidak ditemukan")

        if n == 4 and seg[3] == "cancel" and method == "POST":
            return self._json({"cancelled": app.jobs.cancel(jid)})

        if n == 4 and seg[3] == "preview.jpg" and method in ("GET", "HEAD"):
            job = app.jobs.get(jid)
            if job is None:
                return self._error(404, "job tidak ditemukan")
            data = job.stages if query.get("view", ["detect"])[0] == "stages" else job.preview
            if data is None:
                return self._send(204, b"", "image/jpeg")
            return self._send(200, data, "image/jpeg")

        out = app.store.runs / jid
        if n == 5 and seg[3] == "events" and method in ("GET", "HEAD"):
            m = re.fullmatch(r"(\d{1,4})\.jpg", seg[4])
            f = out / "events" / seg[4]
            if not m or not f.is_file():
                return self._error(404, "bukti tidak ditemukan")
            return self._send_file(f, "image/jpeg", cache="private, max-age=3600")

        if n == 4 and method in ("GET", "HEAD"):
            files = {
                "result.json": ("application/json; charset=utf-8", f"sidewalk-{jid}.json"),
                "result.csv": ("text/csv; charset=utf-8", f"sidewalk-{jid}.csv"),
                "annotated.mp4": ("video/mp4", None),
            }
            if seg[3] in files and (out / seg[3]).is_file():
                ctype, dl = files[seg[3]]
                return self._send_file(out / seg[3], ctype, download=dl if "download" in query else None)
            return self._error(404, "berkas belum tersedia")

        return self._error(404, "endpoint tidak dikenal")

    # --------------------------------------------------------------------- live
    def _live_routes(self, method: str, seg: list[str]) -> None:
        sid = seg[2]
        sess = self.app.live.get(sid) if valid_id(sid) else None
        if sess is None:
            return self._error(404, "sesi live tidak ditemukan atau sudah berakhir")
        n = len(seg)

        if n == 4 and seg[3] == "frame" and method == "POST":
            body = self._read_body(3 * 1024 * 1024)
            try:
                return self._json(sess.push(body))
            except ValueError as e:
                return self._error(400, str(e))

        if n == 4 and seg[3] == "recalibrate" and method == "POST":
            sess.recalibrate()
            return self._json({"ok": True})

        if n == 3 and method == "DELETE":
            self.app.live.close(sid)
            return self._json({"closed": True})

        if n == 3 and method == "GET":
            return self._json({"state": sess.state, "events": sess.events, "counts": dict(sess.engine.counts)})

        if n == 5 and seg[3] == "events" and method in ("GET", "HEAD"):
            f = sess.out_dir / seg[4]
            if re.fullmatch(r"\d{1,4}\.jpg", seg[4]) and f.is_file():
                return self._send_file(f, "image/jpeg", cache="private, max-age=3600")
            return self._error(404, "bukti tidak ditemukan")

        return self._error(404, "endpoint tidak dikenal")


    # ------------------------------------------------------------------- kamera
    def _cam_routes(self, method: str, seg: list[str], query: dict) -> None:
        mgr = self.app.cams
        n = len(seg)

        if n == 2:
            if method == "GET":
                return self._json({"cams": [c.info() for c in mgr.list()], "max": MAX_CAMERAS,
                                   "now": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
            if method == "POST":
                try:
                    cam = mgr.create(self._read_json())
                except (ValueError, TypeError) as e:
                    return self._error(400, str(e))
                return self._json(cam.info(), 201)
            return self._error(405, "metode tidak diizinkan")

        cam = mgr.get(seg[2]) if valid_id(seg[2]) else None
        if cam is None:
            return self._error(404, "kamera tidak ditemukan")

        if n == 3:
            if method == "GET":
                return self._json(cam.info())
            if method == "POST":
                try:
                    updated = mgr.update(cam.spec.id, self._read_json())
                except (ValueError, TypeError) as e:
                    return self._error(400, str(e))
                return self._json(updated.info()) if updated else self._error(404, "kamera tidak ditemukan")
            if method == "DELETE":
                return self._json({"deleted": mgr.delete(cam.spec.id)})
            return self._error(405, "metode tidak diizinkan")

        if n == 4 and seg[3] == "recalibrate" and method == "POST":
            cam.recalibrate()
            return self._json({"ok": True})

        if n == 4 and seg[3] == "snapshot.jpg" and method in ("GET", "HEAD"):
            if query.get("raw", ["0"])[0] == "1":
                jpg = cam.raw_jpeg()
                if jpg is None:
                    return self._error(503, "kamera belum mengirim frame")
                return self._send(200, jpg, "image/jpeg")
            return self._send(200, cam.jpeg(), "image/jpeg")

        if n == 4 and seg[3] == "stream.mjpg" and method == "GET":
            return self._stream(cam)

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

    def _stream(self, cam) -> None:
        """Siaran MJPEG (multipart/x-mixed-replace): didukung <img> di iOS Safari dan Android Chrome."""
        mgr = self.app.cams
        if not mgr.acquire_stream():
            return self._error(503, "terlalu banyak penonton siaran; tutup salah satu tampilan")
        try:
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=ssframe")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(b"--ssframe\r\n")
            ver, sent = -1, 0
            while not cam.removed:
                cam.touch()
                # Bagian pertama baru digambar peramban ketika bagian berikutnya mulai datang. Kamera statis
                # (selesai/jeda) tak punya frame baru, jadi frame pertama dikirim dua kali berselang singkat;
                # selanjutnya habis waktu = kirim ulang sebagai penanda hidup.
                jpg, ver = cam.wait_frame(ver, timeout=0.15 if sent == 1 else 3.0)
                sent += 1
                self.wfile.write(b"Content-Type: image/jpeg\r\nContent-Length: "
                                 + str(len(jpg := jpg or cam.jpeg())).encode() + b"\r\n\r\n" + jpg + b"\r\n--ssframe\r\n")
                self.wfile.flush()
                time.sleep(0.12)                                  # batasi sekitar 8 gambar per detik
        finally:
            mgr.release_stream()


class _HttpError(Exception):
    def __init__(self, status: int, message: str, close: bool = False):
        super().__init__(message)
        self.status, self.message, self.close = status, message, close


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64
    allow_reuse_address = True


def make_server(host: str, port: int, data_dir: str | Path, lan: bool = False,
                token: str | None = None, retention_days: int = 30) -> tuple[_Server, App]:
    app = App(data_dir, token=token, retention_days=retention_days)
    app.lan = lan
    handler = type("BoundHandler", (Handler,), {"app": app})
    server = _Server((host, port), handler)
    app.host, app.port = host, server.server_address[1]
    return server, app


def register_cli_cameras(app: App, specs: list[str]) -> None:
    """`--cam NAMA=SUMBER`: sumber dari operator boleh berupa jalur berkas. Idempoten menurut nama."""
    known = {c.spec.name for c in app.cams.list()}
    for text in specs:
        name, sep, source = text.partition("=")
        name, source = name.strip(), source.strip()
        if not sep or not name or not source:
            print(f"  [lewati] --cam harus berbentuk NAMA=SUMBER: {text!r}", file=sys.stderr)
            continue
        if name in known:
            continue
        if Path(source).expanduser().is_file():
            source = f"file:{Path(source).expanduser().resolve()}"
        try:
            app.cams.create({"name": name, "source": source, "max_loops": None}, trusted=True)
            known.add(name)
        except ValueError as e:
            print(f"  [lewati] kamera {name!r}: {e}", file=sys.stderr)


def serve(host: str = "127.0.0.1", port: int = 8000, data_dir: str | Path = "data", lan: bool = False,
          cams: list[str] | None = None, retention_days: int = 30) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    token = secrets.token_urlsafe(12) if lan else None
    if lan:
        host = "0.0.0.0"
    server, app = make_server(host, port, data_dir, lan=lan, token=token, retention_days=retention_days)
    register_cli_cameras(app, cams or [])
    bar = "=" * 62
    print(f"\n{bar}\n  SIDEWALK SENTINEL v{__version__}\n{bar}")
    print(f"  Laptop : http://localhost:{app.port}/" + (f"?k={token}" if token else ""))
    if lan:
        print(f"  HP     : http://{lan_ip()}:{app.port}/?k={token}")
        print("           (HP dan laptop harus di Wi-Fi yang sama; tautan ini berkunci)")
        print("  Catatan: kamera langsung di HP butuh HTTPS; unggah/rekam video tetap jalan.")
    print(f"  Data   : {app.store.root}")
    print(f"  Kamera : {len(app.cams.list())} terdaftar; kejadian disimpan {retention_days} hari")
    print(f"  ffmpeg : {'ada' if vid.have_ffmpeg() else 'tidak ada (video beranotasi dinonaktifkan)'}")
    print(f"{bar}\n  Tekan Ctrl+C untuk berhenti.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBerhenti.")
    finally:
        app.close()
        server.server_close()

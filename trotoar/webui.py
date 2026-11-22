"""Dasbor web lokal: kamera langsung + overlay deteksi + panel kejadian.

Hanya memakai pustaka standar Python (``http.server``) di sisi server, jadi tidak ada paket tambahan
yang perlu dipasang. Antarmukanya (``trotoar/web/``) berjalan di browser.

Tiga cara memberi gambar:
  * ``browser`` : browser membuka kamera (laptop atau HP) lewat ``getUserMedia`` atau memutar berkas video,
                  lalu mengirim frame JPEG ke ``POST /api/frame``. Server membalas hasil deteksi (JSON).
  * ``server``  : server sendiri membuka sumbernya (webcam berindeks, URL kamera IP/RTSP, atau berkas video)
                  dan menyiarkannya ke browser sebagai MJPEG (``/stream.mjpg``); hasil deteksi diambil lewat
                  ``GET /api/result``.
Overlay (trotoar, mask kendaraan, label) digambar di browser dari koordinat ternormalisasi 0-1, sehingga
gambar kamera tetap mulus walau YOLO hanya sempat memproses beberapa frame per detik.
"""
from __future__ import annotations

import hmac
import json
import mimetypes
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np

from .config import Config
from .decision import PARKING_STATUSES
from .media import is_live_source, open_capture, parse_source
from .pipeline import AlertSink, StreamState
from .sidewalk import save_polygon_file
from .temporal import CameraMotion
from .visualize import draw_frame

WEB_DIR = Path(__file__).parent / "web"
MAX_FRAME_BYTES = 12 * 1024 * 1024
MAX_JSON_BYTES = 256 * 1024


# ----------------------------------------------------------------------------- geometri untuk overlay
def mask_polygons(mask: np.ndarray, nested: bool = False, largest_only: bool = False, proc_side: int = 320,
                  eps_px: float = 1.4, min_area_frac: float = 0.0006, max_points: int = 900) -> list:
    """Mask bool -> daftar poligon [[x, y], ...] berkoordinat ternormalisasi 0-1.

    ``nested=True`` mempertahankan lubang (kontur dalam) agar bisa digambar dengan aturan even-odd.
    Mask diperkecil dulu (``proc_side``) dan kontur disederhanakan supaya JSON tetap kecil.
    """
    h, w = mask.shape[:2]
    if not mask.any():
        return []
    s = min(1.0, proc_side / max(h, w))
    sw, sh = max(1, round(w * s)), max(1, round(h * s))
    small = mask.astype(np.uint8)
    if s < 1.0:
        small = cv2.resize(small, (sw, sh), interpolation=cv2.INTER_NEAREST)
    cnts, _ = cv2.findContours(small, cv2.RETR_CCOMP if nested else cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cnts = [c for c in cnts if cv2.contourArea(c) >= min_area_frac * sw * sh]
    if largest_only and cnts:
        cnts = [max(cnts, key=cv2.contourArea)]
    eps, polys = eps_px, []
    for _ in range(4):
        polys = [cv2.approxPolyDP(c, eps, True).reshape(-1, 2) for c in cnts]
        if sum(len(p) for p in polys) <= max_points:
            break
        eps *= 1.7
    return [[[round((x + 0.5) / sw, 4), round((y + 0.5) / sh, 4)] for x, y in p] for p in polys if len(p) >= 3]


def clean_polygons(polys) -> list:
    """Validasi poligon kiriman browser: daftar poligon, tiap poligon >= 3 titik [x, y] dalam rentang 0-1."""
    if polys is None:
        return []
    if not isinstance(polys, list) or len(polys) > 20:
        raise ValueError("Poligon trotoar tidak valid.")
    out = []
    for poly in polys:
        if not isinstance(poly, list) or not 3 <= len(poly) <= 500:
            raise ValueError("Setiap poligon trotoar butuh minimal 3 titik.")
        pts = []
        for pt in poly:
            if not (isinstance(pt, (list, tuple)) and len(pt) == 2):
                raise ValueError("Titik poligon harus berbentuk [x, y].")
            x, y = float(pt[0]), float(pt[1])
            if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                raise ValueError("Koordinat poligon harus dalam rentang 0-1.")
            pts.append([round(x, 5), round(y, 5)])
        out.append(pts)
    return out


# ----------------------------------------------------------------------------- sumber di sisi server
class ServerSource:
    """Server membuka kamera/aliran/berkas sendiri: satu thread membaca gambar, satu thread menganalisis.

    Gambar terbaru disimpan agar bisa disiarkan sebagai MJPEG (mulus) sementara analisis berjalan di
    kecepatannya sendiri dan melewatkan frame yang tak sempat diproses.
    """

    def __init__(self, engine: "Engine", spec, speed: float = 1.0):
        self.engine = engine
        self.spec = parse_source(spec)
        self.live = is_live_source(self.spec)
        self.speed = max(0.1, min(float(speed), 50.0))
        self.cap, self.file_fps = None, 30.0
        self.cond = threading.Condition()
        self.frame, self.seq, self.t, self.ended = None, 0, 0.0, False
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def open(self) -> None:
        cap = open_capture(self.spec)
        if not cap.isOpened():
            cap.release()
            raise RuntimeError(f"Tidak bisa membuka sumber: {self.spec!r}. Webcam: coba indeks lain (1, 2). "
                               "Kamera IP: cek alamat, Wi-Fi yang sama, dan aplikasi kameranya sedang aktif.")
        self.cap = cap
        fps = cap.get(cv2.CAP_PROP_FPS)
        self.file_fps = fps if fps and 1 < fps <= 240 else 30.0

    def start(self) -> None:
        self._threads = [threading.Thread(target=self._capture, daemon=True),
                         threading.Thread(target=self._analyze, daemon=True)]
        for th in self._threads:
            th.start()

    def stop(self) -> None:
        self._stop.set()
        with self.cond:
            self.cond.notify_all()
        for th in self._threads:
            if th is not threading.current_thread():
                th.join(timeout=3.0)

    def _capture(self) -> None:
        t0, idx = time.monotonic(), 0
        try:
            while not self._stop.is_set():
                if not self.live:                       # berkas video: putar sesuai kecepatan aslinya
                    delay = t0 + idx / (self.file_fps * self.speed) - time.monotonic()
                    if delay > 0 and self._stop.wait(delay):
                        break
                ok, frame = self.cap.read()
                if not ok:
                    break
                t = (time.monotonic() - t0) if self.live else idx / self.file_fps
                idx += 1
                with self.cond:
                    self.frame, self.t, self.seq = frame, t, self.seq + 1
                    self.cond.notify_all()
        finally:
            with self.cond:
                self.ended = True
                self.cond.notify_all()
            try:
                self.cap.release()
            except Exception:                            # pragma: no cover
                pass

    def _analyze(self) -> None:
        last, msg = 0, None
        try:
            while not self._stop.is_set():
                with self.cond:
                    while self.seq == last and not self.ended and not self._stop.is_set():
                        self.cond.wait(0.5)
                    if self._stop.is_set() or self.seq == last:
                        break
                    frame, t, last = self.frame, self.t, self.seq
                self.engine.process(frame, t)
        except Exception as e:                            # noqa: BLE001 - tampilkan apa pun galatnya ke pengguna
            msg = f"Analisis berhenti karena galat: {e}"
        finally:
            self._stop.set()
            self.engine._on_source_end(self, msg or ("Sumber berhenti mengirim gambar." if self.live
                                                     else "Video selesai."))

    def wait_frame(self, last_seq: int, timeout: float = 1.0):
        """(seq, frame, selesai) -- menunggu frame yang lebih baru dari ``last_seq``."""
        with self.cond:
            if self.seq == last_seq and not self.ended and not self._stop.is_set():
                self.cond.wait(timeout)
            return self.seq, self.frame, (self.ended or self._stop.is_set())


# ----------------------------------------------------------------------------- mesin (keadaan bersama)
class Engine:
    """Memegang Analyzer, keadaan aliran, riwayat kejadian, dan sumber aktif. Aman dipakai banyak thread."""

    def __init__(self, cfg: Config, analyzer_factory, out_dir="live_hasil", polygon=None):
        self.cfg = cfg
        self._factory = analyzer_factory
        self.out_dir = Path(out_dir)
        (self.out_dir / "alerts").mkdir(parents=True, exist_ok=True)
        self.sink = AlertSink(self.out_dir, verbose=False)
        self.analyzer = None
        self.allow_server = True                         # False pada mode --remote
        self.model_state, self.model_error = "memuat", None
        self.polygon = polygon or None
        self.alerts: list[dict] = []                    # riwayat; yang terbaru di akhir
        self.active, self.source_label, self.message = None, None, None
        self.seq, self.latest = 0, None
        self.frames, self.fps, self.latency_ms = 0, 0.0, 0.0
        self._ctl = threading.RLock()                   # menyerialkan start/stop
        self._proc = threading.Lock()                   # menyerialkan analisis & perubahan pengaturan
        self._mut = threading.Lock()                    # melindungi bidang status kecil
        self._ready = threading.Event()
        self._src: ServerSource | None = None
        self._state: StreamState | None = None
        self._last_t, self._shape, self._last_done = None, None, None
        self._sw_cache = None
        self._t_start = None

    # ------------------------------------------------------------------ model
    def load_async(self) -> None:
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self) -> None:
        try:
            an = self._factory(self.cfg)
            if self.polygon:
                an.set_polygon(self.polygon)
            self.analyzer = an
            self.model_state = "siap"
        except Exception as e:                            # noqa: BLE001
            self.model_error = str(e) or e.__class__.__name__
            self.model_state = "galat"
        finally:
            self._ready.set()

    def wait_ready(self, timeout: float = 120.0) -> bool:
        return self._ready.wait(timeout) and self.model_state == "siap"

    # ------------------------------------------------------------------ sesi
    def _reset_stream(self) -> None:
        self.analyzer.seg.reset_tracking()
        self._state = StreamState(self.cfg)
        self._last_t = self._shape = self._last_done = None

    def start(self, kind: str, source=None, speed: float = 1.0) -> None:
        if kind not in ("browser", "server"):
            raise ValueError(f"Jenis sumber tidak dikenal: {kind!r}")
        with self._ctl:
            if self.model_state == "memuat":
                raise RuntimeError("Model deteksi masih dimuat. Tunggu sebentar, lalu coba lagi.")
            if self.model_state == "galat":
                raise RuntimeError(f"Model deteksi gagal dimuat: {self.model_error}")
            src = None
            if kind == "server":
                if not self.allow_server:
                    raise RuntimeError("Sumber di sisi server dimatikan pada mode --remote. "
                                       "Pakai kamera atau video lewat browser.")
                if source is None or str(source).strip() == "":
                    raise ValueError("Isi sumbernya dulu: indeks webcam (0), alamat kamera IP, atau path video.")
                src = ServerSource(self, source, speed)
                src.open()
            self._stop_locked()
            with self._proc:
                self._reset_stream()
                self.sink.reset()
            with self._mut:
                self.frames, self.fps, self.latency_ms, self.latest, self.message = 0, 0.0, 0.0, None, None
                self._t_start = time.monotonic()
                self.source_label = str(src.spec) if src else "browser"
                self._src, self.active = src, kind
            if src:
                src.start()

    def stop(self) -> None:
        with self._ctl:
            self._stop_locked()

    def _stop_locked(self) -> None:
        with self._mut:
            src, was, self._src, self.active = self._src, self.active, None, None
        if src:
            src.stop()
        if was:
            self._write_summary()

    def _on_source_end(self, src: ServerSource, msg: str) -> None:
        """Dipanggil thread analisis saat sumber berhenti sendiri (video selesai, kamera putus, galat)."""
        with self._mut:
            if self._src is not src:                        # sudah dihentikan/diganti lewat stop()
                return
            self._src, self.active, self.message = None, None, msg
        self._write_summary()

    def current_source(self) -> ServerSource | None:
        return self._src

    def _write_summary(self) -> None:
        dur = time.monotonic() - self._t_start if self._t_start else 0.0
        summary = {"sumber": self.source_label, "frame_diproses": self.frames, "durasi_s": round(dur, 1),
                   "alert": list(self.sink.events.values()), "config": self.cfg.to_dict()}
        try:
            (self.out_dir / "ringkasan_live.json").write_text(
                json.dumps(summary, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        except OSError:                                    # pragma: no cover
            pass

    # ------------------------------------------------------------------ analisis satu frame
    def process(self, frame: np.ndarray, t: float) -> dict:
        with self._proc:
            an = self.analyzer
            if an is None or self._state is None:
                raise RuntimeError("Model deteksi belum siap.")
            if (self._shape is not None and frame.shape != self._shape) or \
               (self._last_t is not None and t < self._last_t - 0.25):
                self._reset_stream()                         # resolusi berubah / video diputar mundur
            self._shape, self._last_t = frame.shape, t
            t0 = time.perf_counter()
            sidewalk, results = an.step(self._state, frame, t)
            latency = (time.perf_counter() - t0) * 1000.0
            now = time.monotonic()
            if self._last_done is not None:
                inst = 1.0 / max(1e-6, now - self._last_done)
                self.fps = inst if self.fps == 0 else 0.8 * self.fps + 0.2 * inst
            self._last_done = now
            self.latency_ms = latency if self.frames == 0 else 0.8 * self.latency_ms + 0.2 * latency
            self.frames += 1
            new = self.sink.submit(results, t, lambda: draw_frame(frame, sidewalk, results))
            payload = self._payload(frame.shape, sidewalk, results, t)
        with self._mut:
            for ev in new:
                item = dict(ev)
                item["id"] = len(self.alerts) + 1
                item["foto"] = "/alerts/" + Path(ev["gambar"]).name
                self.alerts.append(item)
            payload["alerts"] = len(self.alerts)
            payload["seq"] = self.seq = self.seq + 1
            self.latest = payload
        return payload

    def _payload(self, shape, sidewalk, results, t) -> dict:
        h, w = shape[:2]
        if self._sw_cache is None or self._sw_cache[0] is not sidewalk:
            self._sw_cache = (sidewalk, mask_polygons(sidewalk, nested=True))
        vehicles = []
        for r in results:
            x1, y1, x2, y2 = r.obj.box
            poly = mask_polygons(r.obj.mask, largest_only=True, eps_px=1.2)
            vehicles.append({
                "id": r.track_id, "kelas": r.obj.label, "conf": round(float(r.obj.conf), 2), "status": r.status,
                "dwell": round(float(r.dwell_s), 1), "di_trotoar": bool(r.on_sidewalk), "pengendara": r.has_rider,
                "box": [round(x1 / w, 4), round(y1 / h, 4), round(x2 / w, 4), round(y2 / h, 4)],
                "poly": poly[0] if poly else None})
        return {"t": round(float(t), 2), "w": int(w), "h": int(h), "fps": round(self.fps, 1),
                "latency_ms": round(self.latency_ms), "min_dwell": self.cfg.min_dwell_s,
                "sidewalk": self._sw_cache[1], "vehicles": vehicles,
                "summary": {"kendaraan": len(results), "di_trotoar": sum(1 for r in results if r.on_sidewalk),
                            "parkir": sum(1 for r in results if r.status in PARKING_STATUSES)}}

    # ------------------------------------------------------------------ pengaturan & trotoar
    def settings(self) -> dict:
        c = self.cfg
        return {"min_dwell_s": c.min_dwell_s, "tau": c.tau_contact, "camera_comp": c.compensate_camera}

    def update_settings(self, data: dict) -> dict:
        def num(key, lo, hi, label):
            try:
                v = float(data[key])
            except (TypeError, ValueError):
                raise ValueError(f"{label}: nilainya harus berupa angka.")
            if not lo <= v <= hi:
                raise ValueError(f"{label}: harus antara {lo:g} dan {hi:g}.")
            return v

        changes = {}
        if "min_dwell_s" in data:
            changes["min_dwell_s"] = num("min_dwell_s", 0.5, 3600, "Lama diam")
        if "tau" in data:
            changes["tau"] = num("tau", 0.05, 0.95, "Ambang trotoar")
        if "camera_comp" in data:
            changes["camera_comp"] = bool(data["camera_comp"])
        with self._proc:
            if "min_dwell_s" in changes:
                self.cfg.min_dwell_s = changes["min_dwell_s"]
            if "tau" in changes:
                self.cfg.tau_contact = self.cfg.tau_ring = changes["tau"]
            if "camera_comp" in changes:
                self.cfg.compensate_camera = changes["camera_comp"]
                if self._state is not None:
                    self._state.cam = CameraMotion() if changes["camera_comp"] else None
        return self.settings()

    def set_polygon(self, polys) -> list | None:
        polys = clean_polygons(polys)
        with self._proc:
            self.polygon = polys or None
            if self.analyzer is not None:
                self.analyzer.set_polygon(self.polygon)
            if self._state is not None:
                self._state.force_refresh = True
        if polys:
            save_polygon_file(self.out_dir / "trotoar_polygon.json", polys)
        return self.polygon

    def refresh_sidewalk(self) -> None:
        with self._proc:
            if self._state is not None:
                self._state.force_refresh = True

    # ------------------------------------------------------------------ status untuk UI
    def status(self) -> dict:
        with self._mut:
            return {"model": self.model_state, "model_error": self.model_error, "active": self.active,
                    "source": self.source_label if self.active else None, "message": self.message,
                    "trotoar": "manual" if self.polygon else "otomatis", "polygon": self.polygon or [],
                    "settings": self.settings(), "alerts": len(self.alerts), "frames": self.frames,
                    "fps": round(self.fps, 1), "out_dir": str(self.out_dir.resolve())}


# ----------------------------------------------------------------------------- HTTP
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_LOOPBACK = ("localhost", "127.0.0.1", "::1")
_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
          ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
          ".ico": "image/x-icon", ".jpg": "image/jpeg"}


def _host_name(header: str) -> str:
    header = (header or "").strip().lower()
    if header.startswith("["):
        return header[1:header.find("]")] if "]" in header else header
    return header.split(":")[0]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "SidewalkSentinel"

    def log_message(self, *args) -> None:            # senyap; galat ditangani di bawah
        pass

    @property
    def engine(self) -> Engine:
        return self.server.engine

    # ---- balasan
    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if self.close_connection:
            self.send_header("Connection", "close")          # beri tahu klien agar membuka koneksi baru
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _error(self, code: int, msg: str) -> None:
        self._json({"error": msg}, code)

    def _file(self, path: Path, ctype: str | None = None, extra: dict | None = None) -> None:
        try:
            data = path.read_bytes()
        except OSError:
            return self._error(404, "Berkas tidak ditemukan.")
        self._send(200, data, ctype or _TYPES.get(path.suffix.lower()) or mimetypes.guess_type(str(path))[0]
                   or "application/octet-stream", extra)

    # ---- penjaga
    def _guard(self) -> bool:
        """Tolak Host asing (DNS rebinding) bila server hanya terikat ke loopback; pada mode --remote wajib kunci akses."""
        if self.server.loopback and _host_name(self.headers.get("Host", "")) not in _LOOPBACK:
            self._error(403, "Alamat tidak diizinkan. Buka lewat http://localhost.")
            return False
        token = self.server.token
        if token:
            jar = SimpleCookie(self.headers.get("Cookie", ""))
            have = jar["ss_key"].value if "ss_key" in jar else ""
            if not hmac.compare_digest(have.encode(), token.encode()):
                url = urlparse(self.path)
                key = parse_qs(url.query).get("key", [""])[0]
                if url.path in ("/", "/index.html") and hmac.compare_digest(key.encode(), token.encode()):
                    self.send_response(302)                      # simpan kunci di cookie, lalu bersihkan alamatnya
                    self.send_header("Location", "/")
                    self.send_header("Set-Cookie", f"ss_key={token}; Path=/; HttpOnly; SameSite=Strict")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return False
                self.close_connection = True
                self._error(401, "Butuh kunci akses. Buka tautan lengkap (dengan ?key=...) yang dicetak di terminal.")
                return False
        return True

    def _read_body(self, limit: int) -> bytes | None:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > limit:
            self._error(413, "Isi permintaan terlalu besar.")
            self.close_connection = True
            return None
        return self.rfile.read(n) if n else b""

    def _read_json(self) -> dict | None:
        if not (self.headers.get("Content-Type") or "").lower().startswith("application/json"):
            self.close_connection = True                     # isi permintaan tidak dibaca
            self._error(415, "Content-Type harus application/json.")
            return None
        raw = self._read_body(MAX_JSON_BYTES)
        if raw is None:
            return None
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            self._error(400, "JSON tidak valid.")
            return None
        if not isinstance(data, dict):
            self._error(400, "JSON harus berupa objek.")
            return None
        return data

    # ---- GET
    def do_GET(self) -> None:
        self._dispatch(self._get)

    do_HEAD = do_GET

    def _get(self) -> None:
        url = urlparse(self.path)
        path, qs = url.path, parse_qs(url.query)
        if path in ("/", "/index.html"):
            return self._file(WEB_DIR / "index.html")
        if path.startswith("/static/"):
            name = path[len("/static/"):]
            if _SAFE_NAME.match(name) and (WEB_DIR / name).is_file():
                return self._file(WEB_DIR / name)
            return self._error(404, "Berkas tidak ditemukan.")
        if path == "/favicon.ico":
            return self._file(WEB_DIR / "favicon.svg", "image/svg+xml")
        if path == "/api/status":
            return self._json(self.engine.status())
        if path == "/api/alerts":
            with self.engine._mut:
                items = list(reversed(self.engine.alerts))
            return self._json({"alerts": items})
        if path == "/api/result":
            try:
                since = int(qs.get("since", ["0"])[0])
            except ValueError:
                since = 0
            latest = self.engine.latest
            if latest is None or latest.get("seq", 0) <= since:
                return self._send(204, b"", "application/json")
            return self._json(latest)
        if path.startswith("/alerts/"):
            name = path[len("/alerts/"):]
            if _SAFE_NAME.match(name) and name.lower().endswith(".jpg"):
                return self._file(self.engine.out_dir / "alerts" / name, "image/jpeg")
            return self._error(404, "Berkas tidak ditemukan.")
        if path == "/kejadian.csv":
            return self._file(self.engine.out_dir / "kejadian.csv", "text/csv; charset=utf-8",
                              {"Content-Disposition": 'attachment; filename="kejadian.csv"'})
        if path == "/stream.mjpg":
            return self._mjpeg()
        return self._error(404, "Halaman tidak ditemukan.")

    def _mjpeg(self) -> None:
        src = self.engine.current_source()
        if src is None:
            return self._error(404, "Tidak ada sumber di server yang sedang aktif.")
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        last, t_sent = 0, 0.0
        while self.engine.current_source() is src:
            seq, frame, ended = src.wait_frame(last, 1.0)
            if frame is None or seq == last:
                if ended:
                    break
                continue
            last = seq
            wait = t_sent + 1 / 25 - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            h, w = frame.shape[:2]
            s = min(1.0, 960 / max(h, w))
            small = cv2.resize(frame, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else frame
            ok, jpg = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 78])
            if not ok:
                continue
            data = jpg.tobytes()
            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(data))
            self.wfile.write(data + b"\r\n")
            t_sent = time.monotonic()

    # ---- POST
    def do_POST(self) -> None:
        self._dispatch(self._post)

    def _post(self) -> None:
        url = urlparse(self.path)
        path, qs = url.path, parse_qs(url.query)
        eng = self.engine
        if path == "/api/frame":
            if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "image/jpeg":
                self.close_connection = True                 # isi permintaan tidak dibaca
                return self._error(415, "Content-Type harus image/jpeg.")
            raw = self._read_body(MAX_FRAME_BYTES)
            if raw is None:
                return
            if eng.active != "browser":
                return self._error(409, "Pemantauan belum dimulai. Tekan Mulai pantau.")
            frame = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                return self._error(400, "Gambar tidak bisa dibaca (harus JPEG).")
            try:
                t = float(qs.get("t", ["0"])[0])
            except ValueError:
                return self._error(400, "Parameter t harus angka (detik).")
            try:
                return self._json(eng.process(frame, t))
            except RuntimeError:
                raise                                          # mis. model belum siap -> 409 lewat _dispatch
            except Exception as e:                            # noqa: BLE001 - galat analisis bukan salah klien
                print(f"[web] analisis frame gagal: {e!r}", file=sys.stderr)
                return self._error(500, f"Analisis gagal: {e}")
        data = self._read_json()
        if data is None:
            return
        if path == "/api/start":
            kind = data.get("kind")
            eng.start(kind, data.get("source"), data.get("speed", 1.0))
            return self._json(eng.status())
        if path == "/api/stop":
            eng.stop()
            return self._json(eng.status())
        if path == "/api/sidewalk":
            eng.set_polygon(data.get("polygons"))
            return self._json(eng.status())
        if path == "/api/settings":
            eng.update_settings(data)
            return self._json(eng.status())
        if path == "/api/refresh":
            eng.refresh_sidewalk()
            return self._json(eng.status())
        return self._error(404, "Halaman tidak ditemukan.")

    # ---- penanganan galat umum
    def _dispatch(self, fn) -> None:
        try:
            if self._guard():
                fn()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True
        except (ValueError, TypeError) as e:
            self._safe_error(400, str(e))
        except RuntimeError as e:
            self._safe_error(409, str(e))
        except Exception as e:                            # noqa: BLE001
            print(f"[web] galat tak terduga: {e!r}", file=sys.stderr)
            self._safe_error(500, f"Galat di server: {e}")

    def _safe_error(self, code: int, msg: str) -> None:
        try:
            self._error(code, msg)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            self.close_connection = True


class AppServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, engine: Engine, remote: bool = False):
        super().__init__(addr, Handler)
        self.engine = engine
        self.token = secrets.token_urlsafe(18) if remote else None   # mode --remote: kunci akses acak
        self.loopback = addr[0] in _LOOPBACK and not remote
        if remote:
            engine.allow_server = False


def make_server(engine: Engine, host: str = "127.0.0.1", port: int = 8765, tries: int = 20,
                remote: bool = False) -> AppServer:
    """Buat server; bila port dipakai program lain, coba port berikutnya."""
    last = None
    for p in range(port, port + (tries if port else 1)):
        try:
            return AppServer((host, p), engine, remote)
        except OSError as e:
            last = e
    raise RuntimeError(f"Tidak ada port kosong mulai dari {port}: {last}")


def open_window(url: str) -> None:
    """Buka dasbor sebagai jendela aplikasi (Edge/Chrome mode --app, tanpa bilah alamat); cadangan: browser biasa."""
    cands: list[str] = []
    if sys.platform.startswith("win"):
        for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                cands += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                          os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")]
    elif sys.platform == "darwin":
        cands += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    else:
        cands += [p for p in (shutil.which(n) for n in ("google-chrome", "chromium", "chromium-browser",
                                                         "microsoft-edge")) if p]
    for exe in cands:
        if os.path.exists(exe):
            try:
                subprocess.Popen([exe, f"--app={url}", "--window-size=1440,900"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except OSError:
                continue
    webbrowser.open(url)


def _lan_ip() -> str | None:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


def serve(engine: Engine, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True,
          remote: bool = False) -> None:
    server = make_server(engine, host, port, remote=remote)
    shown = "localhost" if host in ("127.0.0.1", "0.0.0.0", "") else host
    key = f"/?key={server.token}" if server.token else ""
    url = f"http://{shown}:{server.server_port}{key}"
    engine.load_async()
    print(f"Sidewalk Sentinel berjalan di {url}   (hentikan dengan Ctrl+C)")
    if remote:
        print(f"Mode --remote: sambungkan terowongan HTTPS ke port {server.server_port}, mis.\n"
              f"    cloudflared tunnel --url http://localhost:{server.server_port}\n"
              f"lalu buka alamat https yang diberikan, ditambah  /?key={server.token}  di HP.\n"
              "Sumber di sisi server (webcam/RTSP/berkas) dimatikan; pakai kamera/video lewat browser.")
    elif host in ("0.0.0.0", ""):
        ip = _lan_ip()
        if ip:
            print(f"Dari perangkat lain di Wi-Fi yang sama: http://{ip}:{server.server_port}  "
                  "(kamera HP lewat browser butuh HTTPS: pakai --remote, lihat README)")
    print(f"Hasil (foto bukti, kejadian.csv) disimpan di: {engine.out_dir.resolve()}")
    if open_browser:
        threading.Timer(0.5, open_window, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDihentikan.")
    finally:
        engine.stop()
        server.server_close()

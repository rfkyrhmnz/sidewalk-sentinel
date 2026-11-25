"""Kamera server (mode CCTV): sumber video, pekerja yang berjalan terus, dan penyimpanan kejadian.

Server menarik sendiri video dari kamera (RTSP, IP camera HTTP, webcam USB, atau rekaman yang diulang),
belajar latar secara otomatis (lihat monitor.py), lalu mencatat kejadian ke disk. HP dan laptop hanya
menjadi layar pemantau, jadi tidak perlu HTTPS untuk kamera di HP.

Keamanan sumber: antarmuka web tidak boleh dipakai membaca berkas server atau memindai jaringan, jadi
sumber yang diterima API dibatasi ke ``rtsp(s)://``, ``http(s)://``, ``device:N``, ``upload:ID`` (unggahan
yang sudah ada), dan ``demo``. Alamat berkas bebas hanya boleh lewat baris perintah (``--cam``).
Kredensial di alamat RTSP tidak pernah dikembalikan lewat API atau dicatat di log.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import shutil
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import cv2
import numpy as np

from . import render  # noqa: E402
from .config import Config  # noqa: E402
from .jobs import check_roi, new_id, valid_id  # noqa: E402
from .monitor import LEARN, Monitor  # noqa: E402
from .synthetic import ROI as DEMO_ROI  # noqa: E402

log = logging.getLogger("sidewalk")

MAX_CAMERAS = 8
MAX_STREAMS = 12                      # penonton siaran MJPEG serentak
URL_SCHEMES = ("rtsp", "rtsps", "http", "https")
STATUS = ("perlu_area", "menghubungkan", "belajar", "memantau", "terputus", "jeda", "selesai")


class SourceError(Exception):
    """Sumber kamera tidak bisa dibuka."""


RECORDING_KINDS = ("demo", "upload", "file")


def source_kind(text: str) -> str:
    """Jenis sumber dari teksnya, tanpa memeriksa keberadaan berkas (aman dipakai untuk menampilkan)."""
    s = text or ""
    if s == "demo":
        return "demo"
    for prefix, kind in (("upload:", "upload"), ("device:", "device"), ("file:", "file")):
        if s.startswith(prefix):
            return kind
    return "url"


# ====================================================================== sumber kamera
@dataclass(frozen=True)
class Source:
    kind: str            # demo | upload | device | url | file
    value: str

    @property
    def is_recording(self) -> bool:
        return self.kind in RECORDING_KINDS


def parse_source(text: str, trusted: bool = False) -> Source:
    s = str(text or "").strip()
    if not s:
        raise ValueError("sumber kamera kosong")
    if len(s) > 500 or any(ord(c) < 32 for c in s):
        raise ValueError("sumber kamera tidak valid")
    if s == "demo":
        return Source("demo", "")
    if s.startswith("upload:"):
        if not valid_id(s[7:]):
            raise ValueError("id unggahan tidak valid")
        return Source("upload", s[7:])
    if s.startswith("device:"):
        if not (s[7:].isdigit() and int(s[7:]) <= 9):
            raise ValueError("nomor kamera USB harus 0 sampai 9")
        return Source("device", s[7:])
    if s.startswith("file:"):
        if not trusted:
            raise ValueError("sumber berkas hanya boleh dari baris perintah (--cam)")
        path = Path(s[5:]).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"berkas tidak ada: {path}")
        return Source("file", str(path))
    try:
        parts = urlsplit(s)
        scheme, host = parts.scheme.lower(), parts.hostname
    except ValueError as e:
        raise ValueError("alamat kamera tidak valid") from e
    if scheme in URL_SCHEMES and host:
        return Source("url", s)
    raise ValueError("sumber harus rtsp://..., http(s)://..., device:N, upload:ID, atau demo")


_USERINFO = re.compile(r"(://)[^/@\s]*@")
_SECRET_QS = re.compile(r"(?i)([?&](?:pass\w*|pwd|token|key|secret|auth\w*|sig\w*)=)[^&#]*")


def mask_source(text: str) -> str:
    """Samarkan kredensial (user:sandi@ dan parameter rahasia) sebelum ditampilkan atau dicatat."""
    return _SECRET_QS.sub(r"\1***", _USERINFO.sub(r"\1***@", text or ""))


def open_capture(src: Source, target) -> cv2.VideoCapture:
    """Buka sumber. `target` = hasil resolusi (jalur berkas, nomor perangkat, atau URL)."""
    if src.kind == "device":
        cap = cv2.VideoCapture(int(target), cv2.CAP_DSHOW) if sys.platform == "win32" else cv2.VideoCapture(int(target))
    elif src.kind == "url":
        try:
            params = [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 8000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 8000]
            cap = cv2.VideoCapture(str(target), cv2.CAP_FFMPEG, params)
        except (TypeError, AttributeError, cv2.error):
            cap = cv2.VideoCapture(str(target), cv2.CAP_FFMPEG)
    else:
        cap = cv2.VideoCapture(str(target))
    if not cap.isOpened():
        cap.release()
        raise SourceError({"device": "webcam tidak bisa dibuka (dipakai aplikasi lain atau tidak ada)",
                           "url": "tidak bisa terhubung ke kamera (cek alamat, sandi, dan jaringan)"}
                          .get(src.kind, "video tidak bisa dibuka"))
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except cv2.error:
        pass
    return cap


# ===================================================================== spesifikasi
@dataclass
class CameraSpec:
    id: str
    name: str
    source: str
    roi: list | None = None
    settings: dict = field(default_factory=dict)
    learn: str = "normal"
    enabled: bool = True
    max_loops: int | None = None      # sumber rekaman saja: None = ulangi terus
    speed: float = 1.0                # sumber rekaman saja: 1 = waktu nyata, 0 = secepat mungkin
    created: str = ""


def _clean_name(value) -> str:
    name = re.sub(r"[\x00-\x1f\x7f]", "", str(value or "")).strip()
    if not 1 <= len(name) <= 40:
        raise ValueError("nama kamera harus 1 sampai 40 karakter")
    return name


def build_spec(data: dict, existing: CameraSpec | None = None, trusted: bool = False) -> CameraSpec:
    """Validasi dan normalisasi isi permintaan. `existing` = pembaruan (sumber tidak boleh berubah)."""
    if existing is None:
        src = parse_source(data.get("source", ""), trusted=trusted)
        demo = src.kind == "demo"
        spec = CameraSpec(id=new_id(), name="", source=str(data.get("source", "")).strip(),
                          created=datetime.now().astimezone().isoformat(timespec="seconds"))
        if demo:                                    # bawaan demo: cepat belajar, cepat memicu, berhenti sendiri
            spec.name, spec.roi = "Kamera demo", [list(p) for p in DEMO_ROI]
            spec.settings, spec.learn, spec.max_loops = {"park_seconds": 5}, "cepat", 2
        elif src.is_recording:
            spec.learn, spec.max_loops = "cepat", 1
    else:
        if "source" in data and str(data["source"]).strip() != existing.source:
            raise ValueError("sumber kamera tidak bisa diubah; hapus lalu tambahkan kamera baru")
        spec = CameraSpec(**asdict(existing))
        src = Source(source_kind(spec.source), "")

    if "name" in data or not spec.name:
        spec.name = _clean_name(data.get("name", spec.name))
    if "roi" in data:
        spec.roi = None if data["roi"] is None else [list(p) for p in check_roi(data["roi"])]
    if "settings" in data:
        overrides = data["settings"] or {}
        if not isinstance(overrides, dict):
            raise ValueError("settings harus berupa objek")
        Config.from_overrides(overrides)            # menolak kunci tak dikenal / nilai di luar batas
        spec.settings = dict(overrides)
    if "learn" in data:
        if data["learn"] not in LEARN:
            raise ValueError(f"learn harus salah satu dari {sorted(LEARN)}")
        spec.learn = data["learn"]
    if "enabled" in data:
        spec.enabled = bool(data["enabled"])
    if src.is_recording:
        if "max_loops" in data:
            ml = data["max_loops"]
            spec.max_loops = None if ml is None else int(ml)
            if spec.max_loops is not None and not 1 <= spec.max_loops <= 1000:
                raise ValueError("max_loops harus 1..1000 atau kosong (ulangi terus)")
        if "speed" in data:
            spec.speed = float(data["speed"])
            if not 0 <= spec.speed <= 16:
                raise ValueError("speed harus 0..16")
    return spec


# ====================================================================== kejadian
def _now() -> datetime:
    return datetime.now().astimezone()


class EventStore:
    """Kejadian satu kamera: events.jsonl (satu baris per kejadian) dan bukti foto di events/NNN.jpg."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.events_dir = folder / "events"
        self.events_dir.mkdir(parents=True, exist_ok=True)
        self._file = folder / "events.jsonl"
        self._meta = folder / "meta.json"
        self._lock = threading.Lock()
        self.records: list[dict] = []
        self._dts: list[datetime] = []
        self._load()

    def _load(self) -> None:
        if self._file.exists():
            for line in self._file.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    rec = json.loads(line)
                    dt = datetime.fromisoformat(rec["ts"])
                    if not isinstance(rec.get("no"), int):
                        continue
                except (ValueError, KeyError, TypeError):      # baris rusak (mis. mati listrik): lewati
                    continue
                self.records.append(rec)
                self._dts.append(dt)
        nxt = 1 + max((r["no"] for r in self.records), default=0)
        try:
            nxt = max(nxt, int(json.loads(self._meta.read_text(encoding="utf-8"))["next_no"]))
        except (OSError, ValueError, KeyError, TypeError):
            pass
        self.next_no = nxt

    def append(self, ev: dict, snapshot: bytes | None) -> dict:
        with self._lock:
            no = self.next_no
            self.next_no += 1
            now = _now()
            rec = {"no": no, "ts": now.isoformat(timespec="seconds"), "kind": ev["kind"], "label": ev["label"],
                   "reason": ev["reason"], "track_id": ev["track_id"],
                   "snapshot": f"events/{no}.jpg" if snapshot else None}
            if snapshot:
                (self.events_dir / f"{no}.jpg").write_bytes(snapshot)
            with open(self._file, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self._meta.write_text(json.dumps({"next_no": self.next_no}), encoding="utf-8")
            self.records.append(rec)
            self._dts.append(now)
            return rec

    def snapshot_path(self, no: int) -> Path | None:
        p = self.events_dir / f"{int(no)}.jpg"
        return p if p.is_file() else None

    def after(self, no: int, limit: int = 200) -> list[dict]:
        with self._lock:
            return [r for r in self.records if r["no"] > no][:limit]

    def latest(self, limit: int = 50, before_no: int | None = None, kind: str | None = None) -> list[dict]:
        with self._lock:
            rows = [r for r in reversed(self.records)
                    if (before_no is None or r["no"] < before_no) and (kind is None or r["kind"] == kind)]
        return rows[:limit]

    def counts_since(self, since: datetime) -> dict:
        out = {"parkir": 0, "naik_trotoar": 0}
        with self._lock:
            for r, dt in zip(self.records, self._dts):
                if dt >= since and r["kind"] in out:
                    out[r["kind"]] += 1
        return out

    @property
    def seq(self) -> int:
        with self._lock:
            return self.records[-1]["no"] if self.records else 0

    def pairs(self) -> list[tuple[datetime, dict]]:
        with self._lock:
            return list(zip(self._dts, self.records))

    def prune(self, cutoff: datetime, max_events: int) -> int:
        """Hapus kejadian lebih tua dari `cutoff` dan sisakan paling banyak `max_events` terbaru."""
        with self._lock:
            keep = [i for i, d in enumerate(self._dts) if d >= cutoff][-max_events:]
            if len(keep) == len(self.records):
                return 0
            keep_set = set(keep)
            for i, r in enumerate(self.records):
                if i not in keep_set:
                    (self.events_dir / f"{r['no']}.jpg").unlink(missing_ok=True)
            tmp = self._file.with_suffix(".tmp")
            tmp.write_text("".join(json.dumps(self.records[i], ensure_ascii=False) + "\n" for i in keep),
                           encoding="utf-8")
            tmp.replace(self._file)
            removed = len(self.records) - len(keep)
            self.records = [self.records[i] for i in keep]
            self._dts = [self._dts[i] for i in keep]
            return removed


# ======================================================================= gambar
def status_card(lines: list[str], size=(640, 360)) -> np.ndarray:
    """Gambar pengganti saat belum ada frame: teks status di atas panel gelap bergaya HUD."""
    w, h = size
    img = np.full((h, w, 3), (24, 16, 10), np.uint8)
    cv2.rectangle(img, (6, 6), (w - 7, h - 7), (90, 70, 40), 1)
    y = h // 2 - 12 * (len(lines) - 1)
    for i, line in enumerate(lines):
        scale = 0.7 if i == 0 else 0.5
        (tw, _), _ = cv2.getTextSize(line, render.FONT, scale, 1)
        cv2.putText(img, line, (max(10, (w - tw) // 2), y + i * 26), render.FONT, scale,
                    render.CYAN if i == 0 else render.DIM, 1, cv2.LINE_AA)
    return img


def _ascii(text: str, limit: int = 24) -> str:
    return text.encode("ascii", "replace").decode()[:limit]


def _shrink(frame: np.ndarray, width: int) -> np.ndarray:
    h, w = frame.shape[:2]
    if w <= width:
        return frame
    return cv2.resize(frame, (width, max(1, round(h * width / w))), interpolation=cv2.INTER_AREA)


# ======================================================================= kamera
class Camera:
    """Satu kamera: thread yang terus membaca sumber, memantau, dan mencatat kejadian."""

    def __init__(self, spec: CameraSpec, root: Path, resolver):
        self.spec = spec
        self.dir = root / spec.id
        self.events = EventStore(self.dir)
        self._resolver = resolver
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._flags = {"reconf": False, "recal": False}
        self._thread: threading.Thread | None = None
        self.removed = False
        # status publik
        self.status, self.message = "menghubungkan", ""
        self.learn = (0, 0)
        self.relearns = 0
        self.fps = 0.0
        self.size: tuple[int, int] | None = None
        self.connected_since: float | None = None
        # gambar terbaru
        self._jpg: bytes | None = None
        self._jpg_ver = 0
        self._jpg_t = 0.0
        self._frame: np.ndarray | None = None
        self._watch_until = 0.0
        self._last_proc: float | None = None

    # -------------------------------------------------------------- siklus hidup
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=f"cam-{self.spec.id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.removed = True
        self._stop.set()
        self._wake.set()
        with self._cond:
            self._cond.notify_all()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)

    def reconfigure(self, spec: CameraSpec, restart: bool = True) -> None:
        """Terapkan spesifikasi baru. `restart` = bangun ulang sesi (belajar latar dari awal)."""
        with self._lock:
            self.spec = spec
            if restart:
                self._flags["reconf"] = True
        self._wake.set()

    def recalibrate(self) -> None:
        with self._lock:
            self._flags["recal"] = True
        self._wake.set()

    def _take(self, name: str) -> bool:
        with self._lock:
            v, self._flags[name] = self._flags[name], False
        return v

    def _set(self, status: str, message: str = "") -> None:
        with self._lock:
            self.status, self.message = status, message

    # ------------------------------------------------------------------ gambar
    def touch(self) -> None:
        """Ada penonton: render gambar beranotasi dengan laju penuh beberapa detik ke depan."""
        self._watch_until = time.monotonic() + 8.0

    def _want_image(self) -> bool:
        now = time.monotonic()
        return now < self._watch_until or self._jpg is None or now - self._jpg_t > 5.0

    def _publish(self, img: np.ndarray) -> None:
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 72])
        if ok:
            with self._cond:
                self._jpg, self._jpg_t = buf.tobytes(), time.monotonic()
                self._jpg_ver += 1
                self._cond.notify_all()

    def _publish_last(self, monitor: Monitor | None) -> None:
        """Terbitkan frame terakhir yang diproses: kamera yang berhenti (selesai/jeda) harus menampilkan
        adegan terakhirnya, bukan gambar basi dari beberapa detik lalu (tanpa penonton gambar jarang diperbarui)."""
        if monitor is not None and monitor.state == "memantau" and monitor.engine.last:
            self._publish(monitor.engine.annotated())

    def _publish_plain(self, frame: np.ndarray, caption: str) -> None:
        img = _shrink(frame, 640).copy()
        render.put_text(img, caption, (8, 18), render.CYAN, 0.45)
        self._publish(img)

    def jpeg(self) -> bytes:
        """Gambar beranotasi terbaru; bila belum ada, kartu status. Selalu mengembalikan gambar."""
        self.touch()
        with self._lock:
            jpg, status, message = self._jpg, self.status, self.message
        if jpg is not None:
            return jpg
        lines = {"menghubungkan": ["MENGHUBUNGKAN", "menunggu frame pertama"],
                 "terputus": ["TERPUTUS", message or "mencoba lagi"],
                 "jeda": ["DIJEDA", "tekan Lanjutkan untuk memantau"],
                 "selesai": ["REKAMAN SELESAI", "tekan Putar ulang"]}.get(status, [status.upper(), message])
        ok, buf = cv2.imencode(".jpg", status_card(lines), [cv2.IMWRITE_JPEG_QUALITY, 80])
        return buf.tobytes()

    def raw_jpeg(self) -> bytes | None:
        """Frame asli terbaru tanpa anotasi (untuk menandai area). None bila belum ada frame sungguhan."""
        self.touch()
        with self._lock:
            frame = self._frame
        if frame is None:
            return None
        ok, buf = cv2.imencode(".jpg", _shrink(frame, 960), [cv2.IMWRITE_JPEG_QUALITY, 88])
        return buf.tobytes() if ok else None

    def wait_frame(self, last_ver: int, timeout: float = 5.0) -> tuple[bytes | None, int]:
        with self._cond:
            if self._jpg_ver == last_ver and not self.removed:
                self._cond.wait(timeout)
            return self._jpg, self._jpg_ver

    # ------------------------------------------------------------------ informasi
    def info(self) -> dict:
        today = _now().replace(hour=0, minute=0, second=0, microsecond=0)
        counts = self.events.counts_since(today)
        with self._lock:
            spec = self.spec
            last = self.events.records[-1] if self.events.records else None
            return {
                "id": spec.id, "name": spec.name, "source": mask_source(spec.source), "kind": source_kind(spec.source),
                "roi": spec.roi, "settings": spec.settings, "learn": spec.learn, "enabled": spec.enabled,
                "max_loops": spec.max_loops, "speed": spec.speed, "created": spec.created,
                "status": self.status, "message": self.message,
                "learn_progress": {"have": self.learn[0], "need": self.learn[1]},
                "relearns": self.relearns, "fps": round(self.fps, 1), "size": list(self.size) if self.size else None,
                "uptime_s": round(time.time() - self.connected_since) if self.connected_since else None,
                "counts_today": counts, "seq": self.events.seq,
                "last_event": ({"no": last["no"], "kind": last["kind"], "ts": last["ts"]} if last else None),
            }

    # ---------------------------------------------------------------- pekerja
    def _run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            if not self.spec.enabled:
                self._set("jeda", "Dijeda")
                self._wake.wait(0.5)
                self._wake.clear()
                continue
            outcome = self._session()
            if outcome == "stop":
                return
            if outcome == "putus":
                attempt += 1
                wait = min(30, 2 ** min(attempt, 5))
                base = self.message or "sumber terputus"
                for left in range(wait, 0, -1):
                    self._set("terputus", f"{base}. Mencoba lagi dalam {left} dtk")
                    if self._stop.wait(1.0) or self._wake.is_set():
                        break
                self._wake.clear()
            elif outcome == "selesai":
                self._set("selesai", "Rekaman selesai diputar")
                while not self._stop.is_set() and not self._wake.is_set():
                    self._wake.wait(0.5)
                self._wake.clear()
                self._take("recal")             # "putar ulang" = mulai sesi baru
                self._take("reconf")
            else:                               # 'ulang' (konfigurasi berubah) atau 'jeda'
                attempt = 0
                self._wake.clear()

    def _build_monitor(self, spec: CameraSpec, cfg: Config) -> Monitor | None:
        if not spec.roi:
            return None
        mon = Monitor(spec.roi, cfg, spec.learn)
        mon.engine.header_fn = lambda t: (f"{_ascii(self.spec.name)}  {datetime.now():%Y-%m-%d %H:%M:%S}  "
                                          f"P:{mon.counts['parkir']} N:{mon.counts['naik_trotoar']}")
        return mon

    def _session(self) -> str:
        spec = self.spec
        try:
            src = parse_source(spec.source, trusted=True)
            cfg = Config.from_overrides(spec.settings)
            target = self._resolver(src)
            self._set("menghubungkan", "Menghubungkan ke kamera")
            cap = open_capture(src, target)
        except (ValueError, SourceError) as e:
            self._set("terputus", str(e))
            return "putus"
        except Exception:               # noqa: BLE001
            log.exception("kamera %s: galat saat membuka sumber", spec.name)
            self._set("terputus", "galat internal saat membuka sumber")
            return "putus"
        try:
            return self._loop(cap, src, spec, cfg)
        except Exception:               # noqa: BLE001
            log.exception("kamera %s: sesi berhenti karena galat", spec.name)
            self._set("terputus", "galat internal; mencoba lagi")
            return "putus"
        finally:
            cap.release()

    def _loop(self, cap: cv2.VideoCapture, src: Source, spec: CameraSpec, cfg: Config) -> str:
        rec = src.is_recording
        fps_src = cap.get(cv2.CAP_PROP_FPS) if rec else 0.0
        if rec and not 1.0 <= fps_src <= 120.0:
            fps_src = 10.0
        step = max(1, round(fps_src / cfg.target_fps)) if rec else 1
        monitor = self._build_monitor(spec, cfg)
        wall0 = time.monotonic()
        t = t_base = 0.0
        frame_i = loops = errors = 0
        last_proc = -1e9
        got = False
        self.learn, self.relearns = (0, 0), 0

        while True:
            if self._stop.is_set():
                return "stop"
            if not self.spec.enabled:
                self._publish_last(monitor)
                return "jeda"
            if self._take("reconf"):
                return "ulang"
            if self._take("recal") and monitor is not None:
                monitor.recalibrate("diminta pengguna")
            if not cap.grab():
                if rec:
                    loops += 1
                    if spec.max_loops is not None and loops >= spec.max_loops:
                        self._publish_last(monitor)
                        return "selesai"
                    t_base, frame_i = t + 1.0 / fps_src, 0
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    if monitor is not None and monitor.state == "memantau":
                        monitor.engine.tracker.reset()      # adegan rekaman melompat ke awal
                    continue
                self._set("terputus", "aliran video berhenti")
                return "putus"

            now = time.monotonic()
            if rec:
                t = t_base + frame_i / fps_src
                frame_i += 1
                if (frame_i - 1) % step:
                    continue
                if spec.speed > 0:
                    delay = wall0 + t / spec.speed - now
                    if delay > 0 and self._stop.wait(delay):
                        return "stop"
            else:
                t = now - wall0
                if now - last_proc < 1.0 / cfg.target_fps:
                    continue
                last_proc = now
            ok, frame = cap.retrieve()
            if not ok or frame is None:
                continue
            if not got:
                got = True
                self.connected_since = time.time()
            self._note_frame(frame)

            if monitor is None:
                self._set("perlu_area", "Tandai area trotoar untuk mulai memantau")
                if self._want_image():
                    self._publish_plain(frame, "TANDAI AREA TROTOAR")
                continue
            try:
                report = monitor.feed(frame, t)
                errors = 0
            except Exception:           # noqa: BLE001 - satu frame buruk tidak boleh mematikan kamera
                errors += 1
                log.exception("kamera %s: gagal memproses frame", spec.name)
                if errors >= 20:
                    self._set("terputus", "terlalu banyak galat saat memproses frame")
                    return "putus"
                continue
            self._apply(report, monitor, frame)

    def _note_frame(self, frame: np.ndarray) -> None:
        now = time.monotonic()
        with self._lock:
            self._frame = frame
            self.size = (frame.shape[1], frame.shape[0])
            if self._last_proc is not None and now > self._last_proc:
                inst = 1.0 / (now - self._last_proc)
                self.fps = inst if self.fps == 0 else 0.9 * self.fps + 0.1 * inst
            self._last_proc = now

    def _apply(self, rep, monitor: Monitor, frame: np.ndarray) -> None:
        with self._lock:
            self.learn, self.relearns = rep.learn, monitor.relearns
        if rep.state == "belajar":
            have, need = rep.learn
            self._set("belajar", f"Belajar latar {have} dari {need}" + (f" ({rep.note})" if rep.note else ""))
            if self._want_image():
                self._publish_plain(frame, f"BELAJAR LATAR {have}/{need}")
            return
        self._set("memantau", "")
        for ev in rep.events:
            rec = self.events.append(ev.as_dict(), ev.snapshot)
            log.info("kamera %s: %s (kejadian #%d)", self.spec.name, ev.kind, rec["no"])
        if self._want_image():
            if rep.processed:
                self._publish(monitor.engine.annotated())
            else:                                    # frame penutup belajar: engine belum memprosesnya
                self._publish_plain(frame, "LATAR SIAP, MEMANTAU")


# ====================================================================== manajer
class CameraManager:
    def __init__(self, data_root: str | Path, resolver, retention_days: int = 30, max_events: int = 5000):
        self.root = Path(data_root) / "cams"
        self.root.mkdir(parents=True, exist_ok=True)
        self.file = Path(data_root) / "cameras.json"
        self._resolver = resolver
        self.retention_days, self.max_events = retention_days, max_events
        self._cams: dict[str, Camera] = {}
        self._lock = threading.RLock()
        self._streams = threading.BoundedSemaphore(MAX_STREAMS)
        self._stop = threading.Event()

    # ------------------------------------------------------------------- mulai
    def start(self) -> None:
        if self.file.exists():
            try:
                items = json.loads(self.file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                log.warning("cameras.json rusak; kamera tidak dipulihkan")
                items = []
            for item in items if isinstance(items, list) else []:
                try:
                    spec = CameraSpec(**item)
                    if not valid_id(spec.id):
                        continue
                except TypeError:
                    continue
                cam = Camera(spec, self.root, self._resolver)
                self._cams[spec.id] = cam
                cam.start()
        threading.Thread(target=self._janitor, name="cam-janitor", daemon=True).start()

    def shutdown(self) -> None:
        self._stop.set()
        with self._lock:
            cams = list(self._cams.values())
        for cam in cams:
            cam.stop()

    def _save(self) -> None:
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(c.spec) for c in self._cams.values()], ensure_ascii=False, indent=1),
                       encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)              # memuat sandi RTSP: batasi pembacaan
        except OSError:
            pass
        tmp.replace(self.file)

    def _janitor(self) -> None:
        while not self._stop.is_set():
            cutoff = _now() - timedelta(days=self.retention_days)
            with self._lock:
                cams = list(self._cams.values())
            for cam in cams:
                try:
                    n = cam.events.prune(cutoff, self.max_events)
                    if n:
                        log.info("kamera %s: %d kejadian lama dihapus", cam.spec.name, n)
                except OSError:
                    log.exception("gagal merapikan kejadian %s", cam.spec.name)
            self._stop.wait(6 * 3600)

    # ------------------------------------------------------------------- akses
    def get(self, cid: str) -> Camera | None:
        with self._lock:
            return self._cams.get(cid)

    def list(self) -> list[Camera]:
        with self._lock:
            return list(self._cams.values())

    def create(self, data: dict, trusted: bool = False) -> Camera:
        with self._lock:
            if len(self._cams) >= MAX_CAMERAS:
                raise ValueError(f"batas {MAX_CAMERAS} kamera tercapai; hapus salah satu dulu")
            spec = build_spec(data, trusted=trusted)
            cam = Camera(spec, self.root, self._resolver)
            self._cams[spec.id] = cam
            self._save()
        cam.start()
        return cam

    def update(self, cid: str, data: dict) -> Camera | None:
        with self._lock:
            cam = self._cams.get(cid)
            if cam is None:
                return None
            old = asdict(cam.spec)
            spec = build_spec(data, existing=cam.spec)
            new = asdict(spec)
            if new != old:
                light = {"name", "enabled"}               # tidak perlu belajar latar ulang
                cam.reconfigure(spec, restart=any(new[k] != old[k] for k in new if k not in light))
                self._save()
            return cam

    def delete(self, cid: str) -> bool:
        with self._lock:
            cam = self._cams.pop(cid, None)
            if cam is None:
                return False
            self._save()
        cam.stop()
        shutil.rmtree(cam.dir, ignore_errors=True)
        return True

    def acquire_stream(self) -> bool:
        return self._streams.acquire(blocking=False)

    def release_stream(self) -> None:
        self._streams.release()

    # ---------------------------------------------------------------- kejadian
    def feed(self, limit: int = 50, offset: int = 0, kind: str | None = None) -> list[dict]:
        """Kejadian semua kamera, terbaru dulu. Paginasi `offset` (kursor waktu bisa melewatkan dua kejadian
        yang jatuh pada detik yang sama di batas halaman)."""
        rows: list[tuple[datetime, dict]] = []
        for cam in self.list():
            for dt, r in cam.events.pairs():
                if kind is None or r["kind"] == kind:
                    rows.append((dt, {**r, "camera": {"id": cam.spec.id, "name": cam.spec.name},
                                      "snapshot_url": (f"/api/cams/{cam.spec.id}/{r['snapshot']}" if r["snapshot"] else None)}))
        rows.sort(key=lambda x: (x[0], x[1]["no"]), reverse=True)
        return [r for _, r in rows[offset:offset + limit]]

    def csv(self) -> str:
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["waktu", "kamera", "jenis", "alasan", "nomor"])
        for r in reversed(self.feed(limit=100000)):
            w.writerow([r["ts"], r["camera"]["name"], r["label"], r["reason"], r["no"]])
        return out.getvalue()

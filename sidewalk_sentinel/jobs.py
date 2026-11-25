"""Penyimpanan unggahan, job analisis latar belakang, dan sesi kamera langsung."""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from pathlib import Path

import cv2
import numpy as np

from . import video as vid
from .analysis import Cancelled, run_analysis
from .config import Config
from .engine import Engine

log = logging.getLogger("sidewalk")
ID_RE = re.compile(r"^[0-9a-f]{12}$")
ALLOWED_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".3gp"}


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def valid_id(value: str) -> bool:
    return bool(ID_RE.match(value or ""))


def check_roi(roi) -> list[tuple[float, float]]:
    if not isinstance(roi, list) or not 3 <= len(roi) <= 64:
        raise ValueError("ROI harus berisi 3..64 titik")
    pts = []
    for p in roi:
        if not (isinstance(p, (list, tuple)) and len(p) == 2):
            raise ValueError("setiap titik ROI harus [x, y]")
        x, y = float(p[0]), float(p[1])
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise ValueError("koordinat ROI harus ternormalisasi 0..1")
        pts.append((x, y))
    return pts


# =============================================================================== data
class Store:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.uploads = self.root / "uploads"
        self.runs = self.root / "runs"
        self.live = self.root / "live"
        self.demo = self.root / "demo"
        for d in (self.uploads, self.runs, self.live, self.demo):
            d.mkdir(parents=True, exist_ok=True)


class Uploads:
    def __init__(self, store: Store):
        self.store = store
        self._items: dict[str, dict] = {}
        self._lock = threading.Lock()

    def register(self, uid: str, path: Path, name: str, extra: dict | None = None) -> dict:
        path, info = vid.ensure_readable(path)
        item = {"id": uid, "path": path, "name": name, "info": info, **(extra or {})}
        with self._lock:
            self._items[uid] = item
        return item

    def get(self, uid: str) -> dict | None:
        with self._lock:
            item = self._items.get(uid)
        if item:
            return item
        # setelah restart server: pulihkan dari disk
        folder = self.store.uploads / uid
        if valid_id(uid) and folder.is_dir():
            files = sorted(p for p in folder.iterdir() if p.suffix.lower() in ALLOWED_EXT)
            if files:
                try:
                    return self.register(uid, files[-1], files[-1].name)
                except vid.VideoError:
                    return None
        return None

    def frame_jpeg(self, uid: str, t: float, width: int = 960) -> bytes:
        item = self.get(uid)
        if item is None:
            raise KeyError(uid)
        frame = vid.read_frame_at(item["path"], t)
        h, w = frame.shape[:2]
        if w > width:
            frame = cv2.resize(frame, (width, round(h * width / w)), interpolation=cv2.INTER_AREA)
        ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 86])
        if not ok:
            raise vid.VideoError("gagal mengenkode frame")
        return jpg.tobytes()


# ================================================================================ job
class Job:
    def __init__(self, jid: str, upload_id: str, out_dir: Path, roi, cfg: Config, ref_time: float, save_video: bool):
        self.id, self.upload_id, self.out_dir = jid, upload_id, out_dir
        self.roi, self.cfg, self.ref_time, self.save_video = roi, cfg, ref_time, save_video
        self.state = "queued"
        self.progress = 0.0
        self.t = 0.0
        self.counts = {"parkir": 0, "naik_trotoar": 0}
        self.events: list[dict] = []
        self.error: str | None = None
        self.result: dict | None = None
        self.preview: bytes | None = None
        self.stages: bytes | None = None
        self.created = time.time()
        self.cancel = threading.Event()
        self.video_info: dict | None = None

    def public(self) -> dict:
        res = self.result or {}
        return {
            "id": self.id, "upload_id": self.upload_id, "state": self.state,
            "progress": round(self.progress, 4), "t": round(self.t, 2), "counts": self.counts,
            "events": self.events, "error": self.error, "created": self.created,
            "video": self.video_info, "summary": res.get("summary"),
            "processing": res.get("processing"), "has_video": bool(res.get("annotated_video")),
        }


class JobManager:
    def __init__(self, store: Store, uploads: Uploads, max_parallel: int = 1):
        self.store, self.uploads = store, uploads
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._slots = threading.Semaphore(max_parallel)

    def start(self, upload_id: str, roi, cfg: Config, ref_time: float = 0.0, save_video: bool = True) -> Job:
        item = self.uploads.get(upload_id)
        if item is None:
            raise KeyError("upload tidak ditemukan")
        jid = new_id()
        job = Job(jid, upload_id, self.store.runs / jid, roi, cfg, ref_time, save_video)
        job.video_info = item["info"]
        job.out_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._jobs[jid] = job
        threading.Thread(target=self._run, args=(job, item["path"]), daemon=True, name=f"job-{jid}").start()
        return job

    def _run(self, job: Job, path: Path) -> None:
        with self._slots:
            if job.cancel.is_set():
                job.state = "cancelled"
                return
            job.state = "running"

            def on_progress(frac, t, counts):
                job.progress, job.t, job.counts = frac, t, counts

            def on_preview(a: bytes, b: bytes):
                job.preview, job.stages = a, b

            try:
                job.result = run_analysis(
                    path, job.roi, job.cfg, ref_time=job.ref_time, out_dir=job.out_dir,
                    save_video=job.save_video, progress_cb=on_progress, preview_cb=on_preview,
                    event_cb=job.events.append, cancel=job.cancel)
                job.counts = {k: job.result["summary"][k] for k in ("parkir", "naik_trotoar")}
                job.progress, job.state = 1.0, "done"
            except Cancelled:
                job.state = "cancelled"
            except Exception as exc:     # noqa: BLE001 - dilaporkan ke pengguna, jejak lengkap ke log
                log.exception("job %s gagal", job.id)
                job.error = f"{type(exc).__name__}: {exc}"
                job.state = "error"

    def get(self, jid: str) -> Job | None:
        with self._lock:
            return self._jobs.get(jid)

    def public(self, jid: str) -> dict | None:
        job = self.get(jid)
        if job:
            return job.public()
        res_file = self.store.runs / jid / "result.json"          # job selesai sebelum server restart
        if valid_id(jid) and res_file.exists():
            res = json.loads(res_file.read_text(encoding="utf-8"))
            return {"id": jid, "upload_id": None, "state": "done", "progress": 1.0,
                    "t": res["video"].get("duration") or 0, "counts": {
                        k: res["summary"][k] for k in ("parkir", "naik_trotoar")},
                    "events": res["events"], "error": None, "created": res_file.stat().st_mtime,
                    "video": res["video"], "summary": res["summary"], "processing": res["processing"],
                    "has_video": bool(res.get("annotated_video"))}
        return None

    def recent(self, limit: int = 12) -> list[dict]:
        items = []
        for d in self.store.runs.iterdir():
            if d.is_dir() and valid_id(d.name):
                pub = self.public(d.name)
                if pub:
                    items.append(pub)
        items.sort(key=lambda p: p["created"], reverse=True)
        return [{k: p[k] for k in ("id", "state", "created", "summary", "video")} for p in items[:limit]]

    def cancel(self, jid: str) -> bool:
        job = self.get(jid)
        if job and job.state in ("queued", "running"):
            job.cancel.set()
            return True
        return False


# ================================================================================ live
class LiveSession:
    """Satu sesi kamera langsung: kalibrasi referensi, lalu pemantauan frame demi frame."""

    def __init__(self, sid: str, roi, cfg: Config, out_dir: Path):
        self.id, self.out_dir = sid, out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        self.cfg = cfg
        self.engine = Engine(roi, cfg)
        self.state = "calibrating"
        self._calib: list[np.ndarray] = []
        self._calib_last = -1.0
        self.t0 = time.monotonic()
        self.events: list[dict] = []
        self.last_seen = time.time()
        self.lock = threading.Lock()

    def recalibrate(self) -> None:
        with self.lock:
            self.state = "calibrating"
            self._calib.clear()
            self._calib_last = -1.0
            self.engine.ref = None
            self.engine.tracker.reset()

    def push(self, jpeg: bytes) -> dict:
        frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("frame bukan JPEG yang valid")
        with self.lock:
            self.last_seen = time.time()
            now = time.monotonic() - self.t0
            need = self.cfg.ref_frames
            if self.state == "calibrating":
                if self._calib_last < 0 or now - self._calib_last >= 0.15:
                    self._calib.append(frame)
                    self._calib_last = now
                if len(self._calib) >= need:
                    self.engine.set_reference(self._calib)
                    self._calib.clear()
                    self.state = "monitoring"
                have = need if self.state == "monitoring" else len(self._calib)
                return {"state": self.state, "t": round(now, 2), "calib": {"have": have, "need": need},
                        "tracks": [], "counts": dict(self.engine.counts), "new_events": []}

            res = self.engine.process(frame, now)
            W, H = self.engine.size
            new_events = []
            for ev in res.events:
                d = ev.as_dict()
                d["no"] = len(self.events) + 1
                d["snapshot"] = None
                if ev.snapshot:
                    (self.out_dir / f"{d['no']}.jpg").write_bytes(ev.snapshot)
                    d["snapshot"] = f"events/{d['no']}.jpg"
                self.events.append(d)
                new_events.append(d)
            tracks = [{"id": v["id"], "box": [v["bbox"][0] / W, v["bbox"][1] / H, v["bbox"][2] / W, v["bbox"][3] / H],
                       "state": v["state"], "dwell": round(v["dwell"], 1)} for v in res.tracks]
            return {"state": self.state, "t": round(now, 2), "calib": {"have": need, "need": need},
                    "tracks": tracks, "counts": dict(self.engine.counts), "new_events": new_events}


class LiveManager:
    def __init__(self, store: Store, idle_seconds: int = 900):
        self.store = store
        self.idle = idle_seconds
        self._sessions: dict[str, LiveSession] = {}
        self._lock = threading.Lock()

    def create(self, roi, cfg: Config) -> LiveSession:
        self._expire()
        sid = new_id()
        sess = LiveSession(sid, roi, cfg, self.store.live / sid / "events")
        with self._lock:
            self._sessions[sid] = sess
        return sess

    def get(self, sid: str) -> LiveSession | None:
        with self._lock:
            return self._sessions.get(sid)

    def close(self, sid: str) -> bool:
        with self._lock:
            return self._sessions.pop(sid, None) is not None

    def _expire(self) -> None:
        cutoff = time.time() - self.idle
        with self._lock:
            for sid in [k for k, s in self._sessions.items() if s.last_seen < cutoff]:
                del self._sessions[sid]

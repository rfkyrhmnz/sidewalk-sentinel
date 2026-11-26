"""Kamera = perangkat yang membuka aplikasi.

Laptop memakai kamera laptop, HP memakai kamera HP. Tidak ada sumber lain: tidak ada alamat IP, RTSP, berkas video,
atau adegan contoh. Halaman web mengambil gambar dari kamera perangkatnya sendiri, mengirimnya ke server sekitar lima kali
per detik, dan server memantaunya secara langsung: YOLO mengenali kendaraan, aturan zona memutuskan parkir atau naik
trotoar, lalu kejadian dicatat ke disk. Satu perangkat punya satu "kamera" di server; perangkat dikenali dari kunci acak yang disimpan di
peramban, sehingga membuka aplikasi lagi melanjutkan kamera yang sama beserta area trotoar dan riwayatnya.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import shutil
import threading
import time
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np

from .config import Config
from .detector import DetectorUnavailable, SidewalkSegmenter, VehicleDetector
from .engine import Engine
from .report import compute_report
from .store import DurationTracker, EventStore, now
from .util import check_roi, new_id, valid_id

log = logging.getLogger("sidewalk")

MAX_CAMERAS = 8
STATUS = ("perlu_area", "menunggu", "memuat", "model_galat", "memantau", "jeda")
DEVICE_KEY = re.compile(r"[0-9a-f]{16,64}")
MAX_FRAME_PIXELS = 8_000_000                   # sekitar 4K; lebih besar dari ini bukan frame kamera yang wajar
_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def jpeg_size(data: bytes) -> tuple[int, int] | None:
    """(lebar, tinggi) dari header JPEG tanpa mendekode. None bila bukan JPEG atau header tidak ditemukan.

    Dipakai untuk menolak 'bom dekompresi' (JPEG kecil yang menyatakan puluhan ribu piksel per sisi) sebelum
    cv2.imdecode mengalokasikan memori sebesar itu."""
    if data[:2] != b"\xff\xd8":
        return None
    i, n = 2, len(data)
    while i + 4 <= n:
        if data[i] != 0xFF:
            i += 1
            continue
        m = data[i + 1]
        if m == 0xFF:
            i += 1
            continue
        if m in (0x00, 0x01) or 0xD0 <= m <= 0xD8:
            i += 2
            continue
        if m in (0xD9, 0xDA):                    # akhir berkas / awal data tanpa SOF
            return None
        if m in _SOF:
            if i + 9 > n:
                return None
            return int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
        i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    return None


class PushCapture:
    """Saluran frame yang didorong dari halaman web. Hanya frame terbaru yang disimpan: bila server lebih lambat
    dari pengirim, frame lama dibuang, bukan menumpuk (jeda tidak boleh bertambah seiring waktu)."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._frame: np.ndarray | None = None
        self._cur: np.ndarray | None = None
        self._seq = self._taken = 0
        self._closed = False
        self.last_push = 0.0

    def put(self, frame: np.ndarray) -> None:
        with self._cond:
            self._frame, self._seq = frame, self._seq + 1
            self.last_push = time.monotonic()
            self._cond.notify_all()

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    def grab(self, timeout: float = 1.0) -> bool:
        with self._cond:
            if self._seq == self._taken and not self._closed:
                self._cond.wait(timeout)
            if self._seq == self._taken:
                return False
            self._taken, self._cur = self._seq, self._frame
            return True

    def retrieve(self) -> np.ndarray | None:
        return self._cur

    def latest(self) -> np.ndarray | None:
        """Salinan frame terbaru yang diterima (untuk usulan zona), atau None bila belum ada."""
        with self._cond:
            return None if self._frame is None else self._frame.copy()


# ===================================================================== spesifikasi
@dataclass
class CameraSpec:
    id: str
    device: str                       # kunci perangkat (acak, dari peramban)
    name: str
    roi: list | None = None
    settings: dict = field(default_factory=dict)
    enabled: bool = True
    created: str = ""

    @classmethod
    def from_dict(cls, item: dict) -> "CameraSpec":
        """Muat dari cameras.json. Kolom asing diabaikan; ValueError bila isinya tidak layak dipakai."""
        known = {f.name for f in fields(cls)}
        spec = cls(**{k: v for k, v in item.items() if k in known})
        if not valid_id(spec.id) or not DEVICE_KEY.fullmatch(str(spec.device or "")):
            raise ValueError("id atau kunci perangkat tidak valid")
        return spec


def _clean_name(value) -> str:
    name = re.sub(r"[\x00-\x1f\x7f]", "", str(value or "")).strip()
    if not 1 <= len(name) <= 40:
        raise ValueError("nama perangkat harus 1 sampai 40 karakter")
    return name


def apply_changes(spec: CameraSpec, data: dict) -> CameraSpec:
    """Salinan `spec` dengan perubahan dari `data`, tervalidasi. Yang tidak disebut tidak berubah."""
    new = CameraSpec(**asdict(spec))
    if "name" in data:
        new.name = _clean_name(data["name"])
    if "roi" in data:
        new.roi = None if data["roi"] is None else [list(p) for p in check_roi(data["roi"])]
    if "settings" in data:
        overrides = data["settings"] or {}
        if not isinstance(overrides, dict):
            raise ValueError("settings harus berupa objek")
        Config.from_overrides(overrides)            # menolak kunci tak dikenal / nilai di luar batas
        new.settings = dict(overrides)
    if "enabled" in data:
        new.enabled = bool(data["enabled"])
    return new


# ======================================================================= gambar
def _cell(value):
    """Nama perangkat berasal dari pengguna. Sel yang diawali = + - @ dibaca Excel sebagai rumus, jadi diberi tanda petik."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def _ascii(text: str, limit: int = 24) -> str:
    return text.encode("ascii", "replace").decode()[:limit]


# ======================================================================= kamera
class Camera:
    """Satu kamera perangkat: utas yang menerima frame, memantau, dan mencatat kejadian."""

    def __init__(self, spec: CameraSpec, root: Path, detector, sidewalk: SidewalkSegmenter | None = None):
        self.spec = spec
        self.detector, self.sidewalk = detector, sidewalk
        self.dir = root / spec.id
        self.events = EventStore(self.dir)
        self._durations = DurationTracker(self.events)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._flags = {"reconf": False}
        self._thread: threading.Thread | None = None
        self.channel = PushCapture()
        self.removed = False
        # status publik
        self.status, self.message = "menunggu", "Menunggu gambar dari perangkat"
        self.fps = 0.0
        self.size: tuple[int, int] | None = None
        self.connected_since: float | None = None
        self._last_proc: float | None = None
        self._tracks: list[dict] = []

    # -------------------------------------------------------------- siklus hidup
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=f"cam-{self.spec.id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.removed = True
        self._stop.set()
        self._wake.set()
        self.channel.close()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)

    def reconfigure(self, spec: CameraSpec, restart: bool = True) -> None:
        """Terapkan spesifikasi baru. `restart` = bangun ulang mesin (jejak kendaraan dimulai dari awal)."""
        with self._lock:
            self.spec = spec
            if restart:
                self._flags["reconf"] = True
        self._wake.set()

    def _take(self, name: str) -> bool:
        with self._lock:
            v, self._flags[name] = self._flags[name], False
        return v

    def _set(self, status: str, message: str = "") -> None:
        with self._lock:
            self.status, self.message = status, message

    # ------------------------------------------------------------------ perangkat
    def push(self, jpeg: bytes) -> dict:
        """Terima satu frame dari halaman web. ValueError bila frame tidak wajar. Mengembalikan keadaan terkini
        (status, jejak kendaraan, hitungan) agar halaman bisa menggambarnya tanpa permintaan kedua."""
        size = jpeg_size(jpeg)
        if size is None:
            raise ValueError("frame bukan JPEG yang valid")
        if size[0] * size[1] > MAX_FRAME_PIXELS or min(size) < 16:
            raise ValueError(f"ukuran frame {size[0]}x{size[1]} terlalu besar atau terlalu kecil")
        frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("frame bukan JPEG yang valid")
        if self.spec.enabled:
            self.channel.put(frame)
        return self.live_state()

    def live_state(self) -> dict:
        with self._lock:
            return {"status": self.status, "message": self.message, "enabled": self.spec.enabled,
                    "detector": self._detector_status(),
                    "tracks": list(self._tracks), "seq": self.events.seq,
                    "counts_today": self.events.counts_since(now().replace(hour=0, minute=0, second=0, microsecond=0))}

    def _detector_status(self) -> dict:
        st = self.detector.status()
        return {"state": st["state"], "message": st["message"]}

    def propose_zone(self) -> dict:
        """Usulan poligon trotoar dari gambar terbaru perangkat. ValueError bila belum ada gambar,
        DetectorUnavailable bila model trotoar tidak ada. `roi` None bila trotoar tidak terlihat."""
        frame = self.channel.latest()
        if frame is None:
            raise ValueError("Belum ada gambar dari perangkat ini")
        if self.sidewalk is None or not self.sidewalk.available:
            raise DetectorUnavailable("Model trotoar tidak ditemukan. Tandai zona secara manual.")
        out = self.sidewalk.propose(frame)
        return out or {"roi": None, "coverage": 0.0,
                       "message": "Trotoar tidak terlihat jelas di gambar ini. Tandai zona secara manual."}

    def info(self) -> dict:
        counts = self.events.counts_since(now().replace(hour=0, minute=0, second=0, microsecond=0))
        with self._lock:
            spec = self.spec
            last = self.events.records[-1] if self.events.records else None
            return {
                "id": spec.id, "name": spec.name, "roi": spec.roi, "settings": spec.settings,
                "enabled": spec.enabled, "created": spec.created,
                "status": self.status, "message": self.message, "detector": self._detector_status(),
                "fps": round(self.fps, 1), "size": list(self.size) if self.size else None,
                "uptime_s": round(time.time() - self.connected_since) if self.connected_since else None,
                "counts_today": counts, "seq": self.events.seq,
                "last_event": ({"no": last["no"], "kind": last["kind"], "ts": last["ts"]} if last else None),
            }

    # ---------------------------------------------------------------- pekerja
    def _run(self) -> None:
        while not self._stop.is_set():
            if not self.spec.enabled:
                self._set("jeda", "Dijeda")
                self._wake.wait(0.5)
                self._wake.clear()
                continue
            outcome = self._session()
            if outcome == "stop":
                return
            self._wake.clear()

    def _session(self) -> str:
        """Satu rangkaian pemantauan dengan satu Engine. Berakhir saat konfigurasi berubah atau dijeda."""
        spec = self.spec
        self._take("reconf")                              # mesin baru ini sudah memakai spesifikasi terbaru
        try:
            cfg = Config.from_overrides(spec.settings)
        except ValueError:
            log.warning("perangkat %s: pengaturan tersimpan tidak valid; memakai bawaan", spec.name)
            cfg = Config()
        try:
            return self._loop(self._build_engine(spec, cfg), cfg)
        except Exception:               # noqa: BLE001
            log.exception("perangkat %s: pemantauan berhenti karena galat", spec.name)
            self._set("menunggu", "Galat internal; memulai ulang")
            self._stop.wait(2.0)
            return "ulang"
        finally:
            self._durations.finish_all()                 # sesi berakhir (jeda, ubah area, hapus): tutup yang masih terbuka

    def _build_engine(self, spec: CameraSpec, cfg: Config) -> Engine | None:
        if not spec.roi:
            return None
        eng = Engine(spec.roi, self.detector, cfg)
        eng.header_fn = lambda t: (f"{_ascii(self.spec.name)}  {datetime.now():%Y-%m-%d %H:%M:%S}  "
                                   f"P:{eng.counts['parkir']} N:{eng.counts['naik_trotoar']}")
        return eng

    def _loop(self, engine: Engine | None, cfg: Config) -> str:
        cap = self.channel
        wall0 = time.monotonic()
        last_proc = -1e9
        got = False
        errors = 0
        self._tracks = []
        self._set("perlu_area" if engine is None else "menunggu",
                  "Tandai area trotoar untuk mulai memantau" if engine is None else "Menunggu gambar dari perangkat")

        while True:
            if self._stop.is_set():
                return "stop"
            if not self.spec.enabled or self._take("reconf"):
                return "ulang"
            if not cap.grab():
                # Perangkat berhenti mengirim (layar terkunci, tab ditutup): tunggu. Bila perangkat kembali,
                # pemantauan lanjut dari sana.
                if got and time.monotonic() - cap.last_push > 3.0:
                    got = False
                    self.connected_since = None
                    self._set("menunggu", "Perangkat berhenti mengirim gambar. Buka lagi aplikasi ini.")
                continue

            now_m = time.monotonic()
            if now_m - last_proc < 1.0 / cfg.target_fps:
                continue
            last_proc = now_m
            frame = cap.retrieve()
            if frame is None:
                continue
            if not got:
                got = True
                self.connected_since = time.time()
                if engine is not None:
                    engine.reset()                        # setelah jeda panjang, jejak lama tidak berlaku
            self._note_frame(frame)

            if engine is None:
                self._set("perlu_area", "Tandai area trotoar untuk mulai memantau")
                continue
            st = self.detector.status()
            if st["state"] != "siap":                     # model belum dimuat, atau gagal dimuat
                with self._lock:
                    self._tracks = []
                self._set("model_galat" if st["state"] == "galat" else "memuat", st["message"] or "Memuat model YOLO")
                continue
            try:
                result = engine.process(frame, now_m - wall0)
                errors = 0
            except Exception:           # noqa: BLE001 - satu frame buruk tidak boleh mematikan pemantauan
                errors += 1
                log.exception("perangkat %s: gagal memproses frame", self.spec.name)
                if errors >= 20:
                    self._set("menunggu", "Terlalu banyak galat saat memproses frame; memulai ulang")
                    return "ulang"
                continue
            self._apply(result, engine)

    def _note_frame(self, frame: np.ndarray) -> None:
        t = time.monotonic()
        with self._lock:
            self.size = (frame.shape[1], frame.shape[0])
            if self._last_proc is not None and t > self._last_proc:
                inst = 1.0 / (t - self._last_proc)
                self.fps = inst if self.fps == 0 else 0.9 * self.fps + 0.1 * inst
            self._last_proc = t

    def _apply(self, res, engine: Engine) -> None:
        self._set("memantau", "")
        W, H = engine.size
        with self._lock:
            self._tracks = [{"id": v["id"], "box": [v["bbox"][0] / W, v["bbox"][1] / H, v["bbox"][2] / W, v["bbox"][3] / H],
                             "state": v["state"], "dwell": round(v["dwell"], 1), "label": v["label"]} for v in res.tracks]
        for ev in res.events:
            rec = self.events.append(ev.as_dict(), ev.snapshot)
            self._durations.begin(ev.track_id, ev.kind, rec["no"], ev.t_start, ev.t)
            log.info("perangkat %s: %s (kejadian #%d)", self.spec.name, ev.kind, rec["no"])
        self._durations.update({v["id"] for v in res.tracks if v["in_roi"]}, res.t)


# ====================================================================== manajer
class CameraManager:
    def __init__(self, data_root: str | Path, retention_days: int = 30, max_events: int = 5000,
                 detector=None, sidewalk: SidewalkSegmenter | None = None):
        self.detector = detector if detector is not None else VehicleDetector()
        self.sidewalk = sidewalk
        self.root = Path(data_root) / "devices"
        self.root.mkdir(parents=True, exist_ok=True)
        self.file = Path(data_root) / "devices.json"
        self.retention_days, self.max_events = retention_days, max_events
        self._cams: dict[str, Camera] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()

    # ------------------------------------------------------------------- mulai
    def start(self) -> None:
        loader = getattr(self.detector, "start_loading", None)
        if loader is not None:
            loader()                                      # model dimuat di latar; kamera menampilkan "memuat" sampai siap
        if self.file.exists():
            try:
                items = json.loads(self.file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                log.warning("%s rusak; perangkat tidak dipulihkan", self.file.name)
                items = []
            for item in items if isinstance(items, list) else []:
                try:
                    spec = CameraSpec.from_dict(item)
                except (TypeError, ValueError, AttributeError):
                    continue
                cam = Camera(spec, self.root, self.detector, self.sidewalk)
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
            os.chmod(tmp, 0o600)              # kunci perangkat mengenali pemiliknya: batasi pembacaan
        except OSError:
            pass
        tmp.replace(self.file)

    def _janitor(self) -> None:
        while not self._stop.is_set():
            cutoff = now() - timedelta(days=self.retention_days)
            with self._lock:
                cams = list(self._cams.values())
            for cam in cams:
                try:
                    n = cam.events.prune(cutoff, self.max_events)
                    if n:
                        log.info("perangkat %s: %d kejadian lama dihapus", cam.spec.name, n)
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

    def register(self, key: str, name: str = "") -> tuple[Camera, bool]:
        """Kamera milik perangkat dengan kunci `key`; dibuat bila belum ada. Mengembalikan (kamera, baru?).

        Nama bawaan dari peramban hanya dipakai saat pertama kali; sesudah itu nama yang diubah pemilik tetap."""
        if not DEVICE_KEY.fullmatch(str(key or "")):
            raise ValueError("kunci perangkat tidak valid")
        with self._lock:
            for cam in self._cams.values():
                if cam.spec.device == key:
                    return cam, False
            if len(self._cams) >= MAX_CAMERAS:
                raise ValueError(f"batas {MAX_CAMERAS} perangkat tercapai; hapus salah satu di Setelan")
            try:
                base = _clean_name(name)
            except ValueError:
                base = "Perangkat"
            taken = {c.spec.name for c in self._cams.values()}
            final, n = base, 1
            while final in taken:                          # nama kembar menyulitkan membaca laporan
                n += 1
                final = f"{base[:36]} {n}"
            spec = CameraSpec(id=new_id(), device=key, name=final, created=now().isoformat(timespec="seconds"))
            cam = Camera(spec, self.root, self.detector, self.sidewalk)
            self._cams[spec.id] = cam
            self._save()
        cam.start()
        return cam, True

    def update(self, cid: str, data: dict) -> Camera | None:
        with self._lock:
            cam = self._cams.get(cid)
            if cam is None:
                return None
            spec = apply_changes(cam.spec, data)
            if "name" in data and any(c.spec.name == spec.name for c in self._cams.values() if c is not cam):
                raise ValueError("sudah ada perangkat dengan nama itu")
            old, new = asdict(cam.spec), asdict(spec)
            if new != old:
                light = {"name", "enabled"}               # tidak perlu membangun ulang mesin
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

    # ---------------------------------------------------------------- kejadian
    def feed(self, limit: int = 50, offset: int = 0, kind: str | None = None) -> list[dict]:
        """Kejadian semua perangkat, terbaru dulu. Paginasi `offset` (kursor waktu bisa melewatkan dua kejadian
        yang jatuh pada detik yang sama di batas halaman)."""
        rows: list[tuple[datetime, dict]] = []
        for cam in self.list():
            for dt, r in cam.events.pairs():
                if kind is None or r["kind"] == kind:
                    rows.append((dt, {**r, "camera": {"id": cam.spec.id, "name": cam.spec.name},
                                      "snapshot_url": (f"/api/cams/{cam.spec.id}/{r['snapshot']}" if r["snapshot"] else None)}))
        rows.sort(key=lambda x: (x[0], x[1]["no"]), reverse=True)
        return [r for _, r in rows[offset:offset + limit]]

    def report(self, periode: str, camera_id: str | None = None, at: datetime | None = None) -> dict:
        """Laporan ringkasan. ValueError bila periode tidak dikenal, KeyError bila perangkat tidak ada."""
        cams = self.list()
        if camera_id is not None:
            cams = [c for c in cams if c.spec.id == camera_id]
            if not cams:
                raise KeyError(camera_id)
        entries = [(c.spec.id, c.spec.name, c.events.pairs()) for c in cams]
        return compute_report(entries, periode, at or now(), camera_id)

    def csv(self) -> str:
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["waktu", "perangkat", "jenis", "alasan", "nomor", "durasi_detik"])
        for r in reversed(self.feed(limit=100000)):
            w.writerow([_cell(c) for c in (r["ts"], r["camera"]["name"], r["label"], r["reason"], r["no"],
                                           r.get("durasi_s", ""))])
        return out.getvalue()

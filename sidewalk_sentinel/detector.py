"""Detektor YOLO: mengenali kendaraan, dan mengusulkan zona trotoar dari model segmentasi.

Yang dihitung hanya kelas kendaraan (sepeda, motor, mobil, bus, truk). Tangan, orang, hewan, dan benda lain tidak
pernah menjadi kendaraan, karena kelasnya memang bukan kendaraan.

`parse_result` dan `mask_to_polygon` murni (bisa diuji tanpa model). Paket `ultralytics` hanya diimpor saat model
dimuat, jadi bagian lain aplikasi dan tesnya tidak membutuhkannya.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("sentinel.detector")

# nama kelas COCO (huruf kecil) -> nama dalam bahasa Indonesia
VEHICLES = {"bicycle": "sepeda", "motorcycle": "motor", "car": "mobil", "bus": "bus", "truck": "truk"}
SIDEWALK_NAMES = ("sidewalk", "trotoar")

DEFAULT_WEIGHTS = "yolo11n.pt"
DEFAULT_SIDEWALK = Path(__file__).resolve().parent.parent / "models" / "trotoar.pt"
INSTALL_HINT = "Paket ultralytics belum terpasang. Jalankan: pip install ultralytics"


class DetectorUnavailable(RuntimeError):
    """Model tidak bisa dipakai; pesannya ditampilkan apa adanya kepada pengguna."""


@dataclass
class Detection:
    box: tuple[float, float, float, float]      # x1, y1, x2, y2 (piksel frame)
    conf: float
    name: str                                   # nama kelas model, huruf kecil
    label: str                                  # nama Indonesia


def _np(x) -> np.ndarray:
    """Tensor torch / ndarray / daftar -> ndarray."""
    if hasattr(x, "cpu"):
        x = x.cpu()
    if hasattr(x, "numpy"):
        x = x.numpy()
    return np.asarray(x)


def parse_result(res) -> list[Detection]:
    """Satu objek `Results` Ultralytics -> daftar kendaraan. Kelas selain kendaraan dibuang."""
    boxes = getattr(res, "boxes", None)
    if boxes is None or len(boxes) == 0:
        return []
    names = res.names
    cls = _np(boxes.cls).astype(int).ravel()
    conf = _np(boxes.conf).astype(float).ravel()
    xyxy = _np(boxes.xyxy).astype(float).reshape(-1, 4)
    out = []
    for i in range(len(cls)):
        name = str(names[int(cls[i])]).lower()
        if name in VEHICLES:
            out.append(Detection(tuple(float(v) for v in xyxy[i]), float(conf[i]), name, VEHICLES[name]))
    return out


def mask_to_polygon(mask: np.ndarray, max_points: int = 10, min_frac: float = 0.02) -> list[list[float]] | None:
    """Mask bool HxW -> satu poligon (koordinat 0..1) dari komponen terbesar, disederhanakan ke <= max_points titik.
    None bila komponen terbesar lebih kecil dari `min_frac` luas gambar."""
    h, w = mask.shape[:2]
    m = (mask > 0).astype(np.uint8)
    found = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = found[0] if len(found) == 2 else found[1]
    if not contours:
        return None
    cnt = max(contours, key=cv2.contourArea)
    if cv2.contourArea(cnt) < min_frac * h * w:
        return None
    hull = cv2.convexHull(cnt)
    if cv2.contourArea(hull) <= 1.25 * cv2.contourArea(cnt):
        cnt = hull                                # bentuk nyaris cembung: gigitan kecil (mis. kendaraan di tepi trotoar) ditutup
    peri = cv2.arcLength(cnt, True)
    pts = cnt
    for eps in np.linspace(0.005, 0.12, 24):         # makin kasar sampai cukup sedikit titik
        pts = cv2.approxPolyDP(cnt, eps * peri, True)
        if len(pts) <= max_points:
            break
    pts = pts.reshape(-1, 2)
    if len(pts) < 3:
        return None
    return [[round(float(np.clip(x / w, 0, 1)), 4), round(float(np.clip(y / h, 0, 1)), 4)] for x, y in pts]


def _load_yolo(weights: str):
    try:
        from ultralytics import YOLO
    except ImportError as e:
        raise DetectorUnavailable(INSTALL_HINT) from e
    try:
        return YOLO(weights)
    except Exception as e:                                   # noqa: BLE001 - unduhan gagal, berkas rusak, dan sebagainya
        raise DetectorUnavailable(f"Model {weights} tidak bisa dimuat: {e}") from e


class VehicleDetector:
    """YOLO untuk kendaraan. Dimuat pada pemakaian pertama (atau lewat `start_loading`) dan aman dipakai dari banyak utas."""

    def __init__(self, weights: str = DEFAULT_WEIGHTS, device: str | None = None):
        self.weights, self.device = weights, device
        self._model = None
        self._ids: list[int] = []
        self._lock = threading.Lock()
        self._state, self._message = "belum", ""
        self._loader: threading.Thread | None = None

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        return {"state": self._state, "message": self._message, "weights": Path(self.weights).name}

    @property
    def ready(self) -> bool:
        return self._state == "siap"

    # ------------------------------------------------------------------ muat
    def load(self) -> None:
        with self._lock:
            if self._model is not None:
                return
            self._state, self._message = "memuat", "Memuat model YOLO"
            try:
                model = _load_yolo(self.weights)
                ids = [int(i) for i, n in model.names.items() if str(n).lower() in VEHICLES]
                if not ids:
                    raise DetectorUnavailable(f"Model {Path(self.weights).name} tidak punya kelas kendaraan")
            except DetectorUnavailable as e:
                self._state, self._message = "galat", str(e)
                log.error("%s", e)
                raise
            self._model, self._ids = model, ids
            self._state, self._message = "siap", ""
            log.info("model %s siap (%d kelas kendaraan)", self.weights, len(ids))

    def start_loading(self) -> None:
        """Muat di utas latar (unduhan pertama bisa lama) agar server langsung melayani halaman."""
        if self._loader is not None and self._loader.is_alive():
            return
        if self._model is not None:
            return

        def run():
            try:
                self.load()
            except DetectorUnavailable:
                pass                                        # pesannya sudah tersimpan di status

        self._loader = threading.Thread(target=run, name="yolo-load", daemon=True)
        self._loader.start()

    def retry(self) -> None:
        """Coba muat lagi setelah galat (mis. paket baru dipasang atau koneksi pulih)."""
        if self._state == "galat":
            self._state, self._message = "belum", ""
            self.start_loading()

    # ------------------------------------------------------------------ deteksi
    def detect(self, frame: np.ndarray, conf: float = 0.30, imgsz: int = 640) -> list[Detection]:
        if self._model is None:
            raise DetectorUnavailable(self._message or "Model YOLO belum siap")
        with self._lock:                                    # model torch tidak dipakai bersamaan dari banyak utas
            kw = {"conf": conf, "imgsz": imgsz, "classes": self._ids, "verbose": False}
            if self.device:
                kw["device"] = self.device
            res = self._model.predict(frame, **kw)[0]
        return parse_result(res)


class SidewalkSegmenter:
    """Model segmentasi trotoar (YOLO-seg hasil fine-tune). Dipakai sekali saat zona ditandai, untuk mengusulkan poligon."""

    def __init__(self, weights: str | Path | None = DEFAULT_SIDEWALK, device: str | None = None):
        self.weights = Path(weights) if weights else None
        self.device = device
        self._model = None
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return self._model is not None or bool(self.weights and self.weights.is_file())

    def _ensure(self):
        if self._model is None:
            if not self.available:
                raise DetectorUnavailable("Model trotoar tidak ditemukan. Tandai zona secara manual.")
            self._model = _load_yolo(str(self.weights))
        return self._model

    def mask(self, frame: np.ndarray, conf: float = 0.25, imgsz: int = 640) -> np.ndarray:
        """Gabungan semua segmen trotoar sebagai mask bool HxW."""
        h, w = frame.shape[:2]
        with self._lock:
            model = self._ensure()
            kw = {"conf": conf, "imgsz": imgsz, "verbose": False}
            if self.device:
                kw["device"] = self.device
            res = model.predict(frame, **kw)[0]
        out = np.zeros((h, w), np.uint8)
        if getattr(res, "masks", None) is None or getattr(res, "boxes", None) is None:
            return out.astype(bool)
        cls = _np(res.boxes.cls).astype(int).ravel()
        single = len(res.names) == 1
        for poly, c in zip(res.masks.xy, cls):
            if single or str(res.names[int(c)]).lower() in SIDEWALK_NAMES:
                pts = np.asarray(poly, np.float64).reshape(-1, 2)
                if len(pts) >= 3:
                    cv2.fillPoly(out, [np.round(pts).astype(np.int32)], 1)
        return out.astype(bool)

    def propose(self, frame: np.ndarray, max_points: int = 10) -> dict | None:
        """{"roi": poligon 0..1, "coverage": porsi gambar} atau None bila trotoar tidak terlihat."""
        m = self.mask(frame)
        poly = mask_to_polygon(m, max_points)
        if poly is None:
            return None
        return {"roi": poly, "coverage": round(float(m.mean()), 3)}

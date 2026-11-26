"""Zona trotoar otomatis: model segmentasi dijalankan berkala pada frame kamera, hasilnya dirata-ratakan antar-frame,
dan poligon zona diperbarui sendiri. Pengguna tidak perlu menandai apa pun.

Satu frame bisa meleset (bayangan, kendaraan menutupi trotoar, model ragu), jadi mask tiap frame tidak langsung dipakai:
mask dirata-ratakan secara eksponensial pada grid kecil dan zona diambil dari bagian yang konsisten terlihat. Zona hanya
berubah bila bentuknya benar-benar bergeser, supaya kotak dan hitungan tidak ikut bergetar.
"""
from __future__ import annotations

import cv2
import numpy as np

from .detector import mask_to_polygon

GRID_W = 192            # lebar grid rata-rata; tinggi mengikuti rasio gambar
WARM_UPDATES = 6        # pembaruan awal dibuat rapat agar zona cepat muncul dan cepat stabil
IOU_SAME = 0.96         # bentuk yang mirip sekian dianggap tidak berubah
MISSES_TO_CLEAR = 3     # trotoar tak terlihat sekian kali beruntun (mis. kamera berpaling): zona dicabut


class AutoZone:
    def __init__(self, segmenter, alpha: float = 0.4, warm_interval: float = 1.0, interval: float = 4.0):
        self.segmenter = segmenter
        self.alpha, self.warm_interval, self.interval = alpha, warm_interval, interval
        self.reset()

    def reset(self) -> None:
        self.acc: np.ndarray | None = None
        self._bin: np.ndarray | None = None
        self.roi: list[list[float]] | None = None
        self.updates = 0
        self.misses = 0
        self._last = -1e9

    def due(self, t: float) -> bool:
        gap = self.warm_interval if self.updates < WARM_UPDATES else self.interval
        return t - self._last >= gap

    def update(self, frame: np.ndarray, t: float) -> tuple[bool, list[list[float]] | None]:
        """Jalankan model pada `frame`. Mengembalikan (berubah?, zona terkini atau None). Galat model (DetectorUnavailable
        dan sejenisnya) dilemparkan ke pemanggil."""
        self._last = t
        self.updates += 1
        h, w = frame.shape[:2]
        gw, gh = GRID_W, max(8, round(GRID_W * h / w))
        m = self.segmenter.mask(frame)
        small = cv2.resize(m.astype(np.float32), (gw, gh), interpolation=cv2.INTER_AREA)
        if self.acc is None or self.acc.shape != small.shape:
            self.acc = small
        else:
            self.acc = (1.0 - self.alpha) * self.acc + self.alpha * small
        binary = self.acc >= 0.5
        poly = mask_to_polygon(binary) if binary.any() else None

        if poly is None:
            self.misses += 1
            if self.roi is not None and self.misses >= MISSES_TO_CLEAR:
                self.roi, self._bin = None, None
                return True, None
            return False, self.roi
        self.misses = 0
        if self.roi is not None and self._bin is not None and _iou(binary, self._bin) >= IOU_SAME:
            return False, self.roi
        self.roi, self._bin = poly, binary
        return True, poly


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    union = np.logical_or(a, b).sum()
    return 1.0 if union == 0 else float(np.logical_and(a, b).sum() / union)

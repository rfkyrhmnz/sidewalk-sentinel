"""Satu kendaraan terdeteksi dalam bentuk yang dipakai pelacak dan aturan keputusan."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Blob:
    bbox: tuple[int, int, int, int]            # x, y, w, h (piksel frame kerja)
    centroid: tuple[float, float]              # pusat kotak
    contact: float = 0.0                       # porsi bagian bawah kotak yang berada di zona trotoar
    name: str = "car"                          # nama kelas model (huruf kecil)
    label: str = "mobil"                       # nama dalam bahasa Indonesia
    conf: float = 0.0


def contact_ratio(zone_mask: np.ndarray, bbox: tuple[int, int, int, int], strip: float = 0.30) -> float:
    """Porsi 'jalur kaki' (bagian bawah kotak, tempat roda menyentuh tanah) yang berada di dalam zona."""
    x, y, w, h = bbox
    y0 = y + int(h * (1.0 - strip))
    patch = zone_mask[y0:y + h, x:x + w]
    return float((patch > 0).mean()) if patch.size else 0.0

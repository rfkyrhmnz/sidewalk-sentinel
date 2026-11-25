"""Pembersihan mask dengan operasi morfologi (Materi 3).

* opening  = erosi lalu dilasi  -> membuang bintik derau kecil
* closing  = dilasi lalu erosi  -> menyambung pecahan blob satu kendaraan
* hole filling (flood fill dari latar) -> menutup lubang di dalam blob
"""
from __future__ import annotations

import cv2
import numpy as np

from .config import Config


def _odd(n: float, minimum: int) -> int:
    n = max(minimum, int(round(n)))
    return n if n % 2 == 1 else n + 1


def fill_holes(mask: np.ndarray) -> np.ndarray:
    padded = cv2.copyMakeBorder(mask, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
    flood = padded.copy()
    h, w = flood.shape
    cv2.floodFill(flood, np.zeros((h + 2, w + 2), np.uint8), (0, 0), 255)
    holes = cv2.bitwise_not(flood)[1:-1, 1:-1]
    return cv2.bitwise_or(mask, holes)


def clean_mask(raw: np.ndarray, cfg: Config) -> np.ndarray:
    if not cfg.use_morphology:
        return raw
    width = raw.shape[1]
    k_open = _odd(cfg.open_frac * width, 3)
    k_close = _odd(cfg.close_frac * width, 5)
    opened = cv2.morphologyEx(raw, cv2.MORPH_OPEN,
                              cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k_open, k_open)))
    closed = cv2.morphologyEx(opened, cv2.MORPH_CLOSE,
                              cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k_close, k_close)))
    return fill_holes(closed)

"""Geometri kontak kendaraan-trotoar.

Masalah inti: kendaraan yang berdiri DI ATAS trotoar menutupi piksel trotoar, sehingga
mask trotoar "bolong" tepat di bawah kendaraan. Irisan mask penuh (kendaraan ∩ trotoar)
karenanya mendekati nol justru saat kendaraan benar-benar di trotoar. Dua ukuran dipakai:

  * ``contact_ratio``  : porsi *footprint* (pita bawah mask kendaraan = titik kontak roda
                         dengan tanah) yang jatuh di mask trotoar. Berguna bila mask trotoar
                         menutupi area di bawah kendaraan (mis. anotasi amodal / poligon manual).
  * ``ring_ratio``     : porsi *cincin tanah* di sekeliling footprint (di kiri, kanan, dan depan
                         kendaraan) yang merupakan trotoar. Tahan terhadap lubang oklusi: kendaraan
                         di trotoar dikelilingi trotoar, kendaraan di jalan dikelilingi aspal.
"""
from __future__ import annotations

import cv2
import numpy as np

from .config import Config


def footprint_mask(vmask: np.ndarray, frac: float) -> np.ndarray:
    """Pita bawah (``frac`` dari tinggi) mask kendaraan."""
    ys = np.nonzero(vmask.any(axis=1))[0]
    fp = np.zeros_like(vmask, bool)
    if ys.size == 0:
        return fp
    y0, y1 = ys.min(), ys.max()
    band = max(3, int(round((y1 - y0 + 1) * frac)))
    ya = max(y0, y1 - band + 1)
    fp[ya:y1 + 1] = vmask[ya:y1 + 1]
    return fp


def ground_ring(vmask: np.ndarray, fp: np.ndarray, exclude: np.ndarray | None, cfg: Config) -> np.ndarray:
    """Cincin tanah di sekitar footprint (sisi kiri/kanan setinggi roda dan bagian depan/bawah)."""
    xs = np.nonzero(vmask.any(axis=0))[0]
    width = int(xs.max() - xs.min() + 1) if xs.size else 1
    t_side = max(4, int(round(cfg.ring_scale * width)))
    t_below = max(3, int(round(cfg.ring_below_frac * width)))
    gap = max(1, int(round(cfg.ring_gap_frac * width)))
    ell = lambda r: cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    fpu = fp.astype(np.uint8)
    outer_side = cv2.dilate(fpu, ell(t_side)).astype(bool)
    outer_below = cv2.dilate(fpu, ell(t_below)).astype(bool)
    inner = cv2.dilate(vmask.astype(np.uint8), ell(gap)).astype(bool)

    fy = np.nonzero(fp.any(axis=1))[0]
    if fy.size == 0:
        return np.zeros_like(vmask, bool)
    top, bottom = int(fy.min()), int(fy.max())
    rows = np.arange(vmask.shape[0])[:, None]
    # samping: setinggi footprint (tanah di kiri/kanan roda); depan: strip tipis tepat di bawah tepi bawah.
    ring = ((outer_side & (rows >= top) & (rows <= bottom)) | (outer_below & (rows > bottom))) & ~inner
    if exclude is not None:
        ring &= ~exclude                    # bagian yang tertutup objek lain tidak diketahui
    return ring


def _frac(a: np.ndarray, b: np.ndarray) -> float:
    n = int(a.sum())
    return float((a & b).sum()) / n if n else float("nan")


def vehicle_metrics(vmask: np.ndarray, sidewalk: np.ndarray, exclude: np.ndarray | None, cfg: Config) -> dict:
    fp = footprint_mask(vmask, cfg.footprint_frac)
    ring = ground_ring(vmask, fp, exclude, cfg)
    ring_px = int(ring.sum())
    return {
        "contact_ratio": _frac(fp, sidewalk),
        "ring_ratio": _frac(ring, sidewalk) if ring_px >= cfg.min_ring_px else float("nan"),
        "overlap_ratio": _frac(vmask, sidewalk),
        "ring_px": ring_px,
    }

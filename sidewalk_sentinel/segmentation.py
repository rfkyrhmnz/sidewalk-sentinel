"""Segmentasi objek terhadap citra referensi dengan penyaring bayangan.

Dasar teori: pengurangan citra referensi (reference image subtraction) + ambang global Otsu.
Dua mekanisme yang sengaja dipisah:

* *keberadaan*  -> selisih terhadap referensi statis trotoar kosong. Kendaraan yang parkir
  tetap menjadi latar depan selama ia ada, karena referensi hanya diperbarui pada piksel
  yang BUKAN latar depan (running average selektif), jadi kendaraan diam tidak terserap.
* *gerak*       -> dihitung dari pergeseran centroid antar-frame (lihat tracker.py).
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .config import Config
from .preprocess import Prepared


@dataclass
class SegResult:
    diff8: np.ndarray            # selisih ternormalisasi (uint8)
    threshold: float             # ambang yang dipakai (Otsu yang dijepit)
    raw: np.ndarray              # mask biner 0/255 setelah ambang dan buang bayangan
    shadow: np.ndarray | None    # mask bayangan (bool) bila fitur aktif


class Reference:
    """Model latar: median dari beberapa frame trotoar kosong."""

    def __init__(self, L, a, b, V):
        self.L, self.a, self.b, self.V = L, a, b, V

    @classmethod
    def from_prepared(cls, items: list[Prepared]) -> "Reference":
        if not items:
            raise ValueError("butuh minimal satu frame referensi")
        med = lambda attr: np.median(np.stack([getattr(p, attr) for p in items]), axis=0).astype(np.float32)
        return cls(med("L"), med("a"), med("b"), med("V"))

    def update(self, cur: Prepared, fg_mask: np.ndarray, rate: float) -> None:
        """Running average selektif: hanya piksel non-foreground yang diperbarui."""
        if rate <= 0:
            return
        grown = cv2.dilate(fg_mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)), iterations=2)
        w = (grown == 0).astype(np.float32) * rate
        self.L += w * (cur.L - self.L)
        self.a += w * (cur.a - self.a)
        self.b += w * (cur.b - self.b)
        self.V += w * (cur.V - self.V)


def segment(ref: Reference, cur: Prepared, zone: np.ndarray, cfg: Config,
            gain: float = 1.0) -> SegResult:
    """Selisih -> ambang Otsu (dijepit) -> buang bayangan. `zone` = mask uint8 zona perhatian."""
    cur_L = np.clip(cur.L * gain, 0, 255)
    d_l = np.abs(cur_L - ref.L)
    d_c = (np.abs(cur.a - ref.a) + np.abs(cur.b - ref.b)) * cfg.chroma_weight
    diff8 = np.clip(np.maximum(d_l, d_c), 0, 255).astype(np.uint8)

    inside = zone > 0
    vals = diff8[inside]
    if vals.size < 100:
        thr = cfg.diff_floor
    else:
        otsu, _ = cv2.threshold(vals.reshape(-1, 1), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        thr = float(np.clip(otsu, cfg.diff_floor, cfg.diff_cap))
    fg = (diff8 > thr) & inside

    ratio = (cur.V * gain) / np.maximum(ref.V, 1.0)
    chroma = np.abs(cur.a - ref.a) + np.abs(cur.b - ref.b)
    if cfg.use_raw_evidence:
        # Filter homomorfik meninggalkan halo di tepi bayangan/iluminasi yang tajam. Halo itu tidak
        # disertai perubahan kecerahan/warna sungguhan di citra asli, jadi dibuang di sini.
        fg &= (np.abs(ratio - 1.0) > cfg.evidence_ratio) | (chroma > cfg.evidence_chroma)

    shadow = None
    if cfg.use_shadow_removal:
        shadow = (ratio > cfg.shadow_v_lo) & (ratio < cfg.shadow_v_hi) & (chroma < cfg.shadow_chroma_tol)
        fg &= ~shadow

    return SegResult(diff8=diff8, threshold=thr, raw=(fg.astype(np.uint8) * 255), shadow=shadow)

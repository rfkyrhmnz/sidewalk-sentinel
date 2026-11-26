"""Praproses citra.

Penghalusan Gaussian, transformasi pangkat (gamma), CLAHE, dan penyetaraan gain kecerahan.
Filter homomorfik (Gaussian high-emphasis lewat FFT) meredam iluminasi yang berubah pelan,
misalnya bayangan pohon atau awan. Warna diubah ke CIELAB dan HSV; selisih dihitung di ruang
yang memisahkan kecerahan (L) dari warna (a, b).
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .config import Config


@dataclass
class Prepared:
    bgr: np.ndarray   # frame (ukuran kerja), untuk anotasi
    L: np.ndarray     # float32, kecerahan ternormalisasi 0..255
    a: np.ndarray     # float32, kanal kroma a (CIELAB)
    b: np.ndarray     # float32, kanal kroma b (CIELAB)
    V: np.ndarray     # float32, kanal V (HSV) pada citra yang sudah dihaluskan


class Preprocessor:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        self._H: dict[tuple[int, int], np.ndarray] = {}
        self._gamma_lut = self._build_gamma_lut(cfg.gamma)

    # ------------------------------------------------------------------ utilitas
    def resize(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        if w == self.cfg.work_width:
            return frame
        scale = self.cfg.work_width / w
        return cv2.resize(frame, (self.cfg.work_width, max(1, round(h * scale))),
                          interpolation=cv2.INTER_AREA)

    @staticmethod
    def _build_gamma_lut(gamma: float) -> np.ndarray:
        x = np.arange(256, dtype=np.float32) / 255.0
        return np.clip(255.0 * np.power(x, gamma), 0, 255).astype(np.uint8)

    def _homomorphic_filter(self, h: int, w: int) -> np.ndarray:
        """H(u,v) Gaussian high-emphasis pada tata letak rfft2 (siklus/piksel)."""
        key = (h, w)
        if key not in self._H:
            fy = np.fft.fftfreq(h)[:, None]
            fx = np.fft.rfftfreq(w)[None, :]
            d2 = fx * fx + fy * fy
            d0 = max(self.cfg.homo_cutoff, 1e-4)
            gl, gh = self.cfg.homo_gamma_low, self.cfg.homo_gamma_high
            H = (gh - gl) * (1.0 - np.exp(-d2 / (d0 * d0))) + gl
            H[0, 0] = 1.0  # pertahankan kecerahan rata-rata; gain global diurus match_gain
            self._H[key] = H.astype(np.float32)
        return self._H[key]

    def homomorphic(self, L: np.ndarray) -> np.ndarray:
        """Filter homomorfik: log -> FFT -> kalikan H -> IFFT -> exp."""
        h, w = L.shape
        H = self._homomorphic_filter(h, w)
        spectrum = np.fft.rfft2(np.log1p(L))
        out = np.expm1(np.fft.irfft2(spectrum * H, s=(h, w)))
        return np.clip(out, 0, 255).astype(np.float32)

    # ------------------------------------------------------------------- pipeline
    def __call__(self, frame: np.ndarray) -> Prepared:
        bgr = self.resize(frame)
        k = self.cfg.blur_ksize
        blur = cv2.GaussianBlur(bgr, (k, k), 0) if k > 1 else bgr
        lab = cv2.cvtColor(blur, cv2.COLOR_BGR2LAB)
        hsv = cv2.cvtColor(blur, cv2.COLOR_BGR2HSV)
        L8 = lab[..., 0]
        if self.cfg.gamma != 1.0:
            L8 = cv2.LUT(L8, self._gamma_lut)
        if self.cfg.use_clahe:
            L8 = self._clahe.apply(L8)
        L = L8.astype(np.float32)
        if self.cfg.use_homomorphic:
            L = self.homomorphic(L)
        return Prepared(bgr=bgr, L=L,
                        a=lab[..., 1].astype(np.float32),
                        b=lab[..., 2].astype(np.float32),
                        V=hsv[..., 2].astype(np.float32))


def match_gain(cur_L: np.ndarray, ref_L: np.ndarray, bg_mask: np.ndarray | None = None,
               lo: float = 0.6, hi: float = 1.6) -> float:
    """Transformasi linear (gain) agar kecerahan frame setara dengan referensi.

    Gain diestimasi dari median piksel latar saja, sehingga keberadaan kendaraan tidak
    mengacaukan estimasi. Dipakai untuk mengimbangi perubahan cahaya global (awan, auto-exposure).
    """
    sl = (slice(None, None, 4), slice(None, None, 4))
    c, r = cur_L[sl], ref_L[sl]
    if bg_mask is not None:
        m = bg_mask[sl]
        if int(m.sum()) > 200:
            c, r = c[m], r[m]
    gain = (float(np.median(r)) + 1e-3) / (float(np.median(c)) + 1e-3)
    return float(np.clip(gain, lo, hi))

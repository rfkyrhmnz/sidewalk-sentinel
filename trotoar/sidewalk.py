"""Segmentasi trotoar.

Tiga sumber mask trotoar:
  1. ``segment_sidewalk_classical``: pengolahan citra klasik (tanpa training) -> baseline.
  2. ``sidewalk_from_polygon``: poligon manual (cocok untuk video HP di tripod).
  3. Model deep learning (lihat ``vehicles.YoloSidewalkSegmenter``).

Segmentasi klasik di sini adalah *region growing statistik berbasis biji*:
  - fitur piksel = [L*w, a, b, tekstur] (ruang warna CIE-Lab + std lokal luminans),
  - statistik (mean, kovarians) diambil dari "kotak biji" yang diasumsikan trotoar,
  - piksel diterima jika jarak Mahalanobis-nya ke statistik biji <= ambang,
  - dibersihkan dengan morfologi, hanya komponen yang terhubung ke biji yang dipertahankan.
Metode ini rapuh terhadap bayangan dan trotoar yang warnanya mirip jalan; ia dimaksudkan
sebagai pembanding (baseline) terhadap model deep learning, bukan penggantinya.
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

from .config import Config


# ----------------------------------------------------------------------------- util
def _resize_for_proc(img: np.ndarray, max_side: int):
    h, w = img.shape[:2]
    s = min(1.0, max_side / max(h, w))
    if s < 1.0:
        img = cv2.resize(img, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)
    return img, s


def seed_mask(shape, box) -> np.ndarray:
    """Mask boolean kotak biji dari pecahan (x1, y1, x2, y2)."""
    h, w = shape[:2]
    x1, y1, x2, y2 = box
    ya, yb = int(y1 * h), max(int(y1 * h) + 1, int(y2 * h))
    xa, xb = int(x1 * w), max(int(x1 * w) + 1, int(x2 * w))
    m = np.zeros((h, w), bool)
    m[ya:yb, xa:xb] = True
    return m


def _features(small_bgr: np.ndarray, cfg: Config) -> np.ndarray:
    blur = cv2.bilateralFilter(small_bgr, 7, 30, 7)          # haluskan noise, jaga tepi
    lab = cv2.cvtColor(blur, cv2.COLOR_BGR2LAB).astype(np.float32)
    L = lab[..., 0]
    k = int(cfg.texture_ksize) | 1
    mean = cv2.blur(L, (k, k))
    mean2 = cv2.blur(L * L, (k, k))
    tex = np.sqrt(np.maximum(mean2 - mean * mean, 0.0))
    tex = cv2.GaussianBlur(tex, (0, 0), k / 2.0)
    return np.dstack([L * cfg.w_luma, lab[..., 1], lab[..., 2], tex * cfg.w_texture]).astype(np.float32)


def _mahalanobis(feats: np.ndarray, seed: np.ndarray, reg: float) -> np.ndarray:
    S = feats[seed].astype(np.float64)
    mu = S.mean(0)
    cov = np.cov(S.T) + reg * np.eye(S.shape[1])
    inv = np.linalg.inv(cov)
    d = (feats - mu).astype(np.float64)
    m2 = np.einsum("hwi,ij,hwj->hw", d, inv, d)
    return np.sqrt(np.maximum(m2, 0.0)).astype(np.float32)


def _clean(mask: np.ndarray, seed: np.ndarray, cfg: Config) -> np.ndarray:
    h, w = mask.shape
    ms = max(h, w)
    ko = max(3, int(cfg.morph_open_frac * ms) | 1)
    kc = max(5, int(cfg.morph_close_frac * ms) | 1)
    m = mask.astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ko, ko)))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kc, kc)))
    _, labels = cv2.connectedComponents(m, connectivity=8)
    keep = np.unique(labels[seed & (m > 0)])
    keep = keep[keep != 0]
    if keep.size == 0:
        return np.zeros_like(mask)
    out = np.isin(labels, keep)
    return _fill_small_holes(out, cfg.hole_area_frac * h * w)


def _fill_small_holes(mask: np.ndarray, max_area: float) -> np.ndarray:
    """Tutup lubang kecil (noise). Lubang besar (mis. tempat kendaraan) sengaja dibiarkan."""
    m = mask.astype(np.uint8)
    cnts, hier = cv2.findContours(m, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if hier is None:
        return mask
    for i, c in enumerate(cnts):
        if hier[0][i][3] != -1 and cv2.contourArea(c) < max_area:
            cv2.drawContours(m, [c], -1, 1, thickness=cv2.FILLED)
    return m.astype(bool)


def _grabcut_refine(small_bgr, mask, excl, iters: int = 3) -> np.ndarray:
    if mask.sum() < 100:
        return mask
    k = max(3, int(0.02 * max(mask.shape)) | 1)
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    sure_fg = cv2.erode(mask.astype(np.uint8), ker).astype(bool)
    prob = cv2.dilate(mask.astype(np.uint8), ker).astype(bool)
    gc = np.full(mask.shape, cv2.GC_BGD, np.uint8)
    gc[prob] = cv2.GC_PR_BGD
    gc[mask] = cv2.GC_PR_FGD
    gc[sure_fg] = cv2.GC_FGD
    if excl is not None:
        gc[excl] = cv2.GC_BGD
    if not (gc == cv2.GC_FGD).any():
        return mask
    bgd = np.zeros((1, 65), np.float64)
    fgd = np.zeros((1, 65), np.float64)
    try:
        cv2.grabCut(small_bgr, gc, None, bgd, fgd, iters, cv2.GC_INIT_WITH_MASK)
    except cv2.error:
        return mask
    return (gc == cv2.GC_FGD) | (gc == cv2.GC_PR_FGD)


# ----------------------------------------------------------------------------- publik
def segment_sidewalk_classical(img_bgr: np.ndarray, cfg: Config, exclude: np.ndarray | None = None):
    """Segmentasi trotoar klasik. ``exclude`` = mask objek dinamis (kendaraan, orang) yang
    dibuang dari statistik biji dan dari hasil. Mengembalikan (mask bool HxW, info dict)."""
    h, w = img_bgr.shape[:2]
    small, _ = _resize_for_proc(img_bgr, cfg.proc_max_side)
    sh, sw = small.shape[:2]

    excl = None
    if exclude is not None and exclude.any():
        excl = cv2.resize(exclude.astype(np.uint8), (sw, sh), interpolation=cv2.INTER_NEAREST)
        excl = cv2.dilate(excl, np.ones((5, 5), np.uint8)).astype(bool)

    seed = seed_mask((sh, sw), cfg.seed_box)
    seed_valid = seed & ~excl if excl is not None else seed
    info = {"seed_box_px": tuple(int(v) for v in (cfg.seed_box[0] * w, cfg.seed_box[1] * h,
                                                  cfg.seed_box[2] * w, cfg.seed_box[3] * h))}
    if seed_valid.sum() < 50:
        info["warning"] = "kotak biji tertutup objek/terlalu kecil; atur --seed"
        return np.zeros((h, w), bool), info

    feats = _features(small, cfg)
    dist = _mahalanobis(feats, seed_valid, cfg.cov_reg)
    mask = dist <= cfg.maha_thresh
    mask[: int(cfg.min_y_frac * sh)] = False
    if excl is not None:
        mask &= ~excl

    mask = _clean(mask, seed_valid, cfg)
    if cfg.refine == "grabcut":
        mask = _grabcut_refine(small, mask, excl)
        mask = _clean(mask, seed_valid, cfg)

    if (sh, sw) != (h, w):
        mask = cv2.resize(mask.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST).astype(bool)
    info["dist_small"] = dist
    return mask, info


def sidewalk_from_polygon(shape, polygons, normalized: bool = True) -> np.ndarray:
    """Rasterisasi satu/lebih poligon trotoar. ``polygons`` = daftar poligon (daftar titik [x, y])."""
    h, w = shape[:2]
    m = np.zeros((h, w), np.uint8)
    if polygons and not isinstance(polygons[0][0], (list, tuple)):
        polygons = [polygons]                      # satu poligon saja
    for poly in polygons:
        pts = np.asarray(poly, np.float64)
        if normalized:
            pts = pts * np.array([w, h])
        cv2.fillPoly(m, [np.round(pts).astype(np.int32)], 1)
    return m.astype(bool)


def load_polygon_file(path) -> list:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return data["polygons"]
    return data


def save_polygon_file(path, polygons) -> None:
    Path(path).write_text(json.dumps({"normalized": True, "polygons": polygons}, indent=1), encoding="utf-8")

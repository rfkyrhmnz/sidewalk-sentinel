"""Representasi dan deskripsi objek (Materi 5).

Tiap komponen terhubung pada mask diwakili oleh kontur (batas), lalu dideskripsikan dengan
deskriptor bentuk: luas, keliling, centroid, bounding box, rasio aspek, extent, solidity,
compactness (P^2 / 4*pi*A) dan momen invarian Hu pertama. Deskriptor ini dipakai untuk
menyaring noise/pejalan kaki dan sebagai fitur pelacakan.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from .config import Config


@dataclass
class Blob:
    bbox: tuple[int, int, int, int]            # x, y, w, h
    area: float
    perimeter: float
    centroid: tuple[float, float]
    aspect: float                               # lebar / tinggi
    extent: float                               # luas / luas bbox
    solidity: float                             # luas / luas convex hull
    compactness: float                          # P^2 / (4 pi A); lingkaran = 1
    hu0: float                                  # momen Hu pertama (skala log)
    label: str = "noise"                        # 'kendaraan' | 'pejalan' | 'noise'
    contact: float = 0.0                        # porsi jalur kaki yang berada di ROI
    contour: np.ndarray | None = field(default=None, repr=False)

    def as_dict(self) -> dict:
        return {
            "bbox": list(self.bbox), "area": round(self.area, 1),
            "perimeter": round(self.perimeter, 1),
            "centroid": [round(self.centroid[0], 1), round(self.centroid[1], 1)],
            "aspect": round(self.aspect, 3), "extent": round(self.extent, 3),
            "solidity": round(self.solidity, 3), "compactness": round(self.compactness, 3),
            "hu0": round(self.hu0, 3), "label": self.label, "contact": round(self.contact, 3),
        }


def contact_ratio(roi_mask: np.ndarray, bbox: tuple[int, int, int, int], strip: float = 0.30) -> float:
    """Porsi 'jalur kaki' (30% bagian bawah bbox, tempat roda menyentuh tanah) di dalam ROI."""
    x, y, w, h = bbox
    y0 = y + int(h * (1.0 - strip))
    patch = roi_mask[y0:y + h, x:x + w]
    return float((patch > 0).mean()) if patch.size else 0.0


def describe(mask: np.ndarray, roi_mask: np.ndarray, cfg: Config) -> list[Blob]:
    h_img, w_img = mask.shape
    frame_area = float(h_img * w_img)
    found = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = found[0] if len(found) == 2 else found[1]   # kompatibel OpenCV 3/4/5

    blobs: list[Blob] = []
    for cnt in contours:
        area = float(cv2.contourArea(cnt))
        if area < 0.4 * cfg.min_area_frac * frame_area:
            continue                                        # terlalu kecil: tak perlu dideskripsikan
        x, y, w, h = cv2.boundingRect(cnt)
        perimeter = float(cv2.arcLength(cnt, True))
        m = cv2.moments(cnt)
        cx = m["m10"] / m["m00"] if m["m00"] > 0 else x + w / 2.0
        cy = m["m01"] / m["m00"] if m["m00"] > 0 else y + h / 2.0
        hull_area = float(cv2.contourArea(cv2.convexHull(cnt))) or 1.0
        hu = cv2.HuMoments(m).flatten()[0]
        hu0 = -math.copysign(1.0, hu) * math.log10(abs(hu)) if hu != 0 else 0.0
        blob = Blob(
            bbox=(x, y, w, h), area=area, perimeter=perimeter, centroid=(cx, cy),
            aspect=w / max(h, 1), extent=area / max(w * h, 1), solidity=area / hull_area,
            compactness=(perimeter ** 2) / (4 * math.pi * area) if area > 0 else 0.0,
            hu0=hu0, contour=cnt,
        )
        blob.label = classify(blob, frame_area, cfg)
        blob.contact = contact_ratio(roi_mask, blob.bbox)
        blobs.append(blob)
    return blobs


def classify(blob: Blob, frame_area: float, cfg: Config) -> str:
    """Aturan sederhana berbasis deskriptor (bukan pembelajaran mesin)."""
    frac = blob.area / frame_area
    if frac < cfg.min_area_frac or frac > cfg.max_area_frac:
        return "noise"
    if blob.solidity < cfg.min_solidity:
        return "noise"
    if cfg.filter_pedestrians and blob.aspect < cfg.ped_max_aspect and frac < cfg.ped_max_area_frac:
        return "pejalan"
    return "kendaraan"

"""Visualisasi hasil: overlay trotoar, mask kendaraan berwarna menurut status, label, dan banner ringkasan."""
from __future__ import annotations

import math

import cv2
import numpy as np

from .decision import (PARKING_STATUSES, STATUS_CANDIDATE, STATUS_CONFIRMED, STATUS_MOVING, STATUS_PARKED,
                       STATUS_RIDDEN, STATUS_ROAD, VehicleResult)

SIDEWALK_COLOR = (255, 170, 0)            # BGR
STATUS_COLOR = {
    STATUS_ROAD: (60, 200, 60),
    STATUS_RIDDEN: (0, 215, 255),
    STATUS_MOVING: (0, 215, 255),
    STATUS_CANDIDATE: (0, 140, 255),
    STATUS_PARKED: (0, 0, 255),
    STATUS_CONFIRMED: (0, 0, 200),
}
STATUS_SHORT = {
    STATUS_ROAD: "jalan",
    STATUS_RIDDEN: "trotoar+pengendara",
    STATUS_MOVING: "trotoar-bergerak",
    STATUS_CANDIDATE: "kandidat parkir",
    STATUS_PARKED: "PARKIR DI TROTOAR",
    STATUS_CONFIRMED: "PARKIR TERKONFIRMASI",
}


def _blend(img, mask, color, alpha):
    if mask is None or not mask.any():
        return
    layer = img.copy()
    layer[mask] = color
    cv2.addWeighted(layer, alpha, img, 1 - alpha, 0, dst=img)


def _text(img, txt, org, scale, color, thick=1):
    (tw, th), base = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)
    x, y = int(org[0]), int(org[1])
    x = max(0, min(x, img.shape[1] - tw - 4))
    y = max(th + 4, min(y, img.shape[0] - 2))
    cv2.rectangle(img, (x - 2, y - th - 4), (x + tw + 2, y + base), (0, 0, 0), -1)
    cv2.putText(img, txt, (x, y - 2), cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def draw_frame(img_bgr: np.ndarray, sidewalk: np.ndarray, results: list[VehicleResult], banner: str | None = None,
               debug: bool = False) -> np.ndarray:
    out = img_bgr.copy()
    h, w = out.shape[:2]
    scale = max(0.45, w / 1600.0)
    thick = max(1, int(round(scale * 2)))

    _blend(out, sidewalk, SIDEWALK_COLOR, 0.30)
    for r in results:
        _blend(out, r.obj.mask, STATUS_COLOR[r.status], 0.45)
    for r in results:
        col = STATUS_COLOR[r.status]
        cnts, _ = cv2.findContours(r.obj.mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(out, cnts, -1, col, thick)
        x1, y1, x2, y2 = (int(v) for v in r.obj.box)
        tid = f"#{r.track_id} " if r.track_id is not None else ""
        label = f"{tid}{r.obj.label} {r.obj.conf:.2f} | {STATUS_SHORT[r.status]}"
        if r.dwell_s > 0:
            label += f" {r.dwell_s:.0f}s"
        _text(out, label, (x1, y1 - 4), scale * 0.8, col, thick)
        if debug:
            ring = "-" if math.isnan(r.ring_ratio) else f"{r.ring_ratio:.2f}"
            _text(out, f"kontak={r.contact_ratio:.2f} cincin={ring}", (x1, y2 + 18), scale * 0.7, (255, 255, 255), 1)

    n_side = sum(1 for r in results if r.on_sidewalk)
    n_park = sum(1 for r in results if r.status in PARKING_STATUSES)
    head = banner if banner is not None else f"Kendaraan: {len(results)} | di trotoar: {n_side} | parkir di trotoar: {n_park}"
    _text(out, head, (8, int(26 * max(1.0, scale * 1.4))), scale * 0.9, (255, 255, 255), thick)
    return out


draw_text = _text          # alias publik untuk teks berlatar hitam

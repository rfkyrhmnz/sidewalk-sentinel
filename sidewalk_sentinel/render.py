"""Penggambaran anotasi bergaya HUD dan montase tahapan pipeline (hanya ASCII: font Hershey)."""
from __future__ import annotations

import cv2
import numpy as np

# Warna BGR
CYAN = (255, 229, 0)
MAGENTA = (214, 43, 255)
AMBER = (32, 176, 255)
RED = (96, 69, 255)
GREEN = (159, 255, 57)
WHITE = (235, 235, 235)
DIM = (150, 150, 150)

STATE_STYLE = {
    "parkir": (RED, "PARKIR"),
    "naik_trotoar": (MAGENTA, "NAIK TROTOAR"),
    "diam": (AMBER, "DIAM"),
    "bergerak": (CYAN, "BERGERAK"),
    "luar": (DIM, ""),
}
FONT = cv2.FONT_HERSHEY_SIMPLEX


def put_text(img, text, org, color=WHITE, scale=0.45, thick=1):
    x, y = org
    (tw, th), base = cv2.getTextSize(text, FONT, scale, thick)
    x = int(max(2, min(x, img.shape[1] - tw - 6)))
    y = int(max(th + 4, min(y, img.shape[0] - 4)))
    over = img.copy()
    cv2.rectangle(over, (x - 3, y - th - 4), (x + tw + 3, y + base + 1), (10, 8, 6), -1)
    cv2.addWeighted(over, 0.62, img, 0.38, 0, img)
    cv2.putText(img, text, (x, y), FONT, scale, color, thick, cv2.LINE_AA)


def corner_box(img, bbox, color, thick=2, arm=0.28):
    x, y, w, h = bbox
    a = int(max(6, min(w, h) * arm))
    for (px, py, dx, dy) in ((x, y, 1, 1), (x + w, y, -1, 1), (x, y + h, 1, -1), (x + w, y + h, -1, -1)):
        cv2.line(img, (px, py), (px + dx * a, py), color, thick, cv2.LINE_AA)
        cv2.line(img, (px, py), (px, py + dy * a), color, thick, cv2.LINE_AA)
    cv2.rectangle(img, (x, y), (x + w, y + h), color, 1, cv2.LINE_AA)


def draw_roi(img, roi_px, color=CYAN, alpha=0.13):
    over = img.copy()
    cv2.fillPoly(over, [roi_px], color)
    cv2.addWeighted(over, alpha, img, 1 - alpha, 0, img)
    cv2.polylines(img, [roi_px], True, color, 1, cv2.LINE_AA)
    for p in roi_px:
        cv2.circle(img, tuple(int(v) for v in p), 3, color, -1, cv2.LINE_AA)


def draw_progress(img, bbox, frac, color):
    x, y, w, h = bbox
    frac = float(np.clip(frac, 0, 1))
    y0 = min(img.shape[0] - 5, y + h + 4)
    cv2.rectangle(img, (x, y0), (x + w, y0 + 3), (40, 40, 40), -1)
    cv2.rectangle(img, (x, y0), (x + int(w * frac), y0 + 3), color, -1)


def annotate(frame, roi_px, tracks, t, counts, park_seconds):
    out = frame.copy()
    draw_roi(out, roi_px)
    for tr in tracks:
        color, name = STATE_STYLE.get(tr["state"], (DIM, ""))
        corner_box(out, tr["bbox"], color, 2 if tr["state"] != "luar" else 1)
        x, y, w, h = tr["bbox"]
        if tr["state"] == "diam":
            label = f"#{tr['id']} DIAM {tr['dwell']:.0f}/{park_seconds:.0f}s"
            draw_progress(out, tr["bbox"], tr["dwell"] / park_seconds, color)
        elif name:
            label = f"#{tr['id']} {name}"
        else:
            label = f"#{tr['id']}"
        put_text(out, label, (x, y - 6), color, 0.42)
    put_text(out, f"SIDEWALK SENTINEL  T+{t:06.1f}s  PARKIR:{counts['parkir']}  NAIK:{counts['naik_trotoar']}",
             (8, 18), CYAN, 0.45)
    return out


def _tile(img, label, size):
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    tile = cv2.resize(img, size, interpolation=cv2.INTER_AREA)
    put_text(tile, label, (6, size[1] - 8), CYAN, 0.42)
    cv2.rectangle(tile, (0, 0), (size[0] - 1, size[1] - 1), (70, 60, 40), 1)
    return tile


def montage(panels: list[tuple[str, np.ndarray]], cols: int = 3, tile_w: int = 320) -> np.ndarray:
    h, w = panels[0][1].shape[:2]
    size = (tile_w, max(1, round(tile_w * h / w)))
    tiles = [_tile(img, label, size) for label, img in panels]
    while len(tiles) % cols:
        tiles.append(np.zeros_like(tiles[0]))
    rows = [np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)]
    return np.vstack(rows)

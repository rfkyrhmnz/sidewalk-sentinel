"""Generator adegan jalan SINTETIS untuk menguji logika program tanpa dataset nyata.

PENTING: angka akurasi dari data sintetis hanya membuktikan program berjalan sesuai desain
(geometri, alur, temporal). Ia TIDAK mengukur kinerja pada trotoar sungguhan.

Konvensi ``shift=(dx, dy)``: pergeseran titik statis pada gambar (sama dengan ``CameraMotion.offset``).
"""
from __future__ import annotations

import cv2
import numpy as np

from trotoar.vehicles import Detections, Obj

W, H, M = 640, 480, 60           # ukuran gambar dan margin kanvas
CW, CH = W + 2 * M, H + 2 * M


def _P(pts):
    return (np.asarray(pts, np.float32) + M).round().astype(np.int32)


def _background(kind: str, rng):
    img = np.zeros((CH, CW, 3), np.float32)
    gt = np.zeros((CH, CW), np.uint8)
    img[:] = (235, 206, 135)                                           # langit
    # gedung
    x = -M
    while x < W + M:
        bw = int(rng.integers(50, 110))
        col = tuple(int(c) for c in rng.integers(90, 170, 3))
        cv2.rectangle(img, tuple(_P([(x, 60)])[0]), tuple(_P([(x + bw, 200)])[0]), col, -1)
        for wy in range(75, 190, 28):                                  # jendela (fitur untuk optical flow)
            for wx in range(x + 8, x + bw - 14, 24):
                cv2.rectangle(img, tuple(_P([(wx, wy)])[0]), tuple(_P([(wx + 10, wy + 14)])[0]), (60, 60, 60), -1)
        x += bw
    if kind == "across":           # kamera menghadap jalan dari atas trotoar
        cv2.rectangle(img, tuple(_P([(-M, 190)])[0]), tuple(_P([(W + M, 300)])[0]), (95, 95, 95), -1)
        for lx in range(-M, W + M, 70):
            cv2.rectangle(img, tuple(_P([(lx, 233)])[0]), tuple(_P([(lx + 35, 238)])[0]), (240, 240, 240), -1)
        cv2.rectangle(img, tuple(_P([(-M, 298)])[0]), tuple(_P([(W + M, 303)])[0]), (205, 205, 205), -1)
        cv2.rectangle(img, tuple(_P([(-M, 303)])[0]), tuple(_P([(W + M, H + M)])[0]), (150, 165, 175), -1)
        cv2.rectangle(gt, tuple(_P([(-M, 303)])[0]), tuple(_P([(W + M, H + M)])[0]), 1, -1)
        side_region = (slice(303 + M, CH), slice(0, CW))
    else:                          # "along": menyusuri jalan, trotoar di kiri
        road = [(420, 190), (W + M, 190), (W + M, H + M), (113, H + M)]
        side = [(-M, 190), (420, 190), (113, H + M), (-M, H + M)]
        cv2.fillPoly(img, [_P(road)], (95, 95, 95))
        cv2.fillPoly(img, [_P(side)], (150, 165, 175))
        cv2.fillPoly(gt, [_P(side)], 1)
        cv2.line(img, tuple(_P([(420, 190)])[0]), tuple(_P([(113, H + M)])[0]), (205, 205, 205), 5)
        side_region = None
    # paving (garis rapat) + noise tekstur
    side_mask = gt.astype(bool)
    for gx in range(-M, W + M, 40):
        cv2.line(img, tuple(_P([(gx, 190)])[0]), tuple(_P([(gx, H + M)])[0]), (125, 135, 145), 2)
    for gy in range(200, H + M, 40):
        cv2.line(img, tuple(_P([(-M, gy)])[0]), tuple(_P([(W + M, gy)])[0]), (125, 135, 145), 1)
    # paving hanya pada trotoar: kembalikan jalan ke abu-abu polos
    if kind == "across":
        cv2.rectangle(img, tuple(_P([(-M, 190)])[0]), tuple(_P([(W + M, 297)])[0]), (95, 95, 95), -1)
        for lx in range(-M, W + M, 70):
            cv2.rectangle(img, tuple(_P([(lx, 233)])[0]), tuple(_P([(lx + 35, 238)])[0]), (240, 240, 240), -1)
    else:
        road_m = np.zeros((CH, CW), np.uint8)
        cv2.fillPoly(road_m, [_P(road)], 1)
        img[road_m.astype(bool)] = (95, 95, 95)
        cv2.line(img, tuple(_P([(420, 190)])[0]), tuple(_P([(113, H + M)])[0]), (205, 205, 205), 5)
    sigma = np.where(side_mask, 4.0, 5.0)[..., None]
    img += rng.normal(0, 1, img.shape).astype(np.float32) * sigma
    return np.clip(img, 0, 255), gt


def _draw_vehicle(img, vmask, spec, rng):
    x1, y1, x2, y2 = spec["box"]
    w, h = x2 - x1, y2 - y1
    col = spec.get("color", (30, 30, 200))
    shape_mask = np.zeros(vmask.shape, np.uint8)
    # bayangan lembut di bawah kendaraan
    sh = np.zeros(vmask.shape, np.float32)
    cv2.ellipse(sh, tuple(_P([((x1 + x2) / 2, y2 - 0.02 * h)])[0]), (int(0.55 * w), max(3, int(0.06 * h))), 0, 0, 360, 1, -1)
    sh = cv2.GaussianBlur(sh, (0, 0), 3)
    img *= (1 - 0.22 * sh)[..., None]
    if spec["kind"] == "car":
        body = _P([(x1, y1 + .35 * h), (x1 + .15 * w, y1 + .35 * h), (x1 + .3 * w, y1), (x1 + .75 * w, y1),
                   (x1 + .9 * w, y1 + .35 * h), (x2, y1 + .4 * h), (x2, y2 - .15 * h), (x1, y2 - .15 * h)])
        cv2.fillPoly(img, [body], col)
        cv2.fillPoly(shape_mask, [body], 1)
        win = _P([(x1 + .2 * w, y1 + .3 * h), (x1 + .32 * w, y1 + .08 * h), (x1 + .72 * w, y1 + .08 * h), (x1 + .84 * w, y1 + .3 * h)])
        cv2.fillPoly(img, [win], (190, 170, 120))
        for cx in (x1 + .22 * w, x1 + .78 * w):
            c = tuple(_P([(cx, y2 - .12 * h)])[0])
            cv2.circle(img, c, int(.14 * h), (25, 25, 25), -1)
            cv2.circle(shape_mask, c, int(.14 * h), 1, -1)
    else:                                          # motor
        for cx in (x1 + .2 * w, x1 + .8 * w):
            c = tuple(_P([(cx, y2 - .2 * h)])[0])
            cv2.circle(img, c, int(.2 * h), (25, 25, 25), -1)
            cv2.circle(shape_mask, c, int(.2 * h), 1, -1)
        body = _P([(x1 + .3 * w, y1 + .35 * h), (x1 + .75 * w, y1 + .3 * h), (x1 + .85 * w, y1 + .5 * h),
                   (x1 + .6 * w, y2 - .3 * h), (x1 + .25 * w, y2 - .3 * h)])
        cv2.fillPoly(img, [body], col)
        cv2.fillPoly(shape_mask, [body], 1)
        cv2.rectangle(img, tuple(_P([(x1 + .1 * w, y1)])[0]), tuple(_P([(x1 + .3 * w, y1 + .1 * h)])[0]), (40, 40, 40), -1)
        cv2.rectangle(shape_mask, tuple(_P([(x1 + .1 * w, y1)])[0]), tuple(_P([(x1 + .3 * w, y1 + .1 * h)])[0]), 1, -1)
    vmask |= shape_mask.astype(bool)

    pmask = None
    if spec.get("rider"):
        cx = (x1 + x2) / 2
        pmask = np.zeros(vmask.shape, np.uint8)
        torso = _P([(cx - 15, y1 - 52), (cx + 15, y1 - 52), (cx + 17, y1 + 0.75 * h), (cx - 17, y1 + 0.75 * h)])
        cv2.fillPoly(img, [torso], (60, 120, 60))
        cv2.fillPoly(pmask, [torso], 1)
        head = tuple(_P([(cx, y1 - 66)])[0])
        cv2.circle(img, head, 13, (120, 150, 200), -1)
        cv2.circle(pmask, head, 13, 1, -1)
        pmask = pmask.astype(bool)
    return pmask


def _obj(kind, name, label, mask_c, crop, tid):
    m = mask_c[crop]
    ys, xs = np.nonzero(m)
    if ys.size == 0:
        return None
    return Obj(kind, name, label, 0.9, (float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1)), m, tid)


def make_scene(kind: str = "across", vehicles=(), seed: int = 0, shift=(0, 0), frame_noise: float = 0.0, frame_seed: int = 0):
    """Kembalikan (img BGR uint8, gt_trotoar bool, Detections)."""
    rng = np.random.default_rng(seed)
    img, gt = _background(kind, rng)
    items = []
    for spec in vehicles:
        vmask = np.zeros((CH, CW), bool)
        pmask = _draw_vehicle(img, vmask, spec, rng)
        items.append((spec, vmask, pmask))
    dx, dy = int(round(shift[0])), int(round(shift[1]))
    oy, ox = M - dy, M - dx
    crop = (slice(oy, oy + H), slice(ox, ox + W))
    out = np.clip(img[crop], 0, 255)
    if frame_noise > 0:
        out = np.clip(out + np.random.default_rng(frame_seed).normal(0, frame_noise, out.shape), 0, 255)
    dets = Detections()
    for spec, vmask, pmask in items:
        name = "car" if spec["kind"] == "car" else "motorcycle"
        label = "mobil" if spec["kind"] == "car" else "motor"
        o = _obj("vehicle", name, label, vmask, crop, spec.get("track_id"))
        if o is not None:
            dets.vehicles.append(o)
        if pmask is not None:
            p = _obj("person", "person", "orang", pmask, crop, None)
            if p is not None:
                dets.persons.append(p)
    return out.astype(np.uint8), gt[crop].astype(bool), dets


class FakeSegmenter:
    """Segmenter tiruan: ``provider(n_panggilan, img) -> Detections``."""

    def __init__(self, provider):
        self.provider, self.n = provider, 0

    def reset_tracking(self):
        self.n = 0

    def detect(self, img, track=False):
        d = self.provider(self.n, img)
        self.n += 1
        return d

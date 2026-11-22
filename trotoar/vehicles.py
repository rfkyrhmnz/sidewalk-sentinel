"""Segmentasi kendaraan/orang dengan Ultralytics YOLO-seg (+ tracking ByteTrack untuk video).

Modul ini sengaja memisahkan *parsing hasil* (``parse_ultralytics_result``, bisa diuji tanpa
model) dari *pemanggilan model* (``YoloSegmenter``). Komponen lain hanya bergantung pada
``Detections``, sehingga model lain (mis. RT-DETR-seg, Mask R-CNN) bisa dipasang dengan
membuat kelas baru yang punya metode ``detect(img, track)`` dan ``reset_tracking()``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from .config import Config


@dataclass
class Obj:
    kind: str                       # "vehicle" | "person"
    cls_name: str                   # nama kelas asli model (huruf kecil)
    label: str                      # label Indonesia
    conf: float
    box: tuple                      # (x1, y1, x2, y2) piksel
    mask: np.ndarray                # bool HxW
    track_id: int | None = None


@dataclass
class Detections:
    vehicles: list = field(default_factory=list)
    persons: list = field(default_factory=list)
    sidewalk: np.ndarray | None = None   # mask trotoar bila model menghasilkannya


def union_masks(dets: Detections, shape) -> np.ndarray:
    """Gabungan mask semua objek dinamis (kendaraan + orang)."""
    h, w = shape[:2]
    out = np.zeros((h, w), bool)
    for o in list(dets.vehicles) + list(dets.persons):
        out |= o.mask
    return out


def _np(x):
    """Tensor torch / ndarray / list -> ndarray."""
    if hasattr(x, "cpu"):
        x = x.cpu()
    if hasattr(x, "numpy"):
        x = x.numpy()
    return np.asarray(x)


def _poly_mask(poly, shape) -> np.ndarray:
    m = np.zeros(shape[:2], np.uint8)
    pts = np.asarray(poly, np.float64).reshape(-1, 2)
    if len(pts) >= 3:
        cv2.fillPoly(m, [np.round(pts).astype(np.int32)], 1)
    return m.astype(bool)


def _box_mask(box, shape) -> np.ndarray:
    m = np.zeros(shape[:2], bool)
    x1, y1, x2, y2 = (int(round(v)) for v in box)
    m[max(0, y1):max(0, y2), max(0, x1):max(0, x2)] = True
    return m


def parse_ultralytics_result(res, cfg: Config, shape) -> Detections:
    """Ubah satu objek ``Results`` Ultralytics menjadi ``Detections``.

    Memakai poligon ``res.masks.xy`` (koordinat gambar asli) sehingga tidak perlu resize mask.
    Bila model hanya detektor (tanpa mask), dipakai mask kotak sebagai pendekatan kasar.
    """
    out = Detections()
    boxes = getattr(res, "boxes", None)
    if boxes is None or len(boxes) == 0:
        return out
    names = res.names
    cls = _np(boxes.cls).astype(int).ravel()
    conf = _np(boxes.conf).astype(float).ravel()
    xyxy = _np(boxes.xyxy).astype(float).reshape(-1, 4)
    bid = getattr(boxes, "id", None)
    ids = _np(bid).astype(int).ravel() if bid is not None else [None] * len(cls)
    masks = getattr(res, "masks", None)
    polys = masks.xy if masks is not None else [None] * len(cls)

    for i in range(len(cls)):
        name = str(names[int(cls[i])]).lower()
        poly = polys[i] if i < len(polys) else None
        m = _poly_mask(poly, shape) if poly is not None and len(poly) >= 3 else _box_mask(xyxy[i], shape)
        if not m.any():
            continue
        box = tuple(float(v) for v in xyxy[i])
        tid = int(ids[i]) if ids[i] is not None else None
        if name in cfg.vehicle_names:
            out.vehicles.append(Obj("vehicle", name, cfg.vehicle_names[name], float(conf[i]), box, m, tid))
        elif name in cfg.person_names:
            out.persons.append(Obj("person", name, "orang", float(conf[i]), box, m, tid))
        elif name in cfg.sidewalk_names:
            out.sidewalk = m if out.sidewalk is None else (out.sidewalk | m)
    return out


def _require_ultralytics():
    try:
        from ultralytics import YOLO
    except ImportError as e:                                   # pragma: no cover
        raise RuntimeError("Paket 'ultralytics' belum terpasang. Jalankan: pip install ultralytics") from e
    return YOLO


class YoloSegmenter:
    """Pembungkus model YOLO-seg untuk kendaraan + orang (+ trotoar bila kelasnya ada)."""

    def __init__(self, cfg: Config):
        YOLO = _require_ultralytics()
        self.cfg = cfg
        self.model = YOLO(cfg.weights)

    def _kwargs(self) -> dict:
        kw = dict(conf=self.cfg.conf, imgsz=self.cfg.imgsz, verbose=False)
        if self.cfg.device:
            kw["device"] = self.cfg.device
        return kw

    def reset_tracking(self) -> None:
        """Mulai video baru: buang predictor agar tracker dibuat ulang (ID mulai dari awal)."""
        try:
            self.model.predictor = None
        except Exception:                                      # pragma: no cover
            pass

    def detect(self, img_bgr: np.ndarray, track: bool = False) -> Detections:
        if track:
            res = self.model.track(img_bgr, persist=True, tracker=self.cfg.tracker, **self._kwargs())[0]
        else:
            res = self.model.predict(img_bgr, **self._kwargs())[0]
        return parse_ultralytics_result(res, self.cfg, img_bgr.shape)


class YoloSidewalkSegmenter:
    """Model YOLO-seg terpisah yang di-fine-tune hanya untuk kelas trotoar (anotasi 1 kelas saja)."""

    def __init__(self, cfg: Config):
        YOLO = _require_ultralytics()
        self.cfg = cfg
        self.model = YOLO(cfg.sidewalk_weights)

    def segment(self, img_bgr: np.ndarray) -> np.ndarray:
        kw = dict(conf=self.cfg.conf, imgsz=self.cfg.imgsz, verbose=False)
        if self.cfg.device:
            kw["device"] = self.cfg.device
        res = self.model.predict(img_bgr, **kw)[0]
        shape = img_bgr.shape
        out = np.zeros(shape[:2], bool)
        if res.masks is None or res.boxes is None:
            return out
        cls = _np(res.boxes.cls).astype(int).ravel()
        single_class = len(res.names) == 1
        for poly, c in zip(res.masks.xy, cls):
            if single_class or str(res.names[int(c)]).lower() in self.cfg.sidewalk_names:
                out |= _poly_mask(poly, shape)
        return out

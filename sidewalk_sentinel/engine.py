"""Engine: satu objek yang diberi frame demi frame dan menghasilkan kejadian.

Alur per frame:

    YOLO (kendaraan) -> kotak -> pelacak -> keputusan (parkir / naik trotoar)

Zona trotoar (poligon) menentukan di mana sebuah kendaraan dianggap "di trotoar". Detektor memutuskan apa yang
termasuk kendaraan. Engine yang sama dipakai oleh kamera langsung dan tes.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from . import render
from .config import Config
from .decision import Decider, Event
from .features import Blob, contact_ratio
from .tracker import CentroidTracker


@dataclass
class FrameResult:
    t: float
    tracks: list[dict]
    events: list[Event]


class Engine:
    def __init__(self, roi_norm, detector, cfg: Config | None = None):
        self.cfg = cfg or Config()
        self.cfg.validate()
        pts = [(float(min(1, max(0, x))), float(min(1, max(0, y)))) for x, y in roi_norm]
        if len(pts) < 3:
            raise ValueError("ROI trotoar butuh minimal 3 titik")
        self.roi_norm = pts
        self.detector = detector
        self.tracker = CentroidTracker(self.cfg)
        self.decider = Decider(self.cfg)
        self.size: tuple[int, int] | None = None   # (W, H) ukuran kerja
        self.roi_px = self.roi_mask = None
        self.counts = {"parkir": 0, "naik_trotoar": 0}
        self.events: list[Event] = []
        self.last: dict = {}
        self.header_fn = None          # opsional: fungsi(t) -> teks judul HUD (nama perangkat dan jam)

    # --------------------------------------------------------------------- setup
    def _setup(self, h: int, w: int) -> None:
        self.size = (w, h)
        self.roi_px = np.array([[x * w, y * h] for x, y in self.roi_norm], dtype=np.int32)
        self.roi_mask = np.zeros((h, w), np.uint8)
        cv2.fillPoly(self.roi_mask, [self.roi_px], 255)
        self.tracker.reset()                        # ukuran berubah: jejak lama tidak berlaku

    def _fit(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        if w <= self.cfg.work_width:
            return frame
        return cv2.resize(frame, (self.cfg.work_width, max(1, round(h * self.cfg.work_width / w))),
                          interpolation=cv2.INTER_AREA)

    def reset(self) -> None:
        self.tracker.reset()
        self.last = {}

    # ------------------------------------------------------------------- proses
    def _blobs(self, frame: np.ndarray) -> list[Blob]:
        h, w = frame.shape[:2]
        out = []
        for d in self.detector.detect(frame, self.cfg.conf, self.cfg.imgsz):
            x1, y1 = max(0.0, d.box[0]), max(0.0, d.box[1])
            x2, y2 = min(float(w), d.box[2]), min(float(h), d.box[3])
            if x2 - x1 < 2 or y2 - y1 < 2 or (x2 - x1) * (y2 - y1) < self.cfg.min_box_frac * w * h:
                continue
            bbox = (int(x1), int(y1), max(1, int(x2 - x1)), max(1, int(y2 - y1)))
            out.append(Blob(bbox, ((x1 + x2) / 2.0, (y1 + y2) / 2.0),
                            contact_ratio(self.roi_mask, bbox, self.cfg.contact_strip), d.name, d.label, d.conf))
        return out

    def process(self, frame: np.ndarray, t: float, snapshots: bool = True) -> FrameResult:
        frame = self._fit(frame)
        h, w = frame.shape[:2]
        if (w, h) != self.size:
            self._setup(h, w)
        tracks = self.tracker.update(self._blobs(frame), t, float(w))
        events = self.decider.step(tracks, t, float(w))

        views = [{"id": tr.id, "bbox": tr.blob.bbox, "state": Decider.state_of(tr, t),
                  "dwell": (t - tr.still_since) if tr.still_since is not None else 0.0,
                  "in_roi": tr.in_roi, "speed": tr.speed, "label": tr.blob.label, "conf": tr.blob.conf}
                 for tr in tracks]
        for ev in events:
            self.counts[ev.kind] += 1
        self.last = {"t": t, "bgr": frame, "tracks": views, "annotated": None}
        if events and snapshots:
            ok, jpg = cv2.imencode(".jpg", self.annotated(), [cv2.IMWRITE_JPEG_QUALITY, 88])
            for ev in events:
                ev.snapshot = jpg.tobytes() if ok else None
        self.events.extend(events)
        return FrameResult(t=t, tracks=views, events=events)

    # --------------------------------------------------------------- visualisasi
    def annotated(self) -> np.ndarray:
        if not self.last:
            raise RuntimeError("belum ada frame yang diproses")
        if self.last["annotated"] is None:
            header = self.header_fn(self.last["t"]) if self.header_fn else None
            self.last["annotated"] = render.annotate(
                self.last["bgr"], self.roi_px, self.last["tracks"], self.last["t"],
                self.counts, self.cfg.park_seconds, header)
        return self.last["annotated"]

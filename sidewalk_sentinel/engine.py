"""Engine: merangkai seluruh tahap menjadi satu objek yang diberi frame demi frame.

Alur per frame (nomor materi dalam kurung):

    praproses (1,2,6) -> penyetaraan gain (1) -> selisih vs referensi + Otsu + buang bayangan (4,6)
    -> morfologi (3) -> deskripsi blob (5) -> pelacakan -> keputusan (parkir / naik trotoar)

Engine yang sama dipakai oleh analisis video, kamera langsung, CLI, dan tes.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from . import render
from .config import Config
from .decision import Decider, Event
from .features import describe
from .morphology import clean_mask
from .preprocess import Preprocessor, match_gain
from .segmentation import Reference, segment
from .tracker import CentroidTracker


@dataclass
class FrameResult:
    t: float
    tracks: list[dict]
    events: list[Event]
    threshold: float


class Engine:
    def __init__(self, roi_norm, cfg: Config | None = None):
        self.cfg = cfg or Config()
        self.cfg.validate()
        pts = [(float(min(1, max(0, x))), float(min(1, max(0, y)))) for x, y in roi_norm]
        if len(pts) < 3:
            raise ValueError("ROI trotoar butuh minimal 3 titik")
        self.roi_norm = pts
        self.pre = Preprocessor(self.cfg)
        self.tracker = CentroidTracker(self.cfg)
        self.decider = Decider(self.cfg)
        self.ref: Reference | None = None
        self.size: tuple[int, int] | None = None   # (W, H) ukuran kerja
        self.roi_px = self.roi_mask = self.zone = None
        self._prev_clean: np.ndarray | None = None
        self.counts = {"parkir": 0, "naik_trotoar": 0}
        self.events: list[Event] = []
        self.last: dict = {}
        self.header_fn = None          # opsional: fungsi(t) -> teks judul HUD (mode CCTV: nama kamera dan jam)

    # --------------------------------------------------------------------- setup
    def _setup(self, h: int, w: int) -> None:
        self.size = (w, h)
        self.roi_px = np.array([[x * w, y * h] for x, y in self.roi_norm], dtype=np.int32)
        self.roi_mask = np.zeros((h, w), np.uint8)
        cv2.fillPoly(self.roi_mask, [self.roi_px], 255)
        margin = max(3, int(self.cfg.zone_margin_frac * w))
        k = 2 * margin + 1
        self.zone = cv2.dilate(self.roi_mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))

    @property
    def ready(self) -> bool:
        return self.ref is not None

    def set_reference(self, frames: list[np.ndarray]) -> None:
        """Bangun model latar dari frame trotoar KOSONG (median)."""
        preps = [self.pre(f) for f in frames]
        h, w = preps[0].bgr.shape[:2]
        self._setup(h, w)
        self.ref = Reference.from_prepared(preps)
        self.tracker.reset()
        self._prev_clean = None
        self.last = {}

    # ------------------------------------------------------------------- proses
    def process(self, frame: np.ndarray, t: float, snapshots: bool = True) -> FrameResult:
        if self.ref is None:
            raise RuntimeError("referensi belum dibuat; panggil set_reference() dulu")
        cur = self.pre(frame)
        h, w = cur.bgr.shape[:2]
        if (w, h) != self.size:
            raise ValueError("ukuran frame berubah di tengah analisis")

        bg = (self.zone > 0) if self._prev_clean is None else ((self.zone > 0) & (self._prev_clean == 0))
        gain = match_gain(cur.L, self.ref.L, bg)                                   # M1
        cur.L = np.clip(cur.L * gain, 0, 255)
        cur.V = np.clip(cur.V * gain, 0, 255)

        seg = segment(self.ref, cur, self.zone, self.cfg)                          # M4 + M6
        clean = clean_mask(seg.raw, self.cfg)                                      # M3
        blobs = describe(clean, self.roi_mask, self.cfg)                           # M5
        vehicles = [b for b in blobs if b.label == "kendaraan"]
        tracks = self.tracker.update(vehicles, t, float(w))
        events = self.decider.step(tracks, t, float(w))

        views = [{"id": tr.id, "bbox": tr.blob.bbox, "state": Decider.state_of(tr, t),
                  "dwell": (t - tr.still_since) if tr.still_since is not None else 0.0,
                  "in_roi": tr.in_roi, "speed": tr.speed} for tr in tracks]
        for ev in events:
            self.counts[ev.kind] += 1
        self.ref.update(cur, clean, self.cfg.ref_update_rate)
        self._prev_clean = clean

        self.last = {"t": t, "bgr": cur.bgr, "L": cur.L, "diff8": seg.diff8, "raw": seg.raw,
                     "clean": clean, "blobs": blobs, "tracks": views, "threshold": seg.threshold,
                     "annotated": None}
        if events and snapshots:
            ok, jpg = cv2.imencode(".jpg", self.annotated(), [cv2.IMWRITE_JPEG_QUALITY, 88])
            for ev in events:
                ev.snapshot = jpg.tobytes() if ok else None
        self.events.extend(events)
        return FrameResult(t=t, tracks=views, events=events, threshold=seg.threshold)

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

    def stages(self) -> np.ndarray:
        """Montase 6 panel: keluaran tiap materi pada frame terakhir (untuk laporan)."""
        if not self.last:
            raise RuntimeError("belum ada frame yang diproses")
        L = self.last
        desc = (L["bgr"] * 0.45).astype(np.uint8)
        for b in L["blobs"]:
            color = {"kendaraan": render.CYAN, "pejalan": render.AMBER}.get(b.label, render.DIM)
            if b.contour is not None:
                cv2.drawContours(desc, [b.contour], -1, color, 1, cv2.LINE_AA)
            x, y, w, h = b.bbox
            cv2.rectangle(desc, (x, y), (x + w, y + h), color, 1)
            if b.label != "noise":
                render.put_text(desc, f"A={b.area:.0f} r={b.aspect:.2f}", (x, y - 4), color, 0.36)
        diff_vis = cv2.applyColorMap(cv2.convertScaleAbs(L["diff8"], alpha=255.0 / 90.0), cv2.COLORMAP_INFERNO)
        return render.montage([
            ("1 PRAPROSES  M1+M2+M6", np.clip(L["L"], 0, 255).astype(np.uint8)),
            ("2 SELISIH VS REFERENSI", diff_vis),
            (f"3 OTSU T={L['threshold']:.0f} + BAYANGAN  M4+M6", L["raw"]),
            ("4 MORFOLOGI  M3", L["clean"]),
            ("5 DESKRIPSI OBJEK  M5", desc),
            ("6 KEPUTUSAN", self.annotated()),
        ])

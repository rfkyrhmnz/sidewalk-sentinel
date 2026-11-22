"""Pipeline utama: gambar tunggal dan video."""
from __future__ import annotations

import csv
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .config import Config
from .decision import STATUS_CONFIRMED, VehicleResult, decide_frame
from .media import have_ffmpeg, to_h264
from .sidewalk import segment_sidewalk_classical, sidewalk_from_polygon
from .temporal import CameraMotion, DwellTracker, shift_mask
from .vehicles import Detections, union_masks
from .visualize import draw_frame

SIDEWALK_MODES = ("auto", "classical", "model", "polygon")

CSV_FIELDS = ["frame", "t_s", "track_id", "kelas", "conf", "contact_ratio", "ring_ratio", "overlap_ratio",
               "di_trotoar", "ada_pengendara", "status_frame", "status", "dwell_s", "x1", "y1", "x2", "y2"]


@dataclass
class FrameAnalysis:
    dets: Detections
    sidewalk: np.ndarray
    results: list
    exclude: np.ndarray
    info: dict


def result_row(r: VehicleResult, frame: int = 0, t: float = 0.0) -> dict:
    x1, y1, x2, y2 = r.obj.box
    nan = lambda v: "" if (isinstance(v, float) and math.isnan(v)) else round(v, 4)
    return {"frame": frame, "t_s": round(t, 3), "track_id": "" if r.track_id is None else r.track_id,
            "kelas": r.obj.label, "conf": round(r.obj.conf, 3), "contact_ratio": nan(r.contact_ratio),
            "ring_ratio": nan(r.ring_ratio), "overlap_ratio": nan(r.overlap_ratio),
            "di_trotoar": int(r.on_sidewalk), "ada_pengendara": "" if r.has_rider is None else int(r.has_rider),
            "status_frame": r.status_frame, "status": r.status, "dwell_s": round(r.dwell_s, 2),
            "x1": round(x1), "y1": round(y1), "x2": round(x2), "y2": round(y2)}


class Analyzer:
    """Menggabungkan segmenter kendaraan, sumber mask trotoar, geometri, dan logika temporal.

    ``sidewalk_mode``:
        auto      -> model trotoar bila tersedia dan menemukan trotoar, selain itu klasik
        classical -> segmentasi klasik berbasis biji
        model     -> wajib dari model (sidewalk_weights atau kelas 'sidewalk' pada model utama)
        polygon   -> poligon manual (``polygon`` = daftar poligon ternormalisasi)
    """

    def __init__(self, cfg: Config, segmenter, sidewalk_mode: str = "auto", polygon=None, sidewalk_model=None):
        if sidewalk_mode not in SIDEWALK_MODES:
            raise ValueError(f"sidewalk_mode harus salah satu dari {SIDEWALK_MODES}")
        if sidewalk_mode == "polygon" and not polygon:
            raise ValueError("Mode 'polygon' membutuhkan poligon (--polygon file.json)")
        self.cfg, self.seg, self.mode = cfg, segmenter, sidewalk_mode
        self.polygon, self.sw_model = polygon, sidewalk_model

    # ------------------------------------------------------------------ mask trotoar
    def compute_sidewalk(self, img, dets: Detections, exclude):
        if self.mode == "polygon":
            return sidewalk_from_polygon(img.shape, self.polygon), {}
        if self.mode in ("auto", "model"):
            m = None
            if self.sw_model is not None:
                m = self.sw_model.segment(img)
            elif dets.sidewalk is not None:
                m = dets.sidewalk
            if m is not None and (self.mode == "model" or m.any()):
                return m, {}
            if self.mode == "model":
                raise RuntimeError("Mode 'model' tetapi tidak ada model trotoar (--sidewalk-weights) "
                                   "atau kelas 'sidewalk' pada model utama.")
        return segment_sidewalk_classical(img, self.cfg, exclude)

    # ------------------------------------------------------------------ gambar
    def analyze_image(self, img, sidewalk_override: np.ndarray | None = None) -> FrameAnalysis:
        dets = self.seg.detect(img, track=False)
        exclude = union_masks(dets, img.shape)
        if sidewalk_override is not None:
            sw, info = sidewalk_override.astype(bool), {"oracle": True}
        else:
            sw, info = self.compute_sidewalk(img, dets, exclude)
        results = decide_frame(dets, sw, self.cfg, img.shape)
        return FrameAnalysis(dets, sw, results, exclude, info)

    # ------------------------------------------------------------------ video
    def run_video(self, src, out_video, csv_path=None, summary_path=None, h264: bool = True,
                  max_frames: int | None = None, verbose: bool = True) -> dict:
        cfg = self.cfg
        cap = cv2.VideoCapture(str(src))
        if not cap.isOpened():
            raise RuntimeError(f"Tidak bisa membuka video: {src}")
        fps = cap.get(cv2.CAP_PROP_FPS)
        if not fps or fps <= 1 or fps > 240:
            fps = 30.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        stride = max(1, int(cfg.video_stride))

        out_video = Path(out_video)
        out_video.parent.mkdir(parents=True, exist_ok=True)
        raw_path = out_video.with_suffix(".raw.mp4") if (h264 and have_ffmpeg()) else out_video
        writer = None

        self.seg.reset_tracking()
        tracker = DwellTracker(cfg)
        cam = CameraMotion() if cfg.compensate_camera else None
        sw_ref, sw_ref_cam = None, np.zeros(2)
        rows, events = [], {}
        idx, n_proc = -1, 0

        while True:
            ok, frame = cap.read()
            if not ok:
                break
            idx += 1
            if idx % stride:
                continue
            if max_frames and n_proc >= max_frames:
                break
            t = idx / fps
            dets = self.seg.detect(frame, track=True)
            exclude = union_masks(dets, frame.shape)
            cam_off = cam.update(frame, exclude) if cam is not None else np.zeros(2)

            refresh = sw_ref is None or (self.mode != "polygon" and n_proc % max(1, cfg.sidewalk_refresh) == 0)
            if refresh:
                sw_ref, _ = self.compute_sidewalk(frame, dets, exclude)
                sw_ref_cam = cam_off.copy()
            sidewalk = shift_mask(sw_ref, cam_off - sw_ref_cam)

            results = decide_frame(dets, sidewalk, cfg, frame.shape)
            tracker.update(t, results, cam_off)

            for r in results:
                rows.append(result_row(r, idx, t))
                if r.status == STATUS_CONFIRMED and r.track_id is not None:
                    ev = events.setdefault(r.track_id, {"track_id": r.track_id, "kelas": r.obj.label,
                                                        "mulai_s": round(t - r.dwell_s, 2), "terakhir_s": t,
                                                        "durasi_s": r.dwell_s})
                    ev["terakhir_s"], ev["durasi_s"] = round(t, 2), round(r.dwell_s, 2)

            vis = draw_frame(frame, sidewalk, results)
            if writer is None:
                hh, ww = vis.shape[:2]
                writer = cv2.VideoWriter(str(raw_path), cv2.VideoWriter_fourcc(*"mp4v"), fps / stride, (ww, hh))
                if not writer.isOpened():
                    raise RuntimeError("VideoWriter gagal dibuka (codec mp4v tidak tersedia?)")
            writer.write(vis)
            n_proc += 1
            if verbose and n_proc % 50 == 0:
                print(f"  frame {idx + 1}/{total or '?'}  parkir terkonfirmasi: {len(events)}", file=sys.stderr)

        cap.release()
        if writer is not None:
            writer.release()
        if raw_path != out_video and raw_path.exists():
            to_h264(raw_path, out_video)
            raw_path.unlink()

        if csv_path:
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
                w.writeheader()
                w.writerows(rows)
        summary = {"video": str(src), "fps": fps, "frame_diproses": n_proc, "stride": stride,
                   "parkir_terkonfirmasi": sorted(events.values(), key=lambda e: e["mulai_s"]),
                   "config": cfg.to_dict()}
        if summary_path:
            Path(summary_path).write_text(json.dumps(summary, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        return summary

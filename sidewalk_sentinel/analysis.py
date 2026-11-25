"""Menjalankan Engine pada berkas video dan menulis hasilnya ke satu folder."""
from __future__ import annotations

import csv
import json
import threading
import time
from pathlib import Path
from typing import Callable

import cv2

from . import video as vid
from .config import Config
from .engine import Engine


class Cancelled(Exception):
    pass


def run_analysis(
    video_path: str | Path,
    roi_norm: list[tuple[float, float]],
    cfg: Config | None = None,
    ref_time: float = 0.0,
    out_dir: str | Path | None = None,
    save_video: bool = True,
    progress_cb: Callable[[float, float, dict], None] | None = None,
    preview_cb: Callable[[bytes, bytes], None] | None = None,
    event_cb: Callable[[dict], None] | None = None,
    cancel: threading.Event | None = None,
) -> dict:
    cfg = cfg or Config()
    video_path = Path(video_path)
    info = vid.probe(video_path)
    out = Path(out_dir) if out_dir else None
    if out:
        (out / "events").mkdir(parents=True, exist_ok=True)

    step_fps = min(cfg.target_fps, info["fps"])
    step_s = 1.0 / step_fps
    engine = Engine(roi_norm, cfg)
    engine.set_reference(vid.read_frames_from(video_path, ref_time, cfg.ref_frames, step_s))

    writer = tmp_video = None
    started = time.time()
    last_preview = 0.0
    n_frames = 0
    events_out: list[dict] = []
    duration = info["duration"] or 0.0

    try:
        for t, frame in vid.iter_frames(video_path, step_fps):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            res = engine.process(frame, t)
            n_frames += 1

            for ev in res.events:
                d = ev.as_dict()
                d["no"] = len(events_out) + 1
                d["snapshot"] = None
                if out and ev.snapshot:
                    name = f"events/{d['no']}.jpg"
                    (out / name).write_bytes(ev.snapshot)
                    d["snapshot"] = name
                events_out.append(d)
                if event_cb:
                    event_cb(d)

            if save_video and out and vid.have_ffmpeg():
                if writer is None:
                    h, w = engine.annotated().shape[:2]
                    tmp_video = out / "_annotated_raw.mp4"
                    writer = cv2.VideoWriter(str(tmp_video), cv2.VideoWriter_fourcc(*"mp4v"), step_fps, (w, h))
                writer.write(engine.annotated())

            now = time.time()
            if progress_cb and (n_frames % 4 == 0):
                progress_cb(min(0.999, t / duration) if duration else 0.0, t, dict(engine.counts))
            if preview_cb and now - last_preview > 0.35:
                last_preview = now
                ok1, a = cv2.imencode(".jpg", engine.annotated(), [cv2.IMWRITE_JPEG_QUALITY, 72])
                ok2, b = cv2.imencode(".jpg", engine.stages(), [cv2.IMWRITE_JPEG_QUALITY, 72])
                if ok1 and ok2:
                    preview_cb(a.tobytes(), b.tobytes())
    finally:
        if writer is not None:
            writer.release()

    annotated_name = None
    if tmp_video is not None and out:
        try:
            vid.transcode_h264(tmp_video, out / "annotated.mp4")
            annotated_name = "annotated.mp4"
        except vid.VideoError:
            annotated_name = None
        finally:
            tmp_video.unlink(missing_ok=True)

    if preview_cb and n_frames:
        ok1, a = cv2.imencode(".jpg", engine.annotated(), [cv2.IMWRITE_JPEG_QUALITY, 80])
        ok2, b = cv2.imencode(".jpg", engine.stages(), [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok1 and ok2:
            preview_cb(a.tobytes(), b.tobytes())

    elapsed = time.time() - started
    result = {
        "video": info,
        "roi": [list(p) for p in roi_norm],
        "ref_time": ref_time,
        "config": cfg.to_dict(),
        "events": events_out,
        "summary": {
            "parkir": engine.counts["parkir"],
            "naik_trotoar": engine.counts["naik_trotoar"],
            "kendaraan_unik": len({e["track_id"] for e in events_out}),
        },
        "processing": {"frames": n_frames, "seconds": round(elapsed, 2),
                       "fps": round(n_frames / elapsed, 1) if elapsed > 0 else None},
        "annotated_video": annotated_name,
    }
    if out:
        (out / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        with open(out / "result.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["no", "kategori", "track_id", "waktu_konfirmasi_s", "waktu_mulai_s", "alasan", "bbox_xywh"])
            for e in events_out:
                w.writerow([e["no"], e["kind"], e["track_id"], e["t"], e["t_start"], e["reason"], e["bbox"]])
    return result


def parse_roi(text: str) -> list[tuple[float, float]]:
    """ROI dari JSON (``[[x,y],...]`` atau berkas JSON) atau ``"x,y;x,y;..."`` ternormalisasi 0..1."""
    p = Path(text)
    if p.exists():
        text = p.read_text(encoding="utf-8")
    text = text.strip()
    if text.startswith("["):
        data = json.loads(text)
        return [(float(x), float(y)) for x, y in data]
    pts = []
    for chunk in text.split(";"):
        x, y = chunk.split(",")
        pts.append((float(x), float(y)))
    return pts

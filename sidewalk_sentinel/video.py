"""Utilitas video: probe, ambil frame, iterasi frame, dan transcoding H.264 lewat ffmpeg."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np


class VideoError(Exception):
    """Berkas video tidak bisa dibuka atau dibaca."""


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def probe(path: str | Path) -> dict:
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            raise VideoError("video tidak bisa dibuka")
        ok, frame = cap.read()
        if not ok or frame is None:
            raise VideoError("video terbuka tetapi frame pertama tidak terbaca (kodek tidak didukung?)")
        fps = cap.get(cv2.CAP_PROP_FPS)
        if not fps or fps != fps or fps < 1 or fps > 240:
            fps = 30.0
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        h, w = frame.shape[:2]
        return {"width": int(w), "height": int(h), "fps": float(fps), "frames": n,
                "duration": round(n / fps, 2) if n > 0 else None}
    finally:
        cap.release()


def transcode_h264(src: str | Path, dst: str | Path) -> None:
    """Ubah ke H.264/yuv420p agar terbaca OpenCV dan bisa diputar di browser."""
    if not have_ffmpeg():
        raise VideoError("ffmpeg tidak tersedia untuk mengonversi video")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-an",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p",
           "-movflags", "+faststart", str(dst)]
    try:
        subprocess.run(cmd, check=True, timeout=1800, capture_output=True)
    except subprocess.CalledProcessError as e:
        raise VideoError("ffmpeg gagal: " + e.stderr.decode(errors="ignore")[-300:]) from e
    except subprocess.TimeoutExpired as e:
        raise VideoError("ffmpeg melewati batas waktu") from e


def ensure_readable(path: Path) -> tuple[Path, dict]:
    """Probe video; bila gagal dan ffmpeg ada, transcode ke H.264 lalu coba lagi."""
    try:
        return path, probe(path)
    except VideoError as first:
        if not have_ffmpeg():
            raise VideoError(f"{first}. Pasang ffmpeg atau ubah video ke MP4 (H.264).") from first
        fixed = path.with_name(path.stem + "_h264.mp4")
        transcode_h264(path, fixed)
        return fixed, probe(fixed)


def read_frame_at(path: str | Path, t: float) -> np.ndarray:
    cap = cv2.VideoCapture(str(path))
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(round(t * fps))))
        ok, frame = cap.read()
        if not ok:   # t melewati akhir video: ambil frame terakhir yang bisa dibaca
            n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 1)
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, n - 2))
            ok, frame = cap.read()
        if not ok:
            raise VideoError("frame tidak bisa dibaca")
        return frame
    finally:
        cap.release()


def read_frames_from(path: str | Path, t0: float, n: int, step_s: float) -> list[np.ndarray]:
    """Baca n frame mulai t0 dengan jarak step_s detik (untuk median referensi)."""
    cap = cv2.VideoCapture(str(path))
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        stride = max(1, int(round(step_s * fps)))
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(round(t0 * fps))))
        frames, i = [], 0
        while len(frames) < n:
            ok, frame = cap.read()
            if not ok:
                break
            if i % stride == 0:
                frames.append(frame)
            i += 1
        if not frames:
            raise VideoError("tidak ada frame untuk referensi pada waktu itu")
        return frames
    finally:
        cap.release()


def iter_frames(path: str | Path, target_fps: float):
    """Hasilkan (t, frame) pada ~target_fps; frame lain dilewati dengan grab() agar cepat."""
    cap = cv2.VideoCapture(str(path))
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        step = max(1, int(round(fps / target_fps)))
        i = 0
        while True:
            if i % step == 0:
                ok, frame = cap.read()
                if not ok:
                    break
                yield i / fps, frame
            else:
                if not cap.grab():
                    break
            i += 1
    finally:
        cap.release()

"""Penanganan media dari HP (Android/iPhone): HEIC, orientasi EXIF, video VFR, ekstraksi frame."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff", ".heic", ".heif"}
VID_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".3gp", ".webm"}


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def _register_heif() -> None:
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError as e:                                   # pragma: no cover
        raise RuntimeError("File HEIC/HEIF (iPhone) butuh paket 'pillow-heif': pip install pillow-heif") from e


def load_image(path) -> np.ndarray:
    """Baca gambar apa pun (termasuk HEIC) sebagai BGR uint8, dengan orientasi EXIF sudah diterapkan."""
    path = Path(path)
    if path.suffix.lower() in (".heic", ".heif"):
        _register_heif()
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        return np.asarray(im)[:, :, ::-1].copy()


def normalize_image(src, dst_dir, max_side: int | None = None, quality: int = 95) -> Path:
    """HEIC/JPG/PNG -> JPG tegak, tanpa metadata EXIF (termasuk GPS), opsional diperkecil."""
    src, dst_dir = Path(src), Path(dst_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)
    img = load_image(src)
    if max_side:
        h, w = img.shape[:2]
        s = max_side / max(h, w)
        if s < 1:
            img = cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
    dst = dst_dir / (src.stem + ".jpg")
    Image.fromarray(img[:, :, ::-1]).save(dst, "JPEG", quality=quality)     # tanpa exif
    return dst


def _run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg gagal:\n" + r.stderr[-1500:])


def normalize_video(src, dst, fps: float = 30.0, max_side: int | None = 1280, crf: int = 20) -> Path:
    """Video HP -> MP4 H.264, fps konstan, tanpa audio/metadata, rotasi sudah diterapkan."""
    if not have_ffmpeg():
        raise RuntimeError("ffmpeg tidak ditemukan. Pasang ffmpeg lalu ulangi.")
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    vf = []
    if max_side:
        m = int(max_side)
        vf.append(f"scale='if(gt(iw,ih),min(iw,{m}),-2)':'if(gt(iw,ih),-2,min(ih,{m}))'")
    vf.append(f"fps={fps}")
    _run(["ffmpeg", "-y", "-i", str(src), "-an", "-map_metadata", "-1", "-vf", ",".join(vf),
          "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-pix_fmt", "yuv420p", str(dst)])
    return dst


def to_h264(src, dst, crf: int = 22) -> Path:
    """Re-encode keluaran OpenCV (mp4v) ke H.264 agar bisa diputar di HP/browser."""
    _run(["ffmpeg", "-y", "-i", str(src), "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
          "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dst)])
    return Path(dst)


def normalize_dir(src_dir, out_dir, max_side_img: int | None = None, fps: float = 30.0,
                  max_side_vid: int | None = 1280) -> list[Path]:
    src_dir, out_dir = Path(src_dir), Path(out_dir)
    done: list[Path] = []
    for p in sorted(src_dir.rglob("*")):
        ext = p.suffix.lower()
        if ext in IMG_EXT:
            done.append(normalize_image(p, out_dir / "foto", max_side_img))
        elif ext in VID_EXT:
            done.append(normalize_video(p, out_dir / "video" / (p.stem + ".mp4"), fps, max_side_vid))
    return done


def extract_frames(video, out_dir, fps: float = 1.0, prefix: str | None = None, quality: int = 95) -> int:
    """Ambil frame dari video dengan laju ``fps`` (disarankan 1-2 fps untuk anotasi)."""
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"Tidak bisa membuka video: {video}")
    vfps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, int(round(vfps / fps)))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = prefix or Path(video).stem
    i = n = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if i % step == 0:
            cv2.imwrite(str(out_dir / f"{prefix}_{i:06d}.jpg"), frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
            n += 1
        i += 1
    cap.release()
    return n

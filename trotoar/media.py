"""Penanganan media dari HP (Android/iPhone): HEIC, orientasi EXIF, video VFR, ekstraksi frame."""
from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import time
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


# ----------------------------------------------------------------------------- kamera langsung (live)
LIVE_SCHEMES = ("rtsp://", "rtmp://", "http://", "https://", "udp://", "tcp://")


def parse_source(s):
    """'0' -> 0 (webcam indeks 0); selain itu dibiarkan (URL kamera IP atau path berkas video)."""
    s = str(s).strip()
    return int(s) if s.isdigit() else s


def is_live_source(source) -> bool:
    """Webcam (angka) dan URL jaringan dianggap aliran langsung; path berkas dibaca berurutan."""
    return isinstance(source, int) or str(source).lower().startswith(LIVE_SCHEMES)


def open_capture(source) -> "cv2.VideoCapture":
    """Buka kamera/aliran/berkas. Di Windows webcam dicoba lewat DirectShow dulu (jauh lebih cepat terbuka)."""
    if isinstance(source, int):
        if sys.platform.startswith("win"):
            cap = cv2.VideoCapture(source, cv2.CAP_DSHOW)
            if cap.isOpened():
                return cap
            cap.release()
        return cv2.VideoCapture(source)
    return cv2.VideoCapture(str(source))


class LatestFrameReader:
    """Membaca kamera di thread terpisah dan selalu menyerahkan frame TERBARU.

    Tanpa ini, bila pemrosesan (YOLO) lebih lambat daripada kamera, frame menumpuk di buffer dan tampilan
    makin tertinggal dari kenyataan (lag bisa berdetik-detik pada kamera IP). Frame yang tak sempat
    diproses dibuang. ``cap`` boleh objek apa pun yang punya ``read() -> (ok, frame)`` dan ``release()``.
    """

    def __init__(self, cap):
        self.cap = cap
        self._cond = threading.Condition()
        self._frame = None
        self._seq = 0
        self._read_seq = 0
        self._alive = True
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop:
            ok, frame = self.cap.read()
            with self._cond:
                if not ok:
                    self._alive = False
                    self._cond.notify_all()
                    return
                self._frame, self._seq = frame, self._seq + 1
                self._cond.notify_all()

    def read(self, timeout: float = 5.0):
        """(True, frame_terbaru) / (False, None) bila kamera berhenti atau tak ada frame baru dalam ``timeout`` detik."""
        end = time.monotonic() + timeout
        with self._cond:
            while self._seq == self._read_seq and self._alive:
                left = end - time.monotonic()
                if left <= 0:
                    return False, None
                self._cond.wait(left)
            if self._seq == self._read_seq:
                return False, None
            self._read_seq = self._seq
            return True, self._frame

    def stop(self) -> None:
        self._stop = True
        self._thread.join(timeout=2.0)
        try:
            self.cap.release()
        except Exception:                                      # pragma: no cover
            pass


def grab_snapshot(source, out_path, warmup: int = 15) -> Path:
    """Ambil satu gambar dari kamera/aliran/berkas (buang ``warmup`` frame pertama agar eksposur stabil)."""
    cap = open_capture(parse_source(source))
    if not cap.isOpened():
        raise RuntimeError(f"Tidak bisa membuka sumber: {source}")
    frame = None
    for _ in range(max(1, warmup)):
        ok, f = cap.read()
        if not ok:
            break
        frame = f
    cap.release()
    if frame is None:
        raise RuntimeError("Sumber terbuka tetapi tidak mengirim gambar.")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return out_path

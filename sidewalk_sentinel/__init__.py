"""Sidewalk Sentinel: deteksi kendaraan di trotoar dengan pengolahan citra digital klasik."""
import os as _os

# Harus disetel sebelum modul mana pun mengimpor cv2. Semuanya bisa ditimpa lewat variabel lingkungan
# (mis. OPENCV_FFMPEG_LOGLEVEL=32 untuk melihat log FFmpeg saat memecahkan masalah sambungan kamera).
_os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")                       # buang peringatan OpenCV yang berisik
_os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")                    # log FFmpeg bisa memuat alamat kamera
_os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")   # RTSP lewat TCP: tahan paket hilang

from .config import Config
from .engine import Engine

__version__ = "0.1.0"
__all__ = ["Config", "Engine", "__version__"]

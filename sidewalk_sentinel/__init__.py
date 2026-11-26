"""Sidewalk Sentinel: deteksi kendaraan di trotoar dengan pengolahan citra digital klasik."""
import os as _os

# Harus disetel sebelum modul mana pun mengimpor cv2: buang peringatan OpenCV yang berisik.
_os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")

from .config import Config
from .engine import Engine

__version__ = "0.1.0"
__all__ = ["Config", "Engine", "__version__"]

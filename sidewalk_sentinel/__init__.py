"""Sidewalk Sentinel: deteksi kendaraan di trotoar dengan pengolahan citra digital klasik."""
from .config import Config
from .engine import Engine

__version__ = "0.1.0"
__all__ = ["Config", "Engine", "__version__"]

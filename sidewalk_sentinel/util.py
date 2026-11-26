"""Pembantu kecil yang dipakai bersama: id, dan validasi area trotoar."""
from __future__ import annotations

import re
import uuid

ID_RE = re.compile(r"[0-9a-f]{12}")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def valid_id(value: str) -> bool:
    return bool(ID_RE.fullmatch(value or ""))


def check_roi(roi) -> list[tuple[float, float]]:
    """Area trotoar: 3..64 titik ternormalisasi 0..1 (x, y)."""
    if not isinstance(roi, list) or not 3 <= len(roi) <= 64:
        raise ValueError("area trotoar harus berisi 3 sampai 64 titik")
    pts = []
    for p in roi:
        if not (isinstance(p, (list, tuple)) and len(p) == 2):
            raise ValueError("setiap titik area harus berbentuk [x, y]")
        try:
            x, y = float(p[0]), float(p[1])
        except (TypeError, ValueError) as e:
            raise ValueError("koordinat area harus berupa angka") from e
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise ValueError("koordinat area harus ternormalisasi 0 sampai 1")
        pts.append((x, y))
    return pts

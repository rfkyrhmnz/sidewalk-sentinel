"""Logika keputusan per-frame: apakah suatu kendaraan berada di trotoar dan terindikasi parkir."""
from __future__ import annotations

import math
from dataclasses import dataclass

from .config import Config
from .geometry import vehicle_metrics
from .vehicles import Detections, Obj, union_masks

# Status per-frame (satu gambar)
STATUS_ROAD = "DI_JALAN"
STATUS_RIDDEN = "DI_TROTOAR_ADA_PENGENDARA"
STATUS_PARKED = "TERINDIKASI_PARKIR"
# Status tambahan pada video (diisi DwellTracker)
STATUS_MOVING = "DI_TROTOAR_BERGERAK"
STATUS_CANDIDATE = "KANDIDAT_PARKIR"
STATUS_CONFIRMED = "PARKIR_TERKONFIRMASI"

PARKING_STATUSES = (STATUS_PARKED, STATUS_CONFIRMED)


@dataclass
class VehicleResult:
    obj: Obj
    contact_ratio: float
    ring_ratio: float
    overlap_ratio: float
    score: float
    on_sidewalk: bool
    has_rider: bool | None
    status_frame: str
    status: str
    dwell_s: float = 0.0

    @property
    def track_id(self):
        return self.obj.track_id

    @property
    def is_parking_flag(self) -> bool:
        return self.status in PARKING_STATUSES


def has_rider(v: Obj, persons: list[Obj], cfg: Config) -> bool | None:
    """Heuristik: ada orang yang duduk di atas sepeda motor/sepeda? None = tidak dinilai (mobil/bus/truk).

    Syarat: sebagian besar kotak orang beririsan dengan kotak kendaraan, pusat horizontalnya di dalam
    lebar kendaraan, dan kepalanya berada di atas bagian atas kendaraan. Pejalan kaki yang kebetulan
    berdiri tepat di belakang motor bisa lolos (keterbatasan heuristik).
    """
    if v.cls_name not in cfg.ridable_names:
        return None
    vx1, vy1, vx2, vy2 = v.box
    vh = max(1.0, vy2 - vy1)
    for p in persons:
        px1, py1, px2, py2 = p.box
        pa = max(1e-6, (px2 - px1) * (py2 - py1))
        iw = max(0.0, min(px2, vx2) - max(px1, vx1))
        ih = max(0.0, min(py2, vy2) - max(py1, vy1))
        cx = 0.5 * (px1 + px2)
        if iw * ih / pa >= cfg.rider_overlap and vx1 <= cx <= vx2 and py1 < vy1 + cfg.rider_head_frac * vh:
            return True
    return False


def decide_frame(dets: Detections, sidewalk, cfg: Config, shape) -> list[VehicleResult]:
    """Klasifikasikan setiap kendaraan pada satu frame/gambar."""
    h, w = shape[:2]
    exclude = union_masks(dets, shape)
    results: list[VehicleResult] = []
    for v in dets.vehicles:
        if v.mask.sum() < cfg.min_vehicle_area_frac * h * w:
            continue
        m = vehicle_metrics(v.mask, sidewalk, exclude, cfg)
        ring = m["ring_ratio"]
        ring_ok = (not math.isnan(ring)) and ring >= cfg.tau_ring
        on = bool(m["contact_ratio"] >= cfg.tau_contact or ring_ok)
        score = max(m["contact_ratio"] if not math.isnan(m["contact_ratio"]) else 0.0,
                    ring if not math.isnan(ring) else 0.0)
        rider = has_rider(v, dets.persons, cfg)
        if not on:
            st = STATUS_ROAD
        elif rider:
            st = STATUS_RIDDEN
        else:
            st = STATUS_PARKED
        results.append(VehicleResult(v, m["contact_ratio"], ring, m["overlap_ratio"], float(score),
                                     on, rider, st, st))
    return results


def image_level_positive(results: list[VehicleResult]) -> bool:
    """Keputusan tingkat gambar: positif jika ada kendaraan terindikasi parkir di trotoar."""
    return any(r.status_frame == STATUS_PARKED for r in results)

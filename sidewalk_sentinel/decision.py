"""Aturan keputusan dua kategori.

Kategori 1  ``parkir``        kendaraan berada di ROI trotoar dan DIAM selama >= park_seconds.
Kategori 2  ``naik_trotoar``  kendaraan BERGERAK di ROI trotoar; paling meyakinkan bila
                              lintasannya tercatat dimulai dari luar ROI (naik dari jalan).

Kendaraan yang naik lalu berhenti dan parkir memicu kedua kategori (dua pelanggaran berbeda).
Tiap kategori hanya memicu satu kali per lintasan.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import Config
from .tracker import Track

KIND_LABEL = {"parkir": "PARKIR DI TROTOAR", "naik_trotoar": "NAIK TROTOAR"}


@dataclass
class Event:
    kind: str                       # 'parkir' | 'naik_trotoar'
    track_id: int
    t: float                        # waktu konfirmasi (detik)
    t_start: float                  # kapan kondisi mulai terpenuhi
    bbox: tuple[int, int, int, int] # pada resolusi kerja
    reason: str
    snapshot: bytes | None = None   # JPEG bukti (diisi engine)

    def as_dict(self) -> dict:
        return {"kind": self.kind, "label": KIND_LABEL[self.kind], "track_id": self.track_id,
                "t": round(self.t, 2), "t_start": round(self.t_start, 2),
                "bbox": list(self.bbox), "reason": self.reason}


class Decider:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def step(self, tracks: list[Track], t: float, width: float) -> list[Event]:
        cfg = self.cfg
        events: list[Event] = []
        for tr in tracks:
            tr.in_roi = tr.blob.contact >= cfg.contact_ratio_min
            tr.speed = tr.speed_norm(t, cfg.speed_window_s, width)

            if not tr.in_roi:
                tr.seen_outside = True
                tr.still_since = None
                tr.moving_since = None
                continue

            if not tr.seen_in_roi:
                tr.seen_in_roi = True
                tr.entered_from_outside = tr.seen_outside

            if tr.speed is not None:
                if tr.speed <= cfg.still_speed:
                    tr.moving_since = None
                    if tr.still_since is None:
                        tr.still_since = t
                elif tr.speed >= cfg.move_speed:
                    tr.still_since = None
                    if tr.moving_since is None:
                        tr.moving_since = t
                else:                      # zona abu-abu: bukan diam murni, bukan bergerak jelas
                    tr.still_since = None

            if (tr.still_since is not None and "parkir" not in tr.alerted
                    and t - tr.still_since >= cfg.park_seconds):
                tr.alerted.add("parkir")
                events.append(Event("parkir", tr.id, t, tr.still_since, tr.blob.bbox,
                                    f"diam di atas trotoar selama {t - tr.still_since:.1f} detik"))

            if tr.moving_since is not None and "naik_trotoar" not in tr.alerted:
                need = cfg.ride_min_seconds if tr.entered_from_outside else cfg.ride_standalone_seconds
                if t - tr.moving_since >= need:
                    tr.alerted.add("naik_trotoar")
                    why = ("melintas dari jalan ke trotoar sambil bergerak" if tr.entered_from_outside
                           else f"bergerak di atas trotoar selama {t - tr.moving_since:.1f} detik")
                    events.append(Event("naik_trotoar", tr.id, t, tr.moving_since, tr.blob.bbox, why))
        return events

    @staticmethod
    def state_of(tr: Track, t: float) -> str:
        """Status tampilan: parkir | naik_trotoar | diam | bergerak | luar."""
        if "parkir" in tr.alerted and tr.still_since is not None:
            return "parkir"
        if "naik_trotoar" in tr.alerted and tr.moving_since is not None:
            return "naik_trotoar"
        if "parkir" in tr.alerted:
            return "parkir"
        if "naik_trotoar" in tr.alerted:
            return "naik_trotoar"
        if not tr.in_roi:
            return "luar"
        if tr.still_since is not None:
            return "diam"
        if tr.moving_since is not None:
            return "bergerak"
        return "luar" if not tr.seen_in_roi else "bergerak"

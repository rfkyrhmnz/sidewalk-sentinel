"""Pelacak centroid sederhana: mencocokkan kotak kendaraan antar-frame dengan jarak terdekat (greedy).

Dari lintasan centroid dihitung kecepatan ternormalisasi (lebar frame per detik) pada jendela
waktu tertentu; kecepatan inilah yang membedakan kendaraan *diam* (parkir) dari *bergerak*.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import replace

from .config import Config
from .features import Blob


class Track:
    def __init__(self, tid: int, blob: Blob, t: float):
        self.id = tid
        self.blob = blob
        self.t_first = t
        self.t_last = t
        self.hist: deque[tuple[float, float, float]] = deque([(t, *blob.centroid)])
        self.matched = True
        # status keputusan (diisi decision.py)
        self.in_roi = False
        self.seen_outside = False
        self.seen_in_roi = False
        self.entered_from_outside = False
        self.still_since: float | None = None
        self.moving_since: float | None = None
        self.speed: float | None = None
        self.alerted: set[str] = set()

    def update(self, blob: Blob, t: float, window: float, smooth: float = 1.0) -> None:
        if smooth < 1.0:                    # redam getar kotak: kendaraan parkir tidak boleh tampak bergerak
            old = self.blob.bbox
            x, y, w, h = (smooth * n + (1.0 - smooth) * o for n, o in zip(blob.bbox, old))
            blob = replace(blob, bbox=(int(round(x)), int(round(y)), max(1, int(round(w))), max(1, int(round(h)))),
                           centroid=(x + w / 2.0, y + h / 2.0))
        self.blob = blob
        self.t_last = t
        self.matched = True
        self.hist.append((t, *blob.centroid))
        while len(self.hist) > 2 and t - self.hist[0][0] > window + 0.5:
            self.hist.popleft()

    def speed_norm(self, t: float, window: float, width: float) -> float | None:
        """Kecepatan centroid (lebar frame/detik); None bila rentang data terlalu pendek."""
        base = None
        for item in self.hist:
            if t - item[0] <= window:
                base = item
                break
        if base is None:
            return None
        span = t - base[0]
        if span < 0.4 * window:
            return None
        cx, cy = self.blob.centroid
        dist = math.hypot(cx - base[1], cy - base[2])
        return dist / span / width


class CentroidTracker:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.tracks: dict[int, Track] = {}
        self._next = 1

    def reset(self) -> None:
        self.tracks.clear()
        self._next = 1

    def update(self, blobs: list[Blob], t: float, width: float) -> list[Track]:
        cfg = self.cfg
        for tr in self.tracks.values():
            tr.matched = False

        pairs = []
        for tid, tr in self.tracks.items():
            dt = max(t - tr.t_last, 1e-3)
            # frame yang jarang (mis. kamera live 4 fps) membolehkan lompatan lebih jauh
            limit = cfg.track_max_dist_frac * width * min(3.0, max(1.0, dt / 0.12))
            for bi, blob in enumerate(blobs):
                d = math.hypot(blob.centroid[0] - tr.blob.centroid[0],
                               blob.centroid[1] - tr.blob.centroid[1])
                if d <= limit:
                    pairs.append((d, tid, bi))
        pairs.sort()

        used_t, used_b = set(), set()
        for d, tid, bi in pairs:
            if tid in used_t or bi in used_b:
                continue
            used_t.add(tid)
            used_b.add(bi)
            self.tracks[tid].update(blobs[bi], t, cfg.speed_window_s, cfg.smooth)

        for bi, blob in enumerate(blobs):
            if bi not in used_b:
                self.tracks[self._next] = Track(self._next, blob, t)
                self._next += 1

        for tid in [k for k, tr in self.tracks.items() if t - tr.t_last > cfg.track_max_missed_s]:
            del self.tracks[tid]

        return [tr for tr in self.tracks.values() if tr.matched]

"""Dimensi waktu untuk video: kompensasi gerak kamera (translasi) dan penghitung durasi diam (dwell time)."""
from __future__ import annotations

from collections import deque

import cv2
import numpy as np

from .config import Config
from .decision import (STATUS_CANDIDATE, STATUS_CONFIRMED, STATUS_MOVING, STATUS_PARKED, VehicleResult)


class CameraMotion:
    """Estimasi pergeseran latar (translasi saja) antar frame dengan optical flow Lucas-Kanade.

    Dipakai agar kendaraan yang diam tidak tampak "bergerak" ketika HP sedikit bergoyang/panning.
    Hanya translasi: rotasi, zoom, dan perubahan perspektif besar TIDAK dikompensasi.
    ``offset`` = pergeseran kumulatif (px) titik-titik statis terhadap frame pertama.
    """

    def __init__(self, max_side: int = 640, max_corners: int = 300):
        self.max_side = max_side
        self.max_corners = max_corners
        self.prev_gray = None
        self.prev_mask = None
        self.scale = 1.0
        self.offset = np.zeros(2, np.float64)
        self.last_shift = np.zeros(2, np.float64)

    def update(self, frame_bgr: np.ndarray, exclude: np.ndarray | None = None) -> np.ndarray:
        h, w = frame_bgr.shape[:2]
        s = min(1.0, self.max_side / max(h, w))
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        if s < 1.0:
            gray = cv2.resize(gray, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
        mask = np.full(gray.shape, 255, np.uint8)
        if exclude is not None and exclude.any():
            ex = cv2.resize(exclude.astype(np.uint8), (gray.shape[1], gray.shape[0]), interpolation=cv2.INTER_NEAREST)
            mask[cv2.dilate(ex, np.ones((9, 9), np.uint8)) > 0] = 0

        shift = np.zeros(2, np.float64)
        if self.prev_gray is not None and self.prev_gray.shape == gray.shape:
            p0 = cv2.goodFeaturesToTrack(self.prev_gray, self.max_corners, 0.01, 8, mask=self.prev_mask)
            if p0 is not None and len(p0) >= 8:
                p1, st, _ = cv2.calcOpticalFlowPyrLK(self.prev_gray, gray, p0, None, winSize=(21, 21), maxLevel=3)
                if p1 is not None:
                    ok = st.ravel() == 1
                    if ok.sum() >= 8:
                        flow = (p1[ok] - p0[ok]).reshape(-1, 2)
                        shift = np.median(flow, axis=0) / s
        self.last_shift = shift
        self.offset = self.offset + shift
        self.prev_gray, self.prev_mask = gray, mask
        return self.offset.copy()


def shift_mask(mask: np.ndarray, d) -> np.ndarray:
    """Geser mask sebesar d=(dx, dy) piksel (nearest neighbour)."""
    dx, dy = float(d[0]), float(d[1])
    if abs(dx) < 0.5 and abs(dy) < 0.5:
        return mask
    h, w = mask.shape
    M = np.float32([[1, 0, dx], [0, 1, dy]])
    out = cv2.warpAffine(mask.astype(np.uint8), M, (w, h), flags=cv2.INTER_NEAREST, borderValue=0)
    return out.astype(bool)


class _Track:
    def __init__(self, cfg: Config):
        self.votes = deque(maxlen=max(1, cfg.vote_window))
        self.hist = deque()           # (t, cx, cy, lebar)
        self.since = None
        self.last_t = 0.0


class DwellTracker:
    """Menghitung berapa lama sebuah kendaraan (track ID) diam di trotoar tanpa pengendara.

    Parkir terkonfirmasi jika, secara berkelanjutan: (1) mayoritas frame terakhir berstatus
    TERINDIKASI_PARKIR (voting menyaring kedipan segmentasi), dan (2) titik kontak kendaraan
    praktis tidak bergeser dalam jendela ``stationary_window_s`` (setelah kompensasi gerak kamera),
    selama >= ``min_dwell_s``. Kehilangan ID oleh tracker akan mereset hitungan (keterbatasan).
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.tracks: dict[int, _Track] = {}

    def update(self, t: float, results: list[VehicleResult], cam_offset=(0.0, 0.0)) -> None:
        cfg = self.cfg
        cam = np.asarray(cam_offset, np.float64)
        for r in results:
            tid = r.track_id
            if tid is None:
                continue
            tr = self.tracks.setdefault(tid, _Track(cfg))
            tr.last_t = t
            tr.votes.append(r.status_frame == STATUS_PARKED)
            x1, y1, x2, y2 = r.obj.box
            tr.hist.append((t, 0.5 * (x1 + x2) - cam[0], y2 - cam[1], max(1.0, x2 - x1)))
            while tr.hist and t - tr.hist[0][0] > cfg.stationary_window_s:
                tr.hist.popleft()

            voted = len(tr.votes) >= min(3, tr.votes.maxlen) and np.mean(tr.votes) >= cfg.vote_ratio
            span = tr.hist[-1][0] - tr.hist[0][0]
            pts = np.array([(c[1], c[2]) for c in tr.hist])
            # rentang total gerak dalam jendela (peak-to-peak). Simpangan dari median akan menganggap
            # gerak lambat 2x lebih kecil dari sebenarnya, sehingga kendaraan merayap lolos sebagai "diam".
            move = float(np.hypot(np.ptp(pts[:, 0]), np.ptp(pts[:, 1])))
            width = float(np.mean([c[3] for c in tr.hist]))
            known = span >= 0.5 * cfg.stationary_window_s          # riwayat cukup untuk menilai gerak
            stationary = known and move <= cfg.max_move_frac * width

            if voted and stationary:
                if tr.since is None:
                    tr.since = tr.hist[0][0]
                r.dwell_s = max(0.0, t - tr.since)
                r.status = STATUS_CONFIRMED if r.dwell_s >= cfg.min_dwell_s else STATUS_CANDIDATE
            else:
                tr.since = None
                if r.status_frame == STATUS_PARKED:
                    # di trotoar tanpa pengendara, tetapi belum terbukti diam
                    r.status = STATUS_MOVING if (known and not stationary) else STATUS_CANDIDATE
                # selain itu status tetap status_frame (DI_JALAN / DI_TROTOAR_ADA_PENGENDARA)
        # buang track yang sudah lama tak terlihat
        for tid in [k for k, v in self.tracks.items() if t - v.last_t > 5.0]:
            del self.tracks[tid]

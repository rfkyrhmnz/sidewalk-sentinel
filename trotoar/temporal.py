"""Dimensi waktu untuk video: kompensasi gerak kamera (translasi) dan penghitung durasi diam (dwell time)."""
from __future__ import annotations

from collections import deque

import cv2
import numpy as np

from .config import Config
from .decision import (STATUS_CANDIDATE, STATUS_CONFIRMED, STATUS_MOVING, STATUS_PARKED, VehicleResult)


class CameraMotion:
    """Estimasi gerak kamera antar frame dengan optical flow Lucas-Kanade.

    Dua keluaran:
    * ``offset`` : pergeseran kumulatif (px) titik statis terhadap frame pertama (translasi saja; dipakai
      untuk menggeser mask trotoar antar penyegaran).
    * ``H`` : homografi kumulatif 3x3 yang memetakan koordinat frame sekarang ke frame pertama (pose kamera).
      Dihitung dengan RANSAC dari titik-titik fitur, diutamakan yang jatuh pada ``region`` (mask trotoar =
      bidang tanah), jadi zoom, rotasi dan perubahan perspektif akibat HP digerakkan (berjalan mendekat/menjauh)
      ikut terkompensasi untuk titik yang berada di tanah, termasuk titik kontak roda kendaraan.
      Bila homografi tidak layak (fitur sedikit, tidak konsisten, hasil ekstrem) dipakai translasi saja untuk
      langkah itu. ``mode`` mencatat metode langkah terakhir (``"homografi"`` atau ``"translasi"``).

    Keterbatasan: homografi hanya benar untuk titik di bidang tanah. Bila trotoar tidak dikenali dan fitur
    didominasi gedung/pohon jauh, hasilnya mendekati kompensasi rotasi saja, dan kendaraan diam yang dekat
    dengan kamera tetap bisa tampak bergerak (aman: tidak menghasilkan parkir palsu).
    """

    MIN_REGION_PAIRS = 20

    def __init__(self, max_side: int = 640, max_corners: int = 300, homography: bool = True):
        self.max_side = max_side
        self.max_corners = max_corners
        self.use_homography = homography
        self.prev_gray = None
        self.prev_mask = None
        self.prev_region = None
        self.scale = 1.0
        self.offset = np.zeros(2, np.float64)
        self.last_shift = np.zeros(2, np.float64)
        self.H = np.eye(3)
        self.mode = "translasi"

    # -- pasangan titik fitur (frame sebelumnya -> sekarang), koordinat gambar yang sudah diperkecil
    def _pairs(self, gray, mask):
        p0 = cv2.goodFeaturesToTrack(self.prev_gray, self.max_corners, 0.01, 8, mask=mask)
        if p0 is None or len(p0) < 8:
            return None, None
        p1, st, _ = cv2.calcOpticalFlowPyrLK(self.prev_gray, gray, p0, None, winSize=(21, 21), maxLevel=3)
        if p1 is None:
            return None, None
        ok = st.ravel() == 1
        if ok.sum() < 8:
            return None, None
        return p0[ok].reshape(-1, 2), p1[ok].reshape(-1, 2)

    @staticmethod
    def _sane(Hs: np.ndarray, shape) -> bool:
        """Tolak homografi ekstrem: titik sudut tidak boleh meloncat jauh atau terlipat dalam satu langkah."""
        h, w = shape[:2]
        if not np.all(np.isfinite(Hs)):
            return False
        c = np.float64([[0, 0, 1], [w, 0, 1], [w, h, 1], [0, h, 1]]).T
        m = Hs @ c
        if np.any(m[2] <= 1e-6):
            return False
        q = (m[:2] / m[2]).T
        if np.max(np.abs(q - c[:2].T)) > 0.35 * max(h, w):
            return False
        x, y = q[:, 0], q[:, 1]
        area = 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))
        return 0.5 <= area / (w * h) <= 2.0

    def update(self, frame_bgr: np.ndarray, exclude: np.ndarray | None = None,
               region: np.ndarray | None = None) -> np.ndarray:
        h, w = frame_bgr.shape[:2]
        s = min(1.0, self.max_side / max(h, w))
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        if s < 1.0:
            gray = cv2.resize(gray, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
        mask = np.full(gray.shape, 255, np.uint8)
        if exclude is not None and exclude.any():
            ex = cv2.resize(exclude.astype(np.uint8), (gray.shape[1], gray.shape[0]), interpolation=cv2.INTER_NEAREST)
            mask[cv2.dilate(ex, np.ones((9, 9), np.uint8)) > 0] = 0
        rmask = None
        if region is not None and region.any():
            rg = cv2.resize(region.astype(np.uint8), (gray.shape[1], gray.shape[0]), interpolation=cv2.INTER_NEAREST)
            rmask = np.where((rg > 0) & (mask > 0), 255, 0).astype(np.uint8)
            if rmask.mean() < 0.02 * 255:
                rmask = None

        shift = np.zeros(2, np.float64)
        A = np.eye(3)
        mode = "translasi"
        if self.prev_gray is not None and self.prev_gray.shape == gray.shape:
            q0 = q1 = None
            if self.use_homography and self.prev_region is not None:
                q0, q1 = self._pairs(gray, self.prev_region)
                if q0 is not None and len(q0) < self.MIN_REGION_PAIRS:
                    q0 = q1 = None
            if q0 is None:
                q0, q1 = self._pairs(gray, self.prev_mask)
            if q0 is not None:
                shift = np.median(q1 - q0, axis=0) / s
                A = np.array([[1, 0, -shift[0]], [0, 1, -shift[1]], [0, 0, 1]], np.float64)   # sekarang -> sebelumnya
                if self.use_homography and len(q0) >= 12:
                    Hs, inl = cv2.findHomography(q1, q0, cv2.RANSAC, 2.0)
                    if Hs is not None and inl is not None and inl.sum() >= 10 and inl.mean() >= 0.35 and self._sane(Hs, gray.shape):
                        S = np.diag([s, s, 1.0])
                        A = np.linalg.inv(S) @ Hs @ S
                        mode = "homografi"
        self.last_shift = shift
        self.offset = self.offset + shift
        M = self.H @ A
        if np.all(np.isfinite(M)) and abs(M[2, 2]) > 1e-9:
            self.H = M / M[2, 2]
        self.mode = mode
        self.prev_gray, self.prev_mask, self.prev_region = gray, mask, rmask
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


def _window_in_current_frame(raw, H_now: np.ndarray):
    """Petakan dua sudut bawah tiap kotak di jendela ke koordinat frame sekarang lewat inv(H_sekarang) @ H_lampau.
    Mengembalikan daftar (t, cx, y, lebar), atau None bila ada pemetaan yang tidak valid (penyebut <= 0, matriks singular)."""
    try:
        Hinv = np.linalg.inv(H_now)
    except np.linalg.LinAlgError:
        return None
    out = []
    for ti, a1, a2, b2, Hi in raw:
        pts = (Hinv @ Hi) @ np.array([[a1, a2], [b2, b2], [1.0, 1.0]])
        if not np.all(np.isfinite(pts)) or np.any(pts[2] <= 1e-6):
            return None
        xs, ys = pts[0] / pts[2], pts[1] / pts[2]
        out.append((ti, float(xs.mean()), float(ys.mean()), max(1.0, float(abs(xs[1] - xs[0])))))
    return out


class _Track:
    def __init__(self, cfg: Config):
        self.votes = deque(maxlen=max(1, cfg.vote_window))
        self.hist = deque()           # (t, cx, cy, lebar), dalam koordinat frame sekarang (atau dikurangi offset)
        self.raw = deque()            # (t, x1, x2, y2, H) saat kompensasi homografi dipakai
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
        """``cam_offset``: pergeseran kumulatif (dx, dy) ATAU homografi kumulatif 3x3 (``CameraMotion.H``).
        Dengan homografi, titik kontak dan lebar kendaraan dalam jendela diam dipetakan ke koordinat frame sekarang,
        sehingga zoom dan perspektif akibat kamera bergerak tidak dihitung sebagai gerak kendaraan."""
        cfg = self.cfg
        arr = np.asarray(cam_offset, np.float64)
        Hm = arr if arr.shape == (3, 3) else None
        cam = np.zeros(2) if Hm is not None else arr
        for r in results:
            tid = r.track_id
            if tid is None:
                continue
            tr = self.tracks.setdefault(tid, _Track(cfg))
            tr.last_t = t
            tr.votes.append(r.status_frame == STATUS_PARKED)
            x1, y1, x2, y2 = r.obj.box
            if Hm is not None:
                # Simpan kotak mentah + pose kamera, lalu petakan seluruh jendela ke koordinat frame SEKARANG.
                # Hanya transformasi relatif dalam jendela (beberapa detik) yang dipakai, jadi tidak bergantung
                # pada jarak kamera dari frame pertama dan galat kumulatif tidak ikut terbawa.
                tr.raw.append((t, x1, x2, y2, Hm))
                while tr.raw and t - tr.raw[0][0] > cfg.stationary_window_s:
                    tr.raw.popleft()
                hist = _window_in_current_frame(tr.raw, Hm)
                if hist is None:                       # pemetaan tidak valid: anggap belum terbukti diam
                    tr.raw.clear()
                    tr.hist.clear()
                    tr.since = None
                    if r.status_frame == STATUS_PARKED:
                        r.status = STATUS_CANDIDATE
                    continue
                tr.hist = deque(hist)
            else:
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

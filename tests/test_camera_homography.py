"""Kompensasi gerak kamera berbasis homografi: kamera berjalan mendekat (zoom + perspektif) tidak boleh membuat
mobil yang diam tampak bergerak, sedangkan mobil yang benar-benar bergerak tetap harus terdeteksi bergerak.

Adegan sintetis: bidang tanah bertekstur, "mobil" = persegi panjang di tanah, kamera = homografi tanah->gambar
yang berubah tiap frame (area pandang menyempit dan maju, zoom sampai ~2,5x di tepi bawah)."""
import cv2
import numpy as np

from trotoar.config import Config
from trotoar.decision import STATUS_CANDIDATE, STATUS_CONFIRMED, STATUS_MOVING, STATUS_PARKED, VehicleResult
from trotoar.temporal import CameraMotion, DwellTracker
from trotoar.vehicles import Obj

W, H = 640, 480
FPS = 15
IMG_QUAD = np.float32([[120, 150], [520, 150], [639, 479], [0, 479]])      # TL, TR, BR, BL


def _ground() -> np.ndarray:
    rng = np.random.RandomState(3)
    g = np.full((1400, 1600), 128, np.uint8)
    for _ in range(4500):
        x, y, s = rng.randint(0, 1580), rng.randint(0, 1380), rng.randint(6, 22)
        g[y:y + s, x:x + s] = rng.randint(30, 230)
    return cv2.cvtColor(cv2.GaussianBlur(g, (3, 3), 0), cv2.COLOR_GRAY2BGR)


def _cam_h(tau: float) -> np.ndarray:
    """Homografi tanah -> gambar pada waktu relatif tau (0..1): area pandang maju dan menyempit (zoom sampai ~2,5x di tepi bawah)."""
    cx, y_top, y_bot = 800, 450 - 200 * tau, 1150 - 400 * tau
    hw_top, hw_bot = 300 - 150 * tau, 500 - 300 * tau
    quad = np.float32([[cx - hw_top, y_top], [cx + hw_top, y_top], [cx + hw_bot, y_bot], [cx - hw_bot, y_bot]])
    return cv2.getPerspectiveTransform(quad, IMG_QUAD)


def _scene(duration_s: float, car_speed: float):
    """Hasilkan (t, frame, mask_mobil, kotak, H_gt) per frame. car_speed = px tanah/detik searah sumbu x."""
    ground, car_tex = _ground(), np.random.RandomState(9).randint(0, 255, (100, 200, 3)).astype(np.uint8)
    for k in range(int(duration_s * FPS)):
        t = k / FPS
        Hg = _cam_h(t / duration_s)
        g = ground.copy()
        x0 = 640 + car_speed * t
        g[600:700, int(x0):int(x0) + 200] = car_tex
        frame = cv2.warpPerspective(g, Hg, (W, H))
        foot = np.float32([[x0, 600], [x0 + 200, 600], [x0 + 200, 700], [x0, 700]]).reshape(-1, 1, 2)
        pts = cv2.perspectiveTransform(foot, Hg).reshape(-1, 2)
        x1, x2, y2 = pts[:, 0].min(), pts[:, 0].max(), pts[:, 1].max()
        box = (float(x1), float(y2 - 0.6 * (x2 - x1)), float(x2), float(y2))
        mask = np.zeros((H, W), bool)
        mask[int(box[1]):int(box[3]), max(0, int(box[0])):int(box[2])] = True
        yield t, frame, mask, box, Hg


def _run(car_speed: float, homography: bool, duration_s: float = 4.0):
    """Mengembalikan (status per frame, mode kamera per frame, gerak terbesar dibanding lebar kendaraan)."""
    cfg = Config()
    cfg.min_dwell_s = 3.0                       # >= jendela diam (3 s); nilai bawaan produksi 10 s
    cam, trk = CameraMotion(homography=homography), DwellTracker(cfg)
    statuses, modes, worst = [], [], 0.0
    for t, frame, mask, box, _ in _scene(duration_s, car_speed):
        off = cam.update(frame, mask)
        r = VehicleResult(Obj("vehicle", "car", "mobil", 0.9, box, np.zeros((1, 1), bool), 1),
                          0, 0, 0, 1, True, False, STATUS_PARKED, STATUS_PARKED)
        trk.update(t, [r], cam.H if homography else off)
        statuses.append(r.status)
        modes.append(cam.mode)
        hist = trk.tracks[1].hist
        if hist and hist[-1][0] - hist[0][0] >= 0.5 * cfg.stationary_window_s:
            pts = np.array([(c[1], c[2]) for c in hist])
            worst = max(worst, float(np.hypot(np.ptp(pts[:, 0]), np.ptp(pts[:, 1])) / np.mean([c[3] for c in hist])))
    return statuses, modes, worst


def test_homography_maps_ground_points_back_to_reference_frame():
    cam = CameraMotion()
    p_ground = np.float32([[[800, 650]]])
    ref = cv2.perspectiveTransform(p_ground, _cam_h(0.0)).reshape(2)
    errs = []
    for t, frame, mask, box, Hg in _scene(4.0, 0.0):
        cam.update(frame, mask)
        now = cv2.perspectiveTransform(p_ground, Hg).reshape(2)
        mapped = cam.H @ np.array([now[0], now[1], 1.0])
        errs.append(float(np.hypot(*(mapped[:2] / mapped[2] - ref))))
    assert max(errs) < 8.0, f"galat pemetaan terlalu besar: {max(errs):.1f}px"
    assert cam.mode == "homografi"


def test_static_car_with_walking_camera_is_confirmed_with_homography():
    statuses, modes, worst = _run(car_speed=0.0, homography=True)
    assert statuses[-1] == STATUS_CONFIRMED, statuses[-8:]
    assert worst < 0.1
    assert modes.count("homografi") >= 0.9 * len(modes)


def test_homography_cuts_apparent_motion_versus_translation_only():
    """Pembanding dengan perilaku lama (translasi saja) pada adegan yang sama: gerak semu turun lebih dari 4x."""
    _, _, trans = _run(car_speed=0.0, homography=False)
    _, _, hom = _run(car_speed=0.0, homography=True)
    assert trans > 0.25, f"adegan terlalu lembut untuk membedakan metode: {trans:.2f}"
    assert hom < 0.25 * trans, f"translasi {trans:.2f} vs homografi {hom:.2f}"


def test_moving_car_is_still_moving_with_homography():
    statuses, _, worst = _run(car_speed=35.0, homography=True)
    assert STATUS_CONFIRMED not in statuses
    assert statuses[-1] == STATUS_MOVING
    assert worst > 0.35


def test_dwell_tracker_identity_matrix_equals_zero_offset():
    cfg = Config()
    cfg.min_dwell_s = 2.0
    a, b = DwellTracker(cfg), DwellTracker(cfg)
    box = (100.0, 100.0, 300.0, 250.0)
    for k in range(60):
        t = k / FPS
        ra = VehicleResult(Obj("vehicle", "car", "mobil", 0.9, box, np.zeros((1, 1), bool), 1), 0, 0, 0, 1, True, False,
                           STATUS_PARKED, STATUS_PARKED)
        rb = VehicleResult(Obj("vehicle", "car", "mobil", 0.9, box, np.zeros((1, 1), bool), 1), 0, 0, 0, 1, True, False,
                           STATUS_PARKED, STATUS_PARKED)
        a.update(t, [ra], np.eye(3))
        b.update(t, [rb], (0.0, 0.0))
        assert ra.status == rb.status and abs(ra.dwell_s - rb.dwell_s) < 1e-9
    assert ra.status == STATUS_CONFIRMED


def _result(box):
    return VehicleResult(Obj("vehicle", "car", "mobil", 0.9, box, np.zeros((1, 1), bool), 1), 0, 0, 0, 1, True, False,
                         STATUS_PARKED, STATUS_PARKED)


def test_invalid_homography_fails_safe_to_candidate():
    """Pemetaan jendela tak valid (penyebut <= 0) tidak boleh menghasilkan parkir terkonfirmasi dan mereset riwayat."""
    cfg = Config()
    trk = DwellTracker(cfg)
    r = _result((10.0, 10.0, 90.0, 300.0))
    trk.update(0.0, [r], np.eye(3))
    flip = np.array([[1.0, 0, 0], [0, 1.0, 0], [0, 0.01, 1.0]])     # kotak lama jatuh di belakang kamera pada pose baru
    r = _result((10.0, 10.0, 90.0, 300.0))
    trk.update(0.1, [r], flip)
    assert r.status == STATUS_CANDIDATE
    assert not trk.tracks[1].hist and not trk.tracks[1].raw and trk.tracks[1].since is None


def test_singular_homography_fails_safe():
    trk = DwellTracker(Config())
    r = _result((10.0, 10.0, 90.0, 60.0))
    trk.update(0.0, [r], np.zeros((3, 3)))
    assert r.status == STATUS_CANDIDATE

"""Tes DwellTracker, kompensasi gerak kamera, dan pergeseran mask."""
import numpy as np

from synthetic import make_scene
from trotoar.config import Config
from trotoar.decision import (STATUS_CANDIDATE, STATUS_CONFIRMED, STATUS_MOVING, STATUS_PARKED, STATUS_RIDDEN,
                              STATUS_ROAD, VehicleResult)
from trotoar.temporal import CameraMotion, DwellTracker, shift_mask
from trotoar.vehicles import Obj, union_masks

FPS = 5.0


def mk(box, status, tid=1):
    o = Obj("vehicle", "car", "mobil", 0.9, tuple(box), np.zeros((4, 4), bool), tid)
    return VehicleResult(o, 0.0, 1.0, 0.0, 1.0, status != STATUS_ROAD, None, status, status)


def run(boxes_fn, status_fn, seconds=14, cam_fn=None, cfg=None):
    cfg = cfg or Config()
    tr = DwellTracker(cfg)
    out = []
    for n in range(int(seconds * FPS)):
        t = n / FPS
        r = mk(boxes_fn(n), status_fn(n))
        tr.update(t, [r], cam_fn(n) if cam_fn else (0, 0))
        out.append((t, r.status, r.dwell_s))
    return out


def first(out, status):
    return next((t for t, s, _ in out if s == status), None)


def test_parked_vehicle_gets_confirmed_after_min_dwell():
    out = run(lambda n: (100, 100, 200, 200), lambda n: STATUS_PARKED)
    t = first(out, STATUS_CONFIRMED)
    assert t is not None and 9.5 <= t <= 12.5
    assert first(out, STATUS_CANDIDATE) < t                 # sempat berstatus kandidat dulu
    assert out[-1][2] >= 12                                  # dwell terus bertambah


def test_single_frame_flicker_is_absorbed_by_voting():
    out = run(lambda n: (100, 100, 200, 200), lambda n: STATUS_ROAD if n % 7 == 6 else STATUS_PARKED)
    assert first(out, STATUS_CONFIRMED) is not None


def test_moving_vehicle_on_sidewalk_is_never_confirmed():
    out = run(lambda n: (100 + 8 * n, 100, 200 + 8 * n, 200), lambda n: STATUS_PARKED)
    assert first(out, STATUS_CONFIRMED) is None
    assert first(out, STATUS_MOVING) is not None


def test_ridden_vehicle_never_confirmed():
    out = run(lambda n: (100, 100, 200, 200), lambda n: STATUS_RIDDEN)
    assert all(s == STATUS_RIDDEN for _, s, _ in out)


def test_vehicle_on_road_never_flagged():
    out = run(lambda n: (100, 100, 200, 200), lambda n: STATUS_ROAD)
    assert all(s == STATUS_ROAD for _, s, _ in out)


def test_camera_drift_compensation_matters():
    """Kendaraan diam di dunia nyata, kamera menggeser 3 px/frame. Tanpa kompensasi tampak bergerak."""
    off = lambda n: (3.0 * n, 0.0)
    boxes = lambda n: (100 + 3 * n, 100, 200 + 3 * n, 200)          # posisi di gambar ikut bergeser
    with_comp = run(boxes, lambda n: STATUS_PARKED, cam_fn=off)
    without = run(boxes, lambda n: STATUS_PARKED, cam_fn=None)
    assert first(with_comp, STATUS_CONFIRMED) is not None
    assert first(without, STATUS_CONFIRMED) is None


def test_dwell_resets_after_vehicle_moves():
    def box(n):                                              # diam 12 s, lalu pergi
        k = max(0, n - 60)
        return (100 + 15 * k, 100, 200 + 15 * k, 200)
    out = run(box, lambda n: STATUS_PARKED, seconds=18)
    assert first(out, STATUS_CONFIRMED) is not None
    assert out[-1][1] == STATUS_MOVING and out[-1][2] == 0.0


def test_tracks_without_id_are_ignored_and_stale_tracks_pruned():
    cfg = Config()
    tr = DwellTracker(cfg)
    r = mk((0, 0, 10, 10), STATUS_PARKED, tid=None)
    tr.update(0.0, [r])
    assert r.status == STATUS_PARKED and not tr.tracks
    tr.update(1.0, [mk((0, 0, 10, 10), STATUS_PARKED, tid=7)])
    assert 7 in tr.tracks
    tr.update(20.0, [])
    assert 7 not in tr.tracks


def test_camera_motion_estimates_known_shifts():
    veh = [dict(kind="car", box=(200, 350, 400, 440), track_id=1)]
    rng = np.random.default_rng(3)
    s = np.zeros(2)
    cm = CameraMotion()
    errs = []
    for k in range(25):
        if k:
            s = np.clip(s + rng.integers(-3, 4, 2), -25, 25)
        img, gt, dets = make_scene("across", veh, shift=tuple(s), frame_noise=2.0, frame_seed=k)
        off = cm.update(img, union_masks(dets, img.shape))
        errs.append(np.abs(off - s).max())
    assert max(errs) < 2.0, f"galat estimasi gerak kamera maks {max(errs):.2f}px"


def test_shift_mask_translates():
    m = np.zeros((50, 50), bool)
    m[10:20, 10:20] = True
    sm = shift_mask(m, (5, -3))
    ys, xs = np.nonzero(sm)
    assert (xs.min(), xs.max(), ys.min(), ys.max()) == (15, 24, 7, 16)
    assert shift_mask(m, (0.2, 0.1)) is m

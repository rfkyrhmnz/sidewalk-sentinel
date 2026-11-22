"""Tes segmentasi trotoar klasik, geometri kontak, dan keputusan per-frame (data sintetis)."""
import math

import numpy as np
import pytest

from synthetic import FakeSegmenter, make_scene
from trotoar.config import Config
from trotoar.decision import (STATUS_PARKED, STATUS_RIDDEN, STATUS_ROAD, decide_frame, has_rider)
from trotoar.metrics import iou_dice
from trotoar.pipeline import Analyzer
from trotoar.sidewalk import segment_sidewalk_classical, sidewalk_from_polygon
from trotoar.vehicles import Detections, Obj, union_masks

ALONG_SEED = (0.02, 0.80, 0.20, 0.98)


def analyze(kind, vehicles, seed=None, **cfg_kw):
    img, gt, dets = make_scene(kind, vehicles)
    cfg = Config(**cfg_kw)
    if seed:
        cfg.seed_box = seed
    an = Analyzer(cfg, FakeSegmenter(lambda n, i: dets), "classical")
    return an.analyze_image(img), gt, img, dets, cfg


def by_id(fa, tid):
    return next(r for r in fa.results if r.track_id == tid)


# ------------------------------------------------------------------ segmentasi klasik
@pytest.mark.parametrize("kind,seed", [("across", None), ("along", ALONG_SEED)])
def test_classical_sidewalk_iou(kind, seed):
    veh = [dict(kind="car", box=(200, 350, 400, 440), track_id=1)] if kind == "across" else \
          [dict(kind="car", box=(30, 330, 170, 420), track_id=1)]
    fa, gt, img, dets, cfg = analyze(kind, veh, seed)
    iou, dice = iou_dice(fa.sidewalk, gt & ~fa.exclude)
    assert iou >= 0.90, f"IoU terlalu rendah: {iou:.3f}"


def test_seed_covered_by_vehicle_does_not_crash():
    big = [dict(kind="car", box=(150, 380, 500, 478), track_id=1)]       # menutupi seluruh kotak biji
    img, gt, dets = make_scene("across", big)
    cfg = Config()
    mask, info = segment_sidewalk_classical(img, cfg, union_masks(dets, img.shape))
    assert mask.shape == gt.shape
    assert "warning" in info or mask.any()


def test_grabcut_refine_runs_and_keeps_quality():
    img, gt, dets = make_scene("across", [dict(kind="car", box=(200, 350, 400, 440), track_id=1)])
    cfg = Config(refine="grabcut")
    excl = union_masks(dets, img.shape)
    mask, _ = segment_sidewalk_classical(img, cfg, excl)
    assert iou_dice(mask, gt & ~excl)[0] >= 0.85


def test_polygon_matches_ground_truth():
    img, gt, _ = make_scene("across", [])
    poly = [[0, 303 / 480], [1, 303 / 480], [1, 1], [0, 1]]
    assert iou_dice(sidewalk_from_polygon(img.shape, [poly]), gt)[0] >= 0.98


# ------------------------------------------------------------------ keputusan
def test_vehicle_on_sidewalk_flagged_despite_occlusion_hole():
    """Mask trotoar bolong di bawah kendaraan -> contact≈0, tetapi cincin tanah tetap trotoar."""
    fa, *_ = analyze("across", [dict(kind="car", box=(200, 350, 400, 440), track_id=1)])
    r = by_id(fa, 1)
    assert r.contact_ratio < 0.1
    assert r.ring_ratio >= 0.8
    assert r.status == STATUS_PARKED


def test_vehicle_on_road_next_to_curb_not_flagged():
    """Mobil di jalan, tepat di belakang kerb: cincin depan tipis tidak boleh menyeberang kerb."""
    fa, *_ = analyze("across", [dict(kind="car", box=(430, 225, 530, 290), track_id=2)])
    r = by_id(fa, 2)
    assert r.ring_ratio < 0.2
    assert r.status == STATUS_ROAD


def test_rider_changes_status():
    fa, *_ = analyze("across", [dict(kind="motor", box=(60, 360, 130, 440), rider=True, track_id=3),
                                 dict(kind="motor", box=(300, 360, 370, 440), rider=False, track_id=4)])
    assert by_id(fa, 3).has_rider is True and by_id(fa, 3).status == STATUS_RIDDEN
    assert by_id(fa, 4).has_rider is False and by_id(fa, 4).status == STATUS_PARKED


def test_car_has_rider_none():
    fa, *_ = analyze("across", [dict(kind="car", box=(200, 350, 400, 440), track_id=1)])
    assert by_id(fa, 1).has_rider is None


def test_along_view_sidewalk_vs_road():
    fa, *_ = analyze("along", [dict(kind="car", box=(30, 330, 170, 420), track_id=1),
                                dict(kind="car", box=(300, 330, 440, 420), track_id=2)], ALONG_SEED)
    assert by_id(fa, 1).status == STATUS_PARKED
    assert by_id(fa, 2).status == STATUS_ROAD


def test_straddling_vehicle_is_borderline_and_tau_matters():
    """Kendaraan separuh di trotoar: skor di tengah; ambang menentukan keputusan (dokumentasi sensitivitas)."""
    veh = [dict(kind="car", box=(130, 330, 280, 420), track_id=3)]
    fa, *_ = analyze("along", veh, ALONG_SEED)
    r = by_id(fa, 3)
    assert 0.25 < r.score < 0.75
    fa2, *_ = analyze("along", veh, ALONG_SEED, tau_ring=0.3, tau_contact=0.3)
    assert by_id(fa2, 3).on_sidewalk
    fa3, *_ = analyze("along", veh, ALONG_SEED, tau_ring=0.8, tau_contact=0.8)
    assert not by_id(fa3, 3).on_sidewalk


def test_amodal_mask_uses_contact_ratio():
    """Jika mask trotoar menutupi area di bawah kendaraan (poligon/anotasi amodal), rasio kontak ≈ 1."""
    img, gt, dets = make_scene("across", [dict(kind="car", box=(200, 350, 400, 440), track_id=1)])
    cfg = Config()
    an = Analyzer(cfg, FakeSegmenter(lambda n, i: dets), "classical")
    fa = an.analyze_image(img, sidewalk_override=gt)         # gt tidak bolong
    r = by_id(fa, 1)
    assert r.contact_ratio >= 0.9 and r.status == STATUS_PARKED


def test_tiny_vehicle_ignored():
    img, gt, dets = make_scene("across", [dict(kind="car", box=(300, 400, 306, 404), track_id=1)])
    res = decide_frame(dets, gt, Config(), img.shape)
    assert res == []


def test_has_rider_unit_cases():
    cfg = Config()
    mask = np.zeros((10, 10), bool)
    moto = Obj("vehicle", "motorcycle", "motor", .9, (100, 100, 170, 160), mask)
    rider = Obj("person", "person", "orang", .9, (115, 40, 155, 140), mask)
    walker_far = Obj("person", "person", "orang", .9, (300, 40, 340, 140), mask)
    standing_beside_below = Obj("person", "person", "orang", .9, (100, 130, 130, 200), mask)  # kepala di bawah atas motor
    assert has_rider(moto, [rider], cfg) is True
    assert has_rider(moto, [walker_far], cfg) is False
    assert has_rider(moto, [standing_beside_below], cfg) is False
    car = Obj("vehicle", "car", "mobil", .9, (0, 0, 10, 10), mask)
    assert has_rider(car, [rider], cfg) is None


def test_nan_ring_when_surrounded_by_other_objects():
    """Cincin tertutup objek lain -> NaN (tidak dihitung), keputusan jatuh ke rasio kontak."""
    img, gt, dets = make_scene("across", [dict(kind="car", box=(200, 350, 400, 440), track_id=1)])
    cfg = Config(min_ring_px=10 ** 7)
    res = decide_frame(dets, gt & ~union_masks(dets, img.shape), cfg, img.shape)
    assert math.isnan(res[0].ring_ratio) and res[0].status == STATUS_ROAD     # tanpa bukti -> bukan pelanggaran

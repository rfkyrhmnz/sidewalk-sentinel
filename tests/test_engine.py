"""Uji mesin pada adegan sintetis. Kebenaran: 1 parkir + 2 naik trotoar (lihat scenes.py)."""
from __future__ import annotations

from collections import Counter

import pytest

from sidewalk_sentinel import Config, Engine
from sidewalk_sentinel.config import Config as Cfg
from scenes import ROI, Renderer, default_scene

TRUTH = {"parkir": 1, "naik_trotoar": 2}


def run_scene(cfg_over=None, **scene_kw):
    sc = default_scene(**scene_kw)
    r = Renderer(sc)
    cfg = Config.from_overrides({"park_seconds": 5, **(cfg_over or {})})
    eng = Engine(ROI, cfg)
    eng.set_reference(r.empty_frames(cfg.ref_frames))
    for t, frame in r.frames():
        eng.process(frame, t, snapshots=False)
    return eng


# ---------------------------------------------------------------- perilaku inti
@pytest.mark.parametrize("scene", [
    {},
    {"shadow": True},
    {"gradient": 0.35},
    {"gradient": 0.5},
    {"shadow": True, "gradient": 0.35},
], ids=["dasar", "bayangan", "gradien-0.35", "gradien-0.5", "bayangan+gradien"])
def test_pipeline_default_menemukan_tepat_satu_parkir_dan_dua_naik(scene):
    eng = run_scene(**scene)
    assert eng.counts == TRUTH, [(e.kind, e.track_id, round(e.t, 1)) for e in eng.events]


def test_waktu_kejadian_masuk_akal():
    eng = run_scene()
    parkir = next(e for e in eng.events if e.kind == "parkir")
    # motor P berhenti di t=7.0 s; konfirmasi = berhenti + jendela kecepatan + T(5 s)
    assert 12.0 <= parkir.t <= 14.0
    assert parkir.t - parkir.t_start == pytest.approx(5.0, abs=0.3)
    naik = [e for e in eng.events if e.kind == "naik_trotoar"]
    assert any(4.0 <= e.t <= 6.5 for e in naik)      # P naik
    assert any(10.5 <= e.t <= 13.5 for e in naik)    # R naik
    assert all("jalan" in e.reason for e in naik)


def test_mobil_di_jalan_dan_pejalan_kaki_tidak_memicu():
    sc = default_scene()
    r = Renderer(sc)
    eng = Engine(ROI, Config.from_overrides({"park_seconds": 5}))
    eng.set_reference(r.empty_frames(8))
    ped_seen = False
    for t, f in r.frames():
        eng.process(f, t, snapshots=False)
        if 22.0 <= t <= 27.0 and any(b.label == "pejalan" for b in eng.last["blobs"]):
            ped_seen = True
    assert ped_seen, "pejalan kaki seharusnya terdeteksi sebagai blob berlabel 'pejalan'"
    assert len({e.track_id for e in eng.events}) == 2        # hanya P dan R yang memicu
    assert eng.counts == TRUTH


def test_pejalan_kaki_disaring_hanya_bila_filter_aktif():
    on = run_scene()
    off = run_scene({"filter_pedestrians": False, "ride_standalone_seconds": 1.5})
    assert on.counts["naik_trotoar"] == 2
    # tanpa saringan, pejalan kaki (bergerak di trotoar) terhitung sebagai pengendara
    assert off.counts["naik_trotoar"] >= 3


def test_kendaraan_sudah_parkir_sejak_awal_dengan_referensi_kosong():
    """Referensi dari frame kosong terpisah; motor sudah ada sejak t=0 dan tidak pernah bergerak."""
    sc = default_scene()
    sc.actors = [a for a in sc.actors if a.name == "P"]
    sc.actors[0].keys = [(0.0, 300, 317), (1.0, 300, 317)]
    r = Renderer(sc)
    eng = Engine(ROI, Config.from_overrides({"park_seconds": 5}))
    eng.set_reference(r.empty_frames(8))
    for t, f in r.frames():
        eng.process(f, t, snapshots=False)
    assert eng.counts == {"parkir": 1, "naik_trotoar": 0}
    ev = eng.events[0]
    assert ev.kind == "parkir" and 5.0 <= ev.t <= 8.0


def test_trotoar_kosong_tidak_menghasilkan_apa_apa():
    sc = default_scene()
    sc.actors = []
    r = Renderer(sc)
    eng = Engine(ROI, Config.from_overrides({"park_seconds": 5}))
    eng.set_reference(r.empty_frames(8))
    for t, f in r.frames():
        eng.process(f, t, snapshots=False)
    assert eng.counts == {"parkir": 0, "naik_trotoar": 0}


# --------------------------------------- ablation: tiap tahap pengolahan terbukti berguna
def test_ablation_morfologi_mencegah_objek_terpecah():
    """Tanpa opening/closing, pita warna-paving memecah mask satu pengendara jadi dua blob."""
    assert run_scene().counts == TRUTH
    assert run_scene({"use_morphology": False}).counts["naik_trotoar"] > 2


def test_ablation_buang_bayangan_mencegah_parkir_palsu():
    assert run_scene(shadow=True).counts == TRUTH
    assert run_scene({"use_shadow_removal": False}, shadow=True).counts["parkir"] > 1


def test_ablation_homomorfik_menahan_gradien_cahaya():
    assert run_scene(gradient=0.35).counts == TRUTH
    assert run_scene({"use_homomorphic": False}, gradient=0.35).counts != TRUTH


def test_ablation_bukti_citra_asli_mencegah_halo_filter_homomorfik():
    """Halo di tepi bayangan tajam menempel ke pejalan kaki; bukti di citra asli memutus itu."""
    assert run_scene(shadow=True).counts == TRUTH
    assert run_scene({"use_raw_evidence": False}, shadow=True).counts != TRUTH


# --------------------------------------------------------------------- foto bukti
def test_tiap_kejadian_membawa_foto_bukti_jpeg_beranotasi():
    r = Renderer(default_scene())
    eng = Engine(ROI, Config(park_seconds=5))
    eng.set_reference(r.empty_frames(8))
    events = []
    for t, f in r.frames():
        events += eng.process(f, t).events
    assert Counter(e.kind for e in events) == Counter(parkir=1, naik_trotoar=2)
    for e in events:
        assert e.snapshot and e.snapshot[:2] == b"\xff\xd8" and len(e.snapshot) > 1000


# --------------------------------------------------------------- validasi masukan
def test_config_menolak_parameter_tak_dikenal_dan_tak_valid():
    with pytest.raises(ValueError):
        Cfg.from_overrides({"tidak_ada": 1})
    with pytest.raises(ValueError):
        Cfg.from_overrides({"park_seconds": 0})
    with pytest.raises(ValueError):
        Cfg.from_overrides({"still_speed": 0.2, "move_speed": 0.1})
    assert Cfg.from_overrides({"use_clahe": "ya", "park_seconds": "12"}).park_seconds == 12


def test_roi_minimal_tiga_titik_dan_engine_butuh_referensi():
    with pytest.raises(ValueError):
        Engine([(0, 0), (1, 1)])
    eng = Engine(ROI)
    with pytest.raises(RuntimeError):
        eng.process(Renderer(default_scene()).frame(0.0), 0.0)

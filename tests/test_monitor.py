"""Uji Monitor (inti mode CCTV): belajar latar otomatis dan pengaman perubahan adegan.

Pengaman diuji terhadap pembandingnya (pengaman mati), karena tujuannya terukur: tidak boleh lebih buruk
dari tanpa pengaman, dan harus menolong tepat pada gangguan yang membutakan engine.
"""
from __future__ import annotations

import numpy as np
import pytest

from sidewalk_sentinel.config import Config
from sidewalk_sentinel.monitor import Monitor
from scenes import ROI, Renderer, default_scene

STOP_AT = 14.0          # semua kejadian adegan baku sudah terjadi sebelum detik ini


def tint(dr: float, db: float):
    def f(img):
        out = img.astype(np.float32)
        out[..., 2] += dr
        out[..., 0] += db
        return np.clip(out, 0, 255).astype(np.uint8)
    return f


def run(disturb=None, since=3.6, guard=True, until=STOP_AT):
    scene = default_scene(shadow=False, gradient=False, seed=3)
    mon = Monitor(ROI, Config(park_seconds=5, use_scene_guard=guard), learn="cepat")
    events, states = [], []
    for t, frame in Renderer(scene).frames():
        if t > until:
            break
        rep = mon.feed(disturb(frame) if disturb and t >= since else frame, t)
        states.append(rep.state)
        events += [(round(t, 1), e.kind) for e in rep.events]
    return mon, events, states


def kinds(events):
    return sorted(k for _, k in events)


def test_belajar_otomatis_lalu_mendeteksi():
    mon, events, states = run()
    assert states[0] == "belajar" and states[-1] == "memantau"
    assert kinds(events) == ["naik_trotoar", "naik_trotoar", "parkir"]
    assert mon.relearns == 0
    assert mon.counts == {"parkir": 1, "naik_trotoar": 2}


def test_progres_belajar_naik_bertahap_lalu_penuh():
    scene = default_scene(shadow=False, gradient=False, seed=3)
    mon = Monitor(ROI, Config(), learn="cepat")
    seen = []
    for t, frame in Renderer(scene).frames():
        rep = mon.feed(frame, t)
        seen.append(rep.learn[0])
        if rep.state == "memantau":
            break
    assert seen == sorted(seen) and seen[0] == 1 and seen[-1] == 8 == mon.learn_n


def test_gangguan_warna_sedang_tidak_memicu_belajar_ulang():
    """Engine kebal terhadap pergeseran warna sedang. Belajar ulang saat itu menyerap kendaraan yang
    sedang diam ke latar (kejadian parkir hilang), jadi pengaman harus diam."""
    mon, events, _ = run(tint(45, -35))
    assert mon.relearns == 0
    assert kinds(events) == ["naik_trotoar", "naik_trotoar", "parkir"]


def test_gangguan_warna_kuat_membutakan_engine_dan_pengaman_memulihkan():
    mati, ev_mati, _ = run(tint(110, -90), guard=False)
    assert ev_mati == [], "tanpa pengaman engine buta total (seluruh zona menjadi foreground)"

    hidup, ev_hidup, states = run(tint(110, -90), guard=True)
    assert hidup.relearns == 1
    assert hidup.state == "memantau"
    assert len(ev_hidup) >= 1, "setelah belajar ulang, kejadian harus terdeteksi lagi"
    # kejadian palsu tidak boleh muncul selama perubahan berlangsung
    belajar_selesai = next(i for i, s in enumerate(states) if i and s == "memantau" and "belajar" in states[:i])
    assert all(t > 3.6 for t, _ in ev_hidup) and belajar_selesai > 0


def _monitor_siap():
    import cv2  # noqa: F401
    scene = default_scene(shadow=False, gradient=False, seed=3)
    r = Renderer(scene)
    mon = Monitor(ROI, Config(), learn="cepat")
    t = 0.0
    for _ in range(40):
        mon.feed(r.frame(0.0, actors=False), t)
        t += 0.4
    assert mon.state == "memantau"
    return mon, r, t


def test_resolusi_berbeda_dengan_rasio_sama_tidak_memicu_belajar_ulang():
    """Semua frame diskalakan ke lebar kerja, jadi kamera yang berganti 720p/480p tak perlu belajar ulang."""
    import cv2
    mon, r, t = _monitor_siap()
    mon.feed(cv2.resize(r.frame(0.0, actors=False), (480, 270)), t)
    assert mon.relearns == 0 and mon.state == "memantau"


def test_rasio_aspek_berubah_memicu_belajar_ulang_lalu_pulih():
    import cv2
    mon, r, t = _monitor_siap()
    persegi = cv2.resize(r.frame(0.0, actors=False), (640, 480))     # 16:9 menjadi 4:3
    mon.feed(persegi, t)
    assert mon.relearns == 1 and mon.state == "belajar"
    for _ in range(30):                                              # pulih pada bentuk baru
        t += 0.45                                                    # > jarak sampel, tahan terhadap galat desimal
        mon.feed(persegi, t)
    assert mon.state == "memantau"


def test_kalibrasi_ulang_manual_mengosongkan_latar():
    scene = default_scene(shadow=False, gradient=False, seed=3)
    r = Renderer(scene)
    mon = Monitor(ROI, Config(), learn="cepat")
    for i in range(12):
        mon.feed(r.frame(0.0, actors=False), i * 0.4)
    assert mon.state == "memantau"
    mon.recalibrate("diminta pengguna")
    assert mon.state == "belajar" and mon.engine.ref is None and mon.learn_progress == (0, 8)


def test_mode_belajar_tidak_dikenal_ditolak():
    with pytest.raises(ValueError):
        Monitor(ROI, Config(), learn="sembarang")


def test_konfigurasi_pengaman_divalidasi():
    with pytest.raises(ValueError):
        Config(scene_change_frac=0.99).validate()
    with pytest.raises(ValueError):
        Config(scene_change_seconds=0.1).validate()
    cfg = Config.from_overrides({"use_scene_guard": "false", "scene_change_seconds": 5})
    assert cfg.use_scene_guard is False and cfg.scene_change_seconds == 5.0

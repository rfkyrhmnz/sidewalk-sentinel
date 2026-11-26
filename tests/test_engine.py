"""Uji mesin: kotak kendaraan -> pelacak -> keputusan (parkir / naik trotoar), tanpa model sungguhan.

Detektor diganti pembangkit kotak berskrip, sehingga aturan zona, waktu, getar kotak, dan deteksi yang terlewat bisa
diuji tepat dan tanpa menunggu waktu nyata (waktu dikirim sebagai argumen)."""
from __future__ import annotations

import numpy as np
import pytest

from sidewalk_sentinel.config import Config
from sidewalk_sentinel.detector import Detection
from sidewalk_sentinel.engine import Engine

W, H = 640, 360
ZONE = [[0.0, 0.6], [1.0, 0.6], [1.0, 1.0], [0.0, 1.0]]       # sepertiga bawah gambar = trotoar (y >= 216)
FRAME = np.full((H, W, 3), 120, np.uint8)


class Script:
    """Detektor berskrip: `fn(t)` mengembalikan daftar kotak (x1, y1, x2, y2) pada waktu t."""

    def __init__(self, fn, name="car", label="mobil"):
        self.fn, self.name, self.label, self.t = fn, name, label, 0.0

    def detect(self, frame, conf=0.3, imgsz=640):
        return [Detection(b, 0.9, self.name, self.label) for b in self.fn(self.t)]


def run(engine, script, seconds, fps=8.0, start=0.0, frame=FRAME):
    events = []
    n = int(seconds * fps)
    for i in range(n + 1):
        t = start + i / fps
        script.t = t
        events += engine.process(frame, t, snapshots=False).events
    return events


def car_at(x, y, w=100, h=50):
    return (x, y, x + w, y + h)


def eng(script, **cfg):
    return Engine(ZONE, script, Config.from_overrides({"park_seconds": 5, **cfg}))


def test_kendaraan_diam_di_zona_dicatat_parkir_sekali_setelah_batas_waktu():
    e = eng(Script(lambda t: [car_at(300, 250)]))
    ev = run(e, e.detector, 12)
    assert [x.kind for x in ev] == ["parkir"]
    assert 5.0 <= ev[0].t <= 7.5, "dicatat sesudah diam selama park_seconds, bukan lebih awal"
    assert e.counts == {"parkir": 1, "naik_trotoar": 0}


def test_kendaraan_di_jalan_di_luar_zona_tidak_pernah_dicatat():
    e = eng(Script(lambda t: [car_at(300, 100)]))
    assert run(e, e.detector, 15) == []
    assert all(v["state"] == "luar" and not v["in_roi"] for v in e.last["tracks"])


def test_kendaraan_naik_dari_jalan_ke_trotoar_sambil_bergerak_dicatat_naik_trotoar():
    def fn(t):
        y = 150 + 120 * min(t, 1.0)                    # dari jalan (y=150) menyeberang kerb ke trotoar (y=270)
        x = 100 + 90 * t
        return [car_at(x, y)]
    e = eng(Script(fn))
    ev = run(e, e.detector, 4)
    assert "naik_trotoar" in [x.kind for x in ev]
    assert next(x for x in ev if x.kind == "naik_trotoar").reason.startswith("melintas dari jalan")


def test_getar_kotak_beberapa_piksel_tidak_dibaca_sebagai_gerak():
    rng = np.random.default_rng(1)
    jitter = {}

    def fn(t):
        k = round(t * 8)
        j = jitter.setdefault(k, rng.integers(-4, 5, size=2))
        return [car_at(300 + int(j[0]), 250 + int(j[1]), 100 + int(j[0]) // 2, 50)]
    e = eng(Script(fn))
    ev = run(e, e.detector, 12)
    assert [x.kind for x in ev] == ["parkir"], "kendaraan parkir yang kotaknya bergetar tetap parkir, bukan naik trotoar"


def test_deteksi_yang_terlewat_sebentar_tidak_mengulang_hitungan_diam():
    def fn(t):
        return [] if 3.0 <= t < 4.0 else [car_at(300, 250)]       # YOLO melewatkan 1 detik di tengah
    e = eng(Script(fn))
    ev = run(e, e.detector, 12)
    assert [x.kind for x in ev] == ["parkir"] and ev[0].track_id == 1, "jejak yang sama bertahan melewati celah"


def test_kendaraan_pergi_lebih_lama_dari_batas_hilang_dihitung_sebagai_baru():
    def fn(t):
        return [] if 4.0 <= t < 9.0 else [car_at(300, 250)]       # pergi 5 dtk, jauh melewati track_max_missed_s
    e = eng(Script(fn), park_seconds=2)
    ev = run(e, e.detector, 16)
    assert [x.kind for x in ev] == ["parkir", "parkir"] and ev[0].track_id != ev[1].track_id


def test_kotak_terlalu_kecil_diabaikan_dan_kotak_dipotong_ke_dalam_frame():
    e = eng(Script(lambda t: [(300, 250, 304, 254)]), min_box_frac=0.0015)
    assert run(e, e.detector, 8) == [] and e.last["tracks"] == [], "kotak 4x4 px di bawah ukuran minimum"
    e2 = eng(Script(lambda t: [(-50, 200, 700, 400)]))
    e2.process(FRAME, 0.0, snapshots=False)
    x, y, w, h = e2.last["tracks"][0]["bbox"]
    assert x >= 0 and y >= 0 and x + w <= W and y + h <= H


def test_frame_lebar_diperkecil_ke_lebar_kerja_dan_kotak_memakai_ukuran_kerja():
    seen = {}

    class Spy(Script):
        def detect(self, frame, conf=0.3, imgsz=640):
            seen["shape"], seen["conf"], seen["imgsz"] = frame.shape, conf, imgsz
            return super().detect(frame, conf, imgsz)

    e = Engine(ZONE, Spy(lambda t: [car_at(300, 250)]), Config.from_overrides({"conf": 0.42, "imgsz": 512}))
    e.process(np.zeros((720, 1280, 3), np.uint8), 0.0, snapshots=False)
    assert seen["shape"][:2] == (360, 640) and e.size == (640, 360), "frame 1280x720 diperkecil ke lebar kerja 640"
    assert seen["conf"] == 0.42 and seen["imgsz"] == 512, "pengaturan kepekaan sampai ke detektor"


def test_kejadian_menyertakan_foto_bukti_beranotasi_dan_label_kelas():
    e = eng(Script(lambda t: [car_at(300, 250)], name="motorcycle", label="motor"), park_seconds=2)
    ev = []
    for i in range(40):
        e.detector.t = i / 8
        ev += e.process(FRAME, i / 8).events
    assert ev and ev[0].snapshot[:2] == b"\xff\xd8"
    assert e.last["tracks"][0]["label"] == "motor"
    assert e.annotated().shape == (H, W, 3)


def test_roi_kurang_dari_tiga_titik_ditolak_dan_pengaturan_tak_dikenal_ditolak():
    with pytest.raises(ValueError):
        Engine([[0, 0], [1, 1]], Script(lambda t: []))
    with pytest.raises(ValueError, match="tidak dikenal"):
        Config.from_overrides({"diff_floor": 20})                  # parameter metode lama sudah tidak ada
    with pytest.raises(ValueError):
        Config.from_overrides({"conf": 0.99})

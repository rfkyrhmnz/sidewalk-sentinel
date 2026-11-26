"""Uji detektor: penyaring kelas kendaraan, konversi mask ke poligon, status pemuatan model, usulan zona.

Model YOLO sungguhan tidak dijalankan di sini (tanpa torch). Yang diuji adalah semua logika di sekeliling model, dengan
objek `Results` tiruan yang berbentuk sama seperti keluaran ultralytics."""
from __future__ import annotations

import sys
import time

import cv2
import numpy as np
import pytest

from sidewalk_sentinel import detector as det
from sidewalk_sentinel.detector import (DetectorUnavailable, SidewalkSegmenter, VehicleDetector, mask_to_polygon,
                                        parse_result)

COCO = {0: "person", 1: "bicycle", 2: "car", 3: "motorcycle", 5: "bus", 7: "truck", 41: "cup", 67: "cell phone",
        16: "dog", 56: "chair"}


class Boxes:
    def __init__(self, rows):
        self.cls = np.array([r[0] for r in rows], dtype=float)
        self.conf = np.array([r[1] for r in rows], dtype=float)
        self.xyxy = np.array([r[2] for r in rows], dtype=float).reshape(-1, 4)

    def __len__(self):
        return len(self.cls)


class Result:
    def __init__(self, rows, names=COCO, masks=None):
        self.boxes = Boxes(rows) if rows is not None else None
        self.names = names
        self.masks = masks


class Tensorlike:
    """Meniru tensor torch: .cpu().numpy()."""

    def __init__(self, a):
        self.a = np.asarray(a)

    def cpu(self):
        return self

    def numpy(self):
        return self.a

    def __len__(self):
        return len(self.a)


# ============================================================================ penyaring kelas
def test_hanya_kelas_kendaraan_yang_dihitung_tangan_orang_dan_benda_lain_dibuang():
    box = (10, 20, 110, 90)
    rows = [(0, 0.9, box), (41, 0.8, box), (67, 0.95, box), (16, 0.7, box), (56, 0.6, box),   # bukan kendaraan
            (2, 0.88, (1, 2, 3, 4)), (3, 0.5, (5, 6, 7, 8)), (5, 0.6, (9, 9, 19, 19)),
            (7, 0.7, (0, 0, 50, 50)), (1, 0.4, (30, 30, 40, 40))]
    got = parse_result(Result(rows))
    assert [(d.name, d.label) for d in got] == [("car", "mobil"), ("motorcycle", "motor"), ("bus", "bus"),
                                                ("truck", "truk"), ("bicycle", "sepeda")]
    assert got[0].box == (1.0, 2.0, 3.0, 4.0) and got[0].conf == pytest.approx(0.88)


def test_tanpa_kendaraan_hasilnya_kosong_bukan_galat():
    assert parse_result(Result([(0, 0.9, (1, 1, 5, 5)), (67, 0.9, (1, 1, 5, 5))])) == []
    assert parse_result(Result([])) == []
    assert parse_result(Result(None)) == []


def test_nama_kelas_tidak_peka_huruf_besar_kecil_dan_tensor_torch_diterima():
    r = Result([(0, 0.9, (1, 2, 3, 4))], names={0: "Car"})
    r.boxes.cls, r.boxes.conf, r.boxes.xyxy = (Tensorlike(r.boxes.cls), Tensorlike(r.boxes.conf), Tensorlike(r.boxes.xyxy))
    assert [d.name for d in parse_result(r)] == ["car"]


# ============================================================================ mask -> poligon
def _persegi(h=100, w=200, box=(20, 40, 180, 90)):
    m = np.zeros((h, w), bool)
    x0, y0, x1, y1 = box
    m[y0:y1, x0:x1] = True
    return m


def test_mask_persegi_menjadi_poligon_bernilai_0_sampai_1():
    poly = mask_to_polygon(_persegi())
    assert 3 <= len(poly) <= 10
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    assert min(xs) == pytest.approx(0.1, abs=0.02) and max(xs) == pytest.approx(0.9, abs=0.02)
    assert min(ys) == pytest.approx(0.4, abs=0.02) and max(ys) == pytest.approx(0.9, abs=0.02)
    assert all(0 <= v <= 1 for v in xs + ys)


def test_komponen_terbesar_dipilih_dan_yang_terlalu_kecil_ditolak():
    m = _persegi(box=(10, 50, 150, 95))
    m[5:15, 170:190] = True                                           # gumpalan kecil terpisah
    xs = [p[0] for p in mask_to_polygon(m)]
    assert max(xs) < 0.8, "gumpalan kecil tidak ikut"
    assert mask_to_polygon(np.zeros((100, 200), bool)) is None
    kecil = np.zeros((100, 200), bool)
    kecil[10:14, 10:14] = True
    assert mask_to_polygon(kecil) is None


def test_bentuk_rumit_disederhanakan_ke_paling_banyak_sekian_titik():
    m = np.zeros((300, 400), np.uint8)
    pts = np.array([[int(200 + (120 + 30 * (i % 2)) * np.cos(a)), int(150 + (100 + 20 * (i % 2)) * np.sin(a))]
                    for i, a in enumerate(np.linspace(0, 2 * np.pi, 60, endpoint=False))], np.int32)
    cv2.fillPoly(m, [pts], 1)
    assert 3 <= len(mask_to_polygon(m.astype(bool), max_points=8)) <= 8


# ============================================================================ pemuatan model
class FakeModel:
    def __init__(self, names=COCO, result=None):
        self.names, self.result, self.kw = names, result or Result([]), None

    def predict(self, frame, **kw):
        self.kw = kw
        return [self.result]


def test_tanpa_paket_ultralytics_statusnya_galat_dengan_petunjuk_pemasangan(monkeypatch):
    monkeypatch.setitem(sys.modules, "ultralytics", None)             # `from ultralytics import YOLO` -> ImportError
    d = VehicleDetector("apa-saja.pt")
    with pytest.raises(DetectorUnavailable, match="pip install ultralytics"):
        d.load()
    assert d.status()["state"] == "galat" and "pip install ultralytics" in d.status()["message"] and not d.ready
    with pytest.raises(DetectorUnavailable):
        d.detect(np.zeros((40, 40, 3), np.uint8))                     # tidak diam-diam mengembalikan "tak ada kendaraan"


def test_model_dimuat_hanya_dengan_kelas_kendaraan_dan_parameter_diteruskan(monkeypatch):
    model = FakeModel(result=Result([(2, 0.9, (10, 10, 60, 40)), (0, 0.9, (1, 1, 9, 9))]))
    monkeypatch.setattr(det, "_load_yolo", lambda w: model)
    d = VehicleDetector("tiruan.pt", device="cpu")
    assert d.status()["state"] == "belum"
    d.load()
    assert d.ready and d.status()["state"] == "siap"
    got = d.detect(np.zeros((100, 100, 3), np.uint8), conf=0.4, imgsz=480)
    assert [x.name for x in got] == ["car"], "orang dibuang walau model mengembalikannya"
    assert model.kw["conf"] == 0.4 and model.kw["imgsz"] == 480 and model.kw["device"] == "cpu"
    assert sorted(model.kw["classes"]) == [1, 2, 3, 5, 7], "model hanya diminta mencari kelas kendaraan"


def test_model_tanpa_kelas_kendaraan_ditolak_dengan_pesan_jelas(monkeypatch):
    monkeypatch.setattr(det, "_load_yolo", lambda w: FakeModel(names={0: "sidewalk"}))
    d = VehicleDetector("trotoar.pt")
    with pytest.raises(DetectorUnavailable, match="kelas kendaraan"):
        d.load()
    assert d.status()["state"] == "galat"


def test_pemuatan_di_latar_lalu_coba_lagi_setelah_galat(monkeypatch):
    calls = {"n": 0}

    def fake(w):
        calls["n"] += 1
        time.sleep(0.15)
        if calls["n"] == 1:
            raise DetectorUnavailable("unduhan gagal")
        return FakeModel()

    monkeypatch.setattr(det, "_load_yolo", fake)
    d = VehicleDetector("tiruan.pt")
    d.start_loading()
    assert d.status()["state"] in ("belum", "memuat")
    t0 = time.time()
    while d.status()["state"] not in ("galat", "siap") and time.time() - t0 < 5:
        time.sleep(0.05)
    assert d.status() == {"state": "galat", "message": "unduhan gagal", "weights": "tiruan.pt"}
    d.retry()
    t0 = time.time()
    while not d.ready and time.time() - t0 < 5:
        time.sleep(0.05)
    assert d.ready and calls["n"] == 2
    d.retry()                                                         # sudah siap: tidak memuat ulang
    assert calls["n"] == 2


# ============================================================================ model trotoar
class Masks:
    def __init__(self, polys):
        self.xy = [np.array(p, float) for p in polys]


def _seg(monkeypatch, tmp_path, result, name="trotoar.pt"):
    f = tmp_path / name
    f.write_bytes(b"x")
    monkeypatch.setattr(det, "_load_yolo", lambda w: FakeModel(names=result.names, result=result))
    return SidewalkSegmenter(f)


def test_usulan_zona_dari_mask_model_trotoar(monkeypatch, tmp_path):
    poly = [(10, 60), (190, 60), (190, 95), (10, 95)]
    res = Result([(0, 0.9, (10, 60, 190, 95))], names={0: "sidewalk"}, masks=Masks([poly]))
    seg = _seg(monkeypatch, tmp_path, res)
    out = seg.propose(np.zeros((100, 200, 3), np.uint8))
    assert out is not None and 3 <= len(out["roi"]) <= 10
    ys = [p[1] for p in out["roi"]]
    assert min(ys) == pytest.approx(0.6, abs=0.03) and out["coverage"] == pytest.approx(0.9 * 0.35, abs=0.03)


def test_hanya_kelas_trotoar_dipakai_bila_model_punya_banyak_kelas(monkeypatch, tmp_path):
    jalan = [(0, 0), (200, 0), (200, 50), (0, 50)]
    trotoar = [(20, 60), (180, 60), (180, 95), (20, 95)]
    res = Result([(0, 0.9, (0, 0, 200, 50)), (1, 0.9, (20, 60, 180, 95))], names={0: "road", 1: "sidewalk"},
                 masks=Masks([jalan, trotoar]))
    out = _seg(monkeypatch, tmp_path, res).propose(np.zeros((100, 200, 3), np.uint8))
    assert min(p[1] for p in out["roi"]) > 0.5, "jalan tidak ikut diusulkan sebagai trotoar"


def test_trotoar_tak_terlihat_memberi_none_dan_model_tak_ada_memberi_galat(monkeypatch, tmp_path):
    seg = _seg(monkeypatch, tmp_path, Result([], names={0: "sidewalk"}, masks=None))
    assert seg.propose(np.zeros((100, 200, 3), np.uint8)) is None
    ada = SidewalkSegmenter(tmp_path / "tidak-ada.pt")
    assert not ada.available
    with pytest.raises(DetectorUnavailable, match="manual"):
        ada.propose(np.zeros((100, 200, 3), np.uint8))

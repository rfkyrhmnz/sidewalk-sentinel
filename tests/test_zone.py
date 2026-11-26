"""Uji zona otomatis: rata-rata mask antar-frame, kestabilan, hilangnya trotoar, dan pengisian gigitan kendaraan."""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from sidewalk_sentinel.detector import mask_to_polygon
from sidewalk_sentinel.zone import AutoZone

H, W = 180, 320
FRAME = np.zeros((H, W, 3), np.uint8)


class Seq:
    """Segmenter berskrip: tiap panggilan mengembalikan mask berikutnya (yang terakhir diulang)."""

    def __init__(self, *masks):
        self.masks, self.i = list(masks), 0

    def mask(self, frame, conf=0.25, imgsz=640):
        m = self.masks[min(self.i, len(self.masks) - 1)]
        self.i += 1
        return m


def band(y0=110, y1=170, x0=10, x1=310):
    m = np.zeros((H, W), bool)
    m[y0:y1, x0:x1] = True
    return m


def ys(poly):
    return [p[1] for p in poly]


def test_trotoar_terlihat_langsung_menjadi_zona_pada_pembaruan_pertama():
    az = AutoZone(Seq(band()))
    changed, roi = az.update(FRAME, 0.0)
    assert changed and 3 <= len(roi) <= 10
    assert min(ys(roi)) == pytest.approx(110 / H, abs=0.03) and max(ys(roi)) == pytest.approx(170 / H, abs=0.03)
    assert az.roi == roi


def test_bentuk_yang_sama_berulang_tidak_dianggap_berubah():
    az = AutoZone(Seq(band()))
    az.update(FRAME, 0.0)
    for k in range(1, 6):
        changed, roi = az.update(FRAME, float(k))
        assert not changed and roi is not None


def test_satu_frame_meleset_tidak_merusak_zona_tetapi_perpindahan_sungguhan_diikuti():
    az = AutoZone(Seq(band(), band(), np.zeros((H, W), bool), band()))        # frame ke-3: model tidak melihat apa pun
    az.update(FRAME, 0.0)
    az.update(FRAME, 1.0)
    changed, roi = az.update(FRAME, 2.0)
    assert not changed and roi is not None, "satu kali meleset tidak mencabut zona"
    az.update(FRAME, 3.0)
    geser = Seq(*([band(y0=40, y1=100)] * 12))                                # kamera diangkat: trotoar naik ke atas gambar
    az.segmenter = geser
    for k in range(4, 14):
        az.update(FRAME, float(k))
    assert max(ys(az.roi)) < 0.7, "zona mengikuti trotoar yang berpindah"


def test_trotoar_hilang_beberapa_kali_beruntun_mencabut_zona():
    az = AutoZone(Seq(band(), *([np.zeros((H, W), bool)] * 12)))
    az.update(FRAME, 0.0)
    out = [az.update(FRAME, float(k)) for k in range(1, 12)]
    assert out[-1] == (False, None) or any(c and r is None for c, r in out)
    assert az.roi is None, "kamera berpaling dari trotoar: zona dicabut, bukan dibiarkan basi"


def test_belum_pernah_melihat_trotoar_tidak_menghasilkan_zona():
    az = AutoZone(Seq(np.zeros((H, W), bool)))
    assert az.update(FRAME, 0.0) == (False, None) and az.roi is None


def test_jadwal_pembaruan_rapat_di_awal_lalu_renggang():
    az = AutoZone(Seq(band()), warm_interval=1.0, interval=4.0)
    assert az.due(0.0)
    t = 0.0
    for _ in range(5):                                                       # lima celah pertama rapat
        az.update(FRAME, t)
        assert not az.due(t + 0.5) and az.due(t + 1.0)
        t += 1.0
    az.update(FRAME, t)                                                      # pembaruan ke-6: masa awal selesai
    assert not az.due(t + 3.0) and az.due(t + 4.0), "sesudah stabil, model dijalankan lebih jarang"


def test_gigitan_kendaraan_di_tepi_trotoar_ditutup_tetapi_bentuk_cekung_sungguhan_tidak():
    m = band(y0=100, y1=170, x0=10, x1=310)
    m[100:140, 140:200] = False                                              # kendaraan menutupi tepi atas trotoar
    poly = mask_to_polygon(m)
    atas = [p for p in poly if 0.4 < p[0] < 0.7]
    assert not atas or min(p[1] for p in atas) <= 100 / H + 0.03, "gigitan tidak ikut menjadi cekungan zona"

    L = np.zeros((H, W), bool)
    L[20:170, 10:80] = True
    L[120:170, 10:310] = True                                                # trotoar berbentuk L di tikungan
    poly = mask_to_polygon(L)
    img = np.zeros((H, W), np.uint8)
    cv2.fillPoly(img, [np.array([[x * W, y * H] for x, y in poly], np.int32)], 1)
    assert img[60, 250] == 0, "sudut cekung L tidak diisi: bagian jalan tidak dianggap trotoar"

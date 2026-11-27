"""Uji alat diagnosis deteksi (tools/uji_deteksi.py) dengan detektor tiruan yang perilakunya diketahui.

Yang diuji: pemilihan frame, penyiapan gambar seperti di aplikasi, penghitungan per ambang dari satu kali jalan,
kesimpulan yang ditulis, dan keluaran berkas. Mutu YOLO sungguhan tidak diuji di sini."""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

from sidewalk_sentinel.detector import Detection

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import uji_deteksi as U  # noqa: E402


class Palsu:
    """Detektor yang hasilnya ditentukan fungsi `aturan(frame, imgsz) -> [Detection]`; menyaring menurut `conf` seperti YOLO."""

    def __init__(self, aturan):
        self.aturan, self.panggilan = aturan, []

    def detect(self, frame, conf=0.3, imgsz=640):
        self.panggilan.append((frame.shape[:2], conf, imgsz))
        return [d for d in self.aturan(frame, imgsz) if d.conf >= conf]


def mobil(conf):
    return Detection((20.0, 30.0, 120.0, 90.0), conf, "car", "mobil")


def tegak():
    """Gambar tegak (lebih tinggi dari lebar), seperti HP dipegang berdiri."""
    img = np.full((900, 600, 3), 90, np.uint8)
    cv2.rectangle(img, (100, 100), (300, 200), (30, 30, 200), -1)
    return img


def jalankan(aturan, frames=None):
    rows = U.sweep(Palsu(aturan), frames or [tegak()])
    return rows, " ".join(U.kesimpulan(rows))


def baris(rows, putar, imgsz):
    return next(r for r in rows if (r.putar, r.imgsz) == (putar, imgsz))


# ----------------------------------------------------------------------------------------------------- penyiapan
def test_siapkan_memutar_dulu_lalu_memperkecil_ke_lebar_kerja_seperti_aplikasi():
    img = np.zeros((900, 600, 3), np.uint8)                       # tinggi 900, lebar 600
    assert U.siapkan(img, 0, 640).shape[:2] == (900, 600), "lebar 600 <= 640: tidak diubah"
    assert U.siapkan(img, 90, 640).shape[:2] == (427, 640), "diputar jadi 900 lebar, lalu diperkecil ke 640"
    assert U.siapkan(img, 180, 640).shape[:2] == (900, 600)
    assert U.siapkan(img, 270, 640).shape[:2] == (427, 640)
    besar = np.zeros((1080, 1920, 3), np.uint8)
    assert U.siapkan(besar, 0, 640).shape[:2] == (360, 640)


def test_putar_90_searah_jarum_jam_dan_270_berlawanan():
    img = np.zeros((10, 20, 3), np.uint8)
    img[0, 0] = (255, 255, 255)                                   # pojok kiri atas
    assert U.siapkan(img, 90, 640)[0, -1].tolist() == [255, 255, 255], "90: pojok kiri atas pindah ke kanan atas"
    assert U.siapkan(img, 270, 640)[-1, 0].tolist() == [255, 255, 255], "270: pindah ke kiri bawah"


# ----------------------------------------------------------------------------------------------------- hitungan
def test_satu_kali_jalan_per_kombinasi_dan_semua_ambang_dihitung_dari_hasil_yang_sama():
    rows, _ = jalankan(lambda f, s: [mobil(0.40), mobil(0.20), mobil(0.08)])
    assert len(rows) == len(U.ROTASI) * len(U.UKURAN)
    r = baris(rows, 0, 640)
    assert r.jumlah == {0.30: 1, 0.15: 2, 0.05: 3}
    assert r.frame == {0.30: 1, 0.15: 1, 0.05: 1}
    assert r.terbaik == {"mobil": 0.40}


def test_detektor_dipanggil_pada_lantai_terendah_bukan_ambang_bawaan():
    p = Palsu(lambda f, s: [])
    U.sweep(p, [tegak()], rotasi=(0,), ukuran=(640,))
    assert p.panggilan == [((900, 600), 0.05, 640)], "kotak berkeyakinan rendah tidak boleh hilang sebelum dihitung"


def test_frame_yang_punya_kotak_dihitung_terpisah_dari_jumlah_kotak():
    a, b = tegak(), tegak()
    b[:] = 0                                                      # frame kedua gelap: detektor tiruan tidak melihat apa pun
    rows = U.sweep(Palsu(lambda f, s: [mobil(0.5), mobil(0.5)] if f.mean() > 10 else []), [a, b], rotasi=(0,), ukuran=(640,))
    r = rows[0]
    assert (r.jumlah[0.30], r.frame[0.30]) == (2, 1)


# ----------------------------------------------------------------------------------------------------- kesimpulan
def test_kesimpulan_menunjuk_orientasi_bila_hanya_gambar_mendatar_yang_terlihat():
    rows, teks = jalankan(lambda f, s: [mobil(0.6)] if f.shape[1] > f.shape[0] else [])
    assert baris(rows, 0, 640).jumlah[0.30] == 0 and baris(rows, 90, 640).jumlah[0.30] > 0
    assert baris(rows, 270, 640).jumlah[0.30] > 0 and baris(rows, 180, 640).jumlah[0.30] == 0
    assert "tidak mendeteksi apa pun" in teks and "orientasi" in teks and "mendatar" in teks


def test_kesimpulan_menunjuk_ukuran_masukan_bila_hanya_imgsz_besar_yang_melihat():
    rows, teks = jalankan(lambda f, s: [mobil(0.5)] if s >= 960 else [])
    assert baris(rows, 0, 640).jumlah[0.30] == 0 and baris(rows, 0, 960).jumlah[0.30] == 1
    assert "ukuran masukan" in teks and "orientasi" not in teks


def test_kesimpulan_menyuruh_menaikkan_kepekaan_bila_model_ragu():
    rows, teks = jalankan(lambda f, s: [mobil(0.20)])
    assert baris(rows, 0, 640).jumlah[0.30] == 0 and baris(rows, 0, 640).jumlah[0.15] == 1
    assert "kurang yakin" in teks and "Kepekaan" in teks and "5" in teks


def test_kesimpulan_tidak_menyuruh_menaikkan_kepekaan_untuk_kotak_yang_nyaris_pasti_salah():
    rows, teks = jalankan(lambda f, s: [mobil(0.08)])
    assert "salah kenal" in teks and "yolo11s.pt" in teks and "Kepekaan" not in teks


def test_kesimpulan_bila_tidak_ada_apa_pun_menyebut_kemungkinan_dan_bobot_lebih_besar():
    rows, teks = jalankan(lambda f, s: [])
    assert "Tidak ada kendaraan" in teks and "render atau kartun" in teks and "yolo11m.pt" in teks


def test_kesimpulan_bila_bawaan_sudah_mendeteksi_mengalihkan_ke_zona_dan_status():
    rows, teks = jalankan(lambda f, s: [mobil(0.9)])
    assert "sudah mendeteksi" in teks and "bukan di model" in teks and "tidak mendeteksi apa pun" not in teks


# ----------------------------------------------------------------------------------------------------- berkas
def test_foto_menjadi_satu_frame_dan_video_menjadi_frame_yang_tersebar_rata(tmp_path):
    foto = tmp_path / "f.jpg"
    cv2.imwrite(str(foto), tegak())
    assert len(U.bingkai(foto, 5)) == 1

    video = tmp_path / "v.avi"
    w = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 10, (64, 48))
    if not w.isOpened():
        pytest.skip("OpenCV tidak bisa menulis video di lingkungan ini")
    for i in range(30):
        w.write(np.full((48, 64, 3), i * 8, np.uint8))
    w.release()
    got = U.bingkai(video, 4)
    assert len(got) == 4
    nilai = [int(f.mean()) for f in got]
    assert nilai == sorted(nilai) and nilai[0] < 20 and nilai[-1] > 200, f"frame harus dari awal sampai akhir: {nilai}"
    assert U.bingkai(tmp_path / "tidak-ada.mp4", 3) == []


def test_gambar_berkotak_disimpan_dari_kombinasi_terbaik_dengan_warna_menurut_keyakinan(tmp_path):
    img = tegak()
    rows = U.sweep(Palsu(lambda f, s: [mobil(0.6)] if f.shape[1] > f.shape[0] else []), [img])
    berkas = U.simpan(rows, [img], tmp_path / "hasil", 640)
    assert berkas and all(p.is_file() and p.stat().st_size > 0 for p in berkas)
    assert "putar90" in berkas[0].name or "putar270" in berkas[0].name, "kombinasi yang memuat kotak, bukan bawaan yang kosong"
    gambar = cv2.imread(str(berkas[0]))
    kiri, atas = 20, 30                                           # sudut kotak; garis hijau (BGR 80,220,100) pada keyakinan >= 0,30
    area = gambar[atas - 2:atas + 3, kiri - 2:kiri + 40].reshape(-1, 3).astype(int)
    assert (np.abs(area - np.array([80, 220, 100])).sum(axis=1) < 90).any(), "garis kotak hijau harus tergambar"


def test_main_dari_ujung_ke_ujung_mencetak_tabel_kesimpulan_dan_menyimpan_gambar(tmp_path, capsys):
    foto = tmp_path / "layar.jpg"
    cv2.imwrite(str(foto), tegak())
    keluar = tmp_path / "keluar"
    kode = U.main([str(foto), "--keluar", str(keluar)], detector=Palsu(lambda f, s: [mobil(0.6)] if f.shape[1] > f.shape[0] else []))
    out = capsys.readouterr().out
    assert kode == 0
    assert "bawaan aplikasi" in out and "putar" in out and "orientasi" in out
    assert "600x900" in out and len(list(keluar.glob("*.jpg"))) == 1


def test_main_berkas_tidak_ada_atau_bukan_gambar_berhenti_dengan_pesan_jelas(tmp_path, capsys):
    assert U.main([str(tmp_path / "tidak-ada.jpg")], detector=Palsu(lambda f, s: [])) == 2
    assert "tidak ditemukan" in capsys.readouterr().out
    teks = tmp_path / "catatan.txt"
    teks.write_text("bukan gambar")
    assert U.main([str(teks)], detector=Palsu(lambda f, s: [])) == 2
    assert "Tidak bisa membaca" in capsys.readouterr().out


def test_main_melaporkan_model_yang_tidak_bisa_dimuat_tanpa_traceback(tmp_path, capsys, monkeypatch):
    foto = tmp_path / "f.jpg"
    cv2.imwrite(str(foto), tegak())

    class Galat:
        def __init__(self, *a, **k): pass

        def load(self):
            raise U.DetectorUnavailable("Paket ultralytics belum terpasang.")

    monkeypatch.setattr(U, "VehicleDetector", Galat)
    assert U.main([str(foto)]) == 3
    assert "ultralytics belum terpasang" in capsys.readouterr().out

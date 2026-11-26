"""Uji kamera perangkat: pendaftaran, validasi, frame masuk, deteksi langsung, lama kejadian, laporan."""
from __future__ import annotations

import json
import threading
import time

import cv2
import numpy as np
import pytest
from support import ColorDetector, Client, FakeSegmenter, Phone, Street, jpeg, new_key

from sidewalk_sentinel.cameras import (MAX_CAMERAS, CameraManager, CameraSpec, apply_changes, jpeg_size)
from sidewalk_sentinel.server import make_server

ROI = [[0.0, 0.64], [1.0, 0.64], [1.0, 0.99], [0.0, 0.99]]


# =========================================================================== murni
def _spec(**kw):
    base = dict(id="a" * 12, device="b" * 24, name="Ponsel")
    return CameraSpec(**{**base, **kw})


def test_perubahan_valid_diterapkan_dan_yang_tidak_disebut_tetap():
    s = apply_changes(_spec(), {"name": "  Gerbang depan  ", "roi": ROI, "settings": {"park_seconds": 20, "conf": 0.4},
                                "enabled": False})
    assert (s.name, s.enabled, s.settings) == ("Gerbang depan", False, {"park_seconds": 20, "conf": 0.4})
    assert s.roi == [list(p) for p in ROI]
    assert apply_changes(s, {}).roi == s.roi and apply_changes(s, {"roi": None}).roi is None


@pytest.mark.parametrize("data", [
    {"name": ""}, {"name": "x" * 41}, {"name": None},
    {"roi": [[0, 0], [1, 1]]}, {"roi": [[0, 0], [1, 0], [2, 1]]}, {"roi": "bukan daftar"}, {"roi": [[0, 0], [1, "a"], [1, 1]]},
    {"settings": {"tidak_ada": 1}}, {"settings": {"park_seconds": 0}}, {"settings": "bukan objek"},
    {"settings": {"diff_floor": 20}}, {"settings": {"conf": 0.99}}, {"settings": {"conf": 0.01}},
])
def test_perubahan_tidak_valid_ditolak(data):
    with pytest.raises(ValueError):
        apply_changes(_spec(), data)


def test_nama_dibersihkan_dari_karakter_kendali():
    assert apply_changes(_spec(), {"name": "A\x00B\nC"}).name == "ABC"


def test_ukuran_jpeg_dibaca_dari_header_tanpa_mendekode():
    img = np.zeros((90, 160, 3), np.uint8)
    assert jpeg_size(jpeg(img)) == (160, 90)
    assert jpeg_size(b"bukan jpeg") is None and jpeg_size(b"\xff\xd8\xff\xd9") is None


def test_spec_dimuat_dengan_mengabaikan_kolom_lama_dan_menolak_yang_rusak():
    lama = {"id": "a" * 12, "device": "b" * 24, "name": "X", "source": "rtsp://x", "max_loops": 3, "speed": 2.0}
    assert CameraSpec.from_dict(lama).name == "X"
    for bad in ({"id": "zzz", "device": "b" * 24, "name": "X"}, {"id": "a" * 12, "name": "X"},
                {"id": "a" * 12, "device": "pendek", "name": "X"}):
        with pytest.raises((ValueError, TypeError)):
            CameraSpec.from_dict(bad)


# ============================================================================= manajer
@pytest.fixture()
def mgr(tmp_path):
    m = CameraManager(tmp_path, detector=ColorDetector())
    m.start()
    yield m
    m.shutdown()


def test_perangkat_yang_sama_mendapat_kamera_yang_sama(mgr):
    key = new_key()
    cam, created = mgr.register(key, "Laptop Windows")
    again, created2 = mgr.register(key, "nama lain tidak dipakai")
    assert created and not created2 and again is cam and cam.spec.name == "Laptop Windows"
    assert len(mgr.list()) == 1


@pytest.mark.parametrize("key", ["", None, "pendek", "G" * 24, "a" * 65, "a" * 24 + "\n", "../" + "a" * 24])
def test_kunci_perangkat_tak_valid_ditolak(mgr, key):
    with pytest.raises(ValueError):
        mgr.register(key, "X")


def test_nama_kembar_diberi_nomor_dan_nama_kosong_diganti_bawaan(mgr):
    a, _ = mgr.register(new_key(), "Ponsel")
    b, _ = mgr.register(new_key(), "Ponsel")
    c, _ = mgr.register(new_key(), "")
    assert (a.spec.name, b.spec.name, c.spec.name) == ("Ponsel", "Ponsel 2", "Perangkat")
    with pytest.raises(ValueError, match="sudah ada"):
        mgr.update(b.spec.id, {"name": "Ponsel"})


def test_batas_jumlah_perangkat(mgr):
    for i in range(MAX_CAMERAS):
        mgr.register(new_key(), f"P{i}")
    with pytest.raises(ValueError, match="batas"):
        mgr.register(new_key(), "kelebihan")
    existing = mgr.list()[0].spec.device
    assert mgr.register(existing, "")[1] is False, "perangkat yang sudah terdaftar tidak terhitung kelebihan"


def test_perangkat_dan_kejadian_dipulihkan_setelah_restart(tmp_path):
    m1 = CameraManager(tmp_path, detector=ColorDetector())
    m1.start()
    key = new_key()
    cam, _ = m1.register(key, "Gerbang")
    m1.update(cam.spec.id, {"roi": ROI, "settings": {"park_seconds": 7}})
    cam.events.append({"kind": "parkir", "label": "P", "reason": "uji", "track_id": 1}, b"\xff\xd8x")
    m1.shutdown()

    m2 = CameraManager(tmp_path, detector=ColorDetector())
    m2.start()
    try:
        back, created = m2.register(key, "")
        assert not created and back.spec.id == cam.spec.id
        assert back.spec.roi == [list(p) for p in ROI] and back.spec.settings == {"park_seconds": 7}
        assert back.events.seq == 1
    finally:
        m2.shutdown()


def test_berkas_perangkat_rusak_atau_berisi_entri_lama_tidak_menjatuhkan_server(tmp_path):
    (tmp_path / "devices.json").write_text("{bukan json", encoding="utf-8")
    m = CameraManager(tmp_path, detector=ColorDetector())
    m.start()
    assert m.list() == []
    m.shutdown()
    (tmp_path / "devices.json").write_text(json.dumps([
        {"id": "a" * 12, "source": "demo", "name": "lama"},                       # tanpa kunci perangkat: dibuang
        {"id": "c" * 12, "device": "d" * 24, "name": "sah", "source": "rtsp://x"},
        "bukan kamus", 42]), encoding="utf-8")
    m = CameraManager(tmp_path, detector=ColorDetector())
    m.start()
    try:
        assert [c.spec.name for c in m.list()] == ["sah"]
    finally:
        m.shutdown()


# ================================================================================ API
@pytest.fixture()
def srv(tmp_path):
    server, app = make_server("127.0.0.1", 0, tmp_path / "data", detector=ColorDetector(), sidewalk=FakeSegmenter())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield app, Client(app.port)
    app.close()
    server.shutdown()
    server.server_close()


def test_daftar_perangkat_membuat_lalu_melanjutkan(srv):
    app, c = srv
    key = new_key()
    st, a = c.req("POST", "/api/device", {"key": key, "name": "Laptop"})
    st2, b = c.req("POST", "/api/device", {"key": key, "name": "Laptop"})
    assert (st, st2) == (201, 200) and a["id"] == b["id"]
    assert a["status"] in ("perlu_area", "menunggu") and a["roi"] is None and a["enabled"] is True
    assert "device" not in a and "key" not in a, "kunci perangkat tidak dibocorkan lewat API"
    st, lst = c.req("GET", "/api/cams")
    assert [x["id"] for x in lst["cams"]] == [a["id"]]


def test_api_menolak_masukan_buruk(srv):
    app, c = srv
    assert c.req("POST", "/api/device", {"key": "pendek"})[0] == 400
    assert c.req("POST", "/api/device", {})[0] == 400
    assert c.req("GET", "/api/device")[0] == 405
    assert c.req("POST", "/api/cams", {"source": "demo"})[0] == 405, "tidak ada lagi jalur membuat kamera dari sumber lain"
    ph = Phone(c)
    ph.register()
    for body in ({"roi": [[0, 0], [1, 1]]}, {"name": ""}, {"settings": {"x": 1}}, {"settings": {"diff_floor": 20}}):
        assert c.req("POST", f"/api/cams/{ph.id}", body)[0] == 400, body
    assert c.req("POST", f"/api/cams/{ph.id}", b"{bukan json", {"Content-Type": "application/json"})[0] == 400
    assert c.req("GET", f"/api/cams/{'f' * 12}")[0] == 404
    assert c.req("GET", "/api/cams/tidak-valid")[0] == 404
    assert c.req("GET", "/api/cams/%s/snapshot.jpg" % ph.id)[0] == 404, "tidak ada siaran gambar server"
    assert c.req("GET", "/api/cams/%s/stream.mjpg" % ph.id)[0] == 404


def test_frame_menolak_masukan_buruk(srv):
    app, c = srv
    ph = Phone(c)
    ph.register()
    url = f"/api/cams/{ph.id}/frame"
    jpg_h = {"Content-Type": "image/jpeg"}
    assert c.req("POST", url, b"bukan gambar", jpg_h)[0] == 400
    assert c.req("POST", url, b"", jpg_h)[0] == 400
    assert c.req("POST", url, jpeg(np.zeros((8, 8, 3), np.uint8)), jpg_h)[0] == 400, "terlalu kecil"
    good = jpeg(np.zeros((90, 160, 3), np.uint8))
    i = good.index(b"\xff\xc0")                                                         # 'bom dekompresi': header menyatakan 30000x30000
    bomb = good[:i + 5] + (30000).to_bytes(2, "big") + (30000).to_bytes(2, "big") + good[i + 9:]
    st, err = c.req("POST", url, bomb, jpg_h)
    assert st == 400 and "terlalu besar" in err["error"], err
    assert c.req("POST", f"/api/cams/{'e' * 12}/frame", jpeg(np.zeros((90, 160, 3), np.uint8)), jpg_h)[0] == 404
    assert c.req("POST", url, jpeg(np.zeros((90, 160, 3), np.uint8)), jpg_h)[0] == 200


def test_tanpa_model_trotoar_perangkat_meminta_area_lalu_langsung_memantau_setelah_area_diset(tmp_path):
    server, app = make_server("127.0.0.1", 0, tmp_path / "data", detector=ColorDetector(), sidewalk=None)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        c = Client(app.port)
        ph = Phone(c)
        ph.register()
        street = Street()
        ph.stream(street.frame, 5, until=lambda s: s["status"] == "perlu_area")
        assert ph.state["zone"] == "manual" or ph.state["roi"] is None
        assert c.req("GET", f"/api/cams/{ph.id}")[1]["auto_available"] is False
        assert c.req("POST", f"/api/cams/{ph.id}", {"roi": ROI})[0] == 200
        ph.stream(street.frame, 10, until=lambda s: s["status"] == "memantau")      # tanpa fase belajar latar
        assert ph.state["tracks"] == [] and ph.state["counts_today"] == {"parkir": 0, "naik_trotoar": 0}
    finally:
        app.close()
        server.shutdown()
        server.server_close()


def test_zona_otomatis_ditemukan_sendiri_tanpa_langkah_menandai(srv):
    app, c = srv
    ph = Phone(c)
    ph.register()
    street = Street()
    assert c.req("GET", f"/api/cams/{ph.id}")[1]["auto_available"] is True
    ph.stream(street.frame, 15, until=lambda s: s["status"] == "memantau")
    assert ph.state["zone"] == "auto" and ph.state["roi"], "zona dari model langsung dipakai"
    ys = [p[1] for p in ph.state["roi"]]
    assert min(ys) == pytest.approx(0.62, abs=0.03) and max(ys) == pytest.approx(0.95, abs=0.03)
    cam = c.req("GET", f"/api/cams/{ph.id}")[1]
    assert cam["roi"] == ph.state["roi"] and cam["zone"] == "auto"
    assert app.cams.get(ph.id).spec.roi is None, "zona otomatis tidak disimpan sebagai zona manual"


def test_kendaraan_parkir_di_zona_otomatis_dicatat_tanpa_pernah_menandai_zona(srv):
    app, c = srv
    ph = Phone(c)
    ph.register()
    assert c.req("POST", f"/api/cams/{ph.id}", {"settings": {"park_seconds": 2}})[0] == 200
    street = Street()
    ph.stream(street.frame, 15, until=lambda s: s["status"] == "memantau")
    ph.stream(lambda: street.frame(vehicle=True), 25, until=lambda s: s["counts_today"]["parkir"] == 1)


def test_trotoar_tak_terlihat_berstatus_mencari_lalu_memantau_begitu_muncul(srv):
    app, c = srv
    app.sidewalk.roi = None
    ph = Phone(c)
    ph.register()
    street = Street()
    ph.stream(street.frame, 10, until=lambda s: s["status"] == "mencari_trotoar")
    assert ph.state["roi"] is None and "trotoar" in ph.state["message"].lower()
    app.sidewalk.roi = FakeSegmenter().roi                                    # kamera diarahkan ke trotoar
    ph.stream(street.frame, 15, until=lambda s: s["status"] == "memantau")


def test_zona_manual_menggantikan_otomatis_dan_bisa_kembali_ke_otomatis(srv):
    app, c = srv
    ph = Phone(c)
    ph.register()
    street = Street()
    ph.stream(street.frame, 15, until=lambda s: s["status"] == "memantau" and s["zone"] == "auto")
    assert c.req("POST", f"/api/cams/{ph.id}", {"roi": ROI})[0] == 200
    ph.stream(street.frame, 15, until=lambda s: s["status"] == "memantau" and s["zone"] == "manual")
    assert ph.state["roi"] == [list(p) for p in ROI]
    assert c.req("POST", f"/api/cams/{ph.id}", {"roi": None})[0] == 200
    ph.stream(street.frame, 15, until=lambda s: s["status"] == "memantau" and s["zone"] == "auto")


def test_model_trotoar_rusak_tidak_menjatuhkan_kamera_dan_meminta_zona_manual(srv):
    app, c = srv

    def rusak(frame, conf=0.25, imgsz=640):
        raise RuntimeError("bobot rusak")

    app.sidewalk.mask = rusak
    ph = Phone(c)
    ph.register()
    ph.stream(Street().frame, 10, until=lambda s: s["status"] == "perlu_area")
    assert "manual" in ph.state["message"]


def _durasi(c, ph):
    return c.req("GET", f"/api/cams/{ph.id}/events")[1]["events"][0].get("durasi_s")


def _siapkan(c, park_seconds=2):
    ph = Phone(c)
    ph.register()
    street = Street()
    assert c.req("POST", f"/api/cams/{ph.id}", {"roi": ROI, "settings": {"park_seconds": park_seconds}})[0] == 200
    ph.stream(street.frame, 20, until=lambda s: s["status"] == "memantau")
    return ph, street


def test_kendaraan_parkir_terdeteksi_langsung_lalu_durasinya_tercatat_saat_pergi(srv):
    app, c = srv
    ph, street = _siapkan(c)
    ph.stream(lambda: street.frame(vehicle=True), 25, until=lambda s: s["counts_today"]["parkir"] == 1)
    assert any(t["state"] in ("diam", "parkir") for t in ph.state["tracks"]), "jejak dikirim balik untuk digambar halaman"
    box = ph.state["tracks"][0]["box"]
    assert all(0 <= v <= 1 for v in box), "kotak dinormalisasi 0..1 agar halaman bisa menggambar di ukuran apa pun"

    ev = c.req("GET", f"/api/cams/{ph.id}/events")[1]["events"]
    assert [e["kind"] for e in ev] == ["parkir"] and "durasi_s" not in ev[0], "durasi baru diketahui saat kendaraan pergi"
    snap = c.req("GET", ev[0]["snapshot_url"], raw=True)
    assert snap[0].status == 200 and snap[1][:2] == b"\xff\xd8", "foto bukti tersedia"

    ph.stream(street.frame, 20, until=lambda s: _durasi(c, ph) is not None)
    dur = _durasi(c, ph)
    assert 1.5 <= dur <= 20, dur


def test_laporan_dan_csv_memuat_kejadian_dan_durasinya(srv):
    app, c = srv
    ph, street = _siapkan(c)
    ph.stream(lambda: street.frame(vehicle=True), 25, until=lambda s: s["counts_today"]["parkir"] == 1)
    ph.stream(street.frame, 20, until=lambda s: _durasi(c, ph) is not None)

    st, r = c.req("GET", "/api/report?periode=hari_ini")
    assert st == 200 and r["total"] == 1 and r["parkir"] == 1 and r["lama"]["semua"]["n"] == 1
    assert r["per_kamera"][0]["nama"] == "Ponsel uji" and r["terbaru"][0]["snapshot_url"]
    assert c.req("GET", f"/api/report?periode=7_hari&kamera={ph.id}")[1]["total"] == 1
    assert c.req("GET", "/api/report?periode=kemarin")[0] == 400
    assert c.req("GET", "/api/report?kamera=tidak-valid")[0] == 400
    assert c.req("GET", f"/api/report?kamera={'a' * 12}")[0] == 404

    st, body = c.req("GET", "/api/events.csv")
    text = body.decode("utf-8-sig")
    assert text.splitlines()[0] == "waktu,perangkat,jenis,alasan,nomor,durasi_detik"
    assert "Ponsel uji" in text and text.count("\n") == 2


def test_jeda_menghentikan_pemantauan_dan_lanjut_setelah_dilanjutkan(srv):
    app, c = srv
    ph, street = _siapkan(c)
    assert c.req("POST", f"/api/cams/{ph.id}", {"enabled": False})[0] == 200
    c.wait(lambda: c.req("GET", f"/api/cams/{ph.id}")[1]["status"] == "jeda", 5, "harus berstatus jeda")
    assert ph.push(street.frame())["status"] == "jeda"
    assert c.req("POST", f"/api/cams/{ph.id}", {"enabled": True})[0] == 200
    ph.stream(street.frame, 20, until=lambda s: s["status"] == "memantau" and s["enabled"])


def test_tangan_dan_benda_bukan_kendaraan_tidak_pernah_dicatat(srv):
    """Penyebab awal perubahan ke YOLO: pengurang latar menganggap tangan sebagai kendaraan. Di sini detektor hanya
    melihat 'kendaraan' berwarna; benda abu-abu besar di trotoar (tangan, orang) tidak menghasilkan kotak."""
    app, c = srv
    ph, street = _siapkan(c, park_seconds=1)

    def benda():
        img = street.frame()
        cv2.rectangle(img, (280, 250), (380, 340), (150, 160, 175), -1)      # 'tangan': besar, di trotoar, abu-abu
        return img

    ph.stream(benda, 8)
    assert ph.state["tracks"] == [] and ph.state["counts_today"] == {"parkir": 0, "naik_trotoar": 0}


def test_usulan_zona_dari_model_trotoar_dan_semua_keadaan_gagalnya(srv):
    app, c = srv
    ph = Phone(c)
    ph.register()
    url = f"/api/cams/{ph.id}/propose-zone"
    st, err = c.req("POST", url)
    assert st == 409 and "gambar" in err["error"], "belum ada gambar dari perangkat"
    ph.push(Street().frame())
    st, out = c.req("POST", url)
    assert st == 200 and out["roi"] == app.sidewalk.roi and 0 < out["coverage"] <= 1
    app.sidewalk.roi = None                                                   # trotoar tidak terlihat
    st, out = c.req("POST", url)
    assert st == 200 and out["roi"] is None and "manual" in out["message"]
    assert c.req("GET", url)[0] == 404, "hanya POST yang dikenal"
    assert c.req("POST", f"/api/cams/{'f' * 12}/propose-zone")[0] == 404
    app.sidewalk = app.cams.sidewalk = None
    app.cams.get(ph.id).sidewalk = None
    st, err = c.req("POST", url)
    assert st == 503 and "manual" in err["error"], "model trotoar tidak ada"


def test_status_model_memuat_galat_lalu_siap_tampil_di_kamera_dan_info(srv):
    app, c = srv
    det = app.detector
    det.state, det.message = "memuat", "Memuat model YOLO"
    ph = Phone(c)
    ph.register()
    street = Street()
    assert c.req("POST", f"/api/cams/{ph.id}", {"roi": ROI})[0] == 200
    ph.stream(street.frame, 10, until=lambda s: s["status"] == "memuat")
    assert ph.state["detector"]["state"] == "memuat"
    assert c.req("GET", "/api/info")[1]["detector"] == {"state": "memuat", "message": "Memuat model YOLO",
                                                         "weights": "tiruan.pt", "sidewalk_model": True}

    det.state, det.message = "galat", "Paket ultralytics belum terpasang. Jalankan: pip install ultralytics"
    ph.stream(street.frame, 10, until=lambda s: s["status"] == "model_galat")
    assert "pip install ultralytics" in ph.state["message"], "alasan galat sampai ke pengguna"
    assert ph.state["tracks"] == [] and ph.state["counts_today"] == {"parkir": 0, "naik_trotoar": 0}

    assert c.req("POST", "/api/detector/retry")[0] == 200 and det.retried == 1
    assert c.req("GET", "/api/detector/retry")[0] == 405
    ph.stream(street.frame, 10, until=lambda s: s["status"] == "memantau")


def test_pengaturan_tersimpan_dari_versi_lama_tidak_menjatuhkan_pemantauan(tmp_path):
    key = new_key()
    (tmp_path / "devices.json").write_text(json.dumps([{
        "id": "a" * 12, "device": key, "name": "Lama", "roi": ROI, "learn": "normal",
        "settings": {"diff_floor": 20, "min_area_frac": 0.002, "filter_pedestrians": True, "park_seconds": 7}}]),
        encoding="utf-8")
    m = CameraManager(tmp_path, detector=ColorDetector())
    m.start()
    try:
        cam = m.list()[0]
        t0 = time.time()
        while cam.status != "memantau" and time.time() < t0 + 10:
            cam.push(jpeg(Street().frame()))
            time.sleep(0.15)
        assert cam.status == "memantau", cam.message                       # kembali ke pengaturan bawaan
    finally:
        m.shutdown()


def test_menghapus_perangkat_membersihkan_data_dan_frame_berikutnya_404(srv):
    app, c = srv
    ph, street = _siapkan(c)
    folder = app.cams.get(ph.id).dir
    assert folder.exists()
    assert c.req("DELETE", f"/api/cams/{ph.id}")[1] == {"deleted": True}
    assert not folder.exists() and c.req("GET", "/api/cams")[1]["cams"] == []
    st, _ = c.req("POST", f"/api/cams/{ph.id}/frame", jpeg(street.frame()), {"Content-Type": "image/jpeg"})
    assert st == 404, "halaman memakai 404 ini untuk mendaftar ulang"
    assert Phone(c, key=ph.key).register()["id"] != ph.id


def test_berhenti_mengirim_lalu_kembali_melanjutkan_pemantauan(srv):
    app, c = srv
    ph, street = _siapkan(c)
    c.wait(lambda: c.req("GET", f"/api/cams/{ph.id}")[1]["status"] == "menunggu", 8, "tanpa frame, kamera menunggu perangkat")
    ph.stream(street.frame, 6, until=lambda s: s["status"] == "memantau")


def test_dua_perangkat_dipantau_terpisah_dan_laporan_menggabungkannya(srv):
    app, c = srv
    a, street = _siapkan(c)
    b = Phone(c, name="Laptop uji")
    b.register()
    assert a.id != b.id
    cam_a = app.cams.get(a.id)
    cam_a.events.append({"kind": "parkir", "label": "P", "reason": "uji", "track_id": 1}, None)
    cam_b = app.cams.get(b.id)
    cam_b.events.append({"kind": "naik_trotoar", "label": "N", "reason": "uji", "track_id": 1}, None)
    r = c.req("GET", "/api/report?periode=hari_ini")[1]
    assert r["total"] == 2 and {x["nama"] for x in r["per_kamera"]} == {"Ponsel uji", "Laptop uji"}
    assert c.req("GET", f"/api/report?periode=hari_ini&kamera={b.id}")[1]["naik_trotoar"] == 1
    feed = c.req("GET", "/api/events?limit=10")[1]["events"]
    assert {e["camera"]["name"] for e in feed} == {"Ponsel uji", "Laptop uji"}
    assert c.req("GET", "/api/events?kind=salah")[0] == 400


def test_nama_perangkat_berawalan_rumus_dinetralkan_di_csv(srv):
    app, c = srv
    ph = Phone(c, name="=HYPERLINK(\"http://x\")")
    ph.register()
    app.cams.get(ph.id).events.append({"kind": "parkir", "label": "P", "reason": "uji", "track_id": 1}, None)
    text = c.req("GET", "/api/events.csv")[1].decode("utf-8-sig")
    assert "'=HYPERLINK" in text and ",=HYPERLINK" not in text


def test_api_tertutup_tanpa_kunci_pada_mode_lan(tmp_path):
    server, app = make_server("127.0.0.1", 0, tmp_path / "data", lan=True, token="kunci-uji", detector=ColorDetector())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        anon = Client(app.port)
        for method, path in (("GET", "/api/report"), ("GET", "/api/cams"), ("GET", "/api/events.csv"), ("POST", "/api/device")):
            assert anon.req(method, path, {"key": new_key()} if method == "POST" else None)[0] == 403, path
        authed = Client(app.port, cookie="ss_key=kunci-uji")
        assert authed.req("GET", "/api/report")[1]["total"] == 0
        assert authed.req("POST", "/api/device", {"key": new_key(), "name": "HP"})[0] == 201
    finally:
        app.close()
        server.shutdown()
        server.server_close()


def test_semua_perangkat_dihentikan_bersih_saat_server_ditutup(tmp_path):
    server, app = make_server("127.0.0.1", 0, tmp_path / "data", detector=ColorDetector())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    c = Client(app.port)
    phones = [Phone(c, name=f"HP{i}") for i in range(3)]
    for p in phones:
        p.register()
    threads = [cam._thread for cam in app.cams.list()]
    app.close()
    server.shutdown()
    server.server_close()
    assert all(not t.is_alive() for t in threads), "utas kamera harus berhenti saat aplikasi ditutup"


def test_id_dan_kunci_tidak_menerima_baris_baru_di_ujung():
    from sidewalk_sentinel.util import valid_id
    assert valid_id("a" * 12) and not valid_id("a" * 12 + "\n") and not valid_id("")

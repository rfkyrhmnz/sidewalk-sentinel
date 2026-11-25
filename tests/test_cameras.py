"""Uji kamera server (mode CCTV): sumber, penyimpanan kejadian, pekerja, dan API."""
from __future__ import annotations

import http.client
import json
import logging
import threading
import time
from datetime import timedelta

import pytest

from sidewalk_sentinel import cameras as cam_mod
from sidewalk_sentinel.cameras import (EventStore, SourceError, build_spec, mask_source, parse_source,
                                       source_kind)
from sidewalk_sentinel.server import App, make_server
from sidewalk_sentinel.synthetic import ROI


# =========================================================================== murni
@pytest.mark.parametrize("text,kind", [
    ("demo", "demo"), ("device:0", "device"), ("device:9", "device"),
    ("upload:" + "a" * 12, "upload"),
    ("rtsp://admin:sandi@192.168.1.5:554/stream1", "url"), ("RTSP://10.0.0.2/x", "url"),
    ("http://cam.local/mjpeg", "url"), ("https://cam.example/live.m3u8", "url"),
])
def test_sumber_yang_sah_diterima(text, kind):
    assert parse_source(text).kind == kind == source_kind(text)


@pytest.mark.parametrize("text", [
    "", "   ", "file:///etc/passwd", "file:C:\\video.mp4", "/etc/passwd", "C:\\Users\\x\\v.mp4", "../../x.mp4",
    "ftp://host/x", "javascript:alert(1)", "rtsp://", "http://", "device:10", "device:-1", "device:",
    "upload:abc", "upload:../../etc/passwd", "rtsp://h/x\nGET /", "http://h/" + "a" * 600,
])
def test_sumber_berbahaya_atau_rusak_ditolak(text):
    with pytest.raises(ValueError):
        parse_source(text)


def test_sumber_berkas_hanya_untuk_operator(tmp_path):
    f = tmp_path / "rekaman.mp4"
    f.write_bytes(b"x")
    with pytest.raises(ValueError, match="baris perintah"):
        parse_source(f"file:{f}")
    assert parse_source(f"file:{f}", trusted=True).kind == "file"
    with pytest.raises(ValueError, match="tidak ada"):
        parse_source(f"file:{tmp_path / 'hilang.mp4'}", trusted=True)


def test_kredensial_disamarkan():
    m = mask_source("rtsp://admin:s3cr3t@10.0.0.2:554/live?token=abc123&x=1&password=hunter2")
    assert "s3cr3t" not in m and "abc123" not in m and "hunter2" not in m and "admin" not in m
    assert "10.0.0.2:554/live" in m and "x=1" in m
    assert mask_source("http://cam.local/mjpeg") == "http://cam.local/mjpeg"


def test_build_spec_validasi():
    spec = build_spec({"name": "  Depan  ", "source": "device:0"})
    assert spec.name == "Depan" and spec.roi is None and spec.learn == "normal" and spec.enabled
    for bad in ({"name": "", "source": "device:0"}, {"name": "x" * 41, "source": "device:0"},
                {"name": "ok", "source": "device:0", "roi": [[0, 0], [1, 1]]},
                {"name": "ok", "source": "device:0", "roi": [[0, 0], [2, 0], [1, 1]]},
                {"name": "ok", "source": "device:0", "settings": {"tidak_ada": 1}},
                {"name": "ok", "source": "device:0", "settings": {"park_seconds": 0}},
                {"name": "ok", "source": "device:0", "learn": "sembarang"}):
        with pytest.raises(ValueError):
            build_spec(bad)
    # nama dibersihkan dari karakter kontrol
    assert build_spec({"name": "Dep\x00an\n", "source": "device:0"}).name == "Depan"


def test_build_spec_demo_dan_pembaruan():
    demo = build_spec({"source": "demo"})
    assert demo.name == "Kamera demo" and demo.roi and demo.learn == "cepat" and demo.max_loops == 2
    assert demo.settings == {"park_seconds": 5}
    upd = build_spec({"name": "Baru", "roi": None}, existing=demo)
    assert upd.id == demo.id and upd.name == "Baru" and upd.roi is None
    with pytest.raises(ValueError, match="tidak bisa diubah"):
        build_spec({"source": "device:1"}, existing=demo)
    assert build_spec({"source": "demo", "name": "X"}, existing=demo).name == "X"      # sama = boleh
    # kamera jaringan tidak punya max_loops/speed
    assert build_spec({"name": "J", "source": "http://h/x", "max_loops": 5}).max_loops is None


# ================================================================== penyimpanan kejadian
EV = {"kind": "parkir", "label": "PARKIR DI TROTOAR", "reason": "diam 5 detik", "track_id": 1}


def test_event_store_menyimpan_dan_dipulihkan(tmp_path):
    s = EventStore(tmp_path / "c1")
    r1 = s.append(EV, b"\xff\xd8jpg1")
    r2 = s.append({**EV, "kind": "naik_trotoar", "label": "NAIK TROTOAR"}, None)
    assert (r1["no"], r2["no"]) == (1, 2) and r2["snapshot"] is None
    assert s.snapshot_path(1).read_bytes() == b"\xff\xd8jpg1" and s.snapshot_path(2) is None
    s2 = EventStore(tmp_path / "c1")                       # restart
    assert [r["no"] for r in s2.records] == [1, 2] and s2.seq == 2
    assert s2.append(EV, None)["no"] == 3                  # penomoran lanjut
    today = s2.counts_since(s2._dts[0].replace(hour=0, minute=0, second=0))
    assert today == {"parkir": 2, "naik_trotoar": 1}


def test_event_store_toleran_baris_rusak(tmp_path):
    s = EventStore(tmp_path / "c")
    s.append(EV, None)
    with open(s._file, "a", encoding="utf-8") as fh:
        fh.write('{"no": 2, "ts": "bukan-waktu"}\n{terpotong karena mati lis')
    again = EventStore(tmp_path / "c")
    assert [r["no"] for r in again.records] == [1]
    assert again.append(EV, None)["no"] == 2


def test_event_store_retensi_dan_nomor_tidak_dipakai_ulang(tmp_path):
    s = EventStore(tmp_path / "c")
    for _ in range(5):
        s.append(EV, b"jpg")
    assert s.prune(s._dts[0] - timedelta(days=1), max_events=3) == 2          # batas jumlah
    assert [r["no"] for r in s.records] == [3, 4, 5]
    assert s.snapshot_path(1) is None and s.snapshot_path(5) is not None
    assert s.prune(s._dts[-1] + timedelta(days=1), max_events=100) == 3        # lewat batas umur
    assert s.records == [] and list(s.events_dir.iterdir()) == []
    # setelah semuanya dipangkas dan server restart, nomor TIDAK mundur ke 1 (cache foto browser)
    assert EventStore(tmp_path / "c").append(EV, None)["no"] == 6


def test_event_store_halaman_dan_after(tmp_path):
    s = EventStore(tmp_path / "c")
    for i in range(6):
        s.append({**EV, "kind": "parkir" if i % 2 == 0 else "naik_trotoar"}, None)
    assert [r["no"] for r in s.latest(3)] == [6, 5, 4]
    assert [r["no"] for r in s.latest(3, before_no=4)] == [3, 2, 1]
    assert [r["no"] for r in s.latest(10, kind="parkir")] == [5, 3, 1]
    assert [r["no"] for r in s.after(4)] == [5, 6]


# ============================================================================== server
class Client:
    def __init__(self, port, cookie=None):
        self.port, self.cookie = port, cookie

    def req(self, method, path, body=None, raw=False):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        h = {"Cookie": self.cookie} if self.cookie else {}
        if isinstance(body, (dict, list)):
            body, h["Content-Type"] = json.dumps(body).encode(), "application/json"
        conn.request(method, path, body=body, headers=h)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        if raw:
            return resp, data
        try:
            return resp.status, json.loads(data) if data else None
        except ValueError:
            return resp.status, data


def wait_for(pred, timeout=60.0, every=0.2, msg="kondisi tidak tercapai"):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = pred()
        if v:
            return v
        time.sleep(every)
    raise AssertionError(msg)


def cam_info(c, cid):
    st, d = c.req("GET", f"/api/cams/{cid}")
    assert st == 200, d
    return d


@pytest.fixture(scope="module")
def srv(tmp_path_factory):
    server, app = make_server("127.0.0.1", 0, tmp_path_factory.mktemp("data"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield app, Client(app.port)
    app.close()
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def demo_selesai(srv):
    """Kamera demo yang diputar sekali secepat mungkin: dipakai bersama oleh beberapa tes."""
    app, c = srv
    st, d = c.req("POST", "/api/cams", {"source": "demo", "speed": 0, "max_loops": 1})
    assert st == 201, d
    wait_for(lambda: cam_info(c, d["id"])["status"] == "selesai", 90, msg="demo tidak selesai")
    return cam_info(c, d["id"])


def test_kamera_demo_belajar_sendiri_dan_mencatat_kejadian(srv, demo_selesai):
    app, c = srv
    d = demo_selesai
    assert d["counts_today"] == {"parkir": 1, "naik_trotoar": 2} and d["seq"] == 3
    assert d["relearns"] == 0 and d["learn_progress"] == {"have": 8, "need": 8}
    st, ev = c.req("GET", f"/api/cams/{d['id']}/events?limit=10")
    assert st == 200 and ev["seq"] == 3
    assert sorted(e["kind"] for e in ev["events"]) == ["naik_trotoar", "naik_trotoar", "parkir"]
    assert all(e["snapshot_url"].startswith(f"/api/cams/{d['id']}/events/") for e in ev["events"])
    resp, jpg = c.req("GET", ev["events"][0]["snapshot_url"], raw=True)
    assert resp.status == 200 and jpg[:2] == b"\xff\xd8" and "max-age" in resp.getheader("Cache-Control")
    # polling berbasis nomor: hanya yang lebih baru dari 'after'
    st, newer = c.req("GET", f"/api/cams/{d['id']}/events?after=2")
    assert [e["no"] for e in newer["events"]] == [3]


def test_umpan_kejadian_lintas_kamera_dan_csv(srv, demo_selesai):
    app, c = srv
    st, feed = c.req("GET", "/api/events?limit=50")
    assert st == 200 and len(feed["events"]) >= 3
    assert feed["events"][0]["camera"]["id"] and feed["events"][0]["ts"] >= feed["events"][-1]["ts"]
    st, p1 = c.req("GET", "/api/events?limit=2&offset=0")
    st, p2 = c.req("GET", "/api/events?limit=2&offset=2")
    keys = [(e["camera"]["id"], e["no"]) for e in p1["events"] + p2["events"]]
    assert len(keys) == len(set(keys)), "halaman tidak boleh tumpang tindih"
    assert c.req("GET", "/api/events?offset=abc")[0] == 400
    st, only = c.req("GET", "/api/events?kind=parkir")
    assert st == 200 and all(e["kind"] == "parkir" for e in only["events"])
    assert c.req("GET", "/api/events?kind=sembarang")[0] == 400
    resp, body = c.req("GET", "/api/events.csv", raw=True)
    text = body.decode("utf-8-sig")
    assert resp.status == 200 and text.splitlines()[0] == "waktu,kamera,jenis,alasan,nomor"
    assert "PARKIR DI TROTOAR" in text and "Kamera demo" in text
    assert "attachment" in resp.getheader("Content-Disposition")


def test_foto_bukti_berisi_nama_kamera_dan_jam(srv, demo_selesai):
    import cv2
    import numpy as np
    app, c = srv
    f = app.cams.get(demo_selesai["id"]).events.snapshot_path(1)
    img = cv2.imdecode(np.frombuffer(f.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    assert img is not None and img.shape[1] == 640          # frame beranotasi pada lebar kerja


def test_snapshot_dan_siaran_mjpeg(srv):
    app, c = srv
    st, d = c.req("POST", "/api/cams", {"source": "demo", "speed": 1, "max_loops": None})
    cid = d["id"]
    try:
        wait_for(lambda: cam_info(c, cid)["status"] == "memantau", 30)
        resp, jpg = c.req("GET", f"/api/cams/{cid}/snapshot.jpg", raw=True)
        assert resp.status == 200 and jpg[:2] == b"\xff\xd8" and resp.getheader("Content-Type") == "image/jpeg"
        resp, raw = c.req("GET", f"/api/cams/{cid}/snapshot.jpg?raw=1", raw=True)
        assert resp.status == 200 and raw[:2] == b"\xff\xd8" and raw != jpg

        conn = http.client.HTTPConnection("127.0.0.1", c.port, timeout=20)
        conn.request("GET", f"/api/cams/{cid}/stream.mjpg")
        r = conn.getresponse()
        assert r.status == 200 and "multipart/x-mixed-replace" in r.getheader("Content-Type")
        frames = []
        while len(frames) < 3:
            if r.fp.readline().strip() != b"--ssframe":
                continue
            hdr = {}
            while (line := r.fp.readline().strip()):
                k, _, v = line.partition(b":")
                hdr[k.lower()] = v.strip()
            frames.append(r.fp.read(int(hdr[b"content-length"])))
        conn.close()
        assert all(f[:2] == b"\xff\xd8" for f in frames) and len(set(frames)) >= 2, "siaran harus berganti gambar"
    finally:
        c.req("DELETE", f"/api/cams/{cid}")


def test_penonton_sejak_awal_tidak_menjatuhkan_sesi_saat_belajar_selesai(srv):
    """Regresi: frame yang menuntaskan belajar latar belum diproses engine. Dengan penonton terpasang, _apply
    meminta gambar beranotasi saat engine.last masih kosong; sesi crash lalu belajar ulang tanpa akhir."""
    app, c = srv
    st, d = c.req("POST", "/api/cams", {"source": "demo", "speed": 1, "max_loops": None})
    cid = d["id"]
    try:
        seen = set()
        t0 = time.time()
        while time.time() - t0 < 30:
            c.req("GET", f"/api/cams/{cid}/snapshot.jpg", raw=True)       # penonton aktif sepanjang belajar
            info = cam_info(c, cid)
            seen.add(info["status"])
            if info["status"] == "memantau":
                break
            time.sleep(0.1)
        assert "memantau" in seen, f"tidak pernah memantau: {seen}"
        assert "terputus" not in seen, f"sesi jatuh saat belajar selesai: {seen}"
        time.sleep(1.0)
        assert cam_info(c, cid)["status"] == "memantau"
    finally:
        c.req("DELETE", f"/api/cams/{cid}")


def test_siaran_kamera_statis_mengirim_dua_bagian_dengan_cepat(srv, demo_selesai):
    """Regresi: Chrome baru menggambar satu bagian multipart ketika bagian berikutnya mulai datang. Kamera statis
    (selesai/jeda) tak punya frame baru, jadi dulu gambarnya baru tampil setelah ~3 dtk penanda hidup."""
    app, c = srv
    conn = http.client.HTTPConnection("127.0.0.1", c.port, timeout=10)
    conn.request("GET", f"/api/cams/{demo_selesai['id']}/stream.mjpg")
    r = conn.getresponse()
    assert r.status == 200
    t0 = time.time()
    parts = []
    while len(parts) < 2:
        line = r.fp.readline().strip().lower()
        if line.startswith(b"content-length:"):
            n = int(line.split(b":")[1])
            r.fp.readline()                                  # baris kosong pemisah header
            parts.append(r.fp.read(n))
    elapsed = time.time() - t0
    conn.close()
    assert elapsed < 1.5, f"bagian kedua baru tiba setelah {elapsed:.1f} dtk"
    assert all(p[:2] == b"\xff\xd8" for p in parts)


def test_rekaman_selesai_dan_jeda_menerbitkan_frame_terakhir(srv, monkeypatch):
    """Regresi: tanpa penonton gambar jarang diperbarui, jadi rekaman cepat selesai dengan gambar masa belajar
    ("BELAJAR LATAR 1/8") yang basi. Penonton ditiru: hanya gambar pertama yang terbit dengan sendirinya."""
    from sidewalk_sentinel import cameras
    monkeypatch.setattr(cameras.Camera, "_want_image", lambda self: self._jpg is None)
    app, c = srv
    st, d = c.req("POST", "/api/cams", {"source": "demo", "speed": 0, "max_loops": 1})
    cid = d["id"]
    try:
        cam = app.cams.get(cid)
        wait_for(lambda: cam._jpg is not None, 20)
        first = cam._jpg
        wait_for(lambda: cam_info(c, cid)["status"] == "selesai", 90, msg="demo tidak selesai")
        assert cam._jpg != first, "gambar saat selesai masih gambar masa belajar"
        final = cam._jpg
        assert c.req("GET", f"/api/cams/{cid}/snapshot.jpg", raw=True)[1] == final

        # jeda memotret keadaan terakhir juga
        st, d2 = c.req("POST", "/api/cams", {"source": "demo", "speed": 1, "max_loops": None})
        cid2 = d2["id"]
        try:
            cam2 = app.cams.get(cid2)
            wait_for(lambda: cam_info(c, cid2)["status"] == "memantau", 30)
            before = cam2._jpg
            c.req("POST", f"/api/cams/{cid2}", {"enabled": False})
            wait_for(lambda: cam_info(c, cid2)["status"] == "jeda", 10)
            assert cam2._jpg != before, "jeda tidak menerbitkan frame terakhir"
        finally:
            c.req("DELETE", f"/api/cams/{cid2}")
    finally:
        c.req("DELETE", f"/api/cams/{cid}")


def test_jeda_lanjut_dan_kalibrasi_ulang(srv):
    app, c = srv
    st, d = c.req("POST", "/api/cams", {"source": "demo", "speed": 1, "max_loops": None})
    cid = d["id"]
    try:
        wait_for(lambda: cam_info(c, cid)["status"] == "memantau", 30)
        assert c.req("POST", f"/api/cams/{cid}/recalibrate")[0] == 200
        wait_for(lambda: cam_info(c, cid)["status"] == "belajar", 10, msg="harus belajar ulang")
        wait_for(lambda: cam_info(c, cid)["status"] == "memantau", 30, msg="harus kembali memantau")
        assert c.req("POST", f"/api/cams/{cid}", {"enabled": False})[0] == 200
        wait_for(lambda: cam_info(c, cid)["status"] == "jeda", 10, msg="harus dijeda")
        assert c.req("POST", f"/api/cams/{cid}", {"name": "Ganti nama saat jeda"})[1]["status"] in ("jeda",)
        c.req("POST", f"/api/cams/{cid}", {"enabled": True})
        wait_for(lambda: cam_info(c, cid)["status"] in ("belajar", "memantau"), 15, msg="harus lanjut")
        assert cam_info(c, cid)["name"] == "Ganti nama saat jeda"
    finally:
        c.req("DELETE", f"/api/cams/{cid}")


def test_pembaruan_roi_membuat_kamera_mulai_memantau(srv):
    """Alur tambah kamera: tanpa area -> 'perlu_area' dengan frame mentah; setelah area diberikan -> belajar."""
    app, c = srv
    st, d = c.req("POST", "/api/cams", {"name": "Tanpa area", "source": "demo", "roi": None, "speed": 1,
                                        "max_loops": None, "learn": "cepat"})
    assert st == 201, d
    cid = d["id"]
    try:
        wait_for(lambda: cam_info(c, cid)["status"] == "perlu_area", 20)
        resp, raw = c.req("GET", f"/api/cams/{cid}/snapshot.jpg?raw=1", raw=True)
        assert resp.status == 200 and raw[:2] == b"\xff\xd8"
        st, upd = c.req("POST", f"/api/cams/{cid}", {"roi": [list(p) for p in ROI]})
        assert st == 200 and upd["roi"]
        wait_for(lambda: cam_info(c, cid)["status"] in ("belajar", "memantau"), 15)
    finally:
        c.req("DELETE", f"/api/cams/{cid}")


def test_sumber_unggahan_diputar_dan_menghasilkan_kejadian(srv, tmp_path):
    app, c = srv
    video = app.ensure_demo()["path"]
    resp, body = c.req("POST", "/api/upload?name=rekaman.mp4", body=video.read_bytes(), raw=True)
    assert resp.status == 201, body
    uid = json.loads(body)["upload_id"]
    st, d = c.req("POST", "/api/cams", {"name": "Dari unggahan", "source": f"upload:{uid}", "roi": [list(p) for p in ROI],
                                        "speed": 0, "settings": {"park_seconds": 5}})
    assert st == 201, d
    try:
        wait_for(lambda: cam_info(c, d["id"])["status"] == "selesai", 90)
        assert cam_info(c, d["id"])["counts_today"] == {"parkir": 1, "naik_trotoar": 2}
    finally:
        c.req("DELETE", f"/api/cams/{d['id']}")
    # unggahan yang tidak ada: kamera terputus dengan alasan jelas, bukan crash
    st, d = c.req("POST", "/api/cams", {"name": "Hilang", "source": "upload:" + "0" * 12})
    try:
        info = wait_for(lambda: (i := cam_info(c, d["id"]))["status"] == "terputus" and i, 15)
        assert "unggahan" in info["message"]
    finally:
        c.req("DELETE", f"/api/cams/{d['id']}")


# ==================================================================== validasi dan keamanan
def test_api_menolak_masukan_buruk(srv):
    app, c = srv
    bad = [
        {"source": "file:///etc/passwd", "name": "x"}, {"source": "/etc/passwd", "name": "x"},
        {"source": "ftp://h/x", "name": "x"}, {"name": "x"}, {"source": "device:0"},
        {"source": "device:0", "name": "x", "roi": [[0, 0], [1, 0]]},
        {"source": "device:0", "name": "x", "settings": {"nama_ngawur": 1}},
    ]
    for body in bad:
        st, d = c.req("POST", "/api/cams", body)
        assert st == 400 and "error" in d, (body, st, d)
    assert c.req("POST", "/api/cams", body=b"bukan json", raw=True)[0].status == 400
    assert c.req("GET", "/api/cams/tidak-valid")[0] == 404
    assert c.req("GET", f"/api/cams/{'a' * 12}")[0] == 404
    assert c.req("GET", f"/api/cams/{'a' * 12}/snapshot.jpg")[0] == 404
    assert c.req("GET", "/api/cams/../../etc/passwd")[0] in (400, 404)
    assert [i for i in app.cams.list() if i.spec.source.startswith("file")] == []


def test_ubah_sumber_ditolak_dan_hapus_membersihkan(srv):
    app, c = srv
    st, d = c.req("POST", "/api/cams", {"name": "Sementara", "source": "device:3"})
    cid = d["id"]
    st, e = c.req("POST", f"/api/cams/{cid}", {"source": "device:4"})
    assert st == 400 and "tidak bisa diubah" in e["error"]
    folder = app.cams.get(cid).dir
    assert folder.is_dir()
    assert c.req("DELETE", f"/api/cams/{cid}")[1] == {"deleted": True}
    assert not folder.exists() and c.req("GET", f"/api/cams/{cid}")[0] == 404
    assert cid not in (app.cams.file.read_text(encoding="utf-8"))


def test_sandi_rtsp_tidak_bocor_dan_kegagalan_dijelaskan(srv, caplog):
    app, c = srv
    caplog.set_level(logging.DEBUG, logger="sidewalk")
    src = "rtsp://admin:s3cr3t@127.0.0.1:9/live?token=abc123"
    st, d = c.req("POST", "/api/cams", {"name": "CCTV palsu", "source": src})
    assert st == 201, d
    cid = d["id"]
    try:
        info = wait_for(lambda: (i := cam_info(c, cid))["status"] == "terputus" and i, 60, msg="harus terputus")
        assert "tidak bisa terhubung" in info["message"] and "Mencoba lagi" in info["message"]
        everything = json.dumps(c.req("GET", "/api/cams")[1]) + json.dumps(info) + caplog.text
        assert "s3cr3t" not in everything and "abc123" not in everything
        assert info["source"] == "rtsp://***@127.0.0.1:9/live?token=***"
        # belum ada frame sungguhan: editor area tidak boleh menerima kartu status sebagai "frame"
        assert c.req("GET", f"/api/cams/{cid}/snapshot.jpg?raw=1", raw=True)[0].status == 503
        resp, card = c.req("GET", f"/api/cams/{cid}/snapshot.jpg", raw=True)
        assert resp.status == 200 and card[:2] == b"\xff\xd8"           # tetap ada gambar untuk ubin dasbor
    finally:
        c.req("DELETE", f"/api/cams/{cid}")


def test_batas_jumlah_kamera(srv):
    app, c = srv
    made = []
    try:
        for i in range(cam_mod.MAX_CAMERAS - len(app.cams.list())):
            st, d = c.req("POST", "/api/cams", {"name": f"K{i}", "source": "http://127.0.0.1:9/x"})
            assert st == 201, d
            made.append(d["id"])
        st, d = c.req("POST", "/api/cams", {"name": "berlebih", "source": "http://127.0.0.1:9/x"})
        assert st == 400 and "batas" in d["error"]
    finally:
        for cid in made:
            c.req("DELETE", f"/api/cams/{cid}")


def test_mode_lan_mewajibkan_kunci_untuk_api_kamera(tmp_path):
    server, app = make_server("127.0.0.1", 0, tmp_path / "lan", lan=True, token="kunci123")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        anon, authed = Client(app.port), Client(app.port, "ss_key=kunci123")
        for path in ("/api/cams", "/api/events", "/api/events.csv", f"/api/cams/{'a' * 12}/stream.mjpg"):
            assert anon.req("GET", path)[0] == 403, path
        assert anon.req("POST", "/api/cams", {"source": "demo"})[0] == 403
        assert authed.req("GET", "/api/cams")[0] == 200
    finally:
        app.close()
        server.shutdown()
        server.server_close()


# ============================================================================ ketahanan
def test_kamera_dan_kejadian_dipulihkan_setelah_restart(tmp_path):
    root = tmp_path / "data"
    app = App(root)
    cam = app.cams.create({"source": "demo", "speed": 0, "max_loops": 1})
    wait_for(lambda: cam.info()["status"] == "selesai", 90)
    seq = cam.info()["seq"]
    app.cams.update(cam.spec.id, {"enabled": False})            # agar restart tidak memutar ulang rekaman
    wait_for(lambda: cam.info()["status"] == "jeda", 10)
    app.close()

    again = App(root)
    try:
        restored = again.cams.get(cam.spec.id)
        assert restored is not None and restored.spec.name == "Kamera demo" and restored.spec.enabled is False
        assert restored.events.seq == seq == 3
        assert restored.events.snapshot_path(1) is not None
        wait_for(lambda: restored.info()["status"] == "jeda", 10)
    finally:
        again.close()


def test_cameras_json_rusak_tidak_menjatuhkan_server(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    (root / "cameras.json").write_text("{bukan json yang benar", encoding="utf-8")
    app = App(root)
    try:
        assert app.cams.list() == []
    finally:
        app.close()
    (root / "cameras.json").write_text(json.dumps([{"id": "x"}, "aneh", {"id": "a" * 12, "nama": "salah"}]))
    app = App(root)
    try:
        assert app.cams.list() == []
    finally:
        app.close()


def test_sumber_url_diuji_lewat_siaran_mjpeg_kamera_lain(srv):
    """Jalur kamera jaringan (OpenCV/FFmpeg membaca URL) diuji dengan menyuapi satu kamera dari siaran MJPEG
    kamera lain. RTSP memakai jalur pembukaan yang sama; yang berbeda hanya protokol."""
    app, c = srv
    st, a = c.req("POST", "/api/cams", {"name": "Sumber", "source": "demo", "speed": 1, "max_loops": None})
    b = None
    try:
        wait_for(lambda: cam_info(c, a["id"])["status"] == "memantau", 30)
        url = f"http://127.0.0.1:{app.port}/api/cams/{a['id']}/stream.mjpg"
        st, b = c.req("POST", "/api/cams", {"name": "Penonton", "source": url})
        assert st == 201, b
        info = wait_for(lambda: (i := cam_info(c, b["id"]))["status"] == "perlu_area" and i, 60,
                        msg="kamera URL tidak pernah menerima frame")
        assert info["size"] == [640, 360] and info["kind"] == "url"
        resp, raw = c.req("GET", f"/api/cams/{b['id']}/snapshot.jpg?raw=1", raw=True)
        assert resp.status == 200 and raw[:2] == b"\xff\xd8"
    finally:
        for cam in (b, a):
            if cam:
                c.req("DELETE", f"/api/cams/{cam['id']}")


# ====================================================================== kamera perangkat (frame didorong browser)
def _jpeg(frame) -> bytes:
    import cv2
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    assert ok
    return buf.tobytes()


def test_sumber_browser_diterima_dan_dikenali():
    assert parse_source("browser").kind == "browser"
    assert not parse_source("browser").is_recording
    assert cam_mod.source_kind("browser") == "browser"
    assert build_spec({"source": "browser", "name": "HP"}).learn == "normal"


def test_ukuran_jpeg_dibaca_dari_header_tanpa_mendekode():
    import numpy as np
    assert cam_mod.jpeg_size(_jpeg(np.zeros((90, 160, 3), np.uint8))) == (160, 90)
    assert cam_mod.jpeg_size(b"bukan jpeg") is None
    assert cam_mod.jpeg_size(b"\xff\xd8\xff\xd9") is None
    assert cam_mod.jpeg_size(b"") is None


def test_kamera_perangkat_belajar_memantau_dan_mencatat_parkir(srv):
    from sidewalk_sentinel.synthetic import Actor, Renderer, Scene
    app, c = srv
    scene = Scene(seconds=30, actors=[Actor("P", (60, 36), (40, 60, 200), [(0.0, 320, 300)], stay=True)])
    r = Renderer(scene)
    st, d = c.req("POST", "/api/cams", {"source": "browser", "name": "HP uji", "learn": "cepat",
                                        "settings": {"park_seconds": 1}, "roi": [list(p) for p in ROI]})
    assert st == 201 and d["kind"] == "browser", d
    cid = d["id"]
    try:
        wait_for(lambda: cam_info(c, cid)["status"] == "menunggu", 10, msg="tanpa frame harus menunggu perangkat")

        def push(actors, t):
            st, resp = c.req("POST", f"/api/cams/{cid}/frame", _jpeg(r.frame(t, actors=actors)))
            assert st == 200, resp
            return resp

        seen = set()
        t0 = time.time()
        while time.time() - t0 < 20:                      # trotoar kosong: belajar latar sendiri
            resp = push(False, 1.0)
            seen.add(resp["status"])
            if resp["status"] == "memantau":
                break
            time.sleep(0.12)
        assert {"belajar", "memantau"} <= seen, seen
        assert set(resp) >= {"status", "learn_progress", "tracks", "counts_today", "seq"}

        boxes = []
        t0 = time.time()
        while time.time() - t0 < 6 and cam_info(c, cid)["counts_today"]["parkir"] == 0:
            resp = push(True, 5.0)                        # kendaraan datang dan diam di trotoar
            boxes += resp["tracks"]
            time.sleep(0.12)
        info = cam_info(c, cid)
        assert info["counts_today"]["parkir"] == 1, info
        assert boxes and all(0 <= v <= 1 for t in boxes for v in t["box"]), "jejak harus ternormalisasi"
        assert {"id", "box", "state", "dwell"} <= set(boxes[0])
        assert info["relearns"] == 0

        # perangkat berhenti mengirim: menunggu, tanpa membuang latar yang sudah dipelajari
        wait_for(lambda: cam_info(c, cid)["status"] == "menunggu", 12, msg="harus menunggu saat frame berhenti")
        t0 = time.time()
        while time.time() - t0 < 5 and cam_info(c, cid)["status"] != "memantau":
            push(False, 8.0)
            time.sleep(0.12)
        again = cam_info(c, cid)
        assert again["status"] == "memantau" and again["learn_progress"] == info["learn_progress"], again
        assert again["relearns"] == 0
    finally:
        c.req("DELETE", f"/api/cams/{cid}")


def test_frame_perangkat_menolak_masukan_buruk(srv):
    import numpy as np
    app, c = srv
    st, d = c.req("POST", "/api/cams", {"source": "browser", "name": "HP buruk"})
    cid = d["id"]
    st2, demo = c.req("POST", "/api/cams", {"source": "demo", "speed": 1})
    try:
        good = _jpeg(np.zeros((90, 160, 3), np.uint8))
        assert c.req("POST", f"/api/cams/{cid}/frame", b"bukan jpeg")[0] == 400
        assert c.req("POST", f"/api/cams/{cid}/frame", b"")[0] == 400

        # 'bom dekompresi': header menyatakan 30000x30000. Harus ditolak SEBELUM mendekode (pesan khas penjaga).
        i = good.index(b"\xff\xc0")
        bomb = good[:i + 5] + (30000).to_bytes(2, "big") + (30000).to_bytes(2, "big") + good[i + 9:]
        st, err = c.req("POST", f"/api/cams/{cid}/frame", bomb)
        assert st == 400 and "terlalu besar" in err["error"], err

        st, err = c.req("POST", f"/api/cams/{demo['id']}/frame", good)       # bukan kamera perangkat
        assert st == 400 and "perangkat" in err["error"], err
        assert c.req("POST", "/api/cams/000000000000/frame", good)[0] == 404
        assert c.req("GET", f"/api/cams/{cid}/frame")[0] in (404, 405)
    finally:
        c.req("DELETE", f"/api/cams/{cid}")
        c.req("DELETE", f"/api/cams/{demo['id']}")

"""Uji server HTTP end-to-end (tanpa browser): unggah, analisis, Range, kamera langsung, keamanan."""
from __future__ import annotations

import http.client
import json
import threading
import time

import cv2
import pytest

from sidewalk_sentinel.server import make_server
from sidewalk_sentinel.synthetic import ROI, Renderer, default_scene, write_video


class Client:
    def __init__(self, port: int, cookie: str | None = None):
        self.port, self.cookie = port, cookie

    def req(self, method, path, body=None, headers=None, raw=False):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        h = dict(headers or {})
        if self.cookie:
            h["Cookie"] = self.cookie
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
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


@pytest.fixture()
def srv(tmp_path):
    server, app = make_server("127.0.0.1", 0, tmp_path / "data")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield app, Client(app.port)
    server.shutdown()
    server.server_close()


def wait_job(c: Client, jid: str, timeout: float = 180.0) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        st, j = c.req("GET", f"/api/jobs/{jid}")
        assert st == 200, j
        if j["state"] in ("done", "error", "cancelled"):
            return j
        time.sleep(0.4)
    raise AssertionError("job tidak selesai tepat waktu")


# ------------------------------------------------------------------- statis dan keamanan
def test_halaman_statis_dan_header(srv):
    app, c = srv
    resp, body = c.req("GET", "/", raw=True)
    assert resp.status == 200 and b"Sidewalk Sentinel" in body
    assert resp.getheader("Content-Type").startswith("text/html")
    resp, _ = c.req("GET", "/app.js", raw=True)
    assert "javascript" in resp.getheader("Content-Type")
    resp, _ = c.req("GET", "/manifest.webmanifest", raw=True)
    assert "manifest+json" in resp.getheader("Content-Type")
    resp, _ = c.req("GET", "/icon-192.png", raw=True)
    assert resp.status == 200 and resp.getheader("Content-Type") == "image/png"
    assert c.req("GET", "/tidak-ada.js")[0] == 404


@pytest.mark.parametrize("path", ["/../main.py", "/%2e%2e/main.py", "/..%2fmain.py", "/web/../../main.py"])
def test_path_traversal_ditolak(srv, path):
    _, c = srv
    resp, body = c.req("GET", path, raw=True)
    assert resp.status == 404 and b"import" not in body


def test_mode_lan_butuh_kunci(tmp_path):
    server, app = make_server("127.0.0.1", 0, tmp_path / "d", lan=True, token="rahasia123")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        c = Client(app.port)
        assert c.req("GET", "/")[0] == 403
        assert c.req("GET", "/api/info")[0] == 403
        assert c.req("GET", "/?k=salah")[0] == 403
        resp, _ = c.req("GET", "/?k=rahasia123", raw=True)
        assert resp.status == 302 and "ss_key=rahasia123" in resp.getheader("Set-Cookie")
        assert "HttpOnly" in resp.getheader("Set-Cookie")
        authed = Client(app.port, cookie="ss_key=rahasia123")
        assert authed.req("GET", "/")[0] == 200
        assert authed.req("GET", "/api/info")[0] == 200
        # aset PWA publik agar manifest bisa diambil browser tanpa kredensial
        assert c.req("GET", "/manifest.webmanifest")[0] == 200
        assert c.req("POST", "/api/demo")[0] == 403
    finally:
        server.shutdown()
        server.server_close()


def test_koneksi_keepalive_tidak_kacau_bila_body_tak_dibaca(srv):
    """Regresi: POST dengan body '{}' ke endpoint yang tak membaca body merusak permintaan berikutnya."""
    app, c = srv
    conn = http.client.HTTPConnection("127.0.0.1", c.port, timeout=30)

    def call(method, path, body=None):
        headers = {"Content-Type": "application/json"} if body is not None else {}
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        return resp.status, data

    for method, path in [("POST", "/api/demo"), ("POST", f"/api/jobs/{'a' * 12}/cancel"),
                         ("DELETE", f"/api/live/{'b' * 12}"), ("POST", "/api/endpoint-asing")]:
        call(method, path, b"{}")
        st, data = call("GET", "/api/info")                 # harus tetap terbaca di koneksi yang sama
        assert st == 200 and b"Sidewalk Sentinel" in data, (method, path, st, data[:80])
    conn.close()

    # Koneksi tidak berizin pun harus tetap sehat setelah menolak permintaan ber-body
    server, lan_app = make_server("127.0.0.1", 0, app.store.root / "lan", lan=True, token="k")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", lan_app.port, timeout=30)
        conn.request("POST", "/api/demo", body=b"{}", headers={"Content-Type": "application/json"})
        assert conn.getresponse().read() and True
        conn.request("GET", "/manifest.webmanifest")
        resp = conn.getresponse(); resp.read()
        assert resp.status == 200
        conn.close()
    finally:
        server.shutdown(); server.server_close()


# --------------------------------------------------------------------------- alur utama
def test_alur_demo_analisis_hasil_dan_range(srv):
    app, c = srv
    st, demo = c.req("POST", "/api/demo")
    assert st == 200 and demo["info"]["duration"] == 30.0
    uid = demo["upload_id"]

    resp, jpg = c.req("GET", f"/api/uploads/{uid}/frame.jpg?t=12.5&w=640", raw=True)
    assert resp.status == 200 and jpg[:2] == b"\xff\xd8"

    st, r = c.req("POST", "/api/analyze", {"upload_id": uid, "roi": demo["roi"], "settings": {"park_seconds": 5}})
    assert st == 202
    j = wait_job(c, r["job_id"])
    assert j["state"] == "done", j.get("error")
    assert j["summary"]["parkir"] == 1 and j["summary"]["naik_trotoar"] == 2
    assert j["has_video"] is True
    jid = r["job_id"]

    resp, snap = c.req("GET", f"/api/jobs/{jid}/{j['events'][0]['snapshot']}", raw=True)
    assert resp.status == 200 and snap[:2] == b"\xff\xd8"

    resp, csv_ = c.req("GET", f"/api/jobs/{jid}/result.csv?download=1", raw=True)
    assert resp.status == 200 and "attachment" in resp.getheader("Content-Disposition")
    assert len(csv_.decode().strip().splitlines()) == 4          # header + 3 kejadian

    resp, rj = c.req("GET", f"/api/jobs/{jid}/result.json", raw=True)
    assert json.loads(rj)["summary"]["parkir"] == 1

    resp, pv = c.req("GET", f"/api/jobs/{jid}/preview.jpg?view=stages", raw=True)
    assert resp.status == 200 and pv[:2] == b"\xff\xd8"

    # iOS Safari memutar video lewat permintaan Range
    full_resp, full = c.req("GET", f"/api/jobs/{jid}/annotated.mp4", raw=True)
    assert full_resp.status == 200 and full_resp.getheader("Accept-Ranges") == "bytes"
    resp, part = c.req("GET", f"/api/jobs/{jid}/annotated.mp4", headers={"Range": "bytes=0-1"}, raw=True)
    assert resp.status == 206 and part == full[:2]
    assert resp.getheader("Content-Range") == f"bytes 0-1/{len(full)}"
    resp, tail = c.req("GET", f"/api/jobs/{jid}/annotated.mp4", headers={"Range": "bytes=-100"}, raw=True)
    assert resp.status == 206 and tail == full[-100:]
    resp, _ = c.req("GET", f"/api/jobs/{jid}/annotated.mp4", headers={"Range": f"bytes={len(full) + 50}-"}, raw=True)
    assert resp.status == 416

    st, hist = c.req("GET", "/api/jobs")
    assert st == 200 and hist["jobs"][0]["id"] == jid


def test_unggah_video_dan_tolak_berkas_rusak(srv, tmp_path):
    _, c = srv
    src = tmp_path / "klip.mp4"
    write_video(default_scene(seconds=4), str(src))
    st, up = c.req("POST", "/api/upload?name=klip.mp4", src.read_bytes(), {"Content-Type": "video/mp4"})
    assert st == 201 and up["info"]["width"] == 640 and up["info"]["frames"] == 40
    st, info = c.req("GET", f"/api/uploads/{up['upload_id']}")
    assert st == 200 and info["name"] == "klip.mp4"

    st, err = c.req("POST", "/api/upload?name=rusak.mp4", b"ini bukan video" * 100)
    assert st == 422 and "error" in err
    assert c.req("POST", "/api/upload?name=x.mp4", b"")[0] == 411


def test_validasi_masukan(srv):
    _, c = srv
    _, demo = c.req("POST", "/api/demo")
    uid, roi = demo["upload_id"], demo["roi"]
    bad = [
        ({"upload_id": uid, "roi": [[0, 0], [1, 1]]}, 400),                           # < 3 titik
        ({"upload_id": uid, "roi": [[0, 0], [2, 0], [1, 1]]}, 400),                   # di luar 0..1
        ({"upload_id": uid, "roi": roi, "settings": {"tidak_ada": 1}}, 400),          # kunci asing
        ({"upload_id": uid, "roi": roi, "settings": {"park_seconds": 0}}, 400),       # di luar rentang
        ({"upload_id": "0" * 12, "roi": roi}, 404),                                   # upload tak ada
    ]
    for body, status in bad:
        st, err = c.req("POST", "/api/analyze", body)
        assert st == status, (body, st, err)
    assert c.req("POST", "/api/analyze", b"{rusak", {"Content-Type": "application/json"})[0] == 400
    assert c.req("GET", "/api/jobs/zzzz")[0] == 404
    assert c.req("GET", f"/api/jobs/{'a' * 12}")[0] == 404
    assert c.req("GET", "/api/endpoint-asing")[0] == 404


def test_batalkan_job(srv):
    _, c = srv
    _, demo = c.req("POST", "/api/demo")
    _, r = c.req("POST", "/api/analyze", {"upload_id": demo["upload_id"], "roi": demo["roi"],
                                           "settings": {"park_seconds": 5}})
    st, out = c.req("POST", f"/api/jobs/{r['job_id']}/cancel")
    assert st == 200
    j = wait_job(c, r["job_id"])
    assert j["state"] in ("cancelled", "done")      # bisa saja sudah selesai sebelum pembatalan sampai


# ------------------------------------------------------------------------- kamera langsung
def jpeg(frame) -> bytes:
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    assert ok
    return buf.tobytes()


def test_sesi_live_kalibrasi_lalu_deteksi_parkir(srv):
    _, c = srv
    st, s = c.req("POST", "/api/live", {"roi": [list(p) for p in ROI], "settings": {"park_seconds": 1}})
    assert st == 201 and s["need"] == 8
    sid = s["sid"]

    sc = default_scene()
    sc.actors = [a for a in sc.actors if a.name == "P"]
    sc.actors[0].keys = [(0.0, 300, 317), (1.0, 300, 317)]            # motor sudah parkir sejak awal
    r = Renderer(sc)

    last = None
    for _ in range(8):                                                 # kalibrasi pada trotoar kosong
        st, last = c.req("POST", f"/api/live/{sid}/frame", jpeg(r.frame(0.0, actors=False)), {"Content-Type": "image/jpeg"})
        assert st == 200
        time.sleep(0.17)
    assert last["state"] == "monitoring" and last["calib"] == {"have": 8, "need": 8}

    events, saw_track = [], False
    for i in range(60):
        st, d = c.req("POST", f"/api/live/{sid}/frame", jpeg(r.frame(2.0 + i * 0.1)), {"Content-Type": "image/jpeg"})
        assert st == 200
        saw_track |= bool(d["tracks"])
        for t in d["tracks"]:
            assert len(t["box"]) == 4 and all(0.0 <= v <= 1.0 for v in t["box"]) and t["state"] in {
                "luar", "diam", "bergerak", "parkir", "naik_trotoar"}
        events += d["new_events"]
        if events:
            break
        time.sleep(0.1)
    assert saw_track and events and events[0]["kind"] == "parkir"

    resp, snap = c.req("GET", f"/api/live/{sid}/{events[0]['snapshot']}", raw=True)
    assert resp.status == 200 and snap[:2] == b"\xff\xd8"

    assert c.req("POST", f"/api/live/{sid}/recalibrate")[0] == 200
    st, d = c.req("POST", f"/api/live/{sid}/frame", jpeg(r.frame(0.0, actors=False)), {"Content-Type": "image/jpeg"})
    assert d["state"] == "calibrating"

    st, err = c.req("POST", f"/api/live/{sid}/frame", b"bukan jpeg", {"Content-Type": "image/jpeg"})
    assert st == 400
    assert c.req("DELETE", f"/api/live/{sid}")[0] == 200
    assert c.req("POST", f"/api/live/{sid}/frame", jpeg(r.frame(0.0)))[0] == 404


def test_live_menolak_roi_tak_valid(srv):
    _, c = srv
    assert c.req("POST", "/api/live", {"roi": [[0, 0]]})[0] == 400
    assert c.req("POST", "/api/live", {"roi": [[0, 0], [1, 0], [1, 1]], "settings": {"x": 1}})[0] == 400

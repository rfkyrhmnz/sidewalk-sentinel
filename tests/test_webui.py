"""Tes dasbor web: server HTTP sungguhan di port acak, segmenter tiruan, adegan sintetis.

Yang diuji: jalur 'browser' (frame JPEG lewat POST /api/frame, seperti yang dikirim halaman web), jalur 'server'
(server membuka berkas video sendiri + siaran MJPEG), poligon trotoar manual, pengaturan, penjaga keamanan.
TIDAK diuji di sini: tampilan HTML/JS di browser, getUserMedia, YOLO asli.
"""
import http.client
import json
import threading
import time

import cv2
import numpy as np
import pytest

from synthetic import FakeSegmenter
from test_e2e import FPS, PARKED_CAR, ROAD_CAR, SECONDS, build_video
from trotoar import webui
from trotoar.cli import build_parser
from trotoar.config import Config
from trotoar.pipeline import Analyzer
from trotoar.webui import Engine, clean_polygons, make_server, mask_polygons


class Client:
    def __init__(self, port):
        self.port = port
        self.conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)

    def req(self, method, path, body=None, headers=None):
        self.conn.request(method, path, body=body, headers=headers or {})
        r = self.conn.getresponse()
        data = r.read()
        return r.status, r, data

    def get_json(self, path):
        st, _, data = self.req("GET", path)
        return st, (json.loads(data) if data else None)

    def post_json(self, path, obj, headers=None):
        st, _, data = self.req("POST", path, json.dumps(obj), {"Content-Type": "application/json", **(headers or {})})
        return st, (json.loads(data) if data else None)

    def post_frame(self, jpg, t):
        st, _, data = self.req("POST", f"/api/frame?t={t:.3f}", jpg, {"Content-Type": "image/jpeg"})
        return st, (json.loads(data) if data else None)


@pytest.fixture
def web(tmp_path):
    made = []

    def make(dets_all, factory=None, remote=False, **cfg_kw):
        cfg = Config(min_dwell_s=6.0, stationary_window_s=2.0, **cfg_kw)
        factory = factory or (lambda c: Analyzer(
            c, FakeSegmenter(lambda n, img: dets_all[min(n, len(dets_all) - 1)]), "classical"))
        eng = Engine(cfg, factory, out_dir=tmp_path / "out")
        srv = make_server(eng, "127.0.0.1", 0, remote=remote)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        eng.load_async()
        eng._ready.wait(20)
        made.append((eng, srv))
        client = Client(srv.server_port)
        client.srv = srv
        return client, eng

    yield make
    for eng, srv in made:
        eng.stop()
        srv.shutdown()
        srv.server_close()


def video_frames(path):
    cap = cv2.VideoCapture(str(path))
    out = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        out.append(f)
    cap.release()
    return out


def jpeg(frame, q=85):
    return cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, q])[1].tobytes()


# ------------------------------------------------------------------ geometri overlay
def test_mask_polygons_normalized_with_hole():
    m = np.zeros((200, 400), bool)
    m[40:160, 80:320] = True
    polys = mask_polygons(m, nested=True)
    assert len(polys) == 1 and 4 <= len(polys[0]) <= 8
    xs, ys = zip(*polys[0])
    assert 0.18 < min(xs) < 0.25 and 0.75 < max(xs) < 0.83 and 0.18 < min(ys) < 0.25 and 0.75 < max(ys) < 0.83
    m[90:110, 180:220] = False                                         # lubang
    assert len(mask_polygons(m, nested=True)) == 2                     # kontur luar + kontur lubang
    assert len(mask_polygons(m, nested=False)) == 1
    assert mask_polygons(np.zeros((50, 50), bool)) == []


def test_mask_polygons_point_budget():
    rng = np.random.default_rng(0)
    m = cv2.resize((rng.random((60, 80)) > 0.5).astype(np.uint8), (640, 480), interpolation=cv2.INTER_NEAREST) > 0
    polys = mask_polygons(m, nested=True, max_points=300)
    assert sum(len(p) for p in polys) <= 300 * 1.0 + 1500            # disederhanakan, bukan ribuan titik mentah
    assert all(0 <= x <= 1 and 0 <= y <= 1 for p in polys for x, y in p)


def test_clean_polygons_validation():
    assert clean_polygons(None) == [] and clean_polygons([]) == []
    ok = [[[0.1, 0.5], [0.9, 0.5], [0.5, 0.95]]]
    assert clean_polygons(ok) == ok
    for bad in ("x", [[[0, 0], [1, 1]]], [[[0, 0], [1, 1], [2, 0]]], [[[0, 0], [1, 1], ["a", 0]]], [[1, 2, 3]]):
        with pytest.raises(ValueError):
            clean_polygons(bad)


# ------------------------------------------------------------------ jalur browser
def test_browser_flow_alerts_once_and_serves_evidence(web, tmp_path):
    src = tmp_path / "in.mp4"
    dets = build_video(src, lambda k: [PARKED_CAR, ROAD_CAR])
    c, eng = web(dets)
    assert c.get_json("/api/status")[1]["model"] == "siap"
    assert c.post_json("/api/start", {"kind": "browser"})[1]["active"] == "browser"

    last = None
    for k, f in enumerate(video_frames(src)):
        st, last = c.post_frame(jpeg(f), k / FPS)                       # satu koneksi keep-alive dipakai berulang
        assert st == 200, last
    by_id = {v["id"]: v for v in last["vehicles"]}
    assert by_id[1]["status"] == "PARKIR_TERKONFIRMASI" and by_id[1]["dwell"] >= 6.0
    assert by_id[2]["status"] == "DI_JALAN"
    assert last["summary"] == {"kendaraan": 2, "di_trotoar": 1, "parkir": 1}
    assert last["w"] == 640 and last["h"] == 480 and last["alerts"] == 1
    assert last["sidewalk"] and all(0 <= x <= 1 and 0 <= y <= 1 for p in last["sidewalk"] for x, y in p)
    assert by_id[1]["poly"] and 0 <= by_id[1]["box"][0] < by_id[1]["box"][2] <= 1

    _, data = c.get_json("/api/alerts")
    assert len(data["alerts"]) == 1
    a = data["alerts"][0]
    assert a["track_id"] == 1 and a["kelas"] == "mobil" and a["dwell_s"] >= 6.0 and a["foto"].startswith("/alerts/")
    st, resp, body = c.req("GET", a["foto"])
    assert st == 200 and resp.getheader("Content-Type") == "image/jpeg" and body[:2] == b"\xff\xd8"
    st, resp, body = c.req("GET", "/kejadian.csv")
    assert st == 200 and b"mobil" in body and "attachment" in resp.getheader("Content-Disposition")

    assert c.get_json("/api/result?since=0")[1]["seq"] == last["seq"]       # hasil terakhir juga tersedia lewat polling
    assert c.req("GET", f"/api/result?since={last['seq']}")[0] == 204       # tidak ada yang baru

    c.post_json("/api/stop", {})
    summary = json.loads((eng.out_dir / "ringkasan_live.json").read_text(encoding="utf-8"))
    assert len(summary["alert"]) == 1 and summary["frame_diproses"] == FPS * SECONDS


def test_new_session_can_alert_again_and_old_history_kept(web, tmp_path):
    src = tmp_path / "in.mp4"
    dets = build_video(src, lambda k: [PARKED_CAR])
    c, eng = web(dets)
    frames = video_frames(src)
    for session in (1, 2):
        eng.analyzer.seg.reset_tracking()
        c.post_json("/api/start", {"kind": "browser"})
        for k, f in enumerate(frames):
            c.post_frame(jpeg(f), k / FPS)
        assert len(c.get_json("/api/alerts")[1]["alerts"]) == session   # ID tracker diulang, alert baru tetap tercatat
        c.post_json("/api/stop", {})


def test_time_going_backwards_resets_stream_state(web, tmp_path):
    src = tmp_path / "in.mp4"
    dets = build_video(src, lambda k: [PARKED_CAR], n=30)
    c, eng = web(dets)
    frames = video_frames(src)
    c.post_json("/api/start", {"kind": "browser"})
    for k in range(10):
        assert c.post_frame(jpeg(frames[k]), 5 + k / FPS)[0] == 200
    st_before = eng._state
    assert c.post_frame(jpeg(frames[10]), 0.5)[0] == 200                  # video diputar mundur
    assert eng._state is not st_before


def test_frame_rejected_when_not_started_or_bad(web, tmp_path):
    c, _ = web([])
    st, body = c.post_frame(b"\xff\xd8 bukan gambar", 0)
    assert st == 409 and "Mulai pantau" in body["error"]
    c.post_json("/api/start", {"kind": "browser"})
    st, body = c.post_frame(b"bukan jpeg sama sekali", 0)
    assert st == 400 and "JPEG" in body["error"]
    st, _, _ = c.req("POST", "/api/frame?t=abc", b"x", {"Content-Type": "image/jpeg"})
    assert st == 400


# ------------------------------------------------------------------ trotoar manual & pengaturan
def test_manual_polygon_mode_roundtrip(web, tmp_path):
    src = tmp_path / "in.mp4"
    dets = build_video(src, lambda k: [PARKED_CAR], n=12)
    c, eng = web(dets)
    frames = video_frames(src)
    area = [[[0.0, 0.64], [1.0, 0.64], [1.0, 1.0], [0.0, 1.0]]]            # bagian bawah gambar = trotoar sintetis
    st, body = c.post_json("/api/sidewalk", {"polygons": area})
    assert st == 200 and body["trotoar"] == "manual" and body["polygon"] == area
    assert (eng.out_dir / "trotoar_polygon.json").exists()
    assert eng.analyzer.mode == "polygon"

    c.post_json("/api/start", {"kind": "browser"})
    st, p = c.post_frame(jpeg(frames[0]), 0.0)
    assert st == 200 and len(p["sidewalk"]) >= 1
    ys = [y for poly in p["sidewalk"] for _, y in poly]
    assert 0.6 < min(ys) < 0.7                                                # tepi atas trotoar = tepi atas poligon

    st, body = c.post_json("/api/sidewalk", {"polygons": None})
    assert st == 200 and body["trotoar"] == "otomatis" and eng.analyzer.mode == "classical"
    assert c.post_json("/api/sidewalk", {"polygons": [[[0, 0], [1, 1]]]})[0] == 400
    assert c.post_json("/api/sidewalk", {"polygons": "x"})[0] == 400


def test_settings_validate_and_apply(web):
    c, eng = web([])
    st, body = c.post_json("/api/settings", {"min_dwell_s": 20, "tau": 0.6, "camera_comp": False})
    assert st == 200 and body["settings"] == {"min_dwell_s": 20.0, "tau": 0.6, "camera_comp": False}
    assert eng.cfg.min_dwell_s == 20.0 and eng.cfg.tau_contact == eng.cfg.tau_ring == 0.6
    assert eng.cfg.compensate_camera is False
    for bad in ({"min_dwell_s": 0}, {"min_dwell_s": "abc"}, {"tau": 2}, {"tau": None}):
        st, body = c.post_json("/api/settings", bad)
        assert st == 400 and body["error"], bad
    assert eng.cfg.min_dwell_s == 20.0                                       # nilai buruk tidak mengubah apa pun


# ------------------------------------------------------------------ jalur server (berkas video / MJPEG)
def test_server_source_plays_file_to_end_and_alerts(web, tmp_path):
    src = tmp_path / "in.mp4"
    dets = build_video(src, lambda k: [PARKED_CAR, ROAD_CAR])
    c, eng = web(dets)
    # 4x lebih cepat: analisis masih sempat memproses cukup rapat. Pada ~40x frame yang sampai terlalu jarang
    # (> jendela diam) sehingga kendaraan memang tak bisa dinilai "diam": itu perilaku yang disengaja.
    st, body = c.post_json("/api/start", {"kind": "server", "source": str(src), "speed": 4})
    assert st == 200 and body["active"] == "server"
    for _ in range(300):
        if c.get_json("/api/status")[1]["active"] is None:
            break
        time.sleep(0.1)
    s = c.get_json("/api/status")[1]
    assert s["active"] is None and s["message"] == "Video selesai."
    assert s["alerts"] == 1 and s["frames"] >= 20
    assert [a["track_id"] for a in c.get_json("/api/alerts")[1]["alerts"]] == [1]
    assert json.loads((eng.out_dir / "ringkasan_live.json").read_text(encoding="utf-8"))["alert"]


def test_server_source_errors_are_friendly(web, tmp_path):
    c, _ = web([])
    st, body = c.post_json("/api/start", {"kind": "server", "source": str(tmp_path / "tidak_ada.mp4")})
    assert st == 409 and "Tidak bisa membuka sumber" in body["error"]
    assert c.post_json("/api/start", {"kind": "server", "source": "  "})[0] == 400
    assert c.post_json("/api/start", {"kind": "ngawur"})[0] == 400
    assert c.get_json("/api/status")[1]["active"] is None                    # start gagal tidak meninggalkan sesi setengah jadi


def test_mjpeg_stream_is_multipart_jpeg_and_stops_with_source(web, tmp_path):
    src = tmp_path / "in.mp4"
    dets = build_video(src, lambda k: [PARKED_CAR])
    c, eng = web(dets)
    assert c.req("GET", "/stream.mjpg")[0] == 404                              # belum ada sumber
    c.post_json("/api/start", {"kind": "server", "source": str(src), "speed": 1})
    conn = http.client.HTTPConnection("127.0.0.1", c.port, timeout=15)
    conn.request("GET", "/stream.mjpg")
    r = conn.getresponse()
    assert r.status == 200 and r.getheader("Content-Type").startswith("multipart/x-mixed-replace")
    data = b""
    while data.count(b"\xff\xd8") < 2 and len(data) < 400_000:
        data += r.read1(65536)
    assert b"--frame" in data and data.count(b"\xff\xd8") >= 2
    conn.close()
    c.post_json("/api/stop", {})


# ------------------------------------------------------------------ model & penjaga
def test_model_load_failure_is_reported_and_blocks_start(web):
    def boom(cfg):
        raise ImportError("No module named 'ultralytics'")
    c, _ = web([], factory=boom)
    s = c.get_json("/api/status")[1]
    assert s["model"] == "galat" and "ultralytics" in s["model_error"]
    st, body = c.post_json("/api/start", {"kind": "browser"})
    assert st == 409 and "gagal dimuat" in body["error"]


def test_guards_content_type_host_and_paths(web):
    c, _ = web([])
    assert c.req("POST", "/api/start", "{}", {"Content-Type": "text/plain"})[0] == 415
    c2 = Client(c.port)                                                       # koneksi baru (415 menutup yang lama)
    assert c2.req("POST", "/api/frame?t=0", b"x", {"Content-Type": "text/plain"})[0] == 415
    c3 = Client(c.port)
    st, _, body = c3.req("GET", "/api/status", headers={"Host": "evil.example.com"})
    assert st == 403 and b"localhost" in body
    for path in ("/alerts/../ringkasan_live.json", "/alerts/%2e%2e/x.jpg", "/static/../cli.py", "/static/..%2fcli.py",
                 "/alerts/tidak_ada.jpg", "/nope"):
        assert c3.req("GET", path)[0] == 404, path
    assert c3.req("GET", "/")[0] == 200
    st, resp, body = c3.req("GET", "/static/app.js")
    assert st == 200 and "javascript" in resp.getheader("Content-Type") and b"Sidewalk Sentinel" in body
    st, _, body = c3.req("POST", "/api/stop", "{not json", {"Content-Type": "application/json"})
    assert st == 400


def test_static_files_exist_and_reference_each_other():
    html = (webui.WEB_DIR / "index.html").read_text(encoding="utf-8")
    for name in ("app.css", "app.js", "favicon.svg"):
        assert (webui.WEB_DIR / name).is_file() and f"/static/{name}" in html
    js = (webui.WEB_DIR / "app.js").read_text(encoding="utf-8")
    import re
    ids = set(re.findall(r'\$\("([\w-]+)"\)', js))
    missing = [i for i in ids if f'id="{i}"' not in html]
    assert not missing, f"id dipakai JS tapi tidak ada di HTML: {missing}"


def test_cli_ui_subcommand_parses():
    a = build_parser().parse_args(["ui", "--port", "9000", "--no-open", "--host", "0.0.0.0", "--min-dwell", "7"])
    assert a.cmd == "ui" and a.port == 9000 and a.no_open and a.host == "0.0.0.0" and a.min_dwell == 7


def test_analysis_failure_is_reported_as_500_not_400(web):
    def boom(n, img):
        raise ValueError("operands could not be broadcast together")
    c, _ = web([], factory=lambda cfg: Analyzer(cfg, FakeSegmenter(boom), "classical"))
    c.post_json("/api/start", {"kind": "browser"})
    st, body = c.post_frame(jpeg(np.zeros((48, 64, 3), np.uint8)), 0)
    assert st == 500 and "Analisis gagal" in body["error"] and "broadcast" in body["error"]


def test_remote_mode_requires_key_and_disables_server_sources(web, tmp_path):
    c, eng = web([], remote=True)
    key = c.srv.token
    assert key and len(key) >= 20
    for path in ("/", "/api/status", "/api/alerts", "/static/app.js", "/?key=salah"):
        assert c.req("GET", path)[0] == 401, path                      # tanpa kunci / kunci salah: ditolak
    st, resp, _ = c.req("GET", f"/?key={key}")
    assert st == 302 and resp.getheader("Location") == "/"
    cookie = resp.getheader("Set-Cookie")
    assert cookie.startswith("ss_key=") and "HttpOnly" in cookie and "SameSite=Strict" in cookie
    auth = {"Cookie": cookie.split(";")[0]}
    assert c.req("GET", "/api/status", headers=auth)[0] == 200
    assert c.req("GET", "/", headers=auth)[0] == 200
    # lewat terowongan, nama Host-nya asing: tetap boleh asal membawa kunci
    assert c.req("GET", "/api/status", headers={**auth, "Host": "acak.trycloudflare.com"})[0] == 200
    assert c.req("GET", "/api/status", headers={"Cookie": "ss_key=salah"})[0] == 401
    # sumber di sisi server dimatikan; kamera/video lewat browser tetap jalan
    src = tmp_path / "x.mp4"
    build_video(src, lambda k: [], n=5)
    st, body = c.post_json("/api/start", {"kind": "server", "source": str(src)}, headers=auth)
    assert st == 409 and "--remote" in body["error"]
    assert c.post_json("/api/start", {"kind": "browser"}, headers=auth)[0] == 200


def test_local_mode_has_no_key(web):
    c, _ = web([])
    assert c.srv.token is None and c.req("GET", "/api/status")[0] == 200

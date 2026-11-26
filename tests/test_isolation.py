"""Pemisahan data: perangkat A tidak bisa melihat atau mengubah isi perangkat B, sedangkan pusat (laptop pengelola)
melihat semuanya. Diuji di setiap jalur API dengan peminta yang tampak datang lewat tunnel Cloudflare.

Peminta lokal sungguhan (Client biasa ke 127.0.0.1) adalah pusat. 'Remote' di bawah meniru HP yang masuk lewat tunnel:
penyambungnya loopback (cloudflared), tetapi ada Host tunnel dan header Cf-*, sehingga bukan pusat."""
from __future__ import annotations

import json
import threading

import cv2
import numpy as np
import pytest
from support import Client, ColorDetector, FakeSegmenter, new_key

from sidewalk_sentinel.cameras import CameraManager
from sidewalk_sentinel.server import make_server

EV = {"label": "x", "reason": "uji", "track_id": 1}
JPG = cv2.imencode(".jpg", np.full((64, 96, 3), 120, np.uint8))[1].tobytes()
NOT_FOUND = {"error": "perangkat tidak ditemukan"}


class Remote:
    """HP yang masuk lewat tunnel: loopback + Host tunnel + Cf-Connecting-Ip, dengan toples cookie sendiri."""

    def __init__(self, port: int, ip: str, key: str | None = None, name: str = "Ponsel"):
        self.c, self.ip, self.key, self.name = Client(port), ip, key or new_key(), name
        self.jar: dict[str, str] = {}
        self.cam: dict | None = None

    def req(self, method, path, body=None, headers=None, raw=False):
        h = {"Host": "demo-tunnel.trycloudflare.com", "Cf-Connecting-Ip": self.ip, "X-Forwarded-Proto": "https",
             **(headers or {})}
        self.c.cookie = "; ".join(f"{k}={v}" for k, v in self.jar.items()) or None
        resp, data = self.c.req(method, path, body, h, raw=True)
        for line in resp.headers.get_all("Set-Cookie") or []:
            name, _, rest = line.partition("=")
            self.jar[name] = rest.split(";")[0]
        if raw:
            return resp, data
        try:
            return resp.status, json.loads(data) if data else None
        except ValueError:
            return resp.status, data

    def register(self) -> dict:
        st, d = self.req("POST", "/api/device", {"key": self.key, "name": self.name})
        assert st in (200, 201), d
        self.cam = d
        return d

    @property
    def id(self) -> str:
        return self.cam["id"]


@pytest.fixture()
def srv(tmp_path):
    server, app = make_server("127.0.0.1", 0, tmp_path / "data", detector=ColorDetector(), sidewalk=FakeSegmenter())
    app.trust_proxy = True                                  # seperti --hp dengan tunnel
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield app, Client(app.port)
    app.close()
    server.shutdown()
    server.server_close()


def seed(app, cid, kind):
    return app.cams.get(cid).events.append({**EV, "kind": kind}, JPG)


@pytest.fixture()
def dua(srv):
    """Dua perangkat jarak jauh dengan kejadian masing-masing. A: 1 parkir. B: 1 naik trotoar + 1 parkir."""
    app, center = srv
    a = Remote(app.port, "203.0.113.7", name="Ponsel Ayu")
    b = Remote(app.port, "198.51.100.9", name="Ponsel Budi")
    a.register(), b.register()
    ea = seed(app, a.id, "parkir")
    eb1, eb2 = seed(app, b.id, "naik_trotoar"), seed(app, b.id, "parkir")
    return app, center, a, b, ea, eb1, eb2


def text(obj) -> str:
    return json.dumps(obj, ensure_ascii=False) if not isinstance(obj, (bytes, str)) else (
        obj.decode("utf-8-sig") if isinstance(obj, bytes) else obj)


def test_perangkat_hanya_melihat_datanya_sendiri_di_semua_jalur_baca(dua):
    app, center, a, b, ea, eb1, eb2 = dua
    st, d = a.req("GET", "/api/cams")
    assert st == 200 and [c["name"] for c in d["cams"]] == ["Ponsel Ayu"]
    assert b.id not in text(d) and "Budi" not in text(d)

    st, d = a.req("GET", "/api/events?limit=50")
    assert [(e["camera"]["name"], e["no"]) for e in d["events"]] == [("Ponsel Ayu", ea["no"])]

    st, r = a.req("GET", "/api/report?periode=30_hari")
    assert (r["total"], r["parkir"], r["naik_trotoar"]) == (1, 1, 0) and [c["nama"] for c in r["per_kamera"]] == ["Ponsel Ayu"]
    assert "Budi" not in text(r) and b.id not in text(r)

    st, csv_bytes = a.req("GET", "/api/events.csv")
    assert st == 200 and "Ponsel Ayu" in text(csv_bytes) and "Budi" not in text(csv_bytes)

    st, d = a.req("GET", f"/api/cams/{a.id}/events")
    assert st == 200 and [e["no"] for e in d["events"]] == [ea["no"]]
    resp, _ = a.req("GET", f"/api/cams/{a.id}/events/{ea['no']}.jpg", raw=True)
    assert resp.status == 200, "foto buktinya sendiri tetap bisa dibuka"


@pytest.mark.parametrize("method,tail,body", [
    ("GET", "", None),
    ("POST", "", {"name": "Dibajak"}),
    ("POST", "", {"enabled": False}),
    ("POST", "", {"roi": [[0.1, 0.5], [0.9, 0.5], [0.9, 0.9]]}),
    ("DELETE", "", None),
    ("GET", "/events", None),
    ("GET", "/events?after=0", None),
    ("GET", "/events/{eb1}.jpg", None),
    ("DELETE", "/events/{eb1}", None),
    ("POST", "/frame", JPG),
    ("POST", "/propose-zone", {}),
])
def test_perangkat_lain_ditolak_dengan_jawaban_yang_sama_seperti_yang_tidak_ada(dua, method, tail, body):
    app, center, a, b, ea, eb1, eb2 = dua
    path = tail.format(eb1=eb1["no"])
    headers = {"Content-Type": "image/jpeg"} if isinstance(body, bytes) else None
    got = a.req(method, f"/api/cams/{b.id}{path}", body, headers)
    ghost = a.req(method, f"/api/cams/000000000000{path}", body, headers)
    assert got[0] == 404 and got == ghost, "perangkat orang lain tidak boleh dibedakan dari yang memang tidak ada"
    assert got[1] == NOT_FOUND
    # milik B utuh
    st, d = center.req("GET", f"/api/cams/{b.id}")
    assert st == 200 and d["name"] == "Ponsel Budi" and d["enabled"] is True and d["zone"] != "manual"
    assert [e["no"] for e in center.req("GET", f"/api/cams/{b.id}/events")[1]["events"]] == [eb2["no"], eb1["no"]]


def test_laporan_dengan_kamera_orang_lain_dijawab_tidak_ditemukan(dua):
    app, center, a, b, *_ = dua
    assert a.req("GET", f"/api/report?kamera={b.id}") == (404, NOT_FOUND)
    assert a.req("GET", "/api/report?kamera=000000000000") == (404, NOT_FOUND)
    assert a.req("GET", f"/api/report?kamera={a.id}")[1]["total"] == 1
    assert center.req("GET", f"/api/report?kamera={b.id}&periode=30_hari")[1]["total"] == 2


def test_perangkat_tidak_bisa_membuka_sambungan_hp_pusat(dua):
    app, center, a, *_ = dua
    app.set_connect(mode="tunnel", state="siap", url="https://x.trycloudflare.com/?k=RAHASIA", message="")
    st, d = a.req("GET", "/api/connect")
    assert st == 403 and "RAHASIA" not in text(d)
    st, d = center.req("GET", "/api/connect")
    assert st == 200 and d["state"] == "siap"


def test_peran_dilaporkan_di_info(dua):
    app, center, a, *_ = dua
    assert center.req("GET", "/api/info")[1]["role"] == "pusat"
    assert a.req("GET", "/api/info")[1]["role"] == "perangkat"
    assert Remote(app.port, "192.0.2.1").req("GET", "/api/info")[1]["role"] == "perangkat", "tamu pun bukan pusat"


def test_pusat_melihat_semua_perangkat_beserta_ip_penandanya(dua):
    app, center, a, b, ea, eb1, eb2 = dua
    st, d = center.req("GET", "/api/cams")
    by_name = {c["name"]: c for c in d["cams"]}
    assert set(by_name) == {"Ponsel Ayu", "Ponsel Budi"}
    assert by_name["Ponsel Ayu"]["ip"] == "203.0.113.7" and by_name["Ponsel Budi"]["ip"] == "198.51.100.9"
    assert by_name["Ponsel Ayu"]["ip_since"], "sejak kapan IP itu dipakai"
    assert len(center.req("GET", "/api/events?limit=50")[1]["events"]) == 3
    r = center.req("GET", "/api/report?periode=30_hari")[1]
    assert r["total"] == 3 and {c["nama"] for c in r["per_kamera"]} == {"Ponsel Ayu", "Ponsel Budi"}
    csv_text = text(center.req("GET", "/api/events.csv")[1])
    assert "Ponsel Ayu" in csv_text and "Ponsel Budi" in csv_text
    resp, _ = center.req("GET", f"/api/cams/{b.id}/events/{eb1['no']}.jpg", raw=True)
    assert resp.status == 200, "pusat boleh membuka foto bukti perangkat mana pun"


def test_ip_tidak_pernah_terlihat_oleh_perangkat_termasuk_miliknya_sendiri_dan_milik_orang_lain(dua):
    app, center, a, b, *_ = dua
    for payload in (a.req("GET", "/api/cams")[1], a.req("GET", f"/api/cams/{a.id}")[1], a.cam,
                    a.req("POST", f"/api/cams/{a.id}", {"name": "Ayu Baru"})[1]):
        assert '"ip"' not in text(payload) and '"ip_since"' not in text(payload)
        assert "203.0.113.7" not in text(payload) and "198.51.100.9" not in text(payload)


def test_tamu_tanpa_perangkat_terdaftar_tidak_melihat_apa_pun(dua):
    app, center, a, b, ea, eb1, eb2 = dua
    g = Remote(app.port, "192.0.2.50")                       # punya tautan berkunci, belum mendaftar
    assert g.req("GET", "/api/cams")[1]["cams"] == []
    assert g.req("GET", "/api/events?limit=50")[1]["events"] == []
    r = g.req("GET", "/api/report?periode=30_hari")[1]
    assert r["total"] == 0 and r["per_kamera"] == []
    assert text(g.req("GET", "/api/events.csv")[1]).count("\n") <= 1, "hanya baris judul"
    for cam in (a, b):
        assert g.req("GET", f"/api/cams/{cam.id}") == (404, NOT_FOUND)
        assert g.req("GET", f"/api/cams/{cam.id}/events/1.jpg", raw=True)[0].status == 404
        assert g.req("DELETE", f"/api/cams/{cam.id}")[0] == 404
        assert g.req("POST", f"/api/cams/{cam.id}/frame", JPG, {"Content-Type": "image/jpeg"})[0] == 404
    assert len(center.req("GET", "/api/cams")[1]["cams"]) == 2


@pytest.mark.parametrize("cookie", ["", "ss_dev=", "ss_dev=ZZZ", "ss_dev=" + "ab" * 12, "ss_dev=" + "0" * 64,
                                    "ss_dev=../../x", "ss_dev=" + "a" * 500, "ss_dev=ab ab ab ab ab ab ab ab"])
def test_cookie_perangkat_palsu_tidak_membuka_apa_pun(dua, cookie):
    app, center, a, b, *_ = dua
    g = Remote(app.port, "192.0.2.50")
    r = g.req("GET", "/api/cams", headers={"Cookie": cookie} if cookie else None)
    assert r[0] == 200 and r[1]["cams"] == []
    assert g.req("GET", f"/api/cams/{a.id}", headers={"Cookie": cookie} if cookie else None)[0] == 404


def test_kunci_perangkat_adalah_identitas_dan_ip_hanya_penanda_yang_berubah(dua):
    """Bila kunci A dipakai dari jaringan lain, yang terbuka tetap hanya data A (bukan B), dan pusat melihat IP-nya berganti."""
    app, center, a, b, *_ = dua
    before = {c["name"]: c for c in center.req("GET", "/api/cams")[1]["cams"]}["Ponsel Ayu"]
    thief = Remote(app.port, "192.0.2.77", key=a.key, name="apa saja")
    again = thief.register()
    assert again["id"] == a.id and again["name"] == "Ponsel Ayu", "kunci yang sama = perangkat yang sama"
    assert [c["name"] for c in thief.req("GET", "/api/cams")[1]["cams"]] == ["Ponsel Ayu"]
    assert thief.req("GET", f"/api/cams/{b.id}") == (404, NOT_FOUND)
    after = {c["name"]: c for c in center.req("GET", "/api/cams")[1]["cams"]}["Ponsel Ayu"]
    assert after["ip"] == "192.0.2.77" and after["ip"] != before["ip"], "IP berganti terlihat di pusat"
    assert {c["name"]: c for c in center.req("GET", "/api/cams")[1]["cams"]}["Ponsel Budi"]["ip"] == "198.51.100.9"


def test_ip_dari_frame_diperbarui_tanpa_menulis_disk_berulang(dua):
    app, center, a, *_ = dua
    saves = []
    real = app.cams._save
    app.cams._save = lambda: (saves.append(1), real())[1]
    h = {"Content-Type": "image/jpeg"}
    for _ in range(5):
        assert a.req("POST", f"/api/cams/{a.id}/frame", JPG, h)[0] == 200
    assert saves == [], "IP sama: tidak ada penulisan"
    a.ip = "203.0.113.99"
    assert a.req("POST", f"/api/cams/{a.id}/frame", JPG, h)[0] == 200
    assert len(saves) == 1
    assert {c["name"]: c for c in center.req("GET", "/api/cams")[1]["cams"]}["Ponsel Ayu"]["ip"] == "203.0.113.99"


@pytest.mark.parametrize("trust,expected", [(True, "203.0.113.7"), (False, "127.0.0.1")])
def test_header_ip_cloudflare_hanya_dipercaya_saat_tunnel_milik_server(srv, trust, expected):
    app, center = srv
    app.trust_proxy = trust
    r = Remote(app.port, "203.0.113.7")
    r.register()
    assert {c["name"]: c for c in center.req("GET", "/api/cams")[1]["cams"]}["Ponsel"]["ip"] == expected


@pytest.mark.parametrize("headers,why", [
    ({"Host": "demo-tunnel.trycloudflare.com"}, "Host tunnel saja"),
    ({"Host": "localhost:1", "X-Forwarded-For": "203.0.113.7"}, "header proxy saja"),
    ({"Host": "localhost:1", "Cf-Connecting-Ip": "203.0.113.7"}, "header Cloudflare saja"),
    ({"Host": "localhost.evil.com"}, "DNS rebinding"),
    ({"Host": "evil.com"}, "nama lain yang mengarah ke 127.0.0.1"),
])
def test_sambungan_lewat_loopback_yang_tampak_dari_luar_bukan_pusat(dua, headers, why):
    """Inti pelajaran dari memakai IP: di belakang tunnel semua permintaan datang dari 127.0.0.1."""
    app, center, a, b, *_ = dua
    c = Client(app.port)
    st, d = c.req("GET", "/api/info", headers=headers)
    assert d["role"] == "perangkat", why
    assert c.req("GET", "/api/cams", headers=headers)[1]["cams"] == [], why
    assert c.req("GET", "/api/events?limit=50", headers=headers)[1]["events"] == [], why
    assert c.req("GET", "/api/connect", headers=headers)[0] == 403, why


def test_cookie_perangkat_aman_dan_kuncinya_tidak_bocor_di_jawaban(srv):
    app, center = srv
    key = new_key()
    resp, data = Remote(app.port, "203.0.113.7", key=key).req("POST", "/api/device", {"key": key, "name": "P"}, raw=True)
    cookie = resp.headers["Set-Cookie"]
    assert cookie.startswith(f"ss_dev={key};") and "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert "Path=/api" in cookie and "Secure" in cookie, "lewat tunnel https"
    assert key not in data.decode(), "kunci tidak ikut di badan jawaban"
    resp, data = center.req("POST", "/api/device", {"key": new_key(), "name": "Laptop"}, raw=True)
    assert "Secure" not in resp.headers["Set-Cookie"], "http lokal di laptop"
    for who in (center, Remote(app.port, "203.0.113.7", key=key)):
        assert key not in text(who.req("GET", "/api/cams")[1]) and key not in text(who.req("GET", f"/api/events.csv")[1])


def test_perangkat_boleh_menghapus_data_sendiri_tetapi_tidak_milik_orang_lain(dua):
    app, center, a, b, ea, eb1, eb2 = dua
    assert a.req("DELETE", f"/api/cams/{b.id}/events/{eb1['no']}")[0] == 404
    assert a.req("DELETE", f"/api/cams/{a.id}/events/{ea['no']}") == (200, {"deleted": True, "no": ea["no"]})
    assert center.req("GET", f"/api/cams/{a.id}/events")[1]["events"] == []
    assert center.req("DELETE", f"/api/cams/{b.id}/events/{eb1['no']}")[0] == 200, "pusat boleh menghapus milik perangkat mana pun"
    assert a.req("DELETE", f"/api/cams/{a.id}") == (200, {"deleted": True})
    assert [c["name"] for c in center.req("GET", "/api/cams")[1]["cams"]] == ["Ponsel Budi"]


def test_perangkat_yang_dihapus_pusat_mendaftar_ulang_dengan_kunci_yang_sama(dua):
    app, center, a, b, *_ = dua
    assert center.req("DELETE", f"/api/cams/{a.id}") == (200, {"deleted": True})
    assert a.req("GET", "/api/cams")[1]["cams"] == []
    assert a.req("POST", f"/api/cams/{a.id}/frame", JPG, {"Content-Type": "image/jpeg"})[0] == 404, "halaman memakai 404 ini untuk mendaftar ulang"
    old = a.id
    a.register()
    assert a.id != old and [c["id"] for c in a.req("GET", "/api/cams")[1]["cams"]] == [a.id]
    assert a.req("POST", f"/api/cams/{a.id}/frame", JPG, {"Content-Type": "image/jpeg"})[0] == 200


def test_ip_tersimpan_di_disk_dipulihkan_dan_berkas_yang_diedit_tangan_dibersihkan(tmp_path):
    m1 = CameraManager(tmp_path, detector=ColorDetector())
    m1.start()
    cam, _ = m1.register(new_key(), "Gerbang")
    m1.note_ip(cam, "203.0.113.7")
    since = cam.spec.ip_since
    m1.note_ip(cam, "203.0.113.7")
    assert cam.spec.ip_since == since, "IP yang sama tidak mengubah 'sejak'"
    m1.note_ip(cam, "bukan-ip")
    m1.note_ip(cam, "")
    assert cam.spec.ip == "203.0.113.7"
    m1.shutdown()
    m2 = CameraManager(tmp_path, detector=ColorDetector())
    m2.start()
    assert m2.get(cam.spec.id).spec.ip == "203.0.113.7" and m2.get(cam.spec.id).spec.ip_since == since
    m2.shutdown()
    items = json.loads((tmp_path / "devices.json").read_text(encoding="utf-8"))
    items[0]["ip"] = "<script>alert(1)</script>"
    (tmp_path / "devices.json").write_text(json.dumps(items), encoding="utf-8")
    m3 = CameraManager(tmp_path, detector=ColorDetector())
    m3.start()
    try:
        assert m3.get(cam.spec.id).spec.ip == "" and m3.get(cam.spec.id).spec.ip_since == ""
    finally:
        m3.shutdown()


def test_menutup_kamera_hanya_untuk_perangkat_sendiri_dan_terlihat_di_pusat(dua):
    app, center, a, b, *_ = dua
    st, d = a.req("POST", f"/api/cams/{a.id}", {"closed": True})
    assert st == 200 and d["closed"] is True
    assert a.req("POST", f"/api/cams/{b.id}", {"closed": True}) == (404, NOT_FOUND), "kamera orang lain tidak bisa ditutup"
    st, d = center.req("GET", "/api/cams")
    assert {c["name"]: c["closed"] for c in d["cams"]} == {"Ponsel Ayu": True, "Ponsel Budi": False}
    st, d = b.req("GET", "/api/cams")
    assert [(c["name"], c["closed"]) for c in d["cams"]] == [("Ponsel Budi", False)], "B hanya melihat dirinya sendiri"
    assert a.req("POST", f"/api/cams/{a.id}", {"closed": False})[1]["closed"] is False

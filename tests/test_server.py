"""Uji server HTTP (tanpa browser): berkas statis, keamanan, kunci LAN, dan keutuhan koneksi."""
from __future__ import annotations

import http.client
import threading

import pytest
from support import Client, ColorDetector, FakeSegmenter, new_key

from sidewalk_sentinel.server import make_server


@pytest.fixture()
def srv(tmp_path):
    server, app = make_server("127.0.0.1", 0, tmp_path / "data", detector=ColorDetector(), sidewalk=FakeSegmenter())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield app, Client(app.port)
    app.close()
    server.shutdown()
    server.server_close()


def test_halaman_statis_dan_header(srv):
    app, c = srv
    resp, body = c.req("GET", "/", raw=True)
    assert resp.status == 200 and b"Sidewalk Sentinel" in body
    assert resp.getheader("Content-Type").startswith("text/html")
    assert resp.getheader("X-Content-Type-Options") == "nosniff"
    resp, _ = c.req("GET", "/app.js", raw=True)
    assert "javascript" in resp.getheader("Content-Type")
    resp, _ = c.req("GET", "/manifest.webmanifest", raw=True)
    assert "manifest+json" in resp.getheader("Content-Type")
    resp, _ = c.req("GET", "/icon-192.png", raw=True)
    assert resp.status == 200 and resp.getheader("Content-Type") == "image/png"
    assert c.req("GET", "/tidak-ada.js")[0] == 404


def test_info_menyebut_nilai_bawaan_tanpa_membocorkan_jalur_server(srv):
    app, c = srv
    st, info = c.req("GET", "/api/info")
    assert st == 200 and info["name"] == "Sidewalk Sentinel" and info["auth"] is False
    assert info["defaults"]["park_seconds"] == 10
    assert str(app.root) not in json_text(info)


def json_text(obj) -> str:
    import json
    return json.dumps(obj)


@pytest.mark.parametrize("path", ["/../main.py", "/%2e%2e/main.py", "/..%2fmain.py", "/web/../../main.py"])
def test_path_traversal_ditolak(srv, path):
    _, c = srv
    resp, body = c.req("GET", path, raw=True)
    assert resp.status == 404 and b"import" not in body


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/demo"), ("POST", "/api/upload"), ("POST", "/api/analyze"), ("GET", "/api/jobs"),
    ("POST", "/api/live"), ("POST", "/api/cams"),
])
def test_jalur_sumber_lain_sudah_tidak_ada(srv, method, path):
    _, c = srv
    assert c.req(method, path, {})[0] in (404, 405), "aplikasi hanya memakai kamera perangkat sendiri"


def test_mode_lan_butuh_kunci(tmp_path):
    server, app = make_server("127.0.0.1", 0, tmp_path / "d", lan=True, token="rahasia123")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        c = Client(app.port)
        assert c.req("GET", "/")[0] == 403
        assert c.req("GET", "/api/info")[0] == 403
        assert c.req("GET", "/?k=salah")[0] == 403
        resp, body = c.req("GET", "/?k=rahasia123", raw=True)
        assert resp.status == 200 and resp.getheader("Location") is None, "tautan berkunci tidak boleh mengalihkan: kunci harus tetap di alamat"
        assert "ss_key=rahasia123" in resp.getheader("Set-Cookie") and "HttpOnly" in resp.getheader("Set-Cookie")
        assert resp.getheader("Referrer-Policy") == "no-referrer", "kunci ada di alamat, jadi tidak boleh bocor lewat Referer"
        authed = Client(app.port, cookie="ss_key=rahasia123")
        assert authed.req("GET", "/")[0] == 200
        assert authed.req("GET", "/api/info")[0] == 200
        assert c.req("GET", "/manifest.webmanifest")[0] == 200, "aset PWA publik agar manifest bisa diambil tanpa kredensial"
        assert c.req("POST", "/api/device", {"key": new_key()})[0] == 403
    finally:
        app.close()
        server.shutdown()
        server.server_close()


def test_koneksi_keepalive_tidak_kacau_bila_body_tak_dibaca(srv, tmp_path):
    """Regresi: POST dengan body ke endpoint yang tak membaca body merusak permintaan berikutnya."""
    app, c = srv
    conn = http.client.HTTPConnection("127.0.0.1", c.port, timeout=30)

    def call(method, path, body=None):
        headers = {"Content-Type": "application/json"} if body is not None else {}
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        return resp.status, resp.read()

    for method, path in [("POST", "/api/endpoint-asing"), ("POST", f"/api/cams/{'a' * 12}/propose-zone"),
                         ("DELETE", f"/api/cams/{'b' * 12}"), ("GET", "/api/device")]:
        call(method, path, b"{}")
        st, data = call("GET", "/api/info")                 # harus tetap terbaca di koneksi yang sama
        assert st == 200 and b"Sidewalk Sentinel" in data, (method, path, st, data[:80])
    conn.close()

    server, lan_app = make_server("127.0.0.1", 0, tmp_path / "lan", lan=True, token="k")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", lan_app.port, timeout=30)
        conn.request("POST", "/api/device", body=b"{}", headers={"Content-Type": "application/json"})
        assert conn.getresponse().read()
        conn.request("GET", "/manifest.webmanifest")
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 200, "koneksi tidak berizin harus tetap sehat setelah menolak permintaan ber-body"
        conn.close()
    finally:
        lan_app.close()
        server.shutdown()
        server.server_close()


def test_tautan_berkunci_bisa_dipindah_antar_browser_tanpa_cookie(tmp_path):
    """Pratinjau pemindai QR dan Safari punya wadah cookie sendiri: yang terbawa saat pindah hanya alamatnya."""
    server, app = make_server("127.0.0.1", 0, tmp_path / "d", lan=True, token="rahasia123")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = "/?k=rahasia123"
        for _ in range(2):                                      # dua browser berbeda, tanpa cookie satu pun, memakai alamat yang sama
            resp, body = Client(app.port).req("GET", url, raw=True)
            assert resp.status == 200 and b"<html" in body.lower()
        assert Client(app.port).req("GET", "/")[0] == 403, "alamat polos tanpa kunci tetap ditolak"
        halaman = Client(app.port).req("GET", "/")[1]
        assert b"--lan" not in halaman, "teks penolakan tidak boleh menyuruh mode yang tidak dipakai"
        assert b"/hp" in halaman and b"?k=" in halaman, "teks penolakan harus menunjuk cara memperbaikinya"
    finally:
        app.close()
        server.shutdown()
        server.server_close()


def test_kunci_di_tautan_hanya_membuka_halaman_dan_tahan_karakter_aneh(tmp_path):
    server, app = make_server("127.0.0.1", 0, tmp_path / "d", lan=True, token="rahasia123")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        c = Client(app.port)
        assert c.req("GET", "/api/info?k=rahasia123")[0] == 403, "kunci di tautan tidak membuka API"
        assert c.req("POST", "/?k=rahasia123", {})[0] == 403, "hanya GET dan HEAD yang membuka halaman lewat tautan"
        assert c.req("GET", "/index.html?k=rahasia123")[0] == 403, "hanya alamat halaman utama dan /hp"
        for k in ("%C3%A9", "%FF%FE", "rahasia123%00", "x" * 4000, ""):      # non-ASCII, bukan UTF-8, NUL, terlalu panjang, kosong
            assert c.req("GET", f"/?k={k}")[0] == 403, k
        assert Client(app.port, cookie="ss_key=r\u00e9ahasia").req("GET", "/api/info")[0] == 403
        resp, _ = c.req("HEAD", "/?k=rahasia123", raw=True)
        assert resp.status == 200 and "ss_key=rahasia123" in resp.getheader("Set-Cookie")
    finally:
        app.close()
        server.shutdown()
        server.server_close()

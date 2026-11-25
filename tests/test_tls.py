"""HTTPS lokal untuk kamera HP: sertifikat, pemakaian ulang, dan server TLS.

Yang dijaga:
- sertifikat memuat alamat LAN yang dipakai (tanpa itu browser menolak), bukan CA, kunci privat tidak terbaca orang lain;
- dipakai ulang selama masih cocok, dibuat ulang bila alamat berubah, hampir habis, atau pasangan kuncinya rusak;
- server benar-benar bicara TLS, cookie kunci bertanda Secure, dan satu koneksi yang diam saja tidak menahan yang lain;
- tanpa paket cryptography, pesan galatnya jelas dan tidak meninggalkan utas kamera.
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import ssl
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.x509.oid import ExtendedKeyUsageOID  # noqa: E402

from sidewalk_sentinel import server as srv  # noqa: E402
from sidewalk_sentinel import tls  # noqa: E402


def load(cert_path: Path) -> x509.Certificate:
    return x509.load_pem_x509_certificate(cert_path.read_bytes())


def san(cert):
    ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    return set(ext.get_values_for_type(x509.DNSName)), {str(a) for a in ext.get_values_for_type(x509.IPAddress)}


def test_sertifikat_memuat_alamat_lan_dan_bukan_ca(tmp_path):
    cert_path, key_path = tls.ensure_certificate(tmp_path / "tls", ["192.168.1.23"])
    cert = load(cert_path)
    dns, ips = san(cert)
    assert {"localhost"} <= dns and {"127.0.0.1", "192.168.1.23"} <= ips
    assert cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is False
    assert ExtendedKeyUsageOID.SERVER_AUTH in cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    left = cert.not_valid_after_utc - cert.not_valid_before_utc
    assert 800 <= left.days <= tls.VALID_DAYS + 2
    key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    fmt = (serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    assert key.public_key().public_bytes(*fmt) == cert.public_key().public_bytes(*fmt)


@pytest.mark.skipif(os.name == "nt", reason="izin berkas POSIX")
def test_kunci_privat_hanya_bisa_dibaca_pemilik(tmp_path):
    _, key_path = tls.ensure_certificate(tmp_path / "tls", ["10.0.0.5"])
    assert (key_path.stat().st_mode & 0o077) == 0, oct(key_path.stat().st_mode)


def test_sertifikat_dipakai_ulang_selama_masih_cocok(tmp_path):
    d = tmp_path / "tls"
    c1, k1 = tls.ensure_certificate(d, ["192.168.1.23"])
    before = (c1.read_bytes(), k1.read_bytes())
    c2, k2 = tls.ensure_certificate(d, ["192.168.1.23", "localhost"])
    assert (c2.read_bytes(), k2.read_bytes()) == before


def test_sertifikat_dibuat_ulang_bila_alamat_wifi_berubah(tmp_path):
    d = tmp_path / "tls"
    c, _ = tls.ensure_certificate(d, ["192.168.1.23"])
    old = c.read_bytes()
    c, _ = tls.ensure_certificate(d, ["172.20.10.2"])                # pindah jaringan, mis. ke hotspot HP
    assert c.read_bytes() != old
    assert "172.20.10.2" in san(load(c))[1]


def test_sertifikat_hampir_habis_dibuat_ulang(tmp_path):
    d = tmp_path / "tls"
    c, _ = tls.ensure_certificate(d, ["192.168.1.23"], days=10)
    old = c.read_bytes()
    c, _ = tls.ensure_certificate(d, ["192.168.1.23"])
    assert c.read_bytes() != old
    assert (load(c).not_valid_after_utc - load(c).not_valid_before_utc).days >= 800


def test_pasangan_kunci_rusak_dibuat_ulang_dan_bisa_dipakai(tmp_path):
    d = tmp_path / "tls"
    c, k = tls.ensure_certificate(d, ["192.168.1.23"])
    other = tmp_path / "other"
    _, k_other = tls.ensure_certificate(other, ["192.168.1.23"])
    k.write_bytes(k_other.read_bytes())                              # kunci milik sertifikat lain
    c2, k2 = tls.ensure_certificate(d, ["192.168.1.23"])
    tls.server_context(c2, k2)                                       # tidak melempar: pasangannya cocok lagi
    k.write_bytes(b"bukan pem")                                      # berkas rusak total
    c3, k3 = tls.ensure_certificate(d, ["192.168.1.23"])
    tls.server_context(c3, k3)


# ---------------------------------------------------------------------- server TLS
@pytest.fixture()
def https_server(tmp_path):
    server, app = srv.make_server("127.0.0.1", 0, tmp_path / "data", lan=True, token="rahasia-uji", https=True,
                                  hosts=["127.0.0.1"])
    threading.Thread(target=server.serve_forever, daemon=True).start()
    cert = tmp_path / "data" / "tls" / tls.CERT_NAME
    yield app, cert
    app.close()
    server.shutdown()
    server.server_close()


def trusting(cert: Path) -> ssl.SSLContext:
    return ssl.create_default_context(cafile=str(cert))


def get_json(app, cert, path="/api/info", cookie="ss_key=rahasia-uji"):
    conn = http.client.HTTPSConnection("127.0.0.1", app.port, context=trusting(cert), timeout=10)
    try:
        conn.request("GET", path, headers={"Cookie": cookie})
        r = conn.getresponse()
        return r.status, json.loads(r.read() or b"null")
    finally:
        conn.close()


def test_server_melayani_tls_yang_bisa_diverifikasi(https_server):
    app, cert = https_server
    st, info = get_json(app, cert)
    assert st == 200 and "version" in info
    with pytest.raises((ssl.SSLCertVerificationError, urllib.error.URLError)):   # tanpa kepercayaan: ditolak
        urllib.request.urlopen(f"https://127.0.0.1:{app.port}/api/info", timeout=5)


def test_http_polos_ke_port_https_tidak_dilayani(https_server):
    app, _ = https_server
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{app.port}/api/info", timeout=5) as r:
            pytest.fail(f"seharusnya tidak dilayani, malah {r.status}")
    except (urllib.error.URLError, http.client.HTTPException, ConnectionError, OSError):
        pass


def test_cookie_kunci_bertanda_secure_lewat_https(https_server):
    app, cert = https_server
    conn = http.client.HTTPSConnection("127.0.0.1", app.port, context=trusting(cert), timeout=10)
    try:
        conn.request("GET", "/?k=rahasia-uji")
        r = conn.getresponse()
        r.read()
        cookie = r.getheader("Set-Cookie") or ""
    finally:
        conn.close()
    assert r.status == 302 and "ss_key=rahasia-uji" in cookie
    assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=Lax" in cookie


def test_cookie_kunci_tanpa_https_tidak_bertanda_secure(tmp_path):
    server, app = srv.make_server("127.0.0.1", 0, tmp_path / "data", lan=True, token="rahasia-uji")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", app.port, timeout=10)
        conn.request("GET", "/?k=rahasia-uji")
        r = conn.getresponse()
        r.read()
        conn.close()
        assert "Secure" not in (r.getheader("Set-Cookie") or ""), "cookie Secure tak akan terkirim lewat http"
    finally:
        app.close()
        server.shutdown()
        server.server_close()


def test_koneksi_yang_diam_saat_jabat_tangan_tidak_menahan_yang_lain(https_server):
    app, cert = https_server
    stalled = [socket.create_connection(("127.0.0.1", app.port), timeout=5) for _ in range(3)]   # tidak mengirim apa pun
    try:
        st, _ = get_json(app, cert)                      # harus tetap terlayani, bukan menunggu koneksi yang diam
        assert st == 200
    finally:
        for s in stalled:
            s.close()


def test_klien_yang_menolak_sertifikat_tidak_mencetak_traceback(https_server, capfd):
    app, _ = https_server
    ctx = ssl.create_default_context()                   # tidak percaya sertifikat kita: memutus setelah hello
    for _ in range(2):
        with pytest.raises(ssl.SSLError):
            with socket.create_connection(("127.0.0.1", app.port), timeout=5) as raw:
                ctx.wrap_socket(raw, server_hostname="127.0.0.1")
    get_json(app, https_server[1])                       # beri waktu penangan menyelesaikan koneksi tadi
    assert "Traceback" not in capfd.readouterr().err


# ---------------------------------------------------------------------- tanpa paket cryptography
def test_tanpa_cryptography_pesan_jelas_dan_tidak_membocorkan_utas(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "cryptography", None)           # import cryptography -> ImportError
    closed = []
    real_close = srv.App.close
    monkeypatch.setattr(srv.App, "close", lambda self: (closed.append(1), real_close(self))[1])
    with pytest.raises(tls.TlsUnavailable, match="pip install cryptography"):
        srv.make_server("127.0.0.1", 0, tmp_path / "data", https=True, hosts=["127.0.0.1"])
    assert closed == [1], "App sudah dibuat (utas kamera berjalan) dan harus ditutup saat HTTPS gagal"


def test_serve_dengan_https_gagal_dengan_pesan_bukan_traceback(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "cryptography", None)
    with pytest.raises(SystemExit) as e:
        srv.serve(port=0, data_dir=tmp_path / "data", https=True)
    assert "pip install cryptography" in str(e.value)

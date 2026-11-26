"""Sambungan HP: tunnel Cloudflare (dengan cloudflared tiruan), mode --hp, API /api/connect, dan halaman /hp.

Tidak ada jaringan dan tidak ada cloudflared sungguhan: proses tunnel diganti skrip Python kecil yang mencetak log
seperti cloudflared. Yang diuji adalah jalur dari log ke alamat berkunci ke kode QR yang bisa dibaca kamera HP.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import Client, ColorDetector, FakeSegmenter, Phone  # noqa: E402
from sidewalk_sentinel import server as srv, tunnel  # noqa: E402
from sidewalk_sentinel.server import make_server  # noqa: E402

BANNER = """\
2026-11-26T09:00:00Z INF Thank you for trying Cloudflare Tunnel. Doing so, without a Cloudflare account, is a quick way to experiment
2026-11-26T09:00:01Z INF Requesting new quick Tunnel on trycloudflare.com...
2026-11-26T09:00:02Z ERR retrying: Post "https://api.trycloudflare.com/tunnel": temporary
2026-11-26T09:00:04Z INF +--------------------------------------------------------------------------------------------+
2026-11-26T09:00:04Z INF |  Your quick Tunnel has been created! Visit it at (it may take some time to be reachable):  |
2026-11-26T09:00:04Z INF |  https://Quiet-Tea-1234.trycloudflare.com                                                  |
2026-11-26T09:00:04Z INF +--------------------------------------------------------------------------------------------+
"""


def fake(script: str) -> list[str]:
    return [sys.executable, "-u", "-c", script]


def printer(text: str, then: str = "import time; time.sleep(60)") -> list[str]:
    return fake(f"import sys; sys.stderr.write({text!r}); sys.stderr.flush(); {then}")


class Probe:
    def __init__(self):
        self.urls, self.fails = [], []
        self.got = threading.Event()

    def on_url(self, url):
        self.urls.append(url)
        self.got.set()

    def on_fail(self, message):
        self.fails.append(message)
        self.got.set()


# ======================================================================== tunnel
def test_alamat_tunnel_diambil_dari_log_dan_api_trycloudflare_diabaikan():
    assert tunnel.parse_url("INF |  https://Quiet-Tea-1234.trycloudflare.com  |") == "https://quiet-tea-1234.trycloudflare.com"
    assert tunnel.parse_url('ERR Post "https://api.trycloudflare.com/tunnel": EOF') is None
    assert tunnel.parse_url("https://www.cloudflare.com/website-terms/") is None
    assert tunnel.parse_url("tanpa alamat") is None


def test_perintah_memakai_127_0_0_1_bukan_localhost():
    cmd = tunnel.command("/usr/bin/cloudflared", 8123)
    assert cmd[:3] == ["/usr/bin/cloudflared", "tunnel", "--url"] and cmd[3] == "http://127.0.0.1:8123" and "--no-autoupdate" in cmd


def test_cloudflared_dicari_di_path(monkeypatch):
    monkeypatch.setattr(tunnel.shutil, "which", lambda name: "/opt/bin/cloudflared" if name == "cloudflared" else None)
    assert tunnel.find_cloudflared() == "/opt/bin/cloudflared"
    monkeypatch.setattr(tunnel.shutil, "which", lambda name: None)
    monkeypatch.setattr(tunnel.sys, "platform", "linux")
    assert tunnel.find_cloudflared() is None


def test_tunnel_melaporkan_alamat_sekali_dan_berhenti_saat_diminta():
    p = Probe()
    t = tunnel.Tunnel(printer(BANNER), p.on_url, p.on_fail, timeout=20)
    t.start()
    try:
        assert p.got.wait(10)
        assert p.urls == ["https://quiet-tea-1234.trycloudflare.com"] and not p.fails
    finally:
        t.stop()
    assert t.proc.poll() is not None, "proses cloudflared dihentikan"
    time.sleep(0.3)
    assert not p.fails, "berhenti atas permintaan bukan kegagalan"


def test_tunnel_gagal_dijelaskan_bila_proses_langsung_berhenti():
    p = Probe()
    log = 'ERR failed to request quick Tunnel: Post "https://api.trycloudflare.com/tunnel": dial tcp: no such host\\n'
    tunnel.Tunnel(printer(log, then="sys.exit(1)"), p.on_url, p.on_fail, timeout=20).start()
    assert p.got.wait(10)
    assert not p.urls and len(p.fails) == 1 and "internet" in p.fails[0]


def test_tunnel_gagal_bila_tak_ada_alamat_sampai_batas_waktu():
    p = Probe()
    t = tunnel.Tunnel(fake("import time; time.sleep(60)"), p.on_url, p.on_fail, timeout=0.6)
    t.start()
    assert p.got.wait(10)
    assert not p.urls and len(p.fails) == 1 and "belum jadi" in p.fails[0]
    time.sleep(0.5)
    assert t.proc.poll() is not None, "proses dihentikan setelah batas waktu"
    assert len(p.fails) == 1, "kegagalan dilaporkan sekali saja"


def test_tunnel_putus_setelah_jadi_dilaporkan():
    p = Probe()
    tunnel.Tunnel(printer(BANNER, then="import time; time.sleep(0.4)"), p.on_url, p.on_fail, timeout=20).start()
    deadline = time.time() + 10
    while not p.fails and time.time() < deadline:
        time.sleep(0.05)
    assert p.urls and p.fails and "terputus" in p.fails[0]


def test_cloudflared_yang_tak_bisa_dijalankan_dilaporkan():
    p = Probe()
    tunnel.Tunnel(["/jalur/yang/tidak/ada/cloudflared"], p.on_url, p.on_fail).start()
    assert p.fails and "tidak bisa dijalankan" in p.fails[0]


# ================================================================== API /api/connect
@pytest.fixture()
def locked(tmp_path):
    server, app = make_server("127.0.0.1", 0, tmp_path / "data", token="kunci-hp", detector=ColorDetector(), sidewalk=FakeSegmenter())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield app, Client(app.port, cookie="ss_key=kunci-hp")
    app.close()
    server.shutdown()
    server.server_close()


def test_connect_mati_bila_mode_hp_tidak_aktif(locked):
    app, c = locked
    st, d = c.req("GET", "/api/connect")
    assert st == 200 and d["state"] == "off" and d["url"] is None and d["svg"] is None and d["devices"] == []


def test_connect_memberi_tautan_svg_dan_perangkat_yang_sedang_mengirim(locked):
    app, c = locked
    app.set_connect(mode="tunnel", state="memulai", message="Membuat tunnel")
    d = c.req("GET", "/api/connect")[1]
    assert d["state"] == "memulai" and d["url"] is None and d["svg"] is None

    url = "https://quiet-tea-1234.trycloudflare.com/?k=kunci-hp"
    app.set_connect(state="siap", url=url, message="Siap")
    d = c.req("GET", "/api/connect")[1]
    assert d["state"] == "siap" and d["mode"] == "tunnel" and d["url"] == url
    assert d["svg"].startswith("<svg") and "<script" not in d["svg"] and d["devices"] == []

    phone = Phone(c, name="Ponsel uji")
    phone.register()
    assert c.req("GET", "/api/connect")[1]["devices"] == [], "terdaftar tetapi belum mengirim gambar"
    import numpy as np
    phone.push(np.full((360, 640, 3), 120, np.uint8))
    d = c.wait(lambda: c.req("GET", "/api/connect")[1] if c.req("GET", "/api/connect")[1]["devices"] else None, 10)
    assert [x["name"] for x in d["devices"]] == ["Ponsel uji"]


def test_connect_dan_halaman_hp_butuh_kunci(locked):
    app, c = locked
    anon = Client(app.port)
    assert anon.req("GET", "/api/connect")[0] == 403
    assert anon.req("GET", "/hp")[0] == 403
    resp, _ = anon.req("GET", "/hp?k=kunci-hp", raw=True)                 # tautan berkunci di /hp memasang cookie
    assert resp.status == 302 and resp.getheader("Location") == "/hp" and "ss_key=kunci-hp" in resp.getheader("Set-Cookie")
    assert anon.req("GET", "/hp?k=salah")[0] == 403
    st, body = c.req("GET", "/hp")
    assert st == 200 and b"Hubungkan HP" in body and b"/hp.js" in body
    assert c.req("GET", "/hp.js")[0] == 200


# ============================================================== serve --hp end to end
class Run:
    """Menjalankan serve() di utas dengan make_server dibungkus agar server bisa dihentikan dan detektornya tiruan."""

    def __init__(self, monkeypatch, tmp_path, **kw):
        self.made = {}
        real = srv.make_server

        def wrapped(*a, **k):
            k.update(detector=ColorDetector(), sidewalk=FakeSegmenter())
            self.made["server"], self.made["app"] = real(*a, **k)
            return self.made["server"], self.made["app"]

        monkeypatch.setattr(srv, "make_server", wrapped)
        self.opened = []
        monkeypatch.setattr(srv.webbrowser, "open", lambda url: self.opened.append(url))
        self.kw = dict(host="127.0.0.1", port=0, data_dir=tmp_path / "data", hp=True, open_browser=True, **kw)
        self.thread = threading.Thread(target=lambda: srv.serve(**self.kw), daemon=True)

    def __enter__(self):
        self.thread.start()
        deadline = time.time() + 20
        while "app" not in self.made and time.time() < deadline:
            time.sleep(0.05)
        assert "app" in self.made, "serve() tidak membuat server"
        return self.made["app"]

    def __exit__(self, *exc):
        self.made["server"].shutdown()
        self.thread.join(10)


def wait_state(app, wanted, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if app.connect_info()["state"] in wanted:
            return app.connect_info()
        time.sleep(0.05)
    raise AssertionError(f"keadaan sambungan tetap {app.connect_info()['state']}")


def read_out(capsys, needle: str, timeout: float = 10.0) -> str:
    """Keluaran terminal yang terkumpul sampai `needle` muncul (pencetakan berjalan di utas lain)."""
    out, deadline = "", time.time() + timeout
    while needle not in out and time.time() < deadline:
        out += capsys.readouterr().out
        time.sleep(0.05)
    return out


def terminal_qr_to_text(output: str) -> str:
    """Membaca kembali kode QR yang dicetak ke terminal: setiap karakter blok menjadi dua baris piksel."""
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    rows = [ln for ln in output.split("\n") if ln and set(ln) <= set(" ▀▄█")]
    assert len(rows) >= 10, "kode QR tidak tercetak di terminal"
    px = []
    for ln in rows:
        top = [255 if ch in "█▀" else 0 for ch in ln]
        bottom = [255 if ch in "█▄" else 0 for ch in ln]
        px += [top, bottom]
    img = np.array(px, np.uint8)
    img = cv2.resize(img, None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST)
    img = cv2.copyMakeBorder(img, 40, 40, 40, 40, cv2.BORDER_CONSTANT, value=255)
    return cv2.QRCodeDetector().detectAndDecode(img)[0]


def test_hp_dengan_cloudflared_hanya_di_laptop_berkunci_dan_qr_terminal_terbaca(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(tunnel, "find_cloudflared", lambda: "cloudflared-tiruan")
    monkeypatch.setattr(tunnel, "command", lambda exe, port: printer(BANNER))
    with Run(monkeypatch, tmp_path) as app:
        info = wait_state(app, ("siap", "gagal"))
        assert info["state"] == "siap", info
        assert app.host == "127.0.0.1", "dengan tunnel, server tidak dibuka ke Wi-Fi"
        assert app.https is False and app.token, "HTTP biasa (HTTPS dari Cloudflare) dan wajib berkunci"
        url = f"https://quiet-tea-1234.trycloudflare.com/?k={app.token}"
        assert info["url"] == url and info["mode"] == "tunnel"
        c = Client(app.port, cookie=f"ss_key={app.token}")
        assert c.req("GET", "/api/connect")[1]["url"] == url
        out = read_out(capsys, "Jangan dibagikan")
    assert url in out and "Arahkan kamera HP" in out
    assert terminal_qr_to_text(out) == url, "kode QR di terminal harus mengarah ke tautan berkunci"


def test_hp_membuka_halaman_qr_di_laptop_dengan_kunci(monkeypatch, tmp_path):
    monkeypatch.setattr(tunnel, "find_cloudflared", lambda: "cloudflared-tiruan")
    monkeypatch.setattr(tunnel, "command", lambda exe, port: printer(BANNER))
    run = Run(monkeypatch, tmp_path)
    with run as app:
        wait_state(app, ("siap",))
        deadline = time.time() + 5
        while not run.opened and time.time() < deadline:
            time.sleep(0.05)
        assert run.opened == [f"http://localhost:{app.port}/hp?k={app.token}"]


def test_hp_tanpa_buka_browser_bila_diminta(monkeypatch, tmp_path):
    monkeypatch.setattr(tunnel, "find_cloudflared", lambda: "cloudflared-tiruan")
    monkeypatch.setattr(tunnel, "command", lambda exe, port: printer(BANNER))
    run = Run(monkeypatch, tmp_path)
    run.kw["open_browser"] = False
    with run as app:
        wait_state(app, ("siap",))
        time.sleep(1.5)
        assert run.opened == []


def test_hp_tunnel_gagal_tetap_menjalankan_server_dan_menjelaskan(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(tunnel, "find_cloudflared", lambda: "cloudflared-tiruan")
    log = 'ERR failed to request quick Tunnel: dial tcp: no such host\\n'
    monkeypatch.setattr(tunnel, "command", lambda exe, port: printer(log, then="sys.exit(1)"))
    with Run(monkeypatch, tmp_path) as app:
        info = wait_state(app, ("gagal", "siap"))
        assert info["state"] == "gagal" and "internet" in info["message"] and info["url"] is None
        c = Client(app.port, cookie=f"ss_key={app.token}")
        assert c.req("GET", "/api/info")[0] == 200, "aplikasi di laptop tetap jalan"
        d = c.req("GET", "/api/connect")[1]
        assert d["state"] == "gagal" and d["svg"] is None
    assert "Tunnel gagal" in read_out(capsys, "Tunnel gagal")


def test_hp_tanpa_cloudflared_memakai_https_wi_fi_dengan_qr_dan_petunjuk(monkeypatch, tmp_path, capsys):
    pytest.importorskip("cryptography")
    monkeypatch.setattr(tunnel, "find_cloudflared", lambda: None)
    run = Run(monkeypatch, tmp_path)
    with run as app:
        info = wait_state(app, ("siap",))
        assert app.https is True and app.lan and app.token and info["mode"] == "lan"
        assert info["url"] == f"https://{srv.lan_ip()}:{app.port}/?k={app.token}"
        out = read_out(capsys, "Jangan dibagikan")
        time.sleep(0.5)
        assert run.opened == [], "HTTPS bersertifikat sendiri: browser laptop tidak dibuka otomatis"
    assert "cloudflared tidak ditemukan" in out and tunnel.INSTALL_HINT in out
    assert terminal_qr_to_text(out) == info["url"]


def test_tanpa_hp_perilaku_lama_tidak_berubah(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(tunnel, "find_cloudflared", lambda: pytest.fail("cloudflared tidak boleh dicari tanpa --hp"))
    run = Run(monkeypatch, tmp_path)
    run.kw.update(hp=False)
    with run as app:
        assert app.token is None and app.connect_info()["state"] == "off"
        assert Client(app.port).req("GET", "/api/connect")[1]["state"] == "off"

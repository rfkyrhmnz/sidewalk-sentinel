"""Tautan HTTPS + QR untuk kamera HP. cloudflared diganti skrip Python tiruan (tanpa jaringan)."""
import re
import sys
import time

import cv2
import numpy as np
import pytest

from trotoar import phone
from trotoar.cli import main

URLS = [
    "https://calm-river-demo-42.trycloudflare.com/?key=Xk3_u9QdP0aZ7mWbN2sLrTqE",
    "https://very-long-random-words-from-cloudflare.trycloudflare.com/?key=abcdefghijklmnopqrstuvwx",
    "https://a.trycloudflare.com/?key=short",
]

FAKE_OK = """
import sys, time
print("2026-11-23T03:30:00Z INF Thank you for trying Cloudflare Tunnel.", flush=True)
print("2026-11-23T03:30:01Z INF Requesting new quick Tunnel on trycloudflare.com...", flush=True)
print('2026-11-23T03:30:01Z ERR retrying https://api.trycloudflare.com/tunnel', flush=True)
print("2026-11-23T03:30:02Z INF |  https://calm-river-demo-42.trycloudflare.com                |", flush=True)
print("ARGS", " ".join(sys.argv[1:]), flush=True)
time.sleep(1.0)      # alamat dicetak lebih dulu; terowongan baru tersambung beberapa detik kemudian
print("2026-11-23T03:30:04Z INF Registered tunnel connection connIndex=0 connection=abc event=0 ip=198.41.192.67 location=sin06 protocol=quic", flush=True)
time.sleep(60)
"""
FAKE_NO_REGISTER = """
import time
print("2026-11-23T03:30:02Z INF |  https://calm-river-demo-42.trycloudflare.com                |", flush=True)
time.sleep(60)
"""
FAKE_DIES_AFTER_URL = """
import sys, time
print("2026-11-23T03:30:02Z INF |  https://calm-river-demo-42.trycloudflare.com                |", flush=True)
print("2026-11-23T03:30:03Z ERR Unable to establish connection with Cloudflare edge", flush=True)
sys.exit(1)
"""
FAKE_FAIL = """
import sys
print("2026-11-23T03:30:01Z ERR failed to request quick Tunnel: dial tcp: lookup api.trycloudflare.com: no such host", flush=True)
sys.exit(1)
"""


def fake(tmp_path, body):
    p = tmp_path / "fake_cloudflared.py"
    p.write_text(body, encoding="utf-8")
    return [sys.executable, str(p)]


def decode(img):
    return cv2.QRCodeDetector().detectAndDecode(img)[0]


@pytest.mark.parametrize("url", URLS)
def test_qr_image_decodes_back_to_the_url(url):
    assert decode(phone.qr_image(url)) == url


def test_qr_png_is_saved_and_readable(tmp_path):
    p = tmp_path / "qr_iphone.png"
    phone.save_qr_png(URLS[0], p)
    img = cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    assert decode(img) == URLS[0]


def test_qr_terminal_uses_forced_colours_in_both_styles():
    half = phone.qr_terminal(URLS[0], half_blocks=True)
    full = phone.qr_terminal(URLS[0], half_blocks=False)
    n = phone.qr_matrix(URLS[0]).shape[0] + 4                      # + zona hening 2 di tiap sisi
    assert len(half.splitlines()) == (n + 1) // 2 and len(full.splitlines()) == n
    assert "\x1b[30;40m" in half or "\x1b[97;40m" in half          # warna eksplisit, tidak bergantung tema terminal
    assert "\x1b[107m" in full and "\x1b[40m" in full
    assert all(ord(c) < 128 for c in full.replace("\x1b", ""))     # gaya penuh hanya ASCII (aman untuk konsol lama)


def test_find_url_ignores_api_host():
    assert phone.find_url("ERR retry https://api.trycloudflare.com/tunnel") is None
    assert phone.find_url("|  https://calm-river-42.trycloudflare.com   |") == "https://calm-river-42.trycloudflare.com"
    assert phone.find_url("INF Requesting new quick Tunnel on trycloudflare.com...") is None


def test_start_tunnel_reads_url_and_passes_expected_arguments(tmp_path):
    t = phone.start_tunnel(fake(tmp_path, FAKE_OK), 8765, timeout=15)
    try:
        assert t.url == "https://calm-river-demo-42.trycloudflare.com"
        for _ in range(40):
            if any(line.startswith("ARGS") for line in t.tail):
                break
            time.sleep(0.1)
        args = next(line for line in t.tail if line.startswith("ARGS"))
        assert "tunnel --no-autoupdate --url http://127.0.0.1:8765" in args     # 127.0.0.1, bukan localhost (IPv6)
    finally:
        t.close()
    assert t.proc.poll() is not None


def test_start_tunnel_waits_until_connection_is_registered(tmp_path):
    """Alamat sudah muncul sebelum terowongan tersambung (Error 1033 bila dibuka saat itu): tunggu penandanya."""
    t0 = time.time()
    t = phone.start_tunnel(fake(tmp_path, FAKE_OK), 8765, timeout=15)
    try:
        assert t.ready is True and time.time() - t0 >= 0.9
    finally:
        t.close()


def test_start_tunnel_without_ready_marker_still_returns_url_not_ready(tmp_path):
    t0 = time.time()
    t = phone.start_tunnel(fake(tmp_path, FAKE_NO_REGISTER), 8765, timeout=15, ready_timeout=0.5)
    try:
        assert t.url == "https://calm-river-demo-42.trycloudflare.com" and t.ready is False
        assert time.time() - t0 < 10
    finally:
        t.close()


def test_start_tunnel_process_dying_after_url_is_an_error(tmp_path):
    with pytest.raises(phone.TunnelError) as e:
        phone.start_tunnel(fake(tmp_path, FAKE_DIES_AFTER_URL), 8765, timeout=15, ready_timeout=5)
    assert "setelah memberi alamat" in str(e.value) and "Unable to establish" in str(e.value)


def test_session_adds_hint_when_tunnel_not_confirmed(tmp_path, monkeypatch):
    out: list[str] = []
    orig = phone.start_tunnel
    monkeypatch.setattr(phone, "start_tunnel", lambda cmd, port, timeout=45.0, ready_timeout=25.0: orig(cmd, port, timeout, 0.5))
    s = phone.PhoneSession(8765, "K", tmp_path, phone.PhoneOptions(open_image=False), fake(tmp_path, FAKE_NO_REGISTER),
                           out=out.append, timeout=15)
    s.start()
    try:
        assert s.done.wait(20)
    finally:
        s.close()
    text = "\n".join(out)
    assert "belum melaporkan tersambung" in text and "Error 1033" in text


def test_start_tunnel_failure_shows_last_output(tmp_path):
    with pytest.raises(phone.TunnelError) as e:
        phone.start_tunnel(fake(tmp_path, FAKE_FAIL), 8765, timeout=15)
    assert "no such host" in str(e.value) and "berhenti sebelum memberi alamat" in str(e.value)


def test_start_tunnel_missing_binary(tmp_path):
    with pytest.raises(phone.TunnelError):
        phone.start_tunnel([str(tmp_path / "tidak-ada")], 8765)


def test_session_prints_qr_with_key_and_saves_png(tmp_path):
    out: list[str] = []
    s = phone.PhoneSession(8765, "KUNCI-RAHASIA", tmp_path, phone.PhoneOptions(open_image=False),
                           fake(tmp_path, FAKE_OK), out=out.append, timeout=15)
    s.start()
    assert s.done.wait(20)
    try:
        expect = "https://calm-river-demo-42.trycloudflare.com/?key=KUNCI-RAHASIA"
        assert s.url == expect
        text = "\n".join(out)
        assert expect in text and "iPhone" in text and "\x1b[" in text            # tautan + QR berwarna tercetak
        png = tmp_path / "qr_iphone.png"
        img = cv2.imdecode(np.fromfile(str(png), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        assert decode(img) == expect
    finally:
        s.close()


def test_session_failure_calls_on_fail(tmp_path):
    out, failed = [], []
    s = phone.PhoneSession(8765, "k", tmp_path, phone.PhoneOptions(open_image=False), fake(tmp_path, FAKE_FAIL),
                           out=out.append, on_fail=lambda: failed.append(1), timeout=15)
    s.start()
    assert s.done.wait(20)
    assert failed == [1] and any("no such host" in line for line in out)


def test_cli_phone_without_cloudflared_exits_with_install_hint(capsys, tmp_path):
    with pytest.raises(SystemExit) as e:
        main(["phone", "--cloudflared", str(tmp_path / "tidak-ada"), "--out", str(tmp_path / "o")])
    assert e.value.code == 1
    err = capsys.readouterr().err
    assert "winget install" in err and "brew install cloudflared" in err
    assert not (tmp_path / "o").exists()                           # gagal cepat, sebelum membuat apa pun


def test_find_cloudflared_explicit_path(tmp_path):
    f = tmp_path / "cf"
    f.write_text("x")
    assert phone.find_cloudflared(str(f)) == str(f)
    assert phone.find_cloudflared(str(tmp_path / "nope")) is None
    assert re.search(r"cloudflared", phone.INSTALL_HINT)

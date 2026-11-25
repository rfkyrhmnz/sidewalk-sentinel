"""Uji peramban untuk mode kamera langsung. Dilewati bila Playwright atau Chrome tidak tersedia.

Regresi yang dijaga: elemen <video> otomatis di-pause saat dikeluarkan dari dokumen. Perpindahan dari
pratinjau ke editor area ke pemantauan menggantinya (innerHTML); bila tidak diputar ulang, pratinjau
beku dan server menerima frame yang sama terus, sehingga tidak ada deteksi sama sekali.
"""
from __future__ import annotations

import shutil
import threading

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from sidewalk_sentinel.server import make_server  # noqa: E402

# Kamera tiruan: kanvas beranimasi, diambil sebagai MediaStream. Tidak butuh izin kamera sungguhan.
FAKE_CAMERA = """
navigator.mediaDevices.getUserMedia = async () => {
  const c = document.createElement('canvas'); c.width = 640; c.height = 360;
  const x = c.getContext('2d'); let n = 0;
  setInterval(() => { n += 1; x.fillStyle = '#556'; x.fillRect(0, 0, 640, 360); x.fillStyle = '#fc3'; x.fillRect((n * 7) % 600, 150, 40, 30); }, 50);
  return c.captureStream(15);
};
"""


@pytest.fixture()
def base_url(tmp_path):
    server, app = make_server("127.0.0.1", 0, tmp_path / "data")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{app.port}/"
    server.shutdown()
    server.server_close()


@pytest.fixture()
def browser():
    with sync_api.sync_playwright() as p:
        exe = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
        try:
            b = p.chromium.launch(executable_path=exe, args=["--no-sandbox"]) if exe else p.chromium.launch(args=["--no-sandbox"])
        except Exception as e:                       # noqa: BLE001  - tak ada peramban: lewati
            pytest.skip(f"Chrome/Chromium tidak tersedia: {e}")
        yield b
        b.close()


def test_pratinjau_tetap_berjalan_setelah_pindah_tampilan(base_url, browser):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    page.add_init_script(FAKE_CAMERA)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    page.goto(base_url + "#/live")
    page.click("#on")
    page.wait_for_selector("#freeze")
    page.click("#freeze")
    page.wait_for_selector("canvas.editor")
    page.click("#preset")
    page.click("#go")
    page.wait_for_selector("#lstat")                 # tampilan pemantauan: <video> dipasang ulang di sini

    t1 = page.evaluate("() => document.querySelector('video').currentTime")
    page.wait_for_timeout(1500)
    state = page.evaluate("() => { const v = document.querySelector('video'); return [v.paused, v.currentTime]; }")
    assert state[0] is False, "video berhenti setelah dipasang ulang"
    assert state[1] - t1 > 1.0, f"pratinjau beku: currentTime {t1} -> {state[1]}"

    # Server benar-benar menerima frame yang berganti, bukan satu frame berulang.
    page.wait_for_function("() => document.getElementById('lstat').textContent.includes('Memantau')", timeout=20000)
    assert not errors, errors
    ctx.close()

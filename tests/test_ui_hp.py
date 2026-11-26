"""Halaman /hp di Chrome: kode QR yang tampil di layar laptop harus terbaca kamera dan mengarah ke tautan berkunci.

Memakai fixture dari test_ui_pantau (server, browser, page, api). Dilewati bila Playwright atau Chrome tidak ada.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")
cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import Client, Phone  # noqa: E402
from test_ui_pantau import api, browser, page, server  # noqa: E402,F401  (fixture)

URL = "https://quiet-tea-1234.trycloudflare.com/?k=AbCdEfGh12345678"


@pytest.fixture(autouse=True)
def reset_connect(server):
    yield
    server[0].set_connect(mode=None, state="off", url=None, svg=None, message="")


def read_qr(page) -> str:
    png = page.locator("#qr").screenshot()
    img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
    img = cv2.copyMakeBorder(img, 30, 30, 30, 30, cv2.BORDER_CONSTANT, value=255)
    detector = cv2.QRCodeDetector()
    for scale in (1.0, 1.5, 0.75, 2.0):
        scaled = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
        text = detector.detectAndDecode(scaled)[0]
        if text:
            return text
    return ""


def test_qr_di_layar_terbaca_dan_mengarah_ke_tautan_berkunci(server, api, page):
    app = server[0]
    app.set_connect(mode="tunnel", state="siap", url=URL, message="Siap")
    page.goto(page.base + "hp")
    page.wait_for_selector("#qr[data-ready='true'] svg")
    page.wait_for_selector("#status[data-st='siap']")
    assert "Menunggu HP" in page.inner_text("#status")
    assert read_qr(page) == URL, "kode di layar harus terbaca sebagai tautan HP"
    assert page.locator("#copy").is_enabled()
    assert page.evaluate("document.documentElement.dataset.state") == "mencari_trotoar"
    text = page.inner_text("body")
    assert "Jangan bagikan" in text and "Hubungkan HP" in text


def test_menunggu_tunnel_lalu_kode_muncul_sendiri(server, api, page):
    app = server[0]
    app.set_connect(mode="tunnel", state="memulai", message="Membuat tunnel Cloudflare, tunggu beberapa detik.")
    page.goto(page.base + "hp")
    page.wait_for_selector("#status[data-st='memulai']")
    assert page.locator("#qr[data-ready='false'] .qr-wait").count() == 1 and page.locator("#copy").is_disabled()
    app.set_connect(state="siap", url=URL, message="Siap")
    page.wait_for_selector("#qr[data-ready='true'] svg", timeout=8000)
    assert read_qr(page) == URL and page.locator("#copy").is_enabled()


def test_tunnel_gagal_dan_mode_tidak_aktif_dijelaskan(server, api, page):
    app = server[0]
    page.goto(page.base + "hp")
    page.wait_for_selector("#status[data-st='off']")
    assert "python main.py --hp" in page.inner_text("#status") and page.locator("#copy").is_disabled()
    app.set_connect(mode="tunnel", state="gagal", url=None, message="Tidak bisa menghubungi Cloudflare. Periksa koneksi internet laptop.")
    page.wait_for_selector("#status[data-st='gagal']", timeout=8000)
    assert "Periksa koneksi internet" in page.inner_text("#status")
    assert page.locator("#qr[data-ready='true']").count() == 0 and page.locator("#copy").is_disabled()


def test_hp_yang_sudah_tersambung_ditandai_di_laptop(server, api, page):
    app = server[0]
    app.set_connect(mode="tunnel", state="siap", url=URL, message="Siap")
    page.goto(page.base + "hp")
    page.wait_for_selector("#status[data-st='siap']")
    phone = Phone(Client(app.port), name="Ponsel Andi")
    phone.register()
    frame = np.full((360, 640, 3), 120, np.uint8)
    for _ in range(40):                                                   # beberapa gambar berturut-turut, seperti HP sungguhan
        phone.push(frame)
        if page.locator("#status[data-st='tersambung']").count():
            break
        page.wait_for_timeout(250)
    page.wait_for_selector("#status[data-st='tersambung']", timeout=8000)
    assert "Ponsel Andi" in page.inner_text("#status")
    assert page.evaluate("document.documentElement.dataset.state") == "memantau"

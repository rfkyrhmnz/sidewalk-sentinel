"""Uji peramban untuk antarmuka. Dilewati bila Playwright atau Chrome tidak tersedia.

Yang dijaga:
- aplikasi dibuka -> kamera perangkat ini menyala dan terhubung tanpa tombol mulai, tanpa demo, tanpa pilihan sumber;
- alur utama: zona trotoar ditemukan sendiri oleh model (tanpa menandai apa pun) -> kendaraan parkir terdeteksi langsung ->
  pemberitahuan -> Laporan memuat lamanya; zona manual hanya pilihan tambahan dan bisa kembali ke otomatis;
- kejadian lama tidak diumumkan ulang saat halaman dibuka, dan muat ulang melanjutkan pemantauan;
- model kendaraan yang sedang dimuat atau gagal dimuat dijelaskan dengan tombol muat ulang;
- izin kamera ditolak, konteks tidak aman, dan kamera yang dipakai tab lain dijelaskan, bukan diam;
- nama perangkat tampil sebagai teks, bukan HTML;
- jalur HTTPS dengan kunci, halaman Laporan (grafik, tooltip, tabel, cetak), dan tidak ada sisa fitur lama di UI.
"""
from __future__ import annotations

import ssl
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import Client, ColorDetector, FakeSegmenter, Phone, new_key  # noqa: E402
from sidewalk_sentinel.server import make_server  # noqa: E402
from sidewalk_sentinel.store import now as store_now  # noqa: E402

MOBILE = {"viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True}
ROOT = Path(__file__).resolve().parent.parent

# Kamera tiruan: kanvas yang menggambar jalan, kerb, dan trotoar. `window.__vehicle = true` memunculkan kendaraan diam
# di trotoar. Tidak butuh izin kamera sungguhan.
SCENE = """
navigator.mediaDevices.getUserMedia = async () => {
  const c = document.createElement('canvas'); c.width = 640; c.height = 360;
  const x = c.getContext('2d');
  const draw = () => {
    x.fillStyle = '#5c5c60'; x.fillRect(0, 0, 640, 216);
    x.fillStyle = '#b2b2ac'; x.fillRect(0, 216, 640, 14);
    x.fillStyle = '#8c94a0'; x.fillRect(0, 230, 640, 130);
    x.fillStyle = '#69707a';
    for (let i = 0; i < 640; i += 40) x.fillRect(i, 230, 1, 130);
    for (let j = 230; j < 360; j += 28) x.fillRect(0, j, 640, 1);
    x.fillStyle = '#d2d2d2'; for (let i = 10; i < 640; i += 90) x.fillRect(i, 108, 45, 3);
    if (window.__vehicle) { x.fillStyle = '#2846c8'; x.fillRect(300, 280, 70, 36); x.fillStyle = '#191919'; x.fillRect(300, 309, 70, 7); }
  };
  draw(); setInterval(draw, 66);
  return c.captureStream(15);
};
"""


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    server, app = make_server("127.0.0.1", 0, tmp_path_factory.mktemp("data"), detector=ColorDetector(), sidewalk=FakeSegmenter())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield app, f"http://127.0.0.1:{app.port}/"
    app.close()
    server.shutdown()
    server.server_close()


@pytest.fixture()
def api(server):
    c = Client(server[0].port)
    yield c
    st, d = c.req("GET", "/api/cams")                      # bersihkan: tiap tes mulai dari keadaan kosong
    for cam in (d or {}).get("cams", []):
        c.req("DELETE", f"/api/cams/{cam['id']}")


@pytest.fixture(scope="module")
def browser():
    import shutil
    with sync_api.sync_playwright() as p:
        exe = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
        try:
            b = p.chromium.launch(executable_path=exe, args=["--no-sandbox"]) if exe else p.chromium.launch(args=["--no-sandbox"])
        except Exception as e:                       # noqa: BLE001
            pytest.skip(f"Chrome/Chromium tidak tersedia: {e}")
        yield b
        b.close()


@pytest.fixture()
def page(browser, server):
    """Halaman baru. `api` dipasang lebih dulu oleh tes (urutan argumen), sehingga halaman ditutup sebelum `api`
    membersihkan perangkat; bila terbalik, halaman yang masih hidup mendaftar ulang dan meninggalkan perangkat sisa."""
    ctx = browser.new_context(**MOBILE)
    pg = ctx.new_page()
    pg.base = server[1]
    pg.js_errors = []
    pg.on("pageerror", lambda e: pg.js_errors.append(str(e)))
    yield pg
    ctx.close()
    assert not pg.js_errors, pg.js_errors


def cams(api):
    return api.req("GET", "/api/cams")[1]["cams"]


def only_cam(api, page, timeout=15):
    """Perangkat milik halaman ini: pendaftaran bersifat idempoten menurut kunci di localStorage, jadi jawabannya
    selalu perangkat yang sama dengan yang dipakai halaman (bukan sekadar yang pertama di daftar)."""
    api.wait(lambda: cams(api), timeout, "perangkat belum terdaftar di server")
    key = page.evaluate("localStorage.getItem('ss_device')")
    st, cam = api.req("POST", "/api/device", {"key": key, "name": ""})
    assert st == 200, (st, cam)                                       # 200 = sudah ada; 201 berarti baru dibuat
    return cam


def start_monitoring(page, api, park_seconds=2):
    """Dari halaman baru sampai status memantau, tanpa menyentuh server selain lewat UI dan API publik."""
    page.goto(page.base)
    page.wait_for_selector("#vf video", timeout=15000)
    cam = only_cam(api, page)                                          # zona dicari model sendiri: tidak ada langkah menandai
    assert api.req("POST", f"/api/cams/{cam['id']}", {"settings": {"park_seconds": park_seconds}})[0] == 200
    page.wait_for_selector("#conn[data-st='memantau']", timeout=30000)
    return cam


# ===================================================================== alur utama
def test_dibuka_langsung_menyalakan_kamera_tanpa_pilihan_sumber(server, api, page):
    page.add_init_script(SCENE)
    page.goto(server[1])
    page.wait_for_selector("#vf video", timeout=15000)                # kamera menyala sendiri
    cam = only_cam(api, page)
    assert cam["name"] and api.wait(lambda: api.req("GET", f"/api/cams/{cam['id']}")[1]["status"] in ("menunggu", "mencari_trotoar", "memantau"), 10)
    text = page.inner_text("body").lower()
    for bad in ("demo", "rtsp", "unggah", "upload", "url", "alamat kamera", "tambah kamera", "tahapan", "materi", "analisis"):
        assert bad not in text, f"sisa fitur lama di layar: {bad}"
    assert page.locator("input[type=file], input[type=url]").count() == 0
    assert page.locator("a[href='#/analisis'], a[href^='#/pantau/']").count() == 0
    assert page.locator("#nav a").count() == 4


def test_alur_penuh_dari_zona_sampai_parkir_dan_laporan(server, api, page):
    page.add_init_script(SCENE)
    cam = start_monitoring(page, api)
    cid = cam["id"]

    page.evaluate("window.__vehicle = true")                          # kendaraan datang dan diam di trotoar
    page.wait_for_selector("#recent .event[data-k='parkir']", timeout=30000)
    page.wait_for_selector(".toast")                                  # diumumkan
    page.wait_for_selector(".nav-badge")                              # dan ditandai di menu
    assert api.req("GET", f"/api/cams/{cid}")[1]["counts_today"]["parkir"] == 1
    assert page.inner_text("#cP") == "1"
    assert page.locator("#vf .overlay").count() == 1

    page.wait_for_timeout(1800)
    page.evaluate("window.__vehicle = false")                         # kendaraan pergi

    def lamanya():
        ev = api.req("GET", f"/api/cams/{cid}/events?limit=5")[1]["events"][0]
        return ev.get("durasi_s")

    durasi = api.wait(lamanya, 30, "lama parkir harus tercatat setelah kendaraan pergi")
    assert 2.0 <= durasi <= 25.0, durasi
    assert api.req("GET", f"/api/cams/{cid}")[1]["counts_today"]["parkir"] == 1, "pergi tidak menambah kejadian baru"

    page.click("#nav a[href='#/kejadian']")
    page.wait_for_selector("#feed .event")
    assert page.locator(".nav-badge").count() == 0, "lencana hilang setelah kejadian dilihat"
    page.click("[data-f='naik_trotoar']")
    page.wait_for_selector("#feedEmpty .empty")
    page.click("[data-f='parkir']")
    page.wait_for_selector("#feed .event[data-k='parkir']")
    assert page.locator("#conn[data-st='memantau']").count() == 1, "kamera tetap menyala saat pindah halaman"

    page.click("#nav a[href='#/laporan']")
    page.wait_for_selector(".rp-total")
    assert page.inner_text(".rp-total") == "1" and page.inner_text(".rp-kinds [data-k='parkir'] b") == "1"
    assert tile(page, "Terlama") != "—" and tile(page, "Rata-rata parkir") == tile(page, "Terlama")
    assert "Berlangsung" in page.inner_text("#rpEv .event")


def test_muat_ulang_melanjutkan_pemantauan_dan_tanpa_pengumuman_lama(server, api, page):
    page.add_init_script(SCENE)
    cam = start_monitoring(page, api)
    cid = cam["id"]
    page.evaluate("window.__vehicle = true")
    page.wait_for_selector("#recent .event[data-k='parkir']", timeout=30000)
    page.reload()                                                     # perangkat yang sama melanjutkan sendiri
    page.wait_for_selector("#vf video", timeout=15000)
    api.wait(lambda: api.req("GET", f"/api/cams/{cid}")[1]["status"] == "memantau", 15)
    page.wait_for_timeout(2600)                                       # lebih dari satu putaran
    after = api.req("GET", f"/api/cams/{cid}")[1]
    assert len(cams(api)) == 1, "muat ulang tidak boleh membuat perangkat baru"
    assert after["status"] == "memantau" and after["counts_today"]["parkir"] == 1, "hitungan tidak berubah oleh muat ulang"
    assert page.locator(".toast").count() == 0, "kejadian lama tidak boleh diumumkan lagi"
    assert page.locator(".nav-badge").count() == 0


def test_setelan_menyimpan_dan_menghapus_dengan_dua_ketukan(server, api, page):
    page.add_init_script(SCENE)
    cam = start_monitoring(page, api)
    page.click("#nav a[href='#/setelan']")
    page.wait_for_selector("#sName")
    page.fill("#sName", "Gerbang depan")
    page.click("#sNameSave")
    api.wait(lambda: api.req("GET", f"/api/cams/{cam['id']}")[1]["name"] == "Gerbang depan", 10)
    page.fill("#sName", "Gerbang depan")
    page.evaluate("() => { const r = document.getElementById('sPark'); r.value = 30; r.dispatchEvent(new Event('input')); }")
    page.click("#sSave")
    api.wait(lambda: api.req("GET", f"/api/cams/{cam['id']}")[1]["settings"]["park_seconds"] == 30, 10)
    saved = api.req("GET", f"/api/cams/{cam['id']}")[1]["settings"]
    assert set(saved) == {"park_seconds", "conf", "min_box_frac"} and saved["conf"] == 0.30, saved
    assert page.locator("#sPed").count() == 0, "pengaturan pejalan kaki dari metode lama sudah dihapus"
    assert "dicari otomatis" in page.inner_text("#sZoneText"), "Setelan menjelaskan zona otomatis"

    page.click("#sDel")                                               # ketukan pertama hanya meminta konfirmasi
    assert "Ketuk lagi" in page.inner_text("#sDel")
    assert api.req("GET", f"/api/cams/{cam['id']}")[0] == 200
    page.click("#sDel")
    api.wait(lambda: [c["id"] for c in cams(api)] not in ([cam["id"]], []), 15, "perangkat tidak mulai dari awal")   # data lama hilang, perangkat mendaftar ulang
    page.wait_for_selector("#conn[data-st='memantau']", timeout=30000)   # perangkat mendaftar ulang dan menemukan zona lagi


# ===================================================================== zona dan model
def test_zona_ditemukan_sendiri_tanpa_menandai_apa_pun(server, api, page):
    app = server[0]
    page.add_init_script(SCENE)
    page.goto(page.base)
    page.wait_for_selector("#conn[data-st='memantau']", timeout=30000)                # langsung memantau, tanpa tombol mulai
    assert page.locator("#vf[data-st='edit']").count() == 0 and page.locator("text=Mulai pantau").count() == 0
    assert page.locator("#zSave, #zAuto").count() == 0, "penyunting zona tidak muncul dengan sendirinya"
    chip = page.locator("#zoneChip")
    chip.wait_for(state="visible")
    assert chip.inner_text() == "Zona otomatis" and chip.get_attribute("data-zone") == "auto"
    cam = only_cam(api, page)
    info = api.req("GET", f"/api/cams/{cam['id']}")[1]
    assert info["zone"] == "auto" and info["auto_available"] is True and len(info["roi"]) >= 3
    assert page.locator("#zoneBtn").inner_text().strip() == "Zona manual"


def test_zona_manual_menggantikan_otomatis_lalu_kembali_ke_otomatis(server, api, page):
    app = server[0]
    lain = [[0.2, 0.75], [0.8, 0.75], [0.8, 0.98], [0.2, 0.98]]
    page.add_init_script(SCENE)
    cam = start_monitoring(page, api)
    cid = cam["id"]
    app.sidewalk.roi, awal = lain, app.sidewalk.roi
    try:
        page.click("#zoneBtn")                                                        # pilihan tambahan: gambar zona sendiri
        page.wait_for_selector("#vf[data-st='edit']")
        assert page.locator("#zAuto").count() == 1 and page.locator("#zBack").count() == 0, "belum manual: belum ada jalan kembali"
        page.click("#zAuto")                                                          # usulan model sebagai titik awal
        page.wait_for_selector("#hint:has-text('Usulan dari model')", timeout=20000)
        page.click("#zSave")
        api.wait(lambda: api.req("GET", f"/api/cams/{cid}")[1]["zone"] == "manual", 10, "zona manual harus tersimpan")
        assert api.req("GET", f"/api/cams/{cid}")[1]["roi"] == lain
        page.wait_for_selector("#zoneChip:has-text('Zona manual')")
        page.wait_for_selector("#conn[data-st='memantau']", timeout=30000)

        page.click("#zoneBtn")
        page.click("#zBack")                                                          # kembali ke zona otomatis
        api.wait(lambda: api.req("GET", f"/api/cams/{cid}")[1]["zone"] == "auto", 10, "harus kembali ke otomatis")
        page.wait_for_selector("#zoneChip:has-text('Zona otomatis')", timeout=20000)
        assert page.locator("#vf[data-st='edit']").count() == 0
    finally:
        app.sidewalk.roi = awal


def test_trotoar_tak_terlihat_berstatus_mencari_dan_zona_manual_tetap_bisa(server, api, page):
    app = server[0]
    app.sidewalk.roi, awal = None, app.sidewalk.roi
    page.add_init_script(SCENE)
    try:
        page.goto(page.base)
        page.wait_for_selector("#vf[data-st='mencari_trotoar']", timeout=20000)
        assert page.locator("#conn[data-st='mencari_trotoar']").count() == 1
        assert "Arahkan kamera ke trotoar" in page.inner_text("#banner") and page.locator("#vf[data-st='edit']").count() == 0
        assert page.evaluate("document.documentElement.dataset.state") == "mencari_trotoar", "cahaya latar mengikuti status"
        page.click("#zoneBtn")                                                        # zona bawaan penyunting bisa disimpan
        page.wait_for_selector("#vf[data-st='edit']")
        page.click("#zSave")
        page.wait_for_selector("#conn[data-st='memantau']", timeout=30000)
        assert api.req("GET", f"/api/cams/{only_cam(api, page)['id']}")[1]["zone"] == "manual"
    finally:
        app.sidewalk.roi = awal


def test_tanpa_model_trotoar_meminta_zona_manual_dan_tombol_usulan_tidak_ditampilkan(server, api, page):
    app = server[0]
    seg, app.sidewalk, app.cams.sidewalk = app.sidewalk, None, None
    page.add_init_script(SCENE)
    try:
        page.goto(page.base)
        page.wait_for_selector("#vf[data-st='edit']", timeout=15000)                  # tanpa model, satu-satunya jalan adalah menandai
        assert page.locator("#zAuto").count() == 0
        page.click("#nav a[href='#/setelan']")
        page.wait_for_selector("#sModel")
        assert "models/trotoar.pt" in page.inner_text("main, #view, body")
    finally:
        app.sidewalk = app.cams.sidewalk = seg


def test_model_yang_gagal_dimuat_dijelaskan_dan_tombol_muat_ulang_memulihkannya(server, api, page):
    det = server[0].detector
    page.add_init_script(SCENE)
    cam = start_monitoring(page, api)
    det.state, det.message = "galat", "Paket ultralytics belum terpasang. Jalankan: pip install ultralytics"
    page.wait_for_selector("#vf[data-st='model_galat']", timeout=15000)
    assert "pip install ultralytics" in page.inner_text("#banner")
    assert page.locator("#conn[data-st='model_galat']").count() == 1
    page.click("#retryModel")                                                         # server mencoba memuat lagi
    page.wait_for_selector("#vf[data-st='memantau']", timeout=20000)
    assert det.retried >= 1 and page.locator("#retryModel").count() == 0


def test_model_yang_sedang_dimuat_ditampilkan_sebagai_status_bukan_galat(server, api, page):
    det = server[0].detector
    page.add_init_script(SCENE)
    cam = start_monitoring(page, api)
    det.state, det.message = "memuat", "Memuat model YOLO"
    try:
        page.wait_for_selector("#vf[data-st='memuat']", timeout=15000)
        assert "diunduh sekali" in page.inner_text("#banner")
    finally:
        det.state, det.message = "siap", ""
    page.wait_for_selector("#vf[data-st='memantau']", timeout=20000)


# ===================================================================== kamera tidak tersedia
def test_izin_kamera_ditolak_dijelaskan_dengan_tombol_coba_lagi(server, api, page):
    page.add_init_script("navigator.mediaDevices.getUserMedia = async () => { throw new DOMException('ditolak', 'NotAllowedError'); };")
    page.goto(server[1])
    page.wait_for_selector(".vf-blocked:not([hidden]) .blocked-card")
    card = page.inner_text(".blocked-card")
    assert "Izin kamera ditolak" in card and page.locator("#bAct").inner_text() == "Coba lagi"
    assert page.locator("#conn[data-st='off']").count() == 1
    assert cams(api) == [] or all(c["status"] in ("menunggu", "perlu_area") for c in cams(api))


def test_tanpa_koneksi_aman_menjelaskan_cara_memperbaiki(server, api, page):
    page.add_init_script("Object.defineProperty(window, 'isSecureContext', { value: false });")
    page.goto(server[1])
    page.wait_for_selector(".vf-blocked:not([hidden]) .blocked-card")
    card = page.inner_text(".blocked-card")
    assert "python main.py --https" in card and "localhost" in card
    assert cams(api) == [], "tanpa kamera tidak ada yang perlu didaftarkan"


def test_kamera_yang_dipakai_tab_lain_dijelaskan_dan_bisa_diambil_alih(browser, server, api):
    ctx = browser.new_context(**MOBILE)
    ctx.add_init_script(SCENE)
    a, b = ctx.new_page(), ctx.new_page()
    errors = []
    for p in (a, b):
        p.on("pageerror", lambda e: errors.append(str(e)))
    try:
        a.goto(server[1])
        a.wait_for_selector("#vf video", timeout=15000)
        b.goto(server[1])                                             # perangkat yang sama, tab kedua
        b.wait_for_selector(".vf-blocked:not([hidden]) .blocked-card", timeout=15000)
        assert "tab" in b.inner_text(".blocked-card").lower()
        assert len(cams(api)) == 1, "tab kedua tidak boleh membuat perangkat lain"
        b.click("#bAct")                                              # ambil alih
        b.wait_for_selector("#vf video", timeout=15000)
    finally:
        ctx.close()
    assert not errors, errors


def test_nama_perangkat_tampil_sebagai_teks_bukan_html(server, api, page):
    jahat = '<img src=x onerror="window.__xss=1">'
    page.add_init_script(SCENE)
    cam = start_monitoring(page, api)
    api.req("POST", f"/api/cams/{cam['id']}", {"name": jahat})
    other = Phone(api, name="Normal")
    other.register()
    page.click("#nav a[href='#/setelan']")
    page.wait_for_selector("#sName")
    page.wait_for_timeout(400)
    assert page.evaluate("window.__xss") is None, "nama dijalankan sebagai HTML"
    assert page.locator("img[src='x']").count() == 0
    page.click("#nav a[href='#/laporan']")
    page.wait_for_selector(".rp-total, .empty")
    assert [o.text_content() for o in page.locator("#rpDev option").all()][:1] == ["Semua perangkat"]
    page.wait_for_timeout(300)
    assert page.evaluate("window.__xss") is None and page.locator("img[src='x']").count() == 0


def test_https_dengan_kunci_kamera_perangkat_mengirim_lewat_tls(tmp_path, browser):
    pytest.importorskip("cryptography")
    token = "kunci-uji-https"
    srv, app = make_server("127.0.0.1", 0, tmp_path / "data", lan=True, token=token, https=True, hosts=["127.0.0.1"],
                           detector=ColorDetector())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"https://127.0.0.1:{app.port}/"
    ctx = browser.new_context(**MOBILE, ignore_https_errors=True)     # setara menekan Lanjutkan pada peringatan sertifikat
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(SCENE)
    try:
        page.goto(base + f"?k={token}")                               # tautan berkunci: cookie dipasang lewat HTTPS
        page.wait_for_selector("#vf video", timeout=20000)
        assert page.evaluate("window.isSecureContext") is True
        cookies = {c["name"]: c for c in ctx.cookies()}
        assert cookies["ss_key"]["secure"] is True and cookies["ss_key"]["httpOnly"] is True

        def lst():
            return ctx.request.get(base + "api/cams").json()["cams"]

        deadline = time.time() + 15
        while (not lst() or lst()[0]["status"] == "menunggu") and time.time() < deadline:
            time.sleep(0.3)
        assert lst() and lst()[0]["status"] == "perlu_area", "frame dari perangkat tidak sampai ke server lewat HTTPS"

        anon = ssl.create_default_context()                           # klien tanpa cookie; sertifikat uji tidak dipercaya
        anon.check_hostname, anon.verify_mode = False, ssl.CERT_NONE
        assert Client(app.port, https=True).req("GET", "/api/cams")[0] == 403, "tanpa cookie kunci, API tetap tertutup"
    finally:
        ctx.close()
        app.close()
        srv.shutdown()
        srv.server_close()
    assert not errors, errors


# ===================================================================== bersih dari fitur lama
def test_tidak_ada_penyebutan_materi_kuliah_atau_sumber_lain():
    """Teknik pengolahan citra hanyalah alat; aplikasi tidak memetakan dirinya ke materi kuliah dan tidak punya sumber
    lain selain kamera perangkat."""
    everywhere = ("materi", "tahapan", "citra komputer")
    surface = ("rtsp", "demo", "unggah", "upload", "analisis")                    # antarmuka, README, dan pintu masuk
    ui = [*(ROOT / "web").glob("*.js"), *(ROOT / "web").glob("*.html"), *(ROOT / "web").glob("*.css"),
          ROOT / "web" / "manifest.webmanifest", ROOT / "README.md", ROOT / "main.py"]
    code = [*(ROOT / "sidewalk_sentinel").glob("*.py"), *(ROOT / "tests").glob("*.py"), ROOT / "tools" / "make_icons.py"]
    for f in ui + code:
        text = f.read_text(encoding="utf-8").lower()
        banned = everywhere + (surface if f in ui else ())
        for word in banned:
            if f.name == "test_ui_pantau.py":                                     # file ini menyebut kata-kata itu sebagai daftar larangan
                continue
            assert word not in text, f"{f.name} masih memuat '{word}'"


# ===================================================================== laporan
def jpeg_bytes() -> bytes:
    import cv2
    import numpy as np
    img = np.full((360, 640, 3), (90, 90, 96), np.uint8)
    img[230:] = (160, 148, 140)
    return cv2.imencode(".jpg", img)[1].tobytes()


def seed(app, cid, kind, when, durasi=None):
    """Kejadian dengan waktu tertentu lewat API publik EventStore (tanpa menunggu kamera mendeteksi apa pun)."""
    store = app.cams.get(cid).events
    rec = store.append({"kind": kind, "label": kind, "reason": "uji laporan", "track_id": 1}, jpeg_bytes(), when=when)
    if durasi is not None:
        store.patch(rec["no"], durasi_s=durasi)
    return rec


def midnight():
    return store_now().replace(hour=0, minute=0, second=0, microsecond=0)


def tile(page, label):
    """Nilai kartu ringkasan menurut labelnya; spasi tak terputus dinormalkan."""
    return page.locator(f".rp-tile:has(span:text-is('{label}')) b").inner_text().replace(" ", " ")


def add_device(api, name):
    ph = Phone(api, name=name)
    ph.register()
    return ph.id


def test_laporan_ringkasan_periode_dan_filter_perangkat(server, api, page):
    app, url = server
    a, b = add_device(api, "Depan toko"), add_device(api, "Gerbang")
    m = midnight()
    seed(app, a, "parkir", m + timedelta(seconds=5), 60)
    seed(app, a, "naik_trotoar", m + timedelta(seconds=6), 20)
    seed(app, b, "parkir", m + timedelta(seconds=7), 10)
    seed(app, a, "parkir", m - timedelta(days=3), 30)                  # di luar 'hari ini'
    page.goto(url + "#/laporan")
    page.wait_for_selector(".rp-total")
    assert page.inner_text(".rp-total") == "3"
    assert page.inner_text(".rp-kinds [data-k='parkir'] b") == "2" and page.inner_text(".rp-kinds [data-k='naik_trotoar'] b") == "1"
    assert page.locator(".rp-cams tbody tr").count() == 2
    page.click("[data-p='7_hari']")
    page.wait_for_function("() => document.querySelector('.rp-total').textContent === '4'")
    page.select_option("#rpDev", b)
    page.wait_for_function("() => document.querySelector('.rp-total').textContent === '1'")
    assert page.locator(".rp-cams").count() == 0, "satu perangkat: tidak ada rincian per perangkat"
    page.reload()                                                      # pilihan periode dan perangkat diingat
    page.wait_for_function("() => document.querySelector('.rp-total') && document.querySelector('.rp-total').textContent === '1'")
    assert page.locator("[data-p='7_hari'][aria-pressed='true']").count() == 1


def test_laporan_grafik_per_jam_tooltip_papan_ketik_dan_tabel(server, api, page):
    app, url = server
    cid = add_device(api, "Depan toko")
    day = midnight() - timedelta(days=3)
    seed(app, cid, "parkir", day.replace(hour=7, minute=10))
    seed(app, cid, "naik_trotoar", day.replace(hour=7, minute=40))
    seed(app, cid, "parkir", day.replace(hour=8, minute=5))

    page.goto(url + "#/laporan")
    page.click("[data-p='7_hari']")
    page.wait_for_selector("#rpJam .rc-hit")
    assert page.locator("#rpJam .rc-hit").count() == 24
    assert page.locator("#rpJam .rc-peak").text_content() == "2" and "Paling ramai pukul 07.00, 2 kejadian" in page.inner_text(".rp")
    hit = page.locator("#rpJam .rc-hit")
    assert hit.nth(7).get_attribute("aria-label") == "Pukul 07.00: 1 parkir, 1 naik trotoar"
    assert page.locator("#rpJam .rc-tip").is_hidden()

    hit.nth(7).focus()                                                           # papan ketik: sama seperti hover
    tip = page.locator("#rpJam .rc-tip")
    tip.wait_for(state="visible")
    flat = lambda: " ".join(tip.inner_text().split())                            # noqa: E731
    assert flat() == "Pukul 07.00 1 parkir 1 naik trotoar"
    page.keyboard.press("Tab")
    assert flat() == "Pukul 08.00 1 parkir 0 naik trotoar"
    page.locator("#rpJam .rc-toggle").focus()
    assert tip.is_hidden(), "tooltip tertutup saat fokus pergi"

    toggle = page.locator("#rpJam .rc-toggle")
    assert toggle.get_attribute("aria-expanded") == "false" and page.locator("#rpJam .rc-table").is_hidden()
    toggle.click()
    assert toggle.get_attribute("aria-expanded") == "true"
    rows = [[c.inner_text() for c in r.locator("td").all()] for r in page.locator("#rpJam .rc-table tbody tr").all()]
    assert rows == [["Pukul 07.00", "1", "1", "2"], ["Pukul 08.00", "1", "0", "1"]], "hanya jam yang ada kejadiannya"


def test_laporan_keadaan_kosong_memberi_arah(server, api, page):
    app, url = server
    page.goto(url + "#/laporan")
    page.wait_for_selector(".empty")
    assert "Belum ada kejadian pada periode ini" in page.inner_text(".empty") and page.locator(".empty a[href='#/']").count() == 1
    assert page.locator("#rpJam").count() == 0 and page.locator(".notice").count() == 0


@pytest.mark.parametrize("width", [320, 360, 390])
def test_menu_bawah_empat_tab_tetap_satu_baris_di_bawah(server, page, width):
    app, url = server
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(url + "#/laporan")
    page.wait_for_selector("#nav a")
    boxes = [a.bounding_box() for a in page.locator("#nav a").all()]
    assert len(boxes) == 4
    assert len({round(b["y"]) for b in boxes}) == 1, f"tab menu jatuh ke baris lain: {[round(b['y']) for b in boxes]}"
    assert all(b["x"] >= 0 and b["x"] + b["width"] <= width + 1 for b in boxes)
    assert boxes[0]["y"] > 800 - 100, "menu harus menempel di bawah layar HP"
    assert page.locator("#nav a[aria-current='page']").inner_text().strip().startswith("Laporan")
    chip = page.locator("#conn").bounding_box()
    assert chip["x"] >= 0 and chip["x"] + chip["width"] <= width + 1, "penanda status terpotong di tepi layar"


def test_desktop_menu_di_sisi_kiri(server, browser):
    ctx = browser.new_context(viewport={"width": 1366, "height": 800})
    pg = ctx.new_page()
    try:
        pg.goto(server[1] + "#/laporan")
        pg.wait_for_selector("#nav a")
        boxes = [a.bounding_box() for a in pg.locator("#nav a").all()]
        assert len({round(b["x"]) for b in boxes}) == 1 and boxes[0]["x"] < 60
        assert [round(b["y"]) for b in boxes] == sorted(round(b["y"]) for b in boxes)
    finally:
        ctx.close()


def test_laporan_dicetak_putih_dengan_tabel_dan_tanpa_menu(server, api, page, tmp_path):
    app, url = server
    cid = add_device(api, "Depan toko")
    day = midnight() - timedelta(days=2)
    seed(app, cid, "parkir", day.replace(hour=7, minute=10), 30)
    seed(app, cid, "naik_trotoar", day.replace(hour=17, minute=40), 5)
    page.goto(url + "#/laporan")
    page.click("[data-p='7_hari']")
    page.wait_for_selector("#rpJam .rc-hit")
    page.emulate_media(media="print")
    assert page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(255, 255, 255)"
    assert page.locator("#nav").is_hidden() and page.locator(".rc-table").first.is_visible(), "tabel angka ikut tercetak"
    assert page.locator(".rp-print").is_visible() and "Perangkat: semua perangkat" in page.inner_text(".rp-print")
    out = tmp_path / "laporan.pdf"
    page.pdf(path=str(out), format="A4", print_background=True)
    assert out.read_bytes()[:5] == b"%PDF-"
    pypdf = pytest.importorskip("pypdf")
    text = "\n".join(p.extract_text() for p in pypdf.PdfReader(str(out)).pages)
    for needle in ("Laporan Sidewalk Sentinel", "Kejadian per jam", "Pukul 07.00", "Pukul 17.00", "Bukti terbaru"):
        assert needle in text, needle
    assert "Setelan" not in text, "menu navigasi tidak boleh ikut tercetak"

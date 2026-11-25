"""Uji peramban untuk mode pantau (kamera CCTV). Dilewati bila Playwright atau Chrome tidak tersedia.

Yang dijaga:
- alur utama: keadaan kosong -> kamera demo -> memantau -> kejadian, dengan toast dan lencana kejadian baru;
- kejadian lama tidak diumumkan ulang saat halaman dibuka;
- kamera statis (selesai/jeda) menampilkan gambarnya segera (Chrome baru menggambar bagian multipart
  setelah bagian berikutnya mulai datang);
- siaran MJPEG dilepas saat pindah halaman (penonton siaran dibatasi);
- tandai area untuk kamera rekaman, jeda/lanjut, simpan pengaturan, hapus dengan dua ketukan;
- galat formulir tampil sebagai pesan, bukan diam.
"""
from __future__ import annotations

import json
import shutil
import threading
import time
import urllib.error
import urllib.request

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from sidewalk_sentinel.cameras import MAX_STREAMS  # noqa: E402
from sidewalk_sentinel.server import make_server  # noqa: E402

MOBILE = {"viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True}


class Api:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def __call__(self, method: str, path: str, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    def wait(self, pred, timeout=60.0, msg="kondisi tidak tercapai"):
        t0 = time.time()
        while time.time() - t0 < timeout:
            v = pred()
            if v:
                return v
            time.sleep(0.2)
        raise AssertionError(msg)

    def cam(self, cid):
        st, d = self("GET", f"/api/cams/{cid}")
        return d if st == 200 else None


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    server, app = make_server("127.0.0.1", 0, tmp_path_factory.mktemp("data"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield app, f"http://127.0.0.1:{app.port}/"
    app.close()
    server.shutdown()
    server.server_close()


@pytest.fixture()
def api(server):
    a = Api(server[1])
    yield a
    st, d = a("GET", "/api/cams")                      # bersihkan: tiap tes mulai dari keadaan kosong
    for c in (d or {}).get("cams", []):
        a("DELETE", f"/api/cams/{c['id']}")


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        exe = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
        try:
            b = p.chromium.launch(executable_path=exe, args=["--no-sandbox"]) if exe else p.chromium.launch(args=["--no-sandbox"])
        except Exception as e:                       # noqa: BLE001
            pytest.skip(f"Chrome/Chromium tidak tersedia: {e}")
        yield b
        b.close()


@pytest.fixture()
def page(browser):
    ctx = browser.new_context(**MOBILE)
    pg = ctx.new_page()
    pg.js_errors = []
    pg.on("pageerror", lambda e: pg.js_errors.append(str(e)))
    yield pg
    ctx.close()
    assert not pg.js_errors, pg.js_errors


def stream_painted(page):
    return page.wait_for_function(
        "() => { const s = document.getElementById('stream'); return !!s && s.naturalWidth > 0; }", timeout=2000)


def test_demo_dari_keadaan_kosong_sampai_kejadian(server, api, page):
    page.goto(server[1])
    page.click("#demoCam")                            # keadaan kosong: satu ketukan untuk mencoba
    page.wait_for_selector("#stream")
    cid = page.url.split("/pantau/")[1]
    assert api("POST", f"/api/cams/{cid}", {"speed": 8})[0] == 200   # percepat demo agar tes singkat
    page.wait_for_selector("#chip .chip[data-st='memantau']", timeout=30000)
    page.wait_for_selector("#evs .event", timeout=60000)
    page.wait_for_selector(".toast")                  # kejadian baru diumumkan
    page.wait_for_selector(".nav-badge")              # dan ditandai di navigasi
    page.click("a[href='#/kejadian']")
    page.wait_for_selector("#feed .event")
    assert page.locator(".nav-badge").count() == 0, "lencana harus hilang setelah kejadian dilihat"
    page.click("[data-f='parkir']")
    page.wait_for_function("() => document.querySelectorAll('#feed .event[data-k=\"parkir\"]').length > 0")
    assert page.locator("#feed .event[data-k='naik_trotoar']").count() == 0, "filter harus menyaring"


def test_kamera_selesai_langsung_tampil_dan_kejadian_lama_tidak_diumumkan(server, api, page):
    st, d = api("POST", "/api/cams", {"source": "demo", "speed": 0, "max_loops": 1, "name": "Gerbang"})
    cid = d["id"]
    api.wait(lambda: api.cam(cid)["status"] == "selesai", 90, "demo tidak selesai")
    page.goto(server[1] + f"#/pantau/{cid}")
    stream_painted(page)                              # dulu baru tampil setelah ~3 dtk penanda hidup
    page.wait_for_selector("#evs .event")
    page.wait_for_timeout(2600)                       # lebih dari satu putaran polling
    assert page.locator(".toast").count() == 0, "kejadian lama tidak boleh diumumkan lagi"
    assert page.locator(".nav-badge").count() == 0
    assert "Putar ulang" in page.inner_text("#recal")


def test_siaran_dilepas_saat_pindah_halaman(server, api, page):
    app, url = server
    st, d = api("POST", "/api/cams", {"source": "demo", "speed": 1, "max_loops": None})
    cid = d["id"]
    api.wait(lambda: api.cam(cid)["status"] == "memantau", 30)
    page.goto(url + f"#/pantau/{cid}")
    stream_painted(page)
    assert app.cams._streams._value == MAX_STREAMS - 1, "satu siaran seharusnya terbuka"
    page.click(".back")
    page.wait_for_selector(".cam-tile")
    api.wait(lambda: app.cams._streams._value == MAX_STREAMS, 8, "siaran tidak dilepas setelah pindah halaman")


def test_tandai_area_untuk_rekaman_lalu_memantau(server, api, page):
    st, up = api("POST", "/api/demo")
    st, d = api("POST", "/api/cams", {"name": "Rekaman uji", "source": f"upload:{up['upload_id']}", "speed": 8})
    assert st == 201, d
    cid = d["id"]
    page.goto(server[1] + f"#/pantau/{cid}")
    page.wait_for_selector("#state a[href$='/area']")  # belum ada area: ajakan menandai area
    assert api.cam(cid)["status"] == "perlu_area"
    page.click("#state a[href$='/area']")
    page.wait_for_function("() => document.getElementById('aMsg').textContent.startsWith('Gambar')", timeout=20000)
    assert page.is_disabled("#save"), "tanpa titik, tombol simpan harus nonaktif"
    page.click("#preset")
    page.click("#save")
    page.wait_for_selector("#stream")                 # kembali ke halaman kamera
    assert api.cam(cid)["roi"], "area harus tersimpan di server"
    page.wait_for_selector("#evs .event", timeout=60000)
    assert api.cam(cid)["seq"] >= 1


def test_jeda_atur_dan_hapus_dua_ketukan(server, api, page):
    st, d = api("POST", "/api/cams", {"source": "demo", "speed": 1, "max_loops": None, "name": "Uji kontrol"})
    cid = d["id"]
    page.goto(server[1] + f"#/pantau/{cid}")
    page.wait_for_selector("#chip .chip[data-st='memantau']", timeout=30000)

    page.click("#toggle")
    api.wait(lambda: api.cam(cid)["enabled"] is False, 10)
    page.wait_for_selector("#chip .chip[data-st='jeda']")
    assert "Lanjutkan" in page.inner_text("#toggle")
    page.click("#toggle")
    api.wait(lambda: api.cam(cid)["enabled"] is True, 10)

    page.click("#cfg")
    page.wait_for_selector("#sSave")
    page.eval_on_selector("#sPark", "e => { e.value = 20; e.dispatchEvent(new Event('input')); }")
    page.click("[data-size='besar']")
    page.fill("#sName", "Nama baru")
    page.click("#sSave")
    api.wait(lambda: api.cam(cid)["settings"].get("park_seconds") == 20, 10, "pengaturan tidak tersimpan")
    c = api.cam(cid)
    assert c["name"] == "Nama baru" and c["settings"]["min_area_frac"] == 0.004
    page.wait_for_function("() => document.getElementById('cname').textContent === 'Nama baru'")

    page.click("#cfg")
    page.click("#sDel")                               # ketukan pertama hanya meminta konfirmasi
    assert "Ketuk lagi" in page.inner_text("#sDel")
    assert api.cam(cid) is not None, "ketukan pertama tidak boleh menghapus"
    page.click("#sDel")
    api.wait(lambda: api.cam(cid) is None, 10, "kamera tidak terhapus")
    page.wait_for_selector("#demoCam")                # kembali ke keadaan kosong


def test_formulir_tambah_kamera_menjelaskan_galat(server, api, page):
    page.goto(server[1] + "#/tambah")
    page.click("#add")                                # alamat kosong
    assert "alamat kamera" in page.inner_text("#addMsg").lower()
    page.fill("#url", "ftp://contoh.test/video")      # skema tidak diizinkan: pesan dari server
    page.click("#add")
    page.wait_for_selector("#addMsg .notice[role='alert']")
    assert page.inner_text("#addMsg").strip() != "Gagal"
    assert (api("GET", "/api/cams")[1]["cams"]) == []

    page.fill("#url", "http://127.0.0.1:9/tidak-ada")   # sah tetapi tak terjangkau: kamera dibuat, lalu dijelaskan
    page.click("#add")
    page.wait_for_url("**/area")
    cid = page.url.split("/pantau/")[1].split("/")[0]
    api.wait(lambda: api.cam(cid)["status"] in ("terputus", "menghubungkan"), 10)
    page.wait_for_function("() => /belum tersambung/i.test(document.getElementById('aMsg').textContent)", timeout=15000)

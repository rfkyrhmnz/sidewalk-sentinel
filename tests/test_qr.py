"""Pembuat QR: hasilnya harus terbaca pembaca QR sungguhan, bukan hanya terlihat mirip.

- Tautan nyata (alamat tunnel, alamat Wi-Fi, teks UTF-8) dibaca dekoder QR milik OpenCV.
- Semua panjang teks, dari kosong sampai kapasitas versi 10, dibaca balik oleh pembaca kecil di tes ini yang memeriksa
  informasi format, pembuangan topeng, urutan zigzag, penyisipan antar-blok, dan syndrome Reed-Solomon tiap blok.
- Keluaran SVG dan terminal diperiksa bentuknya.
"""
from __future__ import annotations

import random
import string

import pytest

from sidewalk_sentinel import qr

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")


def render(matrix, scale=8, border=4):
    n = len(matrix)
    img = np.full(((n + 2 * border) * scale,) * 2, 255, np.uint8)
    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            if dark:
                img[(y + border) * scale:(y + border + 1) * scale, (x + border) * scale:(x + border + 1) * scale] = 0
    return img


def decode(matrix) -> str:
    detector = cv2.QRCodeDetector()
    for scale, border in ((8, 4), (10, 5), (6, 4), (12, 6)):          # detektor kadang gagal pada satu skala saja
        text = detector.detectAndDecode(render(matrix, scale, border))[0]
        if text:
            return text
    return ""


def rand_text(n, seed):
    rnd = random.Random(seed)
    return "".join(rnd.choice(string.ascii_letters + string.digits + ":/?=.-_") for _ in range(n))


URLS = [
    "https://sharp-quartz-hunting-lemon.trycloudflare.com/?k=AbCdEfGh12345678",
    "https://192.168.100.23:8000/?k=ZyXwVuTs98765432",
    "http://localhost:8000/hp?k=AbCdEfGh12345678",
    "https://a.trycloudflare.com/?k=x",
    "tautan ✓ café",
]


@pytest.mark.parametrize("ecc", ["M", "L"])
@pytest.mark.parametrize("text", URLS)
def test_tautan_nyata_terbaca_dekoder_opencv(text, ecc):
    assert decode(qr.make(text, ecc)) == text


@pytest.mark.parametrize("ecc", ["M", "L"])
def test_teks_terpanjang_terbaca_dekoder_opencv(ecc):
    text = rand_text(qr.capacity(ecc), 7)
    assert decode(qr.make(text, ecc)) == text


# ----------------------------------------------------------------------------------- pembaca mandiri
def gf_pow(a, n):
    r = 1
    for _ in range(n):
        r = qr._gf_mul(r, a)
    return r


def read_back(matrix, ecc) -> bytes:
    n = len(matrix)
    version = (n - 17) // 4
    bits = 0
    cells = [(8, i) for i in range(6)] + [(8, 7), (8, 8), (7, 8)] + [(14 - i, 8) for i in range(9, 15)]
    for i, (x, y) in enumerate(cells):
        bits |= int(matrix[y][x]) << i
    data = (bits ^ 0x5412) >> 10
    assert data >> 3 == qr._FORMAT_ECC[ecc], "kode koreksi pada informasi format salah"
    mask = data & 7

    b = qr._Builder(version, ecc)
    ec_len, groups = qr._BLOCKS[ecc][version]
    n_blocks = sum(g[0] for g in groups)
    n_words = qr._data_codewords(ecc, version) + ec_len * n_blocks
    stream = []
    for right in range(n - 1, 0, -2):
        if right <= 6:
            right -= 1
        for vert in range(n):
            for j in range(2):
                x = right - j
                y = n - 1 - vert if ((right + 1) & 2) == 0 else vert
                if not b.func[y][x]:
                    stream.append(int(matrix[y][x]) ^ int(qr._MASKS[mask](x, y)))
    assert len(stream) >= n_words * 8
    words = [int("".join(map(str, stream[i:i + 8])), 2) for i in range(0, n_words * 8, 8)]

    lengths = [d for cnt, d in groups for _ in range(cnt)]            # kata data tiap blok
    blocks = [[] for _ in lengths]
    k = 0
    for i in range(max(lengths)):
        for bi, d in enumerate(lengths):
            if i < d:
                blocks[bi].append(words[k])
                k += 1
    checks = [[] for _ in lengths]
    for _ in range(ec_len):
        for bi in range(len(lengths)):
            checks[bi].append(words[k])
            k += 1
    for blk, chk in zip(blocks, checks):                               # kata kode sah: semua syndrome nol
        for i in range(ec_len):
            acc, a = 0, gf_pow(2, i)
            for c in blk + chk:
                acc = qr._gf_mul(acc, a) ^ c
            assert acc == 0, "syndrome Reed-Solomon tidak nol"
    flat = [c for blk in blocks for c in blk]
    bitstr = "".join(f"{c:08b}" for c in flat)
    assert bitstr[:4] == "0100", "mode bukan byte"
    cc = 8 if version <= 9 else 16
    count = int(bitstr[4:4 + cc], 2)
    body = bitstr[4 + cc:4 + cc + 8 * count]
    return bytes(int(body[i:i + 8], 2) for i in range(0, len(body), 8))


@pytest.mark.parametrize("ecc", ["M", "L"])
def test_semua_panjang_terbaca_balik_dan_lolos_reed_solomon(ecc):
    for n in range(0, qr.capacity(ecc) + 1):
        text = rand_text(n, 1000 + n)
        assert read_back(qr.make(text, ecc), ecc) == text.encode(), f"panjang {n} ({ecc})"


def test_versi_naik_sesuai_panjang_dan_terlalu_panjang_ditolak():
    assert len(qr.make("x" * 14, "M")) == 21 and len(qr.make("x" * 15, "M")) == 25
    assert len(qr.make("x" * 213, "M")) == 57
    with pytest.raises(ValueError, match="terlalu panjang"):
        qr.make("x" * (qr.capacity("M") + 1), "M")
    with pytest.raises(ValueError):
        qr.make("x", "Q")


def test_modul_tetap_benar_penanda_sudut_dan_modul_gelap():
    m = qr.make("halo")
    n = len(m)
    for ox, oy in ((0, 0), (n - 7, 0), (0, n - 7)):                     # tiga penanda sudut 7x7
        assert all(m[oy][ox + i] and m[oy + 6][ox + i] and m[oy + i][ox] and m[oy + i][ox + 6] for i in range(7))
        assert all(m[oy + 3][ox + i] for i in (2, 3, 4)) and not m[oy + 1][ox + 1]
    assert m[n - 8][8], "modul gelap tetap di (8, n-8)"


def test_svg_memuat_latar_terang_dan_modul_gelap():
    m = qr.make("halo")
    s = qr.svg(m)
    assert s.startswith("<svg") and f'viewBox="0 0 {len(m) + 8} {len(m) + 8}"' in s and 'fill="#ffffff"' in s
    assert 'role="img"' in s and "<script" not in s
    inverted = qr.svg(m, dark="#000", light="#fff")
    assert 'fill="#000"' in inverted
    assert decode(m) == "halo"


def test_terminal_dua_baris_modul_per_baris_teks_dengan_bingkai_terang():
    m = qr.make("halo")
    n = len(m)
    lines = qr.terminal(m, border=2).split("\n")
    assert len(lines) == (n + 4 + 1) // 2 and all(len(line) == n + 4 for line in lines)
    assert set("".join(lines)) <= set(" ▀▄█")
    assert lines[0] == "█" * (n + 4), "bingkai tenang berwarna terang"
    assert lines[1][:2] == "██" and lines[1][2] == " ", "sudut kiri atas penanda: dua modul gelap bertumpuk"

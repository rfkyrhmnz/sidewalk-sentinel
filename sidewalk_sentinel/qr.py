"""Pembuat kode QR tanpa pustaka luar: mode byte (UTF-8), koreksi galat L atau M, versi 1 sampai 10.

Cukup untuk tautan sepanjang 213 byte (level M) atau 271 byte (level L), jauh di atas tautan HP aplikasi ini.
Hasilnya matriks `list[list[bool]]` (True = modul gelap) yang bisa dirender ke SVG atau terminal.
"""
from __future__ import annotations

# Per versi: (kata koreksi per blok, [(jumlah blok, kata data per blok), ...]). Berasal dari tabel ISO/IEC 18004.
_BLOCKS = {
    "L": {1: (7, [(1, 19)]), 2: (10, [(1, 34)]), 3: (15, [(1, 55)]), 4: (20, [(1, 80)]), 5: (26, [(1, 108)]),
          6: (18, [(2, 68)]), 7: (20, [(2, 78)]), 8: (24, [(2, 97)]), 9: (30, [(2, 116)]),
          10: (18, [(2, 68), (2, 69)])},
    "M": {1: (10, [(1, 16)]), 2: (16, [(1, 28)]), 3: (26, [(1, 44)]), 4: (18, [(2, 32)]), 5: (24, [(2, 43)]),
          6: (16, [(4, 27)]), 7: (18, [(4, 31)]), 8: (22, [(2, 38), (2, 39)]), 9: (22, [(3, 36), (2, 37)]),
          10: (26, [(4, 43), (1, 44)])},
}
_FORMAT_ECC = {"L": 1, "M": 0}
_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42],
          9: [6, 26, 46], 10: [6, 28, 50]}
MAX_VERSION = 10


def capacity(ecc: str = "M") -> int:
    """Jumlah byte terbanyak yang muat pada versi terbesar."""
    return _data_codewords(ecc, MAX_VERSION) - 3


def _data_codewords(ecc: str, version: int) -> int:
    return sum(n * d for n, d in _BLOCKS[ecc][version][1])


# --------------------------------------------------------------------------- Reed-Solomon (GF(256), polinomial 0x11D)
def _gf_mul(x: int, y: int) -> int:
    z = 0
    for i in range(7, -1, -1):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree: int) -> list[int]:
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 0x02)
    return result


def _rs_remainder(data: list[int], divisor: list[int]) -> list[int]:
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_mul(coef, factor)
    return result


# ------------------------------------------------------------------------------------------------- aliran data
def _codewords(data: bytes, ecc: str, version: int) -> list[int]:
    cap_bits = _data_codewords(ecc, version) * 8
    bits: list[int] = []

    def put(value: int, n: int) -> None:
        bits.extend((value >> i) & 1 for i in range(n - 1, -1, -1))

    put(0b0100, 4)                                          # mode byte
    put(len(data), 8 if version <= 9 else 16)
    for b in data:
        put(b, 8)
    put(0, min(4, cap_bits - len(bits)))                    # terminator
    put(0, -len(bits) % 8)
    pad = 0xEC
    while len(bits) < cap_bits:
        put(pad, 8)
        pad ^= 0xEC ^ 0x11
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]

    ec_len, groups = _BLOCKS[ecc][version]
    blocks, k = [], 0
    for n, d in groups:
        for _ in range(n):
            blocks.append(words[k:k + d])
            k += d
    divisor = _rs_divisor(ec_len)
    checks = [_rs_remainder(b, divisor) for b in blocks]
    out: list[int] = []
    for i in range(max(len(b) for b in blocks)):            # data disisipkan antar-blok, lalu koreksi galatnya
        out.extend(b[i] for b in blocks if i < len(b))
    for i in range(ec_len):
        out.extend(c[i] for c in checks)
    return out


# ------------------------------------------------------------------------------------------------------- matriks
_MASKS = (
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0,
    lambda x, y: x * y % 2 + x * y % 3 == 0,
    lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
)


class _Builder:
    def __init__(self, version: int, ecc: str):
        self.version, self.ecc = version, ecc
        self.size = version * 4 + 17
        self.mod = [[False] * self.size for _ in range(self.size)]
        self.func = [[False] * self.size for _ in range(self.size)]
        self._draw_patterns()

    def _set(self, x: int, y: int, dark: bool) -> None:
        self.mod[y][x] = dark
        self.func[y][x] = True

    def _draw_patterns(self) -> None:
        size = self.size
        for i in range(size):                               # garis pewaktu
            self._set(6, i, i % 2 == 0)
            self._set(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):   # tiga penanda sudut beserta pemisahnya
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < size and 0 <= y < size:
                        self._set(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        pos = _ALIGN[self.version]
        n = len(pos)
        for i in range(n):                                  # pola penyelaras, kecuali di tiga sudut penanda
            for j in range(n):
                if (i == 0 and j == 0) or (i == 0 and j == n - 1) or (i == n - 1 and j == 0):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self._set(pos[i] + dx, pos[j] + dy, max(abs(dx), abs(dy)) != 1)
        self.draw_format(0)                                 # menandai area format agar tak terisi data
        if self.version >= 7:
            rem = self.version
            for _ in range(12):
                rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
            bits = self.version << 12 | rem
            for i in range(18):
                bit = bool((bits >> i) & 1)
                a, b = size - 11 + i % 3, i // 3
                self._set(a, b, bit)
                self._set(b, a, bit)

    def draw_format(self, mask: int) -> None:
        size = self.size
        data = _FORMAT_ECC[self.ecc] << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412

        def bit(i: int) -> bool:
            return bool((bits >> i) & 1)

        for i in range(6):
            self._set(8, i, bit(i))
        self._set(8, 7, bit(6))
        self._set(8, 8, bit(7))
        self._set(7, 8, bit(8))
        for i in range(9, 15):
            self._set(14 - i, 8, bit(i))
        for i in range(8):
            self._set(size - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self._set(8, size - 15 + i, bit(i))
        self._set(8, size - 8, True)                        # modul gelap tetap

    def place(self, words: list[int]) -> None:
        size, i = self.size, 0
        total = len(words) * 8
        for right in range(size - 1, 0, -2):                # dua kolom sekali jalan, zigzag dari kanan
            if right <= 6:                                  # kolom pewaktu (x = 6) dilewati: 6, 4, 2 bergeser satu
                right -= 1
            for vert in range(size):
                for j in range(2):
                    x = right - j
                    upward = ((right + 1) & 2) == 0
                    y = size - 1 - vert if upward else vert
                    if not self.func[y][x] and i < total:
                        self.mod[y][x] = bool((words[i >> 3] >> (7 - (i & 7))) & 1)
                        i += 1

    def apply_mask(self, mask: int) -> None:
        fn = _MASKS[mask]
        for y in range(self.size):
            for x in range(self.size):
                if not self.func[y][x] and fn(x, y):
                    self.mod[y][x] = not self.mod[y][x]

    def penalty(self) -> int:
        mod, size = self.mod, self.size
        cols = [list(c) for c in zip(*mod)]
        score = 0
        finder_a = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0]
        finder_b = finder_a[::-1]
        for lines in (mod, cols):
            for line in lines:
                run = 1
                for i in range(1, size):
                    if line[i] == line[i - 1]:
                        run += 1
                    else:
                        if run >= 5:
                            score += run - 2
                        run = 1
                if run >= 5:
                    score += run - 2
                ints = [int(v) for v in line]
                for i in range(size - 10):
                    seg = ints[i:i + 11]
                    if seg == finder_a or seg == finder_b:
                        score += 40
        for y in range(size - 1):
            for x in range(size - 1):
                if mod[y][x] == mod[y][x + 1] == mod[y + 1][x] == mod[y + 1][x + 1]:
                    score += 3
        dark = sum(map(sum, mod))
        total = size * size
        score += ((abs(dark * 20 - total * 10) + total - 1) // total - 1) * 10
        return score


def make(text: str, ecc: str = "M") -> list[list[bool]]:
    """Matriks QR untuk `text`. `ecc` 'M' (bawaan, tahan 15% rusak) atau 'L' (lebih kecil)."""
    if ecc not in _BLOCKS:
        raise ValueError("ecc harus 'L' atau 'M'")
    data = text.encode("utf-8")
    version = next((v for v in range(1, MAX_VERSION + 1)
                    if 4 + (8 if v <= 9 else 16) + 8 * len(data) <= 8 * _data_codewords(ecc, v)), None)
    if version is None:
        raise ValueError(f"teks {len(data)} byte terlalu panjang untuk QR (maksimal {capacity(ecc)} byte)")
    words = _codewords(data, ecc, version)
    b = _Builder(version, ecc)
    b.place(words)
    best_mask, best_score = 0, None
    for m in range(8):
        b.apply_mask(m)
        b.draw_format(m)
        s = b.penalty()
        b.apply_mask(m)                                     # XOR lagi = membatalkan
        if best_score is None or s < best_score:
            best_mask, best_score = m, s
    b.apply_mask(best_mask)
    b.draw_format(best_mask)
    return b.mod


def svg(matrix: list[list[bool]], border: int = 4, dark: str = "#0a0e26", light: str = "#ffffff") -> str:
    """SVG berlatar terang dengan bingkai tenang `border` modul; ukuran mengikuti wadahnya."""
    n = len(matrix) + 2 * border
    runs = []
    for y, row in enumerate(matrix):
        x = 0
        while x < len(row):
            if row[x]:
                start = x
                while x < len(row) and row[x]:
                    x += 1
                runs.append(f"M{start + border} {y + border}h{x - start}v1h-{x - start}z")
            else:
                x += 1
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" shape-rendering="crispEdges" '
            f'role="img" aria-label="Kode QR"><rect width="{n}" height="{n}" fill="{light}"/>'
            f'<path fill="{dark}" d="{"".join(runs)}"/></svg>')


def terminal(matrix: list[list[bool]], border: int = 2) -> str:
    """Teks untuk terminal berlatar gelap: dua baris modul per baris teks. Modul terang digambar sebagai blok penuh,
    jadi kode terlihat hitam di atas putih seperti QR biasa."""
    n = len(matrix)

    def dark(x: int, y: int) -> bool:
        return 0 <= x < n and 0 <= y < n and matrix[y][x]

    lines = []
    for y in range(-border, n + border, 2):
        row = []
        for x in range(-border, n + border):
            top, bottom = dark(x, y), dark(x, y + 1)
            row.append(" " if top and bottom else "▄" if top else "▀" if bottom else "█")
        lines.append("".join(row))
    return "\n".join(lines)

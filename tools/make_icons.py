"""Membuat ikon PNG PWA (192, 512, apple-touch 180) dari desain yang sama dengan web/icon.svg.

Pemakaian:  python tools/make_icons.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "web"
BG, PANEL, LINE = (10, 20, 36), (17, 28, 48), (42, 59, 92)
WHITE, YELLOW, CYAN = (233, 240, 251), (255, 211, 31), (56, 225, 255)


def draw(size: int) -> Image.Image:
    s = size / 512
    big = 4                                   # supersampling agar tepi halus
    img = Image.new("RGB", (size * big, size * big), BG)
    d = ImageDraw.Draw(img)
    k = s * big

    def box(x0, y0, x1, y1):
        return [x0 * k, y0 * k, x1 * k, y1 * k]

    d.rounded_rectangle(box(96, 96, 416, 416), radius=56 * k, fill=PANEL, outline=LINE, width=int(8 * k))
    d.rectangle(box(136, 246, 376, 262), fill=WHITE)
    for i in range(7):                         # ubin taktil berupa titik kuning
        cx = 156 + i * 33.3
        d.ellipse(box(cx - 12, 322, cx + 12, 346), fill=YELLOW)
    w = int(14 * k)
    for (x, y, dx, dy) in ((148, 196, 1, -1), (364, 196, -1, -1)):
        d.line([(x * k, (y + dy * 36) * k), (x * k, y * k), ((x + dx * 36) * k, y * k)], fill=CYAN, width=w, joint="curve")
    d.line([(148 * k, 364 * k), (148 * k, 380 * k)], fill=CYAN, width=w)
    d.line([(364 * k, 364 * k), (364 * k, 380 * k)], fill=CYAN, width=w)
    return img.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    for name, size in (("icon-192.png", 192), ("icon-512.png", 512), ("apple-touch-icon.png", 180)):
        draw(size).save(OUT / name, optimize=True)
        print("dibuat:", OUT / name)

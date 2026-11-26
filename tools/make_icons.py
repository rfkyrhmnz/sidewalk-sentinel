"""Membuat ikon PNG PWA (192, 512, apple-touch 180) dari desain yang sama dengan web/icon.svg.

Pemakaian:  python tools/make_icons.py
"""
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parent.parent / "web"
TOP, BOTTOM = (23, 29, 74), (7, 10, 28)
WHITE, MINT, CYAN = (238, 241, 255), (77, 245, 192), (98, 214, 255)


def diagonal(size: int, a, b) -> Image.Image:
    """Gradien diagonal dari kiri atas (a) ke kanan bawah (b)."""
    ramp = Image.linear_gradient("L").resize((size, size))                 # atas -> bawah
    side = ramp.rotate(90, expand=True).transpose(Image.FLIP_LEFT_RIGHT)   # kiri -> kanan
    mask = ImageChops.add(ramp, side, scale=2)
    return Image.composite(Image.new("RGB", (size, size), b), Image.new("RGB", (size, size), a), mask)


def draw(size: int) -> Image.Image:
    big = 4                                   # supersampling agar tepi halus
    n = size * big
    k = n / 512
    img = diagonal(n, TOP, BOTTOM)

    glow = Image.new("L", (n, n), 0)          # cahaya mint di bawah tengah, seperti latar aplikasi
    ImageDraw.Draw(glow).ellipse([100 * k, 190 * k, 412 * k, 500 * k], fill=110)
    glow = glow.filter(ImageFilter.GaussianBlur(70 * k))
    img = Image.composite(Image.new("RGB", (n, n), MINT), img, glow)

    d = ImageDraw.Draw(img)
    w = int(20 * k)

    def corner(x, y, sx, sy):                 # sudut bidikan: dua garis bertemu, ujung dan siku dibulatkan
        pts = [(x, y + sy * 56 * k), (x, y), (x + sx * 56 * k, y)]
        d.line(pts, fill=WHITE, width=w, joint="curve")
        for px, py in (pts[0], pts[1], pts[2]):
            d.ellipse([px - w / 2, py - w / 2, px + w / 2, py + w / 2], fill=WHITE)

    for cx, cy, sx, sy in ((128, 128, 1, 1), (384, 128, -1, 1), (128, 384, 1, -1), (384, 384, -1, -1)):
        corner(cx * k, cy * k, sx, sy)

    x0, y0, x1, y1 = int(206 * k), int(230 * k), int(306 * k), int(330 * k)   # trapesium bergradasi: trotoar dalam perspektif
    grad = diagonal(max(x1 - x0, y1 - y0), CYAN, MINT).crop((0, 0, x1 - x0, y1 - y0))
    trap = Image.new("L", (n, n), 0)
    ImageDraw.Draw(trap).polygon([(206 * k, 330 * k), (228 * k, 230 * k), (284 * k, 230 * k), (306 * k, 330 * k)], fill=255)
    img.paste(grad, (x0, y0), trap.crop((x0, y0, x1, y1)))
    return img.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    for name, size in (("icon-192.png", 192), ("icon-512.png", 512), ("apple-touch-icon.png", 180)):
        draw(size).save(OUT / name, optimize=True)
        print("dibuat:", OUT / name)

"""Adegan sintetis untuk uji dan demo (tanpa perlu rekaman lapangan).

Adegan 30 detik, kamera statis, jalan di atas dan trotoar di bawah:

    C   mobil yang hanya lewat di jalan                  -> tidak boleh memicu apa pun
    P   motor naik dari jalan ke trotoar lalu parkir     -> naik_trotoar + parkir
    R   motor naik dari jalan dan melaju di trotoar      -> naik_trotoar
    ped pejalan kaki di trotoar                          -> disaring (bukan kendaraan)

Pencahayaan naik pelan sepanjang klip, ditambah derau sensor tiap frame. Opsi ``shadow``
menambahkan bayangan (gelap 42%) yang muncul perlahan di kiri-bawah trotoar dan menetap.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

W, H = 640, 360
ROI = [(0.0, 0.64), (1.0, 0.64), (1.0, 0.99), (0.0, 0.99)]


@dataclass
class Actor:
    name: str
    size: tuple[int, int]
    color: tuple[int, int, int]
    keys: list[tuple[float, float, float]]   # (t, x, y) pusat
    stay: bool = False                        # tetap terlihat setelah kunci terakhir
    band: bool = False                        # pita warna-paving (menguji morfologi)

    def pos(self, t: float):
        k = self.keys
        if t < k[0][0] or (t > k[-1][0] and not self.stay):
            return None
        if t >= k[-1][0]:
            return k[-1][1], k[-1][2]
        for (t0, x0, y0), (t1, x1, y1) in zip(k, k[1:]):
            if t0 <= t <= t1:
                a = (t - t0) / (t1 - t0)
                return x0 + a * (x1 - x0), y0 + a * (y1 - y0)
        return None


@dataclass
class Scene:
    seconds: float = 30.0
    fps: float = 10.0
    shadow: bool = False
    gradient: float = 0.0     # kenaikan cahaya lokal (matahari bergeser), skala 0..1, muncul t=8..14
    drift: float = 0.12
    noise: float = 2.5
    seed: int = 7
    actors: list[Actor] = field(default_factory=list)
    truth: list[dict] = field(default_factory=list)

    @property
    def n_frames(self) -> int:
        return int(self.seconds * self.fps)


def default_scene(**kw) -> Scene:
    sc = Scene(**kw)
    sc.actors = [
        Actor("C", (90, 40), (40, 60, 200), [(2.0, -60, 122), (9.0, 700, 122)]),
        Actor("P", (46, 30), (200, 120, 30), [(3.0, 560, 169), (5.5, 400, 260), (7.0, 300, 317)], stay=True),
        Actor("R", (44, 30), (40, 200, 60), [(10.0, 700, 169), (12.0, 520, 259), (18.0, -60, 259)], band=True),
        Actor("ped", (14, 40), (55, 55, 55), [(20.0, 50, 274), (29.0, 450, 274)]),
    ]
    sc.truth = [
        {"actor": "P", "kind": "naik_trotoar"},
        {"actor": "P", "kind": "parkir", "t_stop": 7.0},
        {"actor": "R", "kind": "naik_trotoar"},
    ]
    return sc


def _background(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    img = np.zeros((H, W, 3), np.uint8)
    img[:int(0.60 * H)] = (92, 92, 96)                     # aspal
    img[int(0.60 * H):int(0.64 * H)] = (178, 178, 172)     # kerb
    side = img[int(0.64 * H):]
    side[:] = (140, 148, 160)                              # paving
    for y in range(0, side.shape[0], 28):
        side[y:y + 1] = (105, 112, 122)
    for x in range(0, W, 40):
        side[:, x:x + 1] = (105, 112, 122)
    for x in range(10, W, 90):                             # marka putus-putus di jalan
        cv2.rectangle(img, (x, int(0.30 * H)), (x + 45, int(0.30 * H) + 3), (210, 210, 210), -1)
    tex = rng.normal(0, 4.0, (H, W, 1)).astype(np.float32)
    return np.clip(img.astype(np.float32) + tex, 0, 255).astype(np.uint8)


def _draw_vehicle(img, cx, cy, w, h, color, band=False):
    x0, y0 = int(cx - w / 2), int(cy - h / 2)
    cv2.rectangle(img, (x0, y0), (x0 + w, y0 + h), color, -1)
    cv2.rectangle(img, (x0, y0), (x0 + w, y0 + max(2, h // 4)), tuple(min(255, int(c * 1.3) + 25) for c in color), -1)
    cv2.rectangle(img, (x0, y0 + h - max(3, h // 5)), (x0 + w, y0 + h), (25, 25, 25), -1)   # roda/bayangan
    if band:  # jaket berwarna mirip paving: mask selisih akan terbelah di sini
        cv2.rectangle(img, (x0, y0 + h // 3), (x0 + w, y0 + h // 3 + 7), (140, 148, 160), -1)


class Renderer:
    def __init__(self, scene: Scene):
        self.sc = scene
        self.bg = _background(scene.seed)
        self.rng = np.random.default_rng(scene.seed + 1)
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        # bayangan pohon: elips di kiri-bawah trotoar, bagian dalam rata dengan tepi lunak
        d = np.sqrt(((xx - 110) / 110) ** 2 + ((yy - 330) / 28) ** 2)
        self._shadow_field = np.clip(3.0 * (1.0 - d), 0, 1)
        # gradien cahaya lebar (gelombang >> ukuran kendaraan), terkuat di kiri
        self._grad_field = np.exp(-(((xx - 100) / 230) ** 2)) * np.ones_like(yy)

    def frame(self, t: float, actors: bool = True) -> np.ndarray:
        sc = self.sc
        img = self.bg.copy()
        if actors:
            for a in sorted(sc.actors, key=lambda a: (a.pos(t) or (0, 0))[1]):
                p = a.pos(t)
                if p is not None:
                    _draw_vehicle(img, p[0], p[1], a.size[0], a.size[1], a.color, a.band)
        out = img.astype(np.float32) * (1.0 + sc.drift * t / sc.seconds)
        if sc.gradient:
            ramp = float(np.clip((t - 8.0) / 6.0, 0, 1))
            out *= (1.0 + sc.gradient * ramp * self._grad_field)[..., None]
        if sc.shadow:
            strength = float(np.clip((t - 14.0) / 6.0, 0, 1)) * 0.42
            out *= (1.0 - strength * self._shadow_field)[..., None]
        out += self.rng.normal(0, sc.noise, out.shape)
        return np.clip(out, 0, 255).astype(np.uint8)

    def frames(self):
        for i in range(self.sc.n_frames):
            t = i / self.sc.fps
            yield t, self.frame(t)

    def empty_frames(self, n: int = 8):
        return [self.frame(0.0, actors=False) for _ in range(n)]


def write_video(scene: Scene, path: str) -> str:
    r = Renderer(scene)
    vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), scene.fps, (W, H))
    if not vw.isOpened():
        raise RuntimeError("VideoWriter tidak bisa dibuka")
    for _, f in r.frames():
        vw.write(f)
    vw.release()
    return path

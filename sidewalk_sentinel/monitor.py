"""Pemantauan terus-menerus tanpa thread: belajar latar sendiri, lalu memantau.

Kamera yang menyala sepanjang hari tidak punya orang yang memilihkan satu frame "trotoar kosong", jadi Monitor
belajar latar sendiri: ia mengambil beberapa frame yang tersebar dalam jendela waktu, lalu median per piksel
membuang apa pun yang hanya lewat sebentar (kendaraan di jalan, pejalan kaki). Kendaraan yang DIAM sepanjang
jendela belajar tetap ikut masuk ke latar; "Kalibrasi ulang" menjadi jalan keluarnya.

Pengaman perubahan adegan: bila sebagian besar FRAME mendadak berbeda dari latar (kamera tersenggol, eksposur
melonjak, perangkat dipindah), semua itu bukan kendaraan. Kejadian ditahan, dan bila perubahan bertahan beberapa
detik latar dipelajari ulang. Tanpa ini, satu kali perubahan cahaya membuat deteksi buta selamanya, karena latar
hanya diperbarui pada piksel non-objek.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from .config import Config
from .decision import Event
from .engine import Engine

# (jumlah sampel, jarak antar-sampel dalam detik). "normal" = jendela sekitar 12 detik, untuk pemakaian
# sungguhan; "cepat" = sekitar 3 detik, untuk pengujian otomatis yang dimulai dari trotoar kosong.
LEARN = {"cepat": (8, 0.4), "normal": (10, 1.2)}


@dataclass
class Report:
    state: str                              # 'belajar' | 'memantau'
    learn: tuple[int, int]                  # (sampel terkumpul, sampel dibutuhkan)
    events: list[Event] = field(default_factory=list)
    tracks: list[dict] = field(default_factory=list)
    change: float = 0.0                     # ukuran 'perubahan global' 0..1 (lihat Monitor.feed)
    note: str = ""                          # alasan belajar ulang otomatis, bila ada
    processed: bool = False                 # True bila engine memproses frame ini (ada gambar beranotasi yang segar)


class Monitor:
    def __init__(self, roi, cfg: Config | None = None, learn: str = "normal"):
        if learn not in LEARN:
            raise ValueError(f"mode belajar harus salah satu dari {sorted(LEARN)}")
        self.cfg = cfg or Config()
        self.engine = Engine(roi, self.cfg)
        self.learn_n, self.learn_dt = LEARN[learn]
        self.state = "belajar"
        self.counts = {"parkir": 0, "naik_trotoar": 0}
        self.relearns = 0                    # berapa kali belajar ulang otomatis
        self.last_note = ""
        self._samples: list[np.ndarray] = []
        self._last_sample_t: float | None = None
        self._change_since: float | None = None

    # ------------------------------------------------------------------ kendali
    def recalibrate(self, reason: str = "") -> None:
        self.state = "belajar"
        self._samples = []
        self._last_sample_t = None
        self._change_since = None
        self.engine.ref = None
        self.engine.tracker.reset()
        self.engine.last = {}
        self.last_note = reason

    @property
    def learn_progress(self) -> tuple[int, int]:
        have = self.learn_n if self.state == "memantau" else len(self._samples)
        return have, self.learn_n

    # ------------------------------------------------------------------- proses
    def feed(self, frame: np.ndarray, t: float) -> Report:
        if self.state == "belajar":
            return self._learn(frame, t)
        try:
            res = self.engine.process(frame, t)
        except ValueError:                   # resolusi kamera berubah di tengah jalan
            self.recalibrate("ukuran frame berubah")
            self.relearns += 1
            return self._learn(frame, t)

        # Perubahan global = frame berbeda di mana-mana DAN zona trotoar dibanjiri foreground. Syarat kedua
        # penting: engine kebal terhadap pergeseran warna/terang sedang (ambang Otsu menyesuaikan), dan belajar
        # ulang pada saat itu justru menyerap kendaraan yang sedang diam ke latar. Satu truk besar di trotoar
        # memenuhi syarat kedua tetapi tidak yang pertama, jadi tidak memicu belajar ulang.
        last = self.engine.last
        in_zone = self.engine.zone > 0
        frame_change = float((last["diff8"] > self.cfg.diff_floor).mean())
        zone_fg = float((last["raw"][in_zone] > 0).mean())
        change = min(frame_change, zone_fg)
        events = res.events
        note = ""
        if self.cfg.use_scene_guard and change >= self.cfg.scene_change_frac:
            events = []                      # perubahan global: jangan menuduh kendaraan
            if self._change_since is None:
                self._change_since = t
            if t - self._change_since >= self.cfg.scene_change_seconds:
                self.recalibrate("perubahan adegan: cahaya berubah atau kamera bergeser")
                self.relearns += 1
                return Report("belajar", self.learn_progress, change=change, note=self.last_note)
        else:
            self._change_since = None

        for ev in events:
            self.counts[ev.kind] += 1
        return Report("memantau", self.learn_progress, events, res.tracks, change, note, processed=True)

    def _learn(self, frame: np.ndarray, t: float) -> Report:
        if self._last_sample_t is None or t - self._last_sample_t >= self.learn_dt:
            self._samples.append(self._shrink(frame))
            self._last_sample_t = t
        if len(self._samples) >= self.learn_n:
            self.engine.set_reference(self._samples)
            self._samples = []
            self.state = "memantau"
            self._change_since = None
        return Report(self.state, self.learn_progress, note=self.last_note if self.state == "belajar" else "")

    def _shrink(self, frame: np.ndarray) -> np.ndarray:
        """Simpan sampel pada lebar kerja: 12 frame 1080p penuh memakan puluhan MB tanpa guna."""
        h, w = frame.shape[:2]
        if w <= self.cfg.work_width:
            return frame.copy()
        return cv2.resize(frame, (self.cfg.work_width, max(1, round(h * self.cfg.work_width / w))),
                          interpolation=cv2.INTER_AREA)

"""Konfigurasi mesin deteksi.

Ambang ukuran dinyatakan sebagai *pecahan* lebar/luas frame (bukan piksel) dan ambang waktu dalam detik, supaya
parameter tidak bergantung pada resolusi atau fps kamera.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields


@dataclass
class Config:
    # ---- masukan dan laju analisis -------------------------------------------------
    work_width: int = 640            # frame yang lebih lebar diperkecil ke lebar ini sebelum diproses
    target_fps: float = 8.0          # laju inferensi maksimum; frame berlebih dilewati

    # ---- detektor kendaraan (YOLO) ---------------------------------------------------
    conf: float = 0.30               # ambang keyakinan deteksi; naikkan bila terlalu sering salah, turunkan bila terlewat
    imgsz: int = 640                 # sisi terpanjang masukan model
    min_box_frac: float = 0.0015     # luas kotak minimum (pecahan luas frame); kendaraan yang lebih kecil diabaikan

    # ---- pelacakan dan gerak --------------------------------------------------------
    track_max_dist_frac: float = 0.12   # lompatan maksimum antar-deteksi (pecahan lebar frame)
    track_max_missed_s: float = 2.0     # kendaraan yang terlewat sebentar tetap dianggap kendaraan yang sama
    speed_window_s: float = 2.0         # jendela waktu untuk mengukur kecepatan
    smooth: float = 0.5                 # peredam getar kotak (1 = tanpa peredam); getar kotak tidak boleh terbaca sebagai gerak

    # ---- keputusan ------------------------------------------------------------------
    still_speed: float = 0.015       # lebar frame per detik; di bawah ini = diam
    move_speed: float = 0.040        # di atas ini = bergerak
    contact_strip: float = 0.30      # bagian bawah kotak (tempat roda menyentuh tanah) yang dinilai terhadap zona
    contact_ratio_min: float = 0.5   # porsi bagian bawah kotak yang harus berada di zona
    park_seconds: float = 10.0       # parkir: diam >= T detik di trotoar
    ride_min_seconds: float = 0.6    # naik trotoar: bergerak setelah naik dari jalan
    ride_standalone_seconds: float = 1.5  # bergerak di trotoar tanpa terlihat naik

    # ------------------------------------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_overrides(cls, overrides: dict | None) -> "Config":
        """Buat Config dari dict parsial; kunci tak dikenal ditolak, tipe disesuaikan."""
        cfg = cls()
        if not overrides:
            return cfg
        spec = {f.name: f for f in fields(cls)}
        for key, value in overrides.items():
            if key not in spec:
                raise ValueError(f"parameter tidak dikenal: {key}")
            current = getattr(cfg, key)
            if isinstance(current, bool):
                if isinstance(value, str):
                    value = value.strip().lower() in ("1", "true", "ya", "on")
                value = bool(value)
            elif isinstance(current, int):
                value = int(float(value))
            else:
                value = float(value)
            setattr(cfg, key, value)
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if not 160 <= self.work_width <= 1920:
            raise ValueError("work_width harus 160..1920")
        if not 1 <= self.target_fps <= 60:
            raise ValueError("target_fps harus 1..60")
        if not 0.05 <= self.conf <= 0.95:
            raise ValueError("conf harus 0.05..0.95")
        if not 160 <= self.imgsz <= 1280:
            raise ValueError("imgsz harus 160..1280")
        if not 0 < self.min_box_frac < 0.5:
            raise ValueError("min_box_frac harus 0..0.5")
        if not 0.1 <= self.smooth <= 1.0:
            raise ValueError("smooth harus 0.1..1")
        if not 1 <= self.park_seconds <= 3600:
            raise ValueError("park_seconds harus 1..3600")
        if not 0.05 <= self.contact_strip <= 1.0:
            raise ValueError("contact_strip harus 0.05..1")
        if self.still_speed >= self.move_speed:
            raise ValueError("still_speed harus lebih kecil dari move_speed")

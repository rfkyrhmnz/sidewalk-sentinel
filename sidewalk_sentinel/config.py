"""Konfigurasi seluruh pipeline.

Semua ambang yang berhubungan dengan ukuran dinyatakan sebagai *pecahan* lebar/luas
frame (bukan piksel) dan semua ambang waktu dalam detik, supaya parameter tidak
bergantung pada resolusi atau fps kamera.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields


@dataclass
class Config:
    # ---- masukan dan laju analisis -------------------------------------------------
    work_width: int = 640            # frame diperkecil ke lebar ini sebelum diproses
    target_fps: float = 10.0         # laju analisis; frame berlebih dilewati

    # ---- referensi latar (citra trotoar kosong) -------------------------------------
    ref_frames: int = 8              # jumlah frame yang di-median untuk referensi
    ref_update_rate: float = 0.002   # running average selektif (0 = referensi statis)

    # ---- Materi 1, 2, 6: praproses --------------------------------------------------
    blur_ksize: int = 5              # M1: penghalusan Gaussian (filter spasial)
    gamma: float = 1.0               # M1: transformasi pangkat; 1.0 = nonaktif
    use_homomorphic: bool = True     # M2: filter homomorfik di domain frekuensi
    # Nilai gain di bawah dipilih lewat eksperimen ablation pada adegan SINTETIS (lihat README,
    # bagian Hasil Eksperimen); wajib ditinjau ulang pada rekaman lapangan.
    homo_gamma_low: float = 0.5      # gain frekuensi rendah (iluminasi)
    homo_gamma_high: float = 1.3     # gain frekuensi tinggi (reflektansi/tepi)
    homo_cutoff: float = 0.012       # frekuensi potong, siklus/piksel
    use_clahe: bool = False          # M1: equalisasi histogram adaptif (opsional)

    # ---- Materi 4 (+6): segmentasi --------------------------------------------------
    diff_floor: float = 22.0         # batas bawah ambang selisih (menahan derau)
    diff_cap: float = 70.0           # batas atas ambang Otsu
    chroma_weight: float = 1.5       # bobot selisih warna (a,b) terhadap selisih L
    use_shadow_removal: bool = True  # M6: buang bayangan lewat rasio V + selisih kroma
    shadow_v_lo: float = 0.55
    shadow_v_hi: float = 0.93
    shadow_chroma_tol: float = 9.0
    use_raw_evidence: bool = True    # selisih terfilter harus dikuatkan perubahan nyata di citra asli
    evidence_ratio: float = 0.10     # |V_sekarang/V_ref - 1| minimal
    evidence_chroma: float = 8.0     # atau selisih kroma (a,b) minimal
    zone_margin_frac: float = 0.10   # zona perhatian = ROI dilebarkan sebesar ini

    # ---- Materi 3: morfologi --------------------------------------------------------
    use_morphology: bool = True
    open_frac: float = 0.006         # elemen struktur opening (pecahan lebar)
    close_frac: float = 0.030        # elemen struktur closing (pecahan lebar)

    # ---- Materi 5: representasi dan deskripsi ---------------------------------------
    min_area_frac: float = 0.0015    # luas minimum objek (pecahan luas frame)
    max_area_frac: float = 0.40      # lebih besar dari ini dianggap perubahan cahaya
    min_solidity: float = 0.35
    filter_pedestrians: bool = True
    ped_max_aspect: float = 0.55     # lebar/tinggi di bawah ini + kecil => pejalan kaki
    ped_max_area_frac: float = 0.012

    # ---- pelacakan dan gerak --------------------------------------------------------
    track_max_dist_frac: float = 0.12
    track_max_missed_s: float = 0.8
    speed_window_s: float = 1.0

    # ---- keputusan ------------------------------------------------------------------
    still_speed: float = 0.015       # lebar frame per detik; di bawah ini = diam
    move_speed: float = 0.040        # di atas ini = bergerak
    contact_ratio_min: float = 0.5   # porsi jalur kaki kendaraan yang harus di ROI
    park_seconds: float = 10.0       # kategori 1: diam >= T detik di trotoar
    ride_min_seconds: float = 0.6    # kategori 2: bergerak setelah naik dari jalan
    ride_standalone_seconds: float = 1.5  # bergerak di trotoar tanpa terlihat naik

    # ---- pemantauan terus-menerus (mode CCTV) ---------------------------------------
    use_scene_guard: bool = True       # tahan kejadian dan belajar ulang saat seluruh frame berubah
    scene_change_frac: float = 0.5     # frame berbeda di mana-mana DAN zona penuh foreground => bukan kendaraan
    scene_change_seconds: float = 3.0  # bertahan selama ini => belajar latar ulang (siang/malam, kamera bergeser)

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
        if self.blur_ksize < 1 or self.blur_ksize % 2 == 0:
            raise ValueError("blur_ksize harus ganjil >= 1")
        if not 0.2 <= self.gamma <= 5:
            raise ValueError("gamma harus 0.2..5")
        if not 1 <= self.park_seconds <= 3600:
            raise ValueError("park_seconds harus 1..3600")
        if not 0 <= self.ref_update_rate < 0.5:
            raise ValueError("ref_update_rate harus 0..0.5")
        if not 0 < self.min_area_frac < 0.5:
            raise ValueError("min_area_frac harus 0..0.5")
        if not 0.1 <= self.scene_change_frac <= 0.95:
            raise ValueError("scene_change_frac harus 0.1..0.95")
        if not 0.5 <= self.scene_change_seconds <= 120:
            raise ValueError("scene_change_seconds harus 0.5..120")
        if self.still_speed >= self.move_speed:
            raise ValueError("still_speed harus lebih kecil dari move_speed")

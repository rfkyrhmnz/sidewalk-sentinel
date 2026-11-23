"""Konfigurasi terpusat.

Semua ambang dapat diubah lewat CLI (``--set kunci=nilai``, ``--tau 0.6``, ``--seed ...``)
atau langsung lewat objek ``Config`` di kode. Nilai default adalah titik awal, BUKAN nilai
optimal: tentukan ambang dari data validasi Anda sendiri (lihat ``eval-decision --sweep-tau``).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Config:
    # ------------------------------------------------------------------ model (Ultralytics)
    weights: str = "yolo11n-seg.pt"          # model segmentasi kendaraan + orang (COCO-seg)
    sidewalk_weights: str | None = None      # model segmentasi trotoar hasil fine-tune (opsional)
    conf: float = 0.25
    imgsz: int = 960
    device: str | None = None                # "cpu", "0", "mps", ... (None = otomatis)
    tracker: str = "bytetrack.yaml"

    # nama kelas model (huruf kecil) -> label Indonesia
    vehicle_names: dict = field(default_factory=lambda: {
        "bicycle": "sepeda", "motorcycle": "motor", "car": "mobil", "bus": "bus", "truck": "truk",
    })
    person_names: tuple = ("person",)
    ridable_names: tuple = ("motorcycle", "bicycle")   # kendaraan yang bisa dinilai ada/tidaknya pengendara
    sidewalk_names: tuple = ("sidewalk", "trotoar")

    # ------------------------------------------------------------------ segmentasi trotoar klasik
    proc_max_side: int = 960                 # sisi terpanjang saat pemrosesan klasik
    # kotak "biji" trotoar (x1, y1, x2, y2) sebagai pecahan 0-1 dari lebar/tinggi gambar.
    # Default = bagian bawah-tengah gambar (asumsi: kamera dipegang dari atas trotoar).
    seed_box: tuple = (0.30, 0.86, 0.70, 0.98)
    maha_thresh: float = 3.5                 # jarak Mahalanobis maksimum ke statistik biji
    cov_reg: float = 4.0                     # regularisasi kovarians (cegah ambang terlalu ketat)
    w_luma: float = 0.6                      # bobot kanal L (kurangi sensitivitas terhadap cahaya)
    w_texture: float = 1.0                   # bobot fitur tekstur (std lokal L)
    texture_ksize: int = 7
    min_y_frac: float = 0.30                 # abaikan bagian atas gambar (langit/gedung)
    morph_open_frac: float = 0.004           # ukuran kernel (pecahan sisi terpanjang)
    morph_close_frac: float = 0.015
    hole_area_frac: float = 0.002            # lubang lebih kecil dari ini (pecahan luas) ditutup
    refine: str = "none"                     # "none" | "grabcut"

    # ------------------------------------------------------------------ keputusan per-frame
    footprint_frac: float = 0.15             # pita bawah mask kendaraan = area kontak dengan tanah
    ring_scale: float = 0.30                 # tebal cincin SAMPING = ring_scale * lebar kendaraan
    ring_below_frac: float = 0.10            # tebal cincin DEPAN/bawah (sengaja tipis: tanah di depan
                                             # kendaraan bisa melewati kerb walau roda masih di jalan)
    ring_gap_frac: float = 0.04              # celah dari tepi kendaraan (hindari bayangan)
    min_ring_px: int = 40                    # cincin lebih kecil dari ini dianggap tidak valid
    tau_contact: float = 0.5                 # ambang rasio kontak roda dengan mask trotoar
    tau_ring: float = 0.5                    # ambang rasio trotoar pada cincin tanah sekitar
    min_vehicle_area_frac: float = 0.002     # abaikan kendaraan sangat kecil/jauh
    rider_overlap: float = 0.25              # irisan kotak orang-kendaraan (terhadap kotak orang)
    rider_head_frac: float = 0.35            # kepala pengendara harus di atas bagian atas kendaraan

    # ------------------------------------------------------------------ video (dimensi waktu)
    min_dwell_s: float = 10.0                # lama diam di trotoar sebelum dinyatakan parkir
    stationary_window_s: float = 3.0
    max_move_frac: float = 0.35              # gerak maks. dalam jendela (pecahan lebar kendaraan)
    vote_window: int = 9
    vote_ratio: float = 0.6
    compensate_camera: bool = True           # kompensasi gerak kamera (translasi) pada video HP
    camera_homography: bool = True           # kompensasi zoom/perspektif lewat homografi bidang trotoar (False = translasi saja)
    sidewalk_refresh: int = 10               # hitung ulang mask trotoar tiap N frame (mode klasik)
    video_stride: int = 1                    # proses tiap N frame

    def to_dict(self) -> dict:
        return asdict(self)


def apply_overrides(cfg: Config, pairs: list[str] | None) -> Config:
    """Terapkan daftar 'kunci=nilai' ke Config (tipe nilai mengikuti nilai default)."""
    for p in pairs or []:
        if "=" not in p:
            raise ValueError(f"Format harus kunci=nilai, bukan: {p!r}")
        k, v = (s.strip() for s in p.split("=", 1))
        if not hasattr(cfg, k):
            raise ValueError(f"Parameter tidak dikenal: {k!r}")
        cur = getattr(cfg, k)
        if isinstance(cur, bool):
            val = v.lower() in ("1", "true", "ya", "y", "t", "yes")
        elif isinstance(cur, int):
            val = int(v)
        elif isinstance(cur, float):
            val = float(v)
        elif isinstance(cur, tuple):
            parts = [x.strip() for x in v.split(",") if x.strip()]
            val = tuple(parts) if cur and isinstance(cur[0], str) else tuple(float(x) for x in parts)
        elif isinstance(cur, dict):
            raise ValueError(f"{k} tidak bisa diubah lewat CLI; ubah di kode.")
        else:
            val = None if v.lower() in ("none", "null", "") else v
        setattr(cfg, k, val)
    return cfg

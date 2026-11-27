"""Ukur apa yang dilihat YOLO pada foto atau video Anda, lewat jalur yang sama dengan aplikasi.

Dipakai saat kendaraan tidak terdeteksi: skrip ini mencoba beberapa kombinasi (gambar diputar, ukuran masukan model,
ambang keyakinan) dan menunjukkan mana yang membuat kendaraan terlihat. Hasilnya bukti, bukan tebakan.

Pemakaian:
    python tools/uji_deteksi.py foto_layar.jpg
    python tools/uji_deteksi.py rekaman.mp4 --frames 8 --weights yolo11s.pt --keluar hasil_uji

Foto atau rekaman sebaiknya diambil dari HP yang sama dan dengan cara memegang yang sama seperti saat aplikasi gagal
(mis. HP tegak memfilmkan layar laptop), karena yang diuji justru kondisi itu.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sidewalk_sentinel.config import Config  # noqa: E402
from sidewalk_sentinel.detector import DEFAULT_WEIGHTS, Detection, DetectorUnavailable, VehicleDetector  # noqa: E402

ROTASI = {0: None, 90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
UKURAN = (640, 960, 1280)
AMBANG = (0.30, 0.15, 0.05)              # 0,30 = bawaan aplikasi (kepekaan 3); 0,15 = kepekaan 5; 0,05 = lantai uji
BAWAAN = (0, 640)                        # kombinasi yang dipakai aplikasi sekarang: tanpa putar, imgsz 640


@dataclass
class Baris:
    putar: int
    imgsz: int
    jumlah: dict[float, int] = field(default_factory=dict)       # ambang -> jumlah kotak di semua frame
    frame: dict[float, int] = field(default_factory=dict)        # ambang -> jumlah frame yang punya >= 1 kotak
    terbaik: dict[str, float] = field(default_factory=dict)      # kelas -> keyakinan tertinggi
    kotak: list[list[Detection]] = field(default_factory=list)   # per frame, keyakinan >= lantai


def bingkai(path: Path, n: int) -> list[np.ndarray]:
    """Foto menjadi satu frame; video menjadi `n` frame yang tersebar rata."""
    img = cv2.imread(str(path))
    if img is not None:
        return [img]
    cap = cv2.VideoCapture(str(path))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    out: list[np.ndarray] = []
    if total > 0:
        for idx in np.linspace(0, total - 1, min(n, total)).astype(int):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
            ok, f = cap.read()
            if ok:
                out.append(f)
    cap.release()
    return out


def siapkan(frame: np.ndarray, putar: int, work_width: int) -> np.ndarray:
    """Putar, lalu perkecil ke lebar kerja seperti `Engine._fit`: itulah yang sampai ke model di aplikasi."""
    if ROTASI[putar] is not None:
        frame = cv2.rotate(frame, ROTASI[putar])
    h, w = frame.shape[:2]
    if w > work_width:
        frame = cv2.resize(frame, (work_width, max(1, round(h * work_width / w))), interpolation=cv2.INTER_AREA)
    return frame


def sweep(detector, frames: list[np.ndarray], rotasi=tuple(ROTASI), ukuran=UKURAN, ambang=AMBANG,
          work_width: int | None = None) -> list[Baris]:
    """Jalankan detektor sekali per (putar, imgsz) pada ambang terendah, lalu hitung tiap ambang dari hasil yang sama."""
    lebar = work_width or Config().work_width
    lantai = min(ambang)
    rows = []
    for putar in rotasi:
        siap = [siapkan(f, putar, lebar) for f in frames]
        for imgsz in ukuran:
            row = Baris(putar, imgsz, {a: 0 for a in ambang}, {a: 0 for a in ambang})
            for img in siap:
                dets = [d for d in detector.detect(img, lantai, imgsz) if d.conf >= lantai]
                row.kotak.append(dets)
                for a in ambang:
                    n = sum(1 for d in dets if d.conf >= a)
                    row.jumlah[a] += n
                    row.frame[a] += 1 if n else 0
                for d in dets:
                    row.terbaik[d.label] = max(row.terbaik.get(d.label, 0.0), d.conf)
            rows.append(row)
    return rows


def kesimpulan(rows: list[Baris], ambang=AMBANG) -> list[str]:
    """Kalimat yang menjawab: kenapa bawaan tidak mendeteksi, dan apa yang mengubahnya. Hanya fakta dari tabel."""
    bawaan_ambang, peka, lantai = ambang[0], ambang[1], ambang[2]
    awal = next((r for r in rows if (r.putar, r.imgsz) == BAWAAN), None)
    if awal is None:
        return []
    if awal.jumlah[bawaan_ambang] > 0:
        return [f"Pengaturan bawaan sudah mendeteksi kendaraan ({awal.frame[bawaan_ambang]} dari {len(awal.kotak)} frame). "
                "Bila di aplikasi tetap tidak muncul, masalahnya bukan di model: periksa zona dan status di layar."]
    out = [f"Pengaturan bawaan (tanpa putar, imgsz {BAWAAN[1]}, keyakinan {bawaan_ambang:.2f}) tidak mendeteksi apa pun."]
    ketemu = sorted((r for r in rows if r.jumlah[bawaan_ambang] > 0), key=lambda r: -r.jumlah[bawaan_ambang])
    if ketemu:
        for r in ketemu[:3]:
            out.append(f"Terdeteksi {r.jumlah[bawaan_ambang]} kotak pada keyakinan {bawaan_ambang:.2f} bila gambar diputar {r.putar}° dan "
                       f"imgsz {r.imgsz} ({r.frame[bawaan_ambang]} dari {len(r.kotak)} frame).")
        if all(r.putar != 0 for r in ketemu[:3]):
            out.append("Pola ini menunjuk orientasi: model hanya melihat kendaraan bila gambar tegak. Pegang HP searah dengan "
                       "pemandangannya (mendatar bila yang difilmkan layar landscape).")
        elif all(r.imgsz != BAWAAN[1] for r in ketemu[:3]):
            out.append("Pola ini menunjuk ukuran masukan: kendaraan terlalu kecil pada imgsz bawaan. Mendekatkan kamera atau "
                       "menaikkan imgsz membantu, dengan harga CPU lebih berat.")
        return out
    peka_ada = [r for r in rows if r.jumlah[peka] > 0]
    if peka_ada:
        r = max(peka_ada, key=lambda r: r.jumlah[peka])
        out.append(f"Model melihat kendaraan tetapi kurang yakin: {r.jumlah[peka]} kotak pada keyakinan {peka:.2f} "
                   f"(putar {r.putar}°, imgsz {r.imgsz}). Naikkan Kepekaan di Setelan ke 5.")
        return out
    lemah = [r for r in rows if r.jumlah[lantai] > 0]
    if lemah:
        out.append(f"Hanya ada kotak berkeyakinan sangat rendah (di bawah {peka:.2f}) dan itu biasanya salah kenal. "
                   "Model ini tidak mengenali kendaraan pada gambar tersebut: coba bobot yang lebih besar "
                   "(--weights yolo11s.pt atau yolo11m.pt), atau perbaiki gambarnya (lebih dekat, terang, tanpa pantulan layar).")
        return out
    out.append("Tidak ada kendaraan yang terdeteksi pada kombinasi mana pun, bahkan pada keyakinan 0,05. Kemungkinan: gambar tidak "
               "memuat kendaraan yang dikenali model COCO (render atau kartun sering begitu), terlalu kecil, atau terlalu gelap. "
               "Coba bobot yang lebih besar (--weights yolo11s.pt atau yolo11m.pt) dan foto yang lebih dekat.")
    return out


def tabel(rows: list[Baris], ambang=AMBANG) -> str:
    kepala = f"{'putar':>6} {'imgsz':>6} " + " ".join(f"{'>=' + format(a, '.2f'):>9}" for a in ambang) + "  terbaik"
    baris = [kepala, "-" * len(kepala)]
    for r in rows:
        terbaik = ", ".join(f"{k} {v:.2f}" for k, v in sorted(r.terbaik.items(), key=lambda kv: -kv[1])[:3]) or "-"
        tanda = "  <- bawaan aplikasi" if (r.putar, r.imgsz) == BAWAAN else ""
        baris.append(f"{r.putar:>5}° {r.imgsz:>6} " + " ".join(f"{r.jumlah[a]:>9}" for a in ambang) + f"  {terbaik}{tanda}")
    return "\n".join(baris)


def simpan(rows: list[Baris], frames: list[np.ndarray], folder: Path, work_width: int, ambang=AMBANG) -> list[Path]:
    """Gambar berkotak dari kombinasi yang paling banyak mendeteksi pada keyakinan 0,15 (sama dengan bawaan bila seri)."""
    peka, lantai = ambang[1], ambang[2]
    terbaik = max(rows, key=lambda r: (r.jumlah[peka], r.jumlah[lantai], (r.putar, r.imgsz) == BAWAAN))
    folder.mkdir(parents=True, exist_ok=True)
    warna = {0: (80, 220, 100), 1: (60, 170, 255), 2: (80, 80, 255)}          # hijau >= bawaan, jingga >= peka, merah di bawahnya
    out = []
    for i, (frame, dets) in enumerate(zip(frames, terbaik.kotak)):
        img = siapkan(frame, terbaik.putar, work_width).copy()
        for d in dets:
            tingkat = 0 if d.conf >= ambang[0] else 1 if d.conf >= peka else 2
            x1, y1, x2, y2 = (int(v) for v in d.box)
            cv2.rectangle(img, (x1, y1), (x2, y2), warna[tingkat], 2)
            cv2.putText(img, f"{d.label} {d.conf:.2f}", (x1, max(14, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, warna[tingkat], 1, cv2.LINE_AA)
        p = folder / f"frame{i + 1}_putar{terbaik.putar}_imgsz{terbaik.imgsz}.jpg"
        cv2.imwrite(str(p), img)
        out.append(p)
    return out


def main(argv: list[str] | None = None, detector=None) -> int:
    ap = argparse.ArgumentParser(description="Uji apa yang dilihat YOLO pada foto atau video.")
    ap.add_argument("sumber", help="foto atau video")
    ap.add_argument("--weights", default=DEFAULT_WEIGHTS, help="bobot YOLO kendaraan (bawaan sama dengan aplikasi)")
    ap.add_argument("--device", default=None, help="mis. cpu atau 0 (GPU pertama)")
    ap.add_argument("--frames", type=int, default=6, help="jumlah frame yang diambil dari video")
    ap.add_argument("--keluar", default="hasil_uji", help="folder gambar berkotak")
    a = ap.parse_args(argv)

    path = Path(a.sumber)
    if not path.is_file():
        print(f"Berkas tidak ditemukan: {path}")
        return 2
    frames = bingkai(path, max(1, a.frames))
    if not frames:
        print(f"Tidak bisa membaca gambar atau video dari {path}.")
        return 2

    if detector is None:
        detector = VehicleDetector(a.weights, a.device)
        print(f"Memuat {a.weights} (unduhan pertama butuh internet)...")
        try:
            detector.load()
        except DetectorUnavailable as e:
            print(f"Model tidak bisa dimuat: {e}")
            return 3

    h, w = frames[0].shape[:2]
    print(f"{path.name}: {len(frames)} frame, {w}x{h}. Jumlah kotak kendaraan menurut kombinasi (semua frame dijumlahkan):\n")
    rows = sweep(detector, frames)
    print(tabel(rows))
    print()
    for kalimat in kesimpulan(rows):
        print("-", kalimat)
    berkas = simpan(rows, frames, Path(a.keluar), Config().work_width)
    print(f"\nGambar berkotak dari kombinasi terbaik disimpan di {Path(a.keluar).resolve()} ({len(berkas)} berkas).")
    print("Hijau = keyakinan bawaan aplikasi, jingga = kepekaan 5, merah = di bawahnya (biasanya salah kenal).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

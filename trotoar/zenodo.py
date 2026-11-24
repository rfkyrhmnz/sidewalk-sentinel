"""Zenodo "Dataset for Detecting Motorcyclists in Pedestrian Areas" (CCTV Medellin, CC BY 4.0) -> YOLO.

Sumber: https://zenodo.org/records/7935299 (6.324 gambar 1280x720; ``images/`` + ``etiquetas/``).
Empat kelas menurut halamannya (urutan dan arti):

    MMNAP  motor + pengendara, TIDAK di area pejalan kaki   -> dibuang (gambar jadi contoh negatif: motor di jalan)
    MMAP   motor + pengendara, di area pejalan kaki          -> ``pengendara_lewat``
    MNAP   motor TANPA pengendara, di area pejalan kaki      -> ``kendaraan_parkir``
    PCP    pejalan kaki di zebra cross                       -> dibuang

Yang BELUM diketahui dari halamannya: format anotasi (YOLO txt / Pascal VOC xml / COCO json), apakah id kelas mengikuti urutan di atas,
dan apa batas "area pejalan kaki". Karena itu ada ``inspect_zenodo`` (hanya membaca dan melaporkan) dan semua asumsi bisa diganti
(``--classes``, ``--map``). Konversi hanya didukung untuk YOLO txt dan Pascal VOC xml; format lain berhenti dengan pesan yang jelas.

Peringatan metodologis yang ikut dilaporkan: set ini tidak punya pembagian latih/uji dan tidak mencantumkan kamera, jadi pembagian
acak di sini membuat frame berdekatan dari kamera yang sama jatuh di latih dan val (metrik val cenderung optimistis).
"""
from __future__ import annotations

import random
import shutil
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from .datasets import IMG_EXT, _write_yaml

ZENODO_CLASSES = ("MMNAP", "MMAP", "MNAP", "PCP")          # urutan di halaman Zenodo; id sebenarnya harus dicek
DEFAULT_MAP = {"MNAP": "kendaraan_parkir", "MMAP": "pengendara_lewat"}
IMAGE_DIR_NAMES = {"images", "imagenes", "imágenes", "imgs"}
LABEL_DIR_NAMES = {"etiquetas", "labels", "annotations", "anotaciones"}
_SKIP_TXT = {"classes.txt", "readme.txt", "notes.txt"}


def _find_dir(src: Path, names: set[str]) -> Path | None:
    hits = [p for p in [src, *src.rglob("*")] if p.is_dir() and p.name.lower() in names]
    return min(hits, key=lambda p: len(p.parts)) if hits else None


def find_dirs(src) -> tuple[Path, Path]:
    src = Path(src)
    if not src.is_dir():
        raise FileNotFoundError(f"Folder tidak ada: {src}")
    imgs, lbls = _find_dir(src, IMAGE_DIR_NAMES), _find_dir(src, LABEL_DIR_NAMES)
    if imgs is None or lbls is None:
        isi = sorted(p.name for p in src.iterdir())[:12]
        raise FileNotFoundError(
            f"Tidak menemukan folder gambar ({'/'.join(sorted(IMAGE_DIR_NAMES))}) dan anotasi ({'/'.join(sorted(LABEL_DIR_NAMES))}) di {src}. "
            f"Isi folder: {isi}. Ekstrak PedestrianDataSet.zip lebih dulu dan arahkan --src ke folder hasil ekstraknya.")
    return imgs, lbls


def _label_files(lbls: Path) -> list[Path]:
    return sorted(p for p in lbls.rglob("*") if p.is_file()
                  and ((p.suffix.lower() == ".txt" and p.name.lower() not in _SKIP_TXT) or p.suffix.lower() in (".xml", ".json", ".csv")))


def _image_map(imgs: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for p in sorted(imgs.rglob("*")):
        if p.is_file() and p.suffix.lower() in IMG_EXT:
            out.setdefault(p.stem, p)
    return out


def _class_names(src: Path, lbls: Path, classes) -> tuple[dict[int, str], str]:
    """id -> nama untuk anotasi YOLO. Prioritas: --classes, classes.txt, lalu urutan di halaman Zenodo (ASUMSI)."""
    if classes:
        return {i: str(n) for i, n in enumerate(classes)}, "--classes"
    for folder in (lbls, lbls.parent, src):
        f = folder / "classes.txt"
        if f.is_file():
            names = [ln.strip() for ln in f.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
            return dict(enumerate(names)), str(f)
    return dict(enumerate(ZENODO_CLASSES)), "urutan di halaman Zenodo (ASUMSI, belum dicek pada berkas)"


def _clip(v: float) -> float:
    return min(1.0, max(0.0, v))


def read_yolo(path: Path, names: dict[int, str]) -> list[tuple[str, tuple[float, float, float, float]]]:
    """Baris YOLO (kotak 5 nilai, atau poligon -> kotak pembatasnya) -> [(nama, (x1,y1,x2,y2) ternormalisasi)]."""
    out = []
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        p = ln.split()
        if len(p) < 5:
            continue
        try:
            cid = int(float(p[0]))
            v = [float(x) for x in p[1:]]
        except ValueError:
            continue
        if len(v) == 4:
            cx, cy, w, h = v
            x1, y1, x2, y2 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
        else:
            xs, ys = v[0::2], v[1::2]
            x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
        out.append((names.get(cid, f"id{cid}"), (_clip(x1), _clip(y1), _clip(x2), _clip(y2))))
    return out


def _image_size(path: Path) -> tuple[int, int]:
    from PIL import Image
    with Image.open(path) as im:
        return im.size


def read_voc(path: Path, image: Path | None = None) -> list[tuple[str, tuple[float, float, float, float]]]:
    """Pascal VOC xml -> [(nama, (x1,y1,x2,y2) ternormalisasi)]."""
    root = ET.parse(path).getroot()
    size = root.find("size")
    w = int(float(size.findtext("width") or 0)) if size is not None else 0
    h = int(float(size.findtext("height") or 0)) if size is not None else 0
    if (w <= 0 or h <= 0) and image is not None:
        w, h = _image_size(image)
    if w <= 0 or h <= 0:
        raise ValueError(f"{path.name}: ukuran gambar tidak diketahui")
    out = []
    for obj in root.iter("object"):
        bb = obj.find("bndbox")
        if bb is None:
            continue
        try:
            x1, y1, x2, y2 = (float(bb.findtext(k) or 0) for k in ("xmin", "ymin", "xmax", "ymax"))
        except ValueError:
            continue
        out.append(((obj.findtext("name") or "").strip(), (_clip(x1 / w), _clip(y1 / h), _clip(x2 / w), _clip(y2 / h))))
    return out


def _detect_format(files: list[Path]) -> str:
    ext = Counter(f.suffix.lower() for f in files)
    if not ext:
        return "kosong"
    top = ext.most_common(1)[0][0]
    return {".txt": "yolo", ".xml": "voc"}.get(top, f"tidak didukung ({top})")


def _read_all(src: Path, imgs: Path, lbls: Path, classes):
    """-> (format, sumber nama kelas, [(gambar, berkas label, [(nama, kotak)])], label tanpa gambar, jumlah gambar)"""
    files = _label_files(lbls)
    fmt = _detect_format(files)
    if fmt not in ("yolo", "voc"):
        ext = dict(Counter(f.suffix.lower() for f in files))
        sample = ""
        for f in files[:1]:
            sample = "\n  contoh isi: " + " | ".join(f.read_text(encoding="utf-8", errors="replace").splitlines()[:3])[:300]
        raise ValueError(f"Format anotasi {fmt}: ekstensi di {lbls} = {ext}. Hanya YOLO txt dan Pascal VOC xml yang didukung.{sample}\n"
                         "Kirim pesan ini supaya pembaca format yang cocok bisa ditambahkan.")
    names, names_src = _class_names(src, lbls, classes)
    wanted = ".txt" if fmt == "yolo" else ".xml"
    imap = _image_map(imgs)
    rows, orphan = [], 0
    for f in files:
        if f.suffix.lower() != wanted:
            continue
        img = imap.get(f.stem)
        if img is None:
            orphan += 1
            continue
        boxes = read_yolo(f, names) if fmt == "yolo" else read_voc(f, img)
        rows.append((img, f, boxes))
    return fmt, names_src, rows, orphan, len(imap)


def inspect_zenodo(src, classes=None) -> dict:
    """Hanya membaca: struktur, format, histogram kelas, dan apa yang perlu dicek sebelum konversi."""
    src = Path(src)
    imgs, lbls = find_dirs(src)
    fmt, names_src, rows, orphan, n_img = _read_all(src, imgs, lbls, classes)
    per_class: Counter = Counter()
    img_with: Counter = Counter()
    for _, _, boxes in rows:
        per_class.update(n for n, _ in boxes)
        img_with.update({n for n, _ in boxes})
    first = next(iter(imgs.rglob("*.jp*g")), None) or next(iter(p for p in imgs.rglob("*") if p.suffix.lower() in IMG_EXT), None)
    return {"folder_gambar": str(imgs), "folder_label": str(lbls), "format": fmt, "nama_kelas_dari": names_src,
            "gambar": n_img, "berkas_label": len(rows), "label_tanpa_gambar": orphan,
            "objek_per_kelas": dict(per_class), "gambar_per_kelas": dict(img_with),
            "ukuran_contoh": _image_size(first) if first else None}


def convert_zenodo(src, out, *, classes=None, class_map: dict[str, str] | None = None, val_frac: float = 0.15,
                   neg_ratio: float = 0.3, seed: int = 0, fmt: str = "polygon") -> dict:
    """Tulis set YOLO berkelas ``kendaraan_parkir`` / ``pengendara_lewat``. Kotak ditulis sebagai poligon 4 titik (``fmt='polygon'``,
    bisa digabung ke set segmentasi dengan ``data merge``; Ultralytics tetap membacanya sebagai kotak untuk model deteksi) atau
    kotak YOLO biasa (``fmt='box'``). Gambar tanpa objek target (mis. hanya MMNAP: motor di jalan) dipakai sebagai contoh negatif
    sebanyak ``neg_ratio`` x jumlah gambar positif."""
    if fmt not in ("polygon", "box"):
        raise ValueError("fmt harus 'polygon' atau 'box'.")
    src, out = Path(src), Path(out)
    cmap = {k.lower(): v for k, v in (class_map or DEFAULT_MAP).items()}
    imgs, lbls = find_dirs(src)
    fmt_in, names_src, rows, orphan, _ = _read_all(src, imgs, lbls, classes)
    final = list(dict.fromkeys(cmap.values()))
    seen: Counter = Counter()
    dropped: Counter = Counter()
    pos, neg = [], []
    for img, _, boxes in rows:
        keep = []
        for name, (x1, y1, x2, y2) in boxes:
            seen[name] += 1
            tgt = cmap.get(name.lower())
            if tgt is None:
                dropped[name] += 1
            elif x2 - x1 >= 0.002 and y2 - y1 >= 0.002:
                keep.append((final.index(tgt), (x1, y1, x2, y2)))
        (pos if keep else neg).append((img, keep))
    if not pos:
        raise ValueError(f"Tidak ada objek untuk kelas {sorted(cmap)}. Nama kelas yang terbaca: {dict(seen)} (dari {names_src}). "
                         "Cocokkan dengan --map LAMA=BARU atau --classes menurut urutan id yang benar.")
    rng = random.Random(seed)
    rng.shuffle(neg)
    kept_neg = neg[:int(round(neg_ratio * len(pos)))]
    items = pos + kept_neg
    rng.shuffle(items)
    n_val = min(len(items) - 1, max(1, int(round(val_frac * len(items))))) if len(items) > 1 else 0
    split_of = ["val"] * n_val + ["train"] * (len(items) - n_val)
    stats = {"format_anotasi": fmt_in, "nama_kelas_dari": names_src, "gambar": {"train": 0, "val": 0}, "instans": {n: 0 for n in final},
             "positif": len(pos), "negatif_dipakai": len(kept_neg), "negatif_tersedia": len(neg), "label_tanpa_gambar": orphan,
             "kelas_dibuang": dict(dropped), "kelas_terbaca": dict(seen)}
    used_names: set[tuple[str, str]] = set()
    for (img, keep), split in zip(items, split_of):
        (out / "images" / split).mkdir(parents=True, exist_ok=True)
        (out / "labels" / split).mkdir(parents=True, exist_ok=True)
        name = img.name
        if (split, name) in used_names:
            name = f"{img.parent.name}_{img.name}"
        used_names.add((split, name))
        shutil.copyfile(img, out / "images" / split / name)
        lines = []
        for cid, (x1, y1, x2, y2) in keep:
            if fmt == "box":
                lines.append(f"{cid} {(x1 + x2) / 2:.6f} {(y1 + y2) / 2:.6f} {x2 - x1:.6f} {y2 - y1:.6f}")
            else:
                lines.append(f"{cid} {x1:.6f} {y1:.6f} {x2:.6f} {y1:.6f} {x2:.6f} {y2:.6f} {x1:.6f} {y2:.6f}")
            stats["instans"][final[cid]] += 1
        (out / "labels" / split / (Path(name).stem + ".txt")).write_text("".join(ln + "\n" for ln in lines), encoding="utf-8")
        stats["gambar"][split] += 1
    _write_yaml(out, final, [s for s in ("train", "val") if stats["gambar"][s]])
    return stats

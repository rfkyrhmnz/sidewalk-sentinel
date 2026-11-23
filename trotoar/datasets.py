"""Penyiap dataset publik untuk melatih YOLO.

Dua pekerjaan, keduanya jalan di komputer sendiri setelah dataset diunduh (lihat datasets/README.md):

* ``masks_to_yolo``: dataset segmentasi bermask piksel (Cityscapes, IDD, Mapillary Vistas) -> YOLO-seg satu kelas
  (``sidewalk``), format yang dibaca ``python main.py train`` dan ``--sidewalk-weights``.
* ``merge_yolo``: gabungkan satu atau lebih set berformat YOLO (mis. ekspor Roboflow) dan ubah nama kelasnya.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import cv2
import numpy as np

PRESETS = {
    # Tata letak Cityscapes: leftImg8bit/<split>/<kota>/<nama>_leftImg8bit.png dan gtFine/<split>/<kota>/<nama>_gtFine_labelIds.png.
    # Id 8 = sidewalk pada labelIds Cityscapes.
    "cityscapes": dict(image_dir="leftImg8bit", mask_dir="gtFine", image_suffix="_leftImg8bit.png",
                       mask_suffix="_gtFine_labelIds.png", label_id=8),
}
SPLIT_ALIASES = {"training": "train", "validation": "val", "valid": "val"}   # nama folder bawaan beberapa dataset
MAX_POINTS = 200            # batas titik per poligon agar berkas label tetap kecil
IMG_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".webp")


def _write_yaml(out: Path, names: list[str], splits: list[str]) -> None:
    # Tanpa kunci ``path``: Ultralytics lalu memakai folder tempat data.yaml berada, jadi folder boleh dipindah (mis. ke Colab).
    lines = [f"{s}: images/{s}" for s in splits]
    lines += ["names:"] + [f"  {i}: {n}" for i, n in enumerate(names)]
    (out / "data.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ----------------------------------------------------------------------------- mask piksel -> YOLO-seg
def mask_to_polygons(sel: np.ndarray, min_area: float = 0.0005) -> list[list[tuple[float, float]]]:
    """Mask boolean -> poligon ternormalisasi 0-1 (kontur luar; lubang di dalam bentuk ikut terisi)."""
    h, w = sel.shape[:2]
    cnts, _ = cv2.findContours(sel.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    polys = []
    for c in cnts:
        if cv2.contourArea(c) < min_area * h * w:
            continue
        eps = 0.0015 * cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, eps, True)
        while len(approx) > MAX_POINTS:
            eps *= 1.5
            approx = cv2.approxPolyDP(c, eps, True)
        pts = approx.reshape(-1, 2)
        if len(pts) >= 3:
            polys.append([(float(np.clip(x / w, 0, 1)), float(np.clip(y / h, 0, 1))) for x, y in pts])
    return polys


def _read_mask(path: Path) -> np.ndarray | None:
    """Mask palet/1 kanal -> id kelas; mask RGB -> larik BGR (urutan cv2). None bila tak terbaca."""
    from PIL import Image
    try:
        with Image.open(path) as im:
            return np.array(im.convert("RGB"))[..., ::-1] if im.mode in ("RGB", "RGBA") else np.array(im)
    except OSError:
        return None


def _dir(src: Path, d: str, split: str) -> Path:
    """``d`` boleh memuat ``{split}`` (mis. ``{split}/labels`` pada Mapillary); bila tidak, split menjadi subfolder."""
    return src / d.format(split=split) if "{split}" in d else src / d / split


def _select(mask: np.ndarray, label_id: int | None, label_rgb: tuple[int, int, int] | None) -> np.ndarray:
    if label_rgb is not None:
        if mask.ndim != 3 or mask.shape[2] < 3:
            raise ValueError("--label-rgb butuh mask berwarna (3 kanal).")
        return np.all(mask[..., :3] == np.array(label_rgb[::-1], dtype=mask.dtype), axis=-1)        # cv2 memuat BGR
    if mask.ndim == 3:
        raise ValueError("Mask ini berwarna: pakai --label-rgb R,G,B (bukan --label-id).")
    return mask == label_id


def masks_to_yolo(src, out, *, image_dir: str, mask_dir: str, image_suffix: str, mask_suffix: str,
                  label_id: int | None = None, label_rgb: tuple[int, int, int] | None = None,
                  splits=("train", "val"), max_side: int = 1280, min_area: float = 0.0005,
                  class_name: str = "sidewalk", limit: int | None = None, skip_empty: bool = False) -> dict:
    """Ubah pasangan gambar + mask piksel menjadi dataset YOLO-seg satu kelas. Pembagian train/val mengikuti folder bawaan dataset.
    ``skip_empty``: buang gambar yang tidak memuat kelas itu (untuk dataset serba-ada seperti ADE20K yang sebagian besar berisi dalam ruangan)."""
    src, out = Path(src), Path(out)
    if label_id is None and label_rgb is None:
        raise ValueError("Tentukan --label-id (mask 1 kanal) atau --label-rgb (mask berwarna).")
    stats = {"gambar": 0, "dengan_kelas": 0, "tanpa_kelas": 0, "poligon": 0, "tanpa_gambar": 0, "mask_rusak": 0, "dibuang_kosong": 0, "per_split": {}}
    used = []
    for raw_split in splits:
        split = SPLIT_ALIASES.get(raw_split, raw_split)
        root, iroot = _dir(src, mask_dir, raw_split), _dir(src, image_dir, raw_split)
        if not root.is_dir():
            continue
        (out / "images" / split).mkdir(parents=True, exist_ok=True)
        (out / "labels" / split).mkdir(parents=True, exist_ok=True)
        n = 0
        for mp in sorted(root.rglob("*" + mask_suffix)):
            if limit is not None and n >= limit:
                break
            rel = mp.relative_to(root)
            stem = mp.name[: -len(mask_suffix)]
            ip = iroot / rel.parent / (stem + image_suffix)
            if not ip.is_file():
                stats["tanpa_gambar"] += 1
                continue
            mask, img = _read_mask(mp), cv2.imread(str(ip), cv2.IMREAD_COLOR)
            if mask is None or img is None:
                stats["mask_rusak"] += 1
                continue
            sel = _select(mask, label_id, label_rgb).astype(np.uint8)
            if sel.shape[:2] != img.shape[:2]:
                sel = cv2.resize(sel, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_NEAREST)
            scale = min(1.0, max_side / max(img.shape[:2]))
            if scale < 1.0:
                size = (round(img.shape[1] * scale), round(img.shape[0] * scale))
                img = cv2.resize(img, size, interpolation=cv2.INTER_AREA)
                sel = cv2.resize(sel, size, interpolation=cv2.INTER_NEAREST)
            polys = mask_to_polygons(sel.astype(bool), min_area)
            if skip_empty and not polys:
                stats["dibuang_kosong"] += 1
                continue
            name = "__".join(rel.parent.parts + (stem,))
            cv2.imwrite(str(out / "images" / split / f"{name}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
            (out / "labels" / split / f"{name}.txt").write_text(
                "".join("0 " + " ".join(f"{x:.6f} {y:.6f}" for x, y in p) + "\n" for p in polys), encoding="utf-8")
            n += 1
            stats["gambar"] += 1
            stats["poligon"] += len(polys)
            stats["dengan_kelas" if polys else "tanpa_kelas"] += 1
        stats["per_split"][split] = n
        used.append(split)
    if not used:
        raise FileNotFoundError(f"Tidak ada folder mask untuk split {list(splits)} di bawah {src} (mask-dir '{mask_dir}'). Cek --src, --mask-dir, dan --splits.")
    _write_yaml(out, [class_name], used)
    return stats


# ----------------------------------------------------------------------------- gabung / ubah kelas set YOLO
def _names_of(src: Path) -> dict[int, str]:
    import yaml
    f = src / "data.yaml"
    if not f.is_file():
        raise FileNotFoundError(f"{f} tidak ada: --src harus folder ekspor berformat YOLO.")
    names = (yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get("names", {})
    return dict(enumerate(names)) if isinstance(names, list) else {int(k): str(v) for k, v in names.items()}


def _split_dirs(src: Path) -> dict[str, tuple[Path, Path]]:
    """{'train'|'val'|'test': (folder gambar, folder label)} untuk tata letak Roboflow dan Ultralytics."""
    found = {}
    for split, aliases in (("train", ("train",)), ("val", ("val", "valid")), ("test", ("test",))):
        for a in aliases:
            for imgs, lbls in ((src / a / "images", src / a / "labels"), (src / "images" / a, src / "labels" / a)):
                if imgs.is_dir():
                    found[split] = (imgs, lbls)
                    break
            if split in found:
                break
    return found


def merge_yolo(srcs, out, class_map: dict[str, str] | None = None, names: list[str] | None = None,
               keep_unmapped: bool = False) -> dict:
    """Gabungkan set YOLO. ``class_map`` {nama lama: nama baru}; kelas yang tak dipetakan dibuang kecuali ``keep_unmapped``
    (atau bila ``class_map`` kosong: semua dipertahankan). Gambar yang seluruh labelnya terbuang tidak disalin."""
    srcs, out = [Path(s) for s in srcs], Path(out)
    src_names = [_names_of(s) for s in srcs]
    identity = not class_map

    def new_name(old: str) -> str | None:
        if identity:
            return old
        return class_map.get(old, old if keep_unmapped else None)

    final = list(names) if names else []
    for nm in src_names:                                     # urutan kelas = urutan kemunculan (atau --names)
        for i in sorted(nm):
            nn = new_name(nm[i])
            if nn is not None and nn not in final:
                if names:
                    raise ValueError(f"Kelas '{nn}' (dari '{nm[i]}') tidak ada di --names {names}.")
                final.append(nn)
    if not final:
        raise ValueError("Tidak ada kelas yang tersisa. Cek pemetaan --map (nama lama harus persis seperti di data.yaml sumber).")
    stats = {"gambar": {}, "instans": {n: 0 for n in final}, "dilewati": 0}
    used = []
    for si, (src, nm) in enumerate(zip(srcs, src_names)):
        remap = {i: final.index(nn) for i, old in nm.items() if (nn := new_name(old)) is not None}
        for split, (imgs, lbls) in _split_dirs(src).items():
            (out / "images" / split).mkdir(parents=True, exist_ok=True)
            (out / "labels" / split).mkdir(parents=True, exist_ok=True)
            for ip in sorted(p for p in imgs.iterdir() if p.suffix.lower() in IMG_EXT):
                lp = lbls / (ip.stem + ".txt")
                raw = [ln.split() for ln in lp.read_text(encoding="utf-8").splitlines() if ln.strip()] if lp.is_file() else []
                keep = [[str(remap[int(float(p[0]))])] + p[1:] for p in raw if int(float(p[0])) in remap]
                if raw and not keep:
                    stats["dilewati"] += 1
                    continue
                tag = f"s{si}_{ip.name}"
                shutil.copyfile(ip, out / "images" / split / tag)
                (out / "labels" / split / f"s{si}_{ip.stem}.txt").write_text(
                    "".join(" ".join(p) + "\n" for p in keep), encoding="utf-8")
                stats["gambar"][split] = stats["gambar"].get(split, 0) + 1
                for p in keep:
                    stats["instans"][final[int(p[0])]] += 1
                if split not in used:
                    used.append(split)
    if not used:
        raise FileNotFoundError("Tidak ada folder train/valid/test berisi gambar pada --src.")
    _write_yaml(out, final, [s for s in ("train", "val", "test") if s in used])
    return stats

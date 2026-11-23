"""Penyiap dataset: data buatan yang meniru tata letak Cityscapes dan ekspor Roboflow (tanpa unduhan sungguhan)."""
import cv2
import numpy as np
import pytest

from trotoar import datasets as ds
from trotoar.cli import main


def make_cityscapes(root, splits=("train", "val"), per_split=2, size=(200, 400)):
    """gtFine/<split>/<kota>/<nama>_gtFine_labelIds.png dengan trotoar (id 8) di sepertiga bawah dan jalan (id 7) di tengah."""
    h, w = size
    for split in splits:
        for i in range(per_split):
            city = f"kota{split}"
            name = f"{city}_{i:06d}_000019"
            (root / "leftImg8bit" / split / city).mkdir(parents=True, exist_ok=True)
            (root / "gtFine" / split / city).mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(root / "leftImg8bit" / split / city / f"{name}_leftImg8bit.png"), np.full((h, w, 3), 90, np.uint8))
            lab = np.zeros((h, w), np.uint8)
            lab[h // 3: 2 * h // 3] = 7
            if i == 0:                                              # gambar kedua sengaja tanpa trotoar
                lab[2 * h // 3:] = 8
            cv2.imwrite(str(root / "gtFine" / split / city / f"{name}_gtFine_labelIds.png"), lab)


def read_polys(path):
    rows = [ln.split() for ln in path.read_text().splitlines() if ln.strip()]
    return [(r[0], np.array(r[1:], float).reshape(-1, 2)) for r in rows]


def test_masks_to_yolo_cityscapes_layout(tmp_path):
    src, out = tmp_path / "cs", tmp_path / "yolo"
    make_cityscapes(src)
    st = ds.masks_to_yolo(src, out, **ds.PRESETS["cityscapes"])
    assert st["gambar"] == 4 and st["dengan_kelas"] == 2 and st["tanpa_kelas"] == 2 and st["per_split"] == {"train": 2, "val": 2}
    lbl = out / "labels" / "train" / "kotatrain__kotatrain_000000_000019.txt"
    (cls, pts), = read_polys(lbl)
    assert cls == "0" and pts.min() >= 0 and pts.max() <= 1
    assert pts[:, 1].min() == pytest.approx(2 / 3, abs=0.02) and pts[:, 1].max() > 0.98   # trotoar = sepertiga bawah gambar
    empty = out / "labels" / "train" / "kotatrain__kotatrain_000001_000019.txt"
    assert empty.read_text() == "" and (out / "images" / "train" / "kotatrain__kotatrain_000001_000019.jpg").is_file()
    yml = (out / "data.yaml").read_text()
    assert "train: images/train" in yml and "val: images/val" in yml and "0: sidewalk" in yml and "path:" not in yml


def test_masks_to_yolo_resize_limit_and_rgb(tmp_path):
    src, out = tmp_path / "cs", tmp_path / "yolo"
    make_cityscapes(src, size=(800, 1600))
    st = ds.masks_to_yolo(src, out, max_side=400, limit=1, **ds.PRESETS["cityscapes"])
    assert st["gambar"] == 2 and st["per_split"] == {"train": 1, "val": 1}                # --limit berlaku per split
    img = cv2.imread(str(next((out / "images" / "train").glob("*.jpg"))))
    assert max(img.shape[:2]) == 400
    # mask berwarna (gaya Mapillary): warna (244,35,232) = trotoar
    rgb = tmp_path / "mv"
    (rgb / "images" / "train").mkdir(parents=True)
    (rgb / "labels" / "train").mkdir(parents=True)
    cv2.imwrite(str(rgb / "images" / "train" / "a.jpg"), np.full((100, 200, 3), 50, np.uint8))
    col = np.zeros((100, 200, 3), np.uint8)
    col[60:] = (232, 35, 244)                                                                # BGR dari RGB (244,35,232)
    cv2.imwrite(str(rgb / "labels" / "train" / "a.png"), col)
    out2 = tmp_path / "yolo2"
    ds.masks_to_yolo(rgb, out2, image_dir="images", mask_dir="labels", image_suffix=".jpg", mask_suffix=".png",
                     label_rgb=(244, 35, 232), splits=("train",))
    (_, pts), = read_polys(out2 / "labels" / "train" / "a.txt")
    assert pts[:, 1].min() == pytest.approx(0.6, abs=0.03)


def test_masks_to_yolo_mapillary_layout_with_palette_masks(tmp_path):
    """Tata letak <split>/images dan <split>/labels dengan mask palet (indeks kelas): split 'training' -> 'train'."""
    from PIL import Image
    src, out = tmp_path / "mv", tmp_path / "yolo"
    for split in ("training", "validation"):
        (src / split / "images").mkdir(parents=True)
        (src / split / "labels").mkdir(parents=True)
        cv2.imwrite(str(src / split / "images" / "abc.jpg"), np.full((100, 200, 3), 50, np.uint8))
        idx = np.full((100, 200), 13, np.uint8)               # 13 = jalan
        idx[:40] = 15                                         # 15 = trotoar (Vistas v1.2)
        im = Image.fromarray(idx, mode="P")
        im.putpalette([v for i in range(256) for v in (i, i, i)])
        im.save(src / split / "labels" / "abc.png")
    st = ds.masks_to_yolo(src, out, image_dir="{split}/images", mask_dir="{split}/labels", image_suffix=".jpg",
                          mask_suffix=".png", label_id=15, splits=("training", "validation"))
    assert st["per_split"] == {"train": 1, "val": 1} and st["dengan_kelas"] == 2
    (_, pts), = read_polys(out / "labels" / "val" / "abc.txt")
    assert pts[:, 1].max() == pytest.approx(0.4, abs=0.03)
    assert "train: images/train" in (out / "data.yaml").read_text()


def test_masks_to_yolo_ade20k_layout_with_skip_empty(tmp_path, capsys):
    """images/<split> + annotations/<split>, id kelas 1-150 (trotoar = 12); gambar dalam ruangan tanpa trotoar dibuang."""
    src, out = tmp_path / "ade", tmp_path / "yolo"
    for split in ("training", "validation"):
        (src / "images" / split).mkdir(parents=True)
        (src / "annotations" / split).mkdir(parents=True)
        for name, sidewalk in (("ADE_street", True), ("ADE_kitchen", False)):
            cv2.imwrite(str(src / "images" / split / f"{name}.jpg"), np.full((100, 200, 3), 70, np.uint8))
            lab = np.full((100, 200), 1, np.uint8)            # 1 = dinding
            if sidewalk:
                lab[60:] = 12
            cv2.imwrite(str(src / "annotations" / split / f"{name}.png"), lab)
    st = ds.masks_to_yolo(src, out, image_dir="images", mask_dir="annotations", image_suffix=".jpg", mask_suffix=".png",
                          label_id=12, splits=("training", "validation"), skip_empty=True)
    assert st["gambar"] == 2 and st["dibuang_kosong"] == 2 and st["per_split"] == {"train": 1, "val": 1}
    assert not list((out / "images" / "train").glob("ADE_kitchen*"))
    st = ds.masks_to_yolo(src, tmp_path / "yolo_all", image_dir="images", mask_dir="annotations", image_suffix=".jpg",
                          mask_suffix=".png", label_id=12, splits=("training", "validation"))
    assert st["gambar"] == 4 and st["tanpa_kelas"] == 2                                     # tanpa --skip-empty: semuanya ikut
    main(["data", "masks", "--src", str(src), "--out", str(tmp_path / "cli"), "--image-dir", "images", "--mask-dir", "annotations",
          "--image-suffix", ".jpg", "--mask-suffix", ".png", "--label-id", "12", "--splits", "training,validation", "--skip-empty"])
    assert "Dibuang karena tidak memuat sidewalk: 2" in capsys.readouterr().out


def test_masks_to_yolo_binary_jpeg_masks_with_prefixes(tmp_path, capsys):
    """Tata letak SidewalkDetection: <split>/image/Raw_1.jpg + <split>/label/Label_1.jpg, mask biner JPEG (nilai tidak persis 255)."""
    src, out = tmp_path / "sd", tmp_path / "yolo"
    for split in ("train", "val"):
        (src / split / "image").mkdir(parents=True)
        (src / split / "label").mkdir(parents=True)
        for i, full in ((1, False), (2, True)):
            cv2.imwrite(str(src / split / "image" / f"Raw_{i}.jpg"), np.full((90, 160, 3), 90, np.uint8))
            lab = np.zeros((90, 160, 3), np.uint8)
            lab[40:] = 255 if not full else lab[40:]
            if full:
                lab[:] = 0                                     # tanpa trotoar sama sekali
            cv2.imwrite(str(src / split / "label" / f"Label_{i}.jpg"), lab, [cv2.IMWRITE_JPEG_QUALITY, 60])
        cv2.imwrite(str(src / split / "label" / "Catatan_3.jpg"), np.zeros((9, 9, 3), np.uint8))   # bukan berawalan Label_: harus diabaikan
    st = ds.masks_to_yolo(src, out, image_dir="{split}/image", mask_dir="{split}/label", image_prefix="Raw_", mask_prefix="Label_",
                          image_suffix=".jpg", mask_suffix=".jpg", threshold=127)
    assert st["gambar"] == 4 and st["dengan_kelas"] == 2 and st["tanpa_kelas"] == 2 and st["tanpa_gambar"] == 0
    assert (out / "images" / "train" / "1.jpg").is_file() and (out / "labels" / "val" / "2.txt").read_text() == ""
    rows = (out / "labels" / "train" / "1.txt").read_text().strip().splitlines()
    assert len(rows) == 1 and rows[0].startswith("0 ")
    ys = [float(v) for v in rows[0].split()[2::2]]
    assert min(ys) > 0.40 and max(ys) > 0.97                      # poligon mengikuti bagian bawah gambar (y >= 40/90)
    main(["data", "masks", "--src", str(src), "--out", str(tmp_path / "cli"), "--image-dir", "{split}/image", "--mask-dir", "{split}/label",
          "--image-prefix", "Raw_", "--mask-prefix", "Label_", "--image-suffix", ".jpg", "--mask-suffix", ".jpg", "--threshold", "127"])
    assert "4 gambar" in capsys.readouterr().out
    main(["data", "masks", "--src", str(src), "--out", str(tmp_path / "cli2"), "--preset", "sidewalkdetection"])      # preset = hal yang sama
    assert "4 gambar" in capsys.readouterr().out
    assert (tmp_path / "cli2" / "labels" / "train" / "1.txt").read_text() == (tmp_path / "cli" / "labels" / "train" / "1.txt").read_text()


def test_masks_to_yolo_auto_image_extension_walkscapes_layout(tmp_path):
    """imgs/<split>/x.(jpg|jpeg|png) + masks/<split>/x_mask.png (id 1 = trotoar, 255 = void): ekstensi gambar dicari otomatis."""
    src, out = tmp_path / "ws", tmp_path / "yolo"
    for split in ("train", "val"):
        (src / "imgs" / split).mkdir(parents=True)
        (src / "masks" / split).mkdir(parents=True)
        for name, ext in (("poland_1", ".jpg"), ("poland_2", ".jpeg"), ("poland_3", ".png")):
            cv2.imwrite(str(src / "imgs" / split / (name + ext)), np.full((80, 120, 3), 60, np.uint8))
            lab = np.zeros((80, 120), np.uint8)
            lab[50:] = 1
            lab[:5] = 255
            cv2.imwrite(str(src / "masks" / split / f"{name}_mask.png"), lab)
    st = ds.masks_to_yolo(src, out, image_dir="imgs", mask_dir="masks", image_suffix="auto", mask_suffix="_mask.png", label_id=1)
    assert st["gambar"] == 6 and st["dengan_kelas"] == 6 and st["tanpa_gambar"] == 0
    main(["data", "masks", "--src", str(src), "--out", str(tmp_path / "cli"), "--preset", "walkscapes"])
    assert (tmp_path / "cli" / "labels" / "val" / "poland_2.txt").read_text() == (out / "labels" / "val" / "poland_2.txt").read_text()
    (src / "imgs" / "train" / "poland_1.jpg").unlink()
    assert ds.masks_to_yolo(src, tmp_path / "yolo2", image_dir="imgs", mask_dir="masks", image_suffix="auto",
                            mask_suffix="_mask.png", label_id=1)["tanpa_gambar"] == 1


def test_masks_to_yolo_errors_are_clear(tmp_path):
    with pytest.raises(FileNotFoundError, match="Tidak ada folder"):
        ds.masks_to_yolo(tmp_path, tmp_path / "o", **ds.PRESETS["cityscapes"])
    with pytest.raises(ValueError, match="label-id"):
        ds.masks_to_yolo(tmp_path, tmp_path / "o", image_dir="a", mask_dir="b", image_suffix=".jpg", mask_suffix=".png")


def make_yolo_export(root, names, rows, splits=("train", "valid")):
    """Ekspor gaya Roboflow: <split>/images, <split>/labels, data.yaml. rows: [(nama, [baris label])]."""
    for split in splits:
        (root / split / "images").mkdir(parents=True)
        (root / split / "labels").mkdir(parents=True)
        for stem, lines in rows:
            cv2.imwrite(str(root / split / "images" / f"{stem}.jpg"), np.zeros((20, 20, 3), np.uint8))
            (root / split / "labels" / f"{stem}.txt").write_text("".join(ln + "\n" for ln in lines))
    if isinstance(names, dict):                                  # gaya Ultralytics: names: {0: a, 1: b}
        (root / "data.yaml").write_text("names:\n" + "".join(f"  {k}: {v}\n" for k, v in names.items()))
    else:                                                        # gaya Roboflow: names: ['a', 'b']
        (root / "data.yaml").write_text("names: [" + ", ".join(f"'{n}'" for n in names) + "]\nnc: %d\n" % len(names))


def test_merge_yolo_remaps_drops_and_keeps_polygons(tmp_path):
    a, b, out = tmp_path / "a", tmp_path / "b", tmp_path / "out"
    make_yolo_export(a, ["car-on-sidewalk", "crack", "motorcycle-on-sidewalk"], [
        ("x1", ["0 0.5 0.5 0.2 0.2", "1 0.1 0.1 0.1 0.1"]),        # mobil + retakan: retakan dibuang
        ("x2", ["1 0.3 0.3 0.1 0.1"]),                              # hanya retakan: gambar dilewati
        ("x3", []),                                                 # gambar negatif asli: dipertahankan
    ])
    make_yolo_export(b, {0: "illegal-parking", 1: "tree"}, [("y1", ["0 0.1 0.1 0.9 0.1 0.5 0.9"])], splits=("train", "test"))
    st = ds.merge_yolo([a, b], out, class_map={"car-on-sidewalk": "kendaraan_di_trotoar", "motorcycle-on-sidewalk": "motor_di_trotoar",
                                               "illegal-parking": "kendaraan_di_trotoar"},
                       names=["kendaraan_di_trotoar", "motor_di_trotoar"])
    assert st["gambar"] == {"train": 3, "val": 2, "test": 1} and st["dilewati"] == 2    # x2 di train dan di valid
    assert st["instans"] == {"kendaraan_di_trotoar": 4, "motor_di_trotoar": 0}
    assert (out / "labels" / "train" / "s0_x1.txt").read_text() == "0 0.5 0.5 0.2 0.2\n"
    assert (out / "labels" / "train" / "s1_y1.txt").read_text() == "0 0.1 0.1 0.9 0.1 0.5 0.9\n"      # baris poligon utuh
    assert (out / "labels" / "train" / "s0_x3.txt").read_text() == "" and (out / "images" / "val" / "s0_x1.jpg").is_file()
    yml = (out / "data.yaml").read_text()
    assert "0: kendaraan_di_trotoar" in yml and "1: motor_di_trotoar" in yml and "val: images/val" in yml and "test: images/test" in yml


def test_merge_yolo_without_map_keeps_everything_and_validates(tmp_path):
    a, out = tmp_path / "a", tmp_path / "out"
    make_yolo_export(a, ["car", "bike"], [("x", ["1 0.5 0.5 0.1 0.1"])], splits=("train",))
    st = ds.merge_yolo([a], out)
    assert st["instans"] == {"car": 0, "bike": 1}
    with pytest.raises(ValueError, match="Tidak ada kelas"):
        ds.merge_yolo([a], tmp_path / "o2", class_map={"salah-ketik": "x"})
    with pytest.raises(FileNotFoundError, match="data.yaml"):
        ds.merge_yolo([tmp_path / "tidak-ada"], tmp_path / "o3")


def test_cli_data_commands(tmp_path, capsys):
    src, out = tmp_path / "cs", tmp_path / "yolo"
    make_cityscapes(src, splits=("train",), per_split=1)
    main(["data", "masks", "--src", str(src), "--out", str(out), "--preset", "cityscapes"])
    text = capsys.readouterr().out
    assert "1 gambar" in text and "python main.py train --data" in text and (out / "data.yaml").is_file()
    a, o2 = tmp_path / "a", tmp_path / "m"
    make_yolo_export(a, ["car-on-sidewalk"], [("x", ["0 0.5 0.5 0.2 0.2"])], splits=("train",))
    main(["data", "merge", "--src", str(a), "--out", str(o2), "--map", "car-on-sidewalk=parkir"])
    assert "parkir" in (o2 / "data.yaml").read_text()
    with pytest.raises(SystemExit, match="preset"):
        main(["data", "masks", "--src", str(src), "--out", str(tmp_path / "z")])
    with pytest.raises(SystemExit, match="R,G,B"):
        main(["data", "masks", "--src", str(src), "--out", str(tmp_path / "z"), "--preset", "cityscapes", "--label-rgb", "1,2"])

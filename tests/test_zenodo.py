"""Konverter Zenodo (motor di area pejalan kaki). Data SINTETIS: format anotasi aslinya belum diketahui,
jadi yang diuji adalah pembaca YOLO txt dan Pascal VOC xml, pemetaan kelas, negatif, pembagian, dan pesan galat."""
import cv2
import numpy as np
import pytest

from trotoar import datasets as ds
from trotoar import zenodo as zn
from trotoar.cli import main

W, H = 128, 72


def _img(path, seed=0):
    rng = np.random.default_rng(seed)
    cv2.imwrite(str(path), rng.integers(0, 255, (H, W, 3), dtype=np.uint8))


def _yolo_set(tmp_path, with_classes_txt=True, n_pos=6, n_neg=10):
    """Gambar i<n_pos: berisi MMAP (id 1) dan MNAP (id 2) + MMNAP (id 0); sisanya hanya MMNAP/PCP."""
    root = tmp_path / "zen"
    (root / "images").mkdir(parents=True)
    (root / "etiquetas").mkdir()
    for i in range(n_pos + n_neg):
        _img(root / "images" / f"cam{i:03d}.jpg", i)
        rows = ["0 0.20 0.50 0.10 0.20"]                       # MMNAP
        if i < n_pos:
            rows += ["1 0.50 0.50 0.20 0.40", "2 0.80 0.60 0.10 0.30"]
        else:
            rows += ["3 0.60 0.60 0.05 0.20"]                  # PCP
        (root / "etiquetas" / f"cam{i:03d}.txt").write_text("\n".join(rows) + "\n", encoding="utf-8")
    if with_classes_txt:
        (root / "etiquetas" / "classes.txt").write_text("MMNAP\nMMAP\nMNAP\nPCP\n", encoding="utf-8")
    return root


def _voc_set(tmp_path):
    root = tmp_path / "zenvoc"
    (root / "images").mkdir(parents=True)
    (root / "etiquetas").mkdir()
    for i in range(4):
        _img(root / "images" / f"v{i}.jpg", i)
        objs = ""
        todo = [("MMAP", (10, 10, 60, 50)), ("MNAP", (70, 20, 100, 60)), ("MMNAP", (0, 0, 20, 20))] if i < 2 else [("MMNAP", (0, 0, 20, 20))]
        for name, (x1, y1, x2, y2) in todo:
            objs += f"<object><name>{name}</name><bndbox><xmin>{x1}</xmin><ymin>{y1}</ymin><xmax>{x2}</xmax><ymax>{y2}</ymax></bndbox></object>"
        # i=0 tanpa <size> (ukuran dibaca dari gambar); lainnya dengan <size>
        size = "" if i == 0 else f"<size><width>{W}</width><height>{H}</height></size>"
        (root / "etiquetas" / f"v{i}.xml").write_text(f"<annotation>{size}{objs}</annotation>", encoding="utf-8")
    return root


def _rows(path):
    return [ln.split() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_inspect_reports_format_classes_and_counts(tmp_path):
    info = zn.inspect_zenodo(_yolo_set(tmp_path))
    assert info["format"] == "yolo" and info["gambar"] == 16 and info["berkas_label"] == 16
    assert info["objek_per_kelas"] == {"MMNAP": 16, "MMAP": 6, "MNAP": 6, "PCP": 10}
    assert info["gambar_per_kelas"]["MMAP"] == 6 and info["ukuran_contoh"] == (W, H)
    assert info["nama_kelas_dari"].endswith("classes.txt")


def test_inspect_flags_assumed_class_order_without_classes_txt(tmp_path):
    info = zn.inspect_zenodo(_yolo_set(tmp_path, with_classes_txt=False))
    assert "ASUMSI" in info["nama_kelas_dari"] and info["objek_per_kelas"]["MMAP"] == 6


def test_classes_flag_overrides_id_order(tmp_path):
    root = _yolo_set(tmp_path, with_classes_txt=False)
    info = zn.inspect_zenodo(root, classes=["PCP", "MMNAP", "MMAP", "MNAP"])    # id 1 = MMNAP, id 2 = MMAP, id 3 = MNAP
    assert info["nama_kelas_dari"] == "--classes" and info["objek_per_kelas"]["MMNAP"] == 6 and info["objek_per_kelas"]["MNAP"] == 10


def test_convert_maps_classes_drops_others_and_uses_negatives(tmp_path):
    out = tmp_path / "out"
    st = zn.convert_zenodo(_yolo_set(tmp_path), out, neg_ratio=0.5, val_frac=0.25, seed=1)
    assert st["positif"] == 6 and st["negatif_tersedia"] == 10 and st["negatif_dipakai"] == 3
    assert st["instans"] == {"kendaraan_parkir": 6, "pengendara_lewat": 6}
    assert st["kelas_dibuang"] == {"MMNAP": 16, "PCP": 10}
    assert sum(st["gambar"].values()) == 9 and st["gambar"]["val"] >= 1
    yaml = (out / "data.yaml").read_text(encoding="utf-8")
    assert "0: kendaraan_parkir" in yaml and "1: pengendara_lewat" in yaml and "path:" not in yaml
    # setiap gambar punya berkas label; poligon 4 titik = 1 + 8 nilai; negatif = berkas kosong
    n_empty = 0
    for split in ("train", "val"):
        for ip in (out / "images" / split).glob("*.jpg"):
            rows = _rows(out / "labels" / split / (ip.stem + ".txt"))
            n_empty += not rows
            assert all(len(r) == 9 and r[0] in ("0", "1") for r in rows)
    assert n_empty == 3


def test_polygon_corners_are_the_box(tmp_path):
    out = tmp_path / "out"
    zn.convert_zenodo(_yolo_set(tmp_path, n_pos=1, n_neg=0), out, val_frac=0.0)
    rows = _rows(next((out / "labels" / "train").glob("*.txt")))
    mmap = next(r for r in rows if r[0] == "1")                      # MMAP: cx=.5 cy=.5 w=.2 h=.4 -> x .4-.6, y .3-.7
    v = [float(x) for x in mmap[1:]]
    assert v == pytest.approx([0.4, 0.3, 0.6, 0.3, 0.6, 0.7, 0.4, 0.7])


def test_box_format_writes_five_values(tmp_path):
    out = tmp_path / "out"
    zn.convert_zenodo(_yolo_set(tmp_path, n_pos=2, n_neg=0), out, fmt="box", val_frac=0.5)
    rows = [r for sp in ("train", "val") for f in (out / "labels" / sp).glob("*.txt") for r in _rows(f)]
    assert rows and all(len(r) == 5 for r in rows)


def test_voc_with_and_without_size(tmp_path):
    out = tmp_path / "out"
    st = zn.convert_zenodo(_voc_set(tmp_path), out, neg_ratio=1.0, val_frac=0.25)
    assert st["format_anotasi"] == "voc" and st["positif"] == 2 and st["negatif_dipakai"] == 2
    assert st["instans"] == {"kendaraan_parkir": 2, "pengendara_lewat": 2}
    rows = [r for sp in ("train", "val") for f in (out / "labels" / sp).glob("*.txt") for r in _rows(f)]
    mmap = next(r for r in rows if r[0] == "1")
    assert [float(x) for x in mmap[1:]] == pytest.approx([10 / W, 10 / H, 60 / W, 10 / H, 60 / W, 50 / H, 10 / W, 50 / H], abs=1e-5)


def test_custom_map_and_deterministic_seed(tmp_path):
    root = _yolo_set(tmp_path)
    a = zn.convert_zenodo(root, tmp_path / "a", class_map={"MMNAP": "jalan_motor"}, neg_ratio=0, seed=3)
    assert list(a["instans"]) == ["jalan_motor"] and a["instans"]["jalan_motor"] == 16
    b1 = zn.convert_zenodo(root, tmp_path / "b1", neg_ratio=0.5, seed=7)
    b2 = zn.convert_zenodo(root, tmp_path / "b2", neg_ratio=0.5, seed=7)
    assert sorted(p.name for p in (tmp_path / "b1" / "images" / "val").iterdir()) == sorted(p.name for p in (tmp_path / "b2" / "images" / "val").iterdir())
    assert b1["gambar"] == b2["gambar"]


def test_unsupported_format_gives_clear_error(tmp_path):
    root = tmp_path / "z"
    (root / "images").mkdir(parents=True)
    (root / "etiquetas").mkdir()
    _img(root / "images" / "a.jpg")
    (root / "etiquetas" / "a.json").write_text('{"shapes": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="tidak didukung") as e:
        zn.inspect_zenodo(root)
    assert ".json" in str(e.value) and "shapes" in str(e.value)


def test_no_target_objects_lists_names_seen(tmp_path):
    root = _yolo_set(tmp_path, with_classes_txt=False)
    with pytest.raises(ValueError, match="Nama kelas yang terbaca") as e:
        zn.convert_zenodo(root, tmp_path / "o", class_map={"XYZ": "x"})
    assert "MMNAP" in str(e.value)


def test_missing_folders_message_lists_contents(tmp_path):
    (tmp_path / "kosong").mkdir()
    (tmp_path / "kosong" / "PedestrianDataSet.zip").write_bytes(b"zip")
    with pytest.raises(FileNotFoundError, match="Ekstrak PedestrianDataSet.zip"):
        zn.find_dirs(tmp_path / "kosong")


def test_three_class_merge_with_a_sidewalk_set(tmp_path):
    """Bahan model 3-kelas: set trotoar (poligon) + set kendaraan -> trotoar / kendaraan_parkir / pengendara_lewat."""
    sw = tmp_path / "sw"
    for split in ("train", "val"):
        (sw / "images" / split).mkdir(parents=True)
        (sw / "labels" / split).mkdir(parents=True)
        _img(sw / "images" / split / f"s_{split}.jpg")
        (sw / "labels" / split / f"s_{split}.txt").write_text("0 0.1 0.6 0.9 0.6 0.9 0.95 0.1 0.95\n", encoding="utf-8")
    ds._write_yaml(sw, ["sidewalk"], ["train", "val"])
    veh = tmp_path / "veh"
    zn.convert_zenodo(_yolo_set(tmp_path, n_pos=4, n_neg=0), veh, val_frac=0.5)
    st = ds.merge_yolo([sw, veh], tmp_path / "all", names=["trotoar", "kendaraan_parkir", "pengendara_lewat"],
                       class_map={"sidewalk": "trotoar", "kendaraan_parkir": "kendaraan_parkir", "pengendara_lewat": "pengendara_lewat"})
    assert st["instans"] == {"trotoar": 2, "kendaraan_parkir": 4, "pengendara_lewat": 4}
    assert (tmp_path / "all" / "data.yaml").read_text(encoding="utf-8").count(":") >= 5


def test_cli_inspect_and_convert(tmp_path, capsys):
    root = _yolo_set(tmp_path)
    main(["data", "zenodo", "--src", str(root), "--inspect"])
    out = capsys.readouterr().out
    assert "objek_per_kelas" in out and "MMAP" in out and "--classes" in out
    main(["data", "zenodo", "--src", str(root), "--out", str(tmp_path / "o")])
    out = capsys.readouterr().out
    assert "Selesai" in out and "kendaraan_parkir" in out and "optimistis" in out and "yolo11n.pt" in out
    with pytest.raises(SystemExit, match="--out"):
        main(["data", "zenodo", "--src", str(root)])
    with pytest.raises(SystemExit):
        main(["data", "zenodo", "--src", str(tmp_path / "tidak_ada"), "--inspect"])


def test_colab_notebook_commands_match_the_real_cli():
    """Setiap perintah `!python main.py ...` di notebook harus lolos pemeriksaan argumen parser CLI yang sebenarnya."""
    import json
    import shlex
    from pathlib import Path

    from trotoar.cli import build_parser

    nb = json.loads((Path(__file__).resolve().parent.parent / "notebooks" / "latih_kendaraan.ipynb").read_text(encoding="utf-8"))
    cmds = [ln.strip()[1:].strip() for c in nb["cells"] if c["cell_type"] == "code" for ln in c["source"] if ln.strip().startswith(("!python main.py", "!    python"))]
    cmds += [ln.strip()[1:].strip() for c in nb["cells"] if c["cell_type"] == "code" for ln in c["source"]
             if ln.strip().startswith("!") and "python main.py" in ln and ln.strip()[1:].strip() not in cmds]
    cmds = sorted(set(c for c in cmds if c.startswith("python main.py")))
    assert len(cmds) >= 6
    for c in cmds:
        argv = shlex.split(c)[2:]
        build_parser().parse_args(argv)                              # SystemExit bila ada argumen yang tak dikenal

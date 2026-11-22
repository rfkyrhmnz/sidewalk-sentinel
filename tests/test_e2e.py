"""Tes ujung-ke-ujung: video & CLI dengan segmenter tiruan pada adegan sintetis.

Jalur YOLO asli TIDAK diuji di sini (ultralytics/bobot tidak tersedia); yang diuji adalah seluruh
pipeline di sekitarnya: segmentasi trotoar klasik, geometri, tracking-dwell, kompensasi gerak kamera,
penulisan video/CSV/JSON, serta perintah CLI.
"""
import csv
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from synthetic import FakeSegmenter, make_scene
from trotoar.cli import main
from trotoar.config import Config
from trotoar.media import have_ffmpeg
from trotoar.pipeline import Analyzer

FPS = 5
SECONDS = 14

PARKED_CAR = dict(kind="car", box=(200, 350, 400, 440), track_id=1)
ROAD_CAR = dict(kind="car", box=(430, 225, 530, 290), track_id=2, color=(200, 60, 30))


def build_video(path, vehicles_fn, shift_fn=None, n=FPS * SECONDS):
    """Tulis video sintetis; kembalikan daftar Detections per frame."""
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (640, 480))
    dets_all = []
    for k in range(n):
        shift = shift_fn(k) if shift_fn else (0, 0)
        img, gt, dets = make_scene("across", vehicles_fn(k), shift=shift, frame_noise=2.0, frame_seed=k)
        w.write(img)
        dets_all.append(dets)
    w.release()
    return dets_all


def analyzer_for(dets_all, **cfg_kw):
    cfg = Config(min_dwell_s=6.0, stationary_window_s=2.0, **cfg_kw)
    return Analyzer(cfg, FakeSegmenter(lambda n, img: dets_all[n]), "classical"), cfg


def moving_motor(k):
    x = 20 + 8 * k
    return dict(kind="motor", box=(x, 240, x + 60, 300), rider=True, track_id=3)


@pytest.mark.parametrize("shaky", [False, True])
def test_video_confirms_only_the_parked_car(tmp_path, shaky):
    rng = np.random.default_rng(1)
    walk = np.cumsum(rng.integers(-2, 3, (FPS * SECONDS, 2)), axis=0).clip(-20, 20) if shaky else None
    shift_fn = (lambda k: tuple(walk[k])) if shaky else None
    src = tmp_path / "in.mp4"
    dets_all = build_video(src, lambda k: [PARKED_CAR, ROAD_CAR, moving_motor(k)], shift_fn)
    an, _ = analyzer_for(dets_all)
    s = an.run_video(src, tmp_path / "out" / "o.mp4", tmp_path / "o.csv", tmp_path / "o.json",
                     h264=have_ffmpeg(), verbose=False)
    ev = s["parkir_terkonfirmasi"]
    assert [e["track_id"] for e in ev] == [1], f"seharusnya hanya mobil #1, dapat {ev}"
    assert ev[0]["kelas"] == "mobil" and ev[0]["durasi_s"] >= 6.0
    assert ev[0]["mulai_s"] <= 2.5
    rows = list(csv.DictReader(open(tmp_path / "o.csv", encoding="utf-8")))
    assert rows and {r["track_id"] for r in rows} == {"1", "2", "3"}
    assert not any(r["status"] == "PARKIR_TERKONFIRMASI" for r in rows if r["track_id"] in ("2", "3"))
    cap = cv2.VideoCapture(str(tmp_path / "out" / "o.mp4"))
    assert cap.isOpened() and cap.get(cv2.CAP_PROP_FRAME_COUNT) >= FPS * SECONDS - 2
    cap.release()
    assert json.loads((tmp_path / "o.json").read_text(encoding="utf-8"))["parkir_terkonfirmasi"]


def test_camera_compensation_matters_end_to_end(tmp_path):
    """Motor sempit parkir, kamera panning bolak-balik 3 px/frame: hanya terdeteksi bila gerak kamera dikompensasi."""
    motor = dict(kind="motor", box=(300, 360, 370, 440), rider=False, track_id=4)

    def pan(k):
        pos = (3 * k) % 160
        return ((pos if pos <= 80 else 160 - pos) - 40, 0)
    src = tmp_path / "in.mp4"
    dets_all = build_video(src, lambda k: [motor], pan)
    found = {}
    for comp in (True, False):
        an, _ = analyzer_for(dets_all, compensate_camera=comp)
        s = an.run_video(src, tmp_path / f"o{comp}.mp4", h264=False, verbose=False)
        found[comp] = [e["track_id"] for e in s["parkir_terkonfirmasi"]]
    assert found[True] == [4]
    assert found[False] == []


def test_video_vehicle_crossing_sidewalk_is_not_parking(tmp_path):
    src = tmp_path / "in.mp4"

    def motor_no_rider(k):
        x = 10 + 8 * k
        return [dict(kind="motor", box=(x, 380, x + 70, 460), rider=False, track_id=9)]
    dets_all = build_video(src, motor_no_rider, n=FPS * 10)
    an, _ = analyzer_for(dets_all)
    s = an.run_video(src, tmp_path / "o.mp4", tmp_path / "o.csv", h264=False, verbose=False)
    assert s["parkir_terkonfirmasi"] == []
    statuses = {r["status"] for r in csv.DictReader(open(tmp_path / "o.csv", encoding="utf-8"))}
    assert "DI_TROTOAR_BERGERAK" in statuses


def test_polygon_mode_on_video_with_camera_shift(tmp_path):
    """Poligon trotoar ditetapkan pada frame pertama dan ikut bergeser mengikuti gerak kamera."""
    rng = np.random.default_rng(5)
    walk = np.cumsum(rng.integers(-2, 3, (FPS * SECONDS, 2)), axis=0).clip(-15, 15)
    src = tmp_path / "in.mp4"
    dets_all = build_video(src, lambda k: [PARKED_CAR], lambda k: tuple(walk[k]))
    cfg = Config(min_dwell_s=6.0, stationary_window_s=2.0)
    poly = [[[0, 303 / 480], [1, 303 / 480], [1, 1], [0, 1]]]
    an = Analyzer(cfg, FakeSegmenter(lambda n, img: dets_all[n]), "polygon", polygon=poly)
    s = an.run_video(src, tmp_path / "o.mp4", h264=False, verbose=False)
    assert len(s["parkir_terkonfirmasi"]) == 1


# ------------------------------------------------------------------ CLI
SCENES = {
    "pos1": ("across", [PARKED_CAR]),
    "pos2": ("across", [dict(kind="motor", box=(300, 360, 370, 440), rider=False, track_id=1)]),
    "neg1": ("across", [ROAD_CAR]),
    "neg2": ("across", [dict(kind="motor", box=(60, 360, 130, 440), rider=True, track_id=1)]),
}


@pytest.fixture()
def dataset(tmp_path):
    imgs, gts, dets = tmp_path / "imgs", tmp_path / "gt", {}
    imgs.mkdir(); gts.mkdir()
    for name, (kind, veh) in SCENES.items():
        img, gt, d = make_scene(kind, veh, seed=hash(name) % 5)
        cv2.imwrite(str(imgs / f"{name}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
        cv2.imwrite(str(gts / f"{name}.png"), gt.astype(np.uint8) * 255)
        dets[name] = (cv2.imread(str(imgs / f"{name}.jpg")), d)
    with open(tmp_path / "labels.csv", "w", newline="") as f:
        f.write("image,label\npos1.jpg,1\npos2.jpg,parkir\nneg1.jpg,0\nneg2.jpg,bukan\n")

    def factory(cfg):                         # pilih Detections dengan gambar paling mirip
        def provider(n, img):
            best = min(dets.values(), key=lambda v: float(np.abs(v[0].astype(np.int16) - img.astype(np.int16)).mean()))
            return best[1]
        return FakeSegmenter(provider)
    return tmp_path, factory


def test_cli_image_and_eval_flow(dataset, capsys):
    tmp, factory = dataset
    out = tmp / "hasil"
    main(["image", str(tmp / "imgs"), "--out", str(out), "--save-masks", "--debug"], segmenter_factory=factory)
    txt = capsys.readouterr().out
    assert "pos1.jpg: 1 kendaraan, 1 terindikasi parkir" in txt
    assert "neg1.jpg: 1 kendaraan, 0 terindikasi parkir" in txt
    assert "neg2.jpg: 1 kendaraan, 0 terindikasi parkir" in txt           # ada pengendara -> bukan parkir
    assert (out / "pos1_hasil.jpg").exists() and (out / "pos1_sidewalk.png").exists()

    main(["eval-seg", "--pred", str(out), "--gt", str(tmp / "gt")])
    assert "Rata-rata (4 gambar)" in capsys.readouterr().out

    main(["eval-decision", "--images", str(tmp / "imgs"), "--labels", str(tmp / "labels.csv"),
          "--sweep-tau", "0.3,0.7", "--out", str(tmp / "ev.csv")], segmenter_factory=factory)
    txt = capsys.readouterr().out
    rows = list(csv.DictReader(open(tmp / "ev.csv")))
    base = next(r for r in rows if abs(float(r["tau"]) - 0.5) < 1e-9)
    assert (base["tp"], base["fp"], base["fn"], base["tn"]) == ("2", "0", "0", "2")
    assert len(rows) == 3 and "Evaluasi tingkat gambar (n=4)" in txt

    main(["eval-decision", "--images", str(tmp / "imgs"), "--labels", str(tmp / "labels.csv"),
          "--oracle-sidewalk", str(tmp / "gt")], segmenter_factory=factory)
    assert "[GT trotoar]" in capsys.readouterr().out


def test_cli_image_polygon_mode(dataset, capsys):
    tmp, factory = dataset
    poly = tmp / "poly.json"
    poly.write_text(json.dumps({"normalized": True, "polygons": [[[0, .63], [1, .63], [1, 1], [0, 1]]]}))
    main(["image", str(tmp / "imgs" / "pos1.jpg"), "--out", str(tmp / "h2"), "--polygon", str(poly)],
         segmenter_factory=factory)
    assert "1 terindikasi parkir" in capsys.readouterr().out
    rows = list(csv.DictReader(open(tmp / "h2" / "hasil_gambar.csv")))
    assert float(rows[0]["contact_ratio"]) >= 0.9                       # poligon menutupi area di bawah kendaraan


def test_cli_errors_are_friendly(tmp_path, capsys):
    with pytest.raises(SystemExit):
        main(["image", str(tmp_path / "tidak_ada.jpg"), "--sidewalk", "polygon"], segmenter_factory=lambda c: None)
    assert "Galat" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["image", "x.jpg", "--set", "bukan_param=1"], segmenter_factory=lambda c: None)
    assert "Parameter tidak dikenal" in capsys.readouterr().err


def test_cli_without_ultralytics_gives_install_hint(tmp_path, capsys):
    pytest.importorskip("PIL")
    try:
        import ultralytics  # noqa: F401
        pytest.skip("ultralytics terpasang")
    except ImportError:
        pass
    cv2.imwrite(str(tmp_path / "a.jpg"), np.zeros((50, 50, 3), np.uint8))
    with pytest.raises(SystemExit):
        main(["image", str(tmp_path / "a.jpg"), "--out", str(tmp_path / "o")])
    assert "pip install ultralytics" in capsys.readouterr().err


@pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg tidak ada")
def test_cli_prepare_and_frames(tmp_path, capsys):
    from PIL import Image
    (tmp_path / "raw").mkdir()
    Image.new("RGB", (80, 50), (1, 2, 3)).save(tmp_path / "raw" / "a.jpg")
    w = cv2.VideoWriter(str(tmp_path / "raw" / "v.avi"), cv2.VideoWriter_fourcc(*"MJPG"), 10, (160, 120))
    for i in range(20):
        w.write(np.full((120, 160, 3), i * 10, np.uint8))
    w.release()
    main(["prepare", str(tmp_path / "raw"), "--out", str(tmp_path / "bersih"), "--fps", "10"])
    assert (tmp_path / "bersih" / "foto" / "a.jpg").exists() and (tmp_path / "bersih" / "video" / "v.mp4").exists()
    main(["frames", str(tmp_path / "bersih" / "video" / "v.mp4"), "--out", str(tmp_path / "fr"), "--fps", "2"])
    assert len(list((tmp_path / "fr").glob("*.jpg"))) == 4

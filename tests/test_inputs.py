"""Tes parser hasil Ultralytics (objek tiruan), metrik, dan penanganan media HP."""
import math
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from trotoar import media
from trotoar.config import Config, apply_overrides
from trotoar.metrics import cohen_kappa, confusion, iou_dice, parse_label, prf
from trotoar.vehicles import parse_ultralytics_result


# ------------------------------------------------------------------ parser Ultralytics (tiruan)
class T:                                      # meniru tensor torch: .cpu().numpy()
    def __init__(self, a):
        self.a = np.asarray(a)

    def cpu(self):
        return self

    def numpy(self):
        return self.a


class FakeBoxes:
    def __init__(self, cls, conf, xyxy, ids=None):
        self.cls, self.conf, self.xyxy = T(cls), T(conf), T(xyxy)
        self.id = T(ids) if ids is not None else None
        self._n = len(cls)

    def __len__(self):
        return self._n


class FakeMasks:
    def __init__(self, xy):
        self.xy = xy


class FakeRes:
    def __init__(self, names, boxes, masks):
        self.names, self.boxes, self.masks = names, boxes, masks


def sq(x1, y1, x2, y2):
    return np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], float)


NAMES = {0: "person", 2: "car", 3: "motorcycle", 9: "traffic light", 20: "Sidewalk"}


def test_parser_maps_classes_ids_and_sidewalk_union():
    boxes = FakeBoxes([2, 3, 0, 9, 20, 20], [.9, .8, .7, .6, .5, .5],
                      [[10, 10, 60, 40], [70, 10, 100, 50], [75, 0, 95, 30], [0, 0, 5, 5], [0, 60, 50, 100], [50, 60, 100, 100]],
                      ids=[5, 6, 7, 8, 9, 10])
    masks = FakeMasks([sq(10, 10, 60, 40), sq(70, 10, 100, 50), sq(75, 0, 95, 30), sq(0, 0, 5, 5),
                       sq(0, 60, 50, 100), sq(50, 60, 100, 100)])
    d = parse_ultralytics_result(FakeRes(NAMES, boxes, masks), Config(), (120, 120, 3))
    assert [v.label for v in d.vehicles] == ["mobil", "motor"]
    assert [v.track_id for v in d.vehicles] == [5, 6]
    assert [p.label for p in d.persons] == ["orang"]
    assert d.sidewalk is not None and d.sidewalk[80, 20] and d.sidewalk[80, 80]       # gabungan dua poligon
    assert d.vehicles[0].mask[20, 30] and not d.vehicles[0].mask[80, 30]
    assert all(o.cls_name != "traffic light" for o in d.vehicles + d.persons)          # kelas lain diabaikan


def test_parser_without_masks_falls_back_to_box_and_without_ids():
    boxes = FakeBoxes([2], [.9], [[10, 10, 60, 40]])
    d = parse_ultralytics_result(FakeRes(NAMES, boxes, None), Config(), (100, 100, 3))
    assert len(d.vehicles) == 1 and d.vehicles[0].track_id is None and d.vehicles[0].mask[20, 30]


def test_parser_handles_no_detections():
    d = parse_ultralytics_result(FakeRes(NAMES, FakeBoxes([], [], np.zeros((0, 4))), None), Config(), (50, 50, 3))
    assert d.vehicles == [] and d.persons == [] and d.sidewalk is None


# ------------------------------------------------------------------ config & metrik
def test_apply_overrides_types():
    c = apply_overrides(Config(), ["min_dwell_s=7.5", "vote_window=5", "compensate_camera=false",
                                   "seed_box=0.1,0.8,0.5,0.99", "refine=grabcut", "device=cpu"])
    assert (c.min_dwell_s, c.vote_window, c.compensate_camera, c.refine, c.device) == (7.5, 5, False, "grabcut", "cpu")
    assert c.seed_box == (0.1, 0.8, 0.5, 0.99)
    with pytest.raises(ValueError):
        apply_overrides(Config(), ["tidak_ada=1"])


def test_iou_dice_and_prf():
    a = np.zeros((10, 10), bool); a[:, :5] = True
    b = np.zeros((10, 10), bool); b[:, :10] = True
    iou, dice = iou_dice(a, b)
    assert math.isclose(iou, 0.5) and math.isclose(dice, 2 * 50 / 150)
    assert iou_dice(np.zeros((3, 3), bool), np.zeros((3, 3), bool)) == (1.0, 1.0)
    tp, fp, fn, tn = confusion([1, 1, 0, 0, 1], [1, 0, 0, 1, 1])
    assert (tp, fp, fn, tn) == (2, 1, 1, 1)
    m = prf(tp, fp, fn, tn)
    assert math.isclose(m["precision"], 2 / 3) and math.isclose(m["recall"], 2 / 3) and math.isclose(m["f1"], 2 / 3)


def test_kappa_and_labels():
    assert cohen_kappa([1, 0, 1, 0], [1, 0, 1, 0]) == 1.0
    assert abs(cohen_kappa([1, 1, 0, 0], [1, 0, 1, 0])) < 1e-9          # sepakat murni kebetulan
    assert parse_label("Ya") and not parse_label("bukan") and parse_label(1)
    with pytest.raises(ValueError):
        parse_label("mungkin")


# ------------------------------------------------------------------ media HP
def test_exif_orientation_applied_and_exif_stripped(tmp_path):
    im = Image.new("RGB", (100, 60), (200, 30, 30))
    exif = Image.Exif()
    exif[0x0112] = 6                                          # iPhone portrait: putar 90° agar tegak
    exif[0x8825] = {1: "S"}                                   # ada blok GPS
    src = tmp_path / "IMG_0001.jpg"
    im.save(src, exif=exif)
    assert media.load_image(src).shape[:2] == (100, 60)       # sudah tegak (tinggi 100, lebar 60)
    out = media.normalize_image(src, tmp_path / "bersih")
    assert out.suffix == ".jpg" and len(Image.open(out).getexif()) == 0
    assert media.load_image(out).shape[:2] == (100, 60)


def test_heic_without_plugin_gives_clear_error(tmp_path):
    try:
        import pillow_heif  # noqa: F401
        pytest.skip("pillow-heif terpasang")
    except ImportError:
        pass
    p = tmp_path / "x.heic"
    p.write_bytes(b"bukan heic")
    with pytest.raises(RuntimeError, match="pillow-heif"):
        media.load_image(p)


@pytest.mark.skipif(not media.have_ffmpeg(), reason="ffmpeg tidak ada")
def test_video_normalize_and_extract_frames(tmp_path):
    src = tmp_path / "hp.avi"
    w = cv2.VideoWriter(str(src), cv2.VideoWriter_fourcc(*"MJPG"), 24, (320, 240))
    for i in range(72):                                       # 3 detik @24 fps
        w.write(np.full((240, 320, 3), i * 3, np.uint8))
    w.release()
    out = media.normalize_video(src, tmp_path / "out" / "hp.mp4", fps=15, max_side=160)
    cap = cv2.VideoCapture(str(out))
    assert cap.isOpened()
    assert abs(cap.get(cv2.CAP_PROP_FPS) - 15) < 0.5
    assert max(cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) == 160
    cap.release()
    n = media.extract_frames(src, tmp_path / "fr", fps=1.0)
    assert n == 3 and len(list((tmp_path / "fr").glob("*.jpg"))) == 3


def test_open_video_distinguishes_missing_from_unreadable(tmp_path):
    """OpenCV memberi isOpened()=False untuk dua kasus berbeda; pesannya harus menunjuk perbaikan yang tepat."""
    with pytest.raises(RuntimeError, match="tidak ditemukan") as e:
        media.open_video(tmp_path / "IMG_0001.mp4")
    assert "folder kerja" in str(e.value)
    rusak = tmp_path / "rusak.mp4"
    rusak.write_bytes(b"bukan video sama sekali" * 50)
    with pytest.raises(RuntimeError, match="HEVC") as e:
        media.open_video(rusak)
    assert "prepare" in str(e.value) and "tidak ditemukan" not in str(e.value)


def test_cli_video_missing_file_is_friendly(tmp_path, capsys):
    from trotoar.cli import main
    with pytest.raises(SystemExit):
        main(["video", str(tmp_path / "tidak_ada.mp4"), "--out", str(tmp_path / "o")], segmenter_factory=lambda c: None)
    err = capsys.readouterr().err
    assert "Galat" in err and "tidak ditemukan" in err

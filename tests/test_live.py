"""Tes mode live (ala CCTV). Kamera sungguhan tidak tersedia di CI, jadi:
  * sumber = berkas video sintetis (dibaca berurutan, jam = nomor frame / fps),
  * LatestFrameReader diuji dengan kamera tiruan.
Jalur webcam/RTSP asli (cv2.VideoCapture pada perangkat) TIDAK diuji di sini.
"""
import csv
import json
import time

import cv2
import numpy as np
import pytest

from synthetic import FakeSegmenter
from test_e2e import PARKED_CAR, ROAD_CAR, analyzer_for, build_video, FPS, SECONDS
from trotoar import media
from trotoar.cli import main
from trotoar.config import Config
from trotoar.pipeline import Analyzer


# ------------------------------------------------------------------ sumber & pembaca frame
def test_parse_and_classify_sources():
    assert media.parse_source("0") == 0 and media.parse_source(" 2 ") == 2
    assert media.parse_source("rtsp://u:p@10.0.0.5/stream") == "rtsp://u:p@10.0.0.5/stream"
    assert media.is_live_source(0) and media.is_live_source("http://192.168.1.5:8080/video")
    assert media.is_live_source("RTSP://x/y")
    assert not media.is_live_source("rekaman.mp4") and not media.is_live_source("C:\\video\\a.mp4")


class FakeCap:
    def __init__(self, n=60, delay=0.004, block_after=None):
        self.i, self.n, self.delay, self.block_after = 0, n, delay, block_after
        self.released = False

    def read(self):
        if self.block_after is not None and self.i >= self.block_after:
            time.sleep(0.6)                                  # kamera "macet"
            return False, None
        if self.i >= self.n:
            return False, None
        time.sleep(self.delay)
        self.i += 1
        return True, np.full((2, 2, 3), self.i, np.uint8)

    def release(self):
        self.released = True


def test_latest_frame_reader_skips_stale_frames_and_ends():
    r = media.LatestFrameReader(FakeCap(n=60, delay=0.004))
    seen = []
    while True:
        ok, f = r.read(timeout=1.0)
        if not ok:
            break
        seen.append(int(f[0, 0, 0]))
        time.sleep(0.03)                                     # konsumen lambat (seperti YOLO di CPU)
    r.stop()
    assert seen == sorted(seen) and len(set(seen)) == len(seen)
    assert len(seen) < 40, "frame basi seharusnya dibuang, bukan diproses semuanya"
    assert seen[-1] >= 55, "frame terakhir (terbaru) harus tetap sampai"


def test_latest_frame_reader_timeout_when_camera_stalls():
    cap = FakeCap(n=5, block_after=2)
    r = media.LatestFrameReader(cap)
    got = [r.read(timeout=0.3) for _ in range(3)]
    assert got[0][0] and got[1][0]
    assert got[2] == (False, None)                          # kamera macet -> menyerah, tidak menggantung
    r.stop()
    assert cap.released


# ------------------------------------------------------------------ run_live (sumber berkas, tanpa jendela)
def test_run_live_alerts_once_per_vehicle_and_logs(tmp_path):
    src = tmp_path / "in.mp4"
    dets_all = build_video(src, lambda k: [PARKED_CAR, ROAD_CAR])
    an, _ = analyzer_for(dets_all)
    out = tmp_path / "live"
    s = an.run_live(src, out, show=False, record=True, verbose=False)

    assert s["frame_diproses"] == FPS * SECONDS
    assert [a["track_id"] for a in s["alert"]] == [1]                   # satu alert, hanya untuk mobil di trotoar
    rows = list(csv.DictReader(open(out / "kejadian.csv", encoding="utf-8")))
    assert len(rows) == 1 and rows[0]["kelas"] == "mobil" and float(rows[0]["dwell_s"]) >= 6.0
    alert_img = cv2.imread(rows[0]["gambar"])
    assert alert_img is not None and alert_img.shape[:2] == (480, 640)
    cap = cv2.VideoCapture(str(out / "rekaman.mp4"))
    assert cap.isOpened() and cap.get(cv2.CAP_PROP_FRAME_COUNT) >= FPS * SECONDS - 2
    cap.release()
    assert json.loads((out / "ringkasan_live.json").read_text(encoding="utf-8"))["alert"]


def test_run_live_no_alert_for_vehicle_on_road(tmp_path):
    src = tmp_path / "in.mp4"
    dets_all = build_video(src, lambda k: [ROAD_CAR], n=FPS * 10)
    an, _ = analyzer_for(dets_all)
    s = an.run_live(src, tmp_path / "live", show=False, verbose=False)
    assert s["alert"] == [] and not (tmp_path / "live" / "kejadian.csv").exists()


def test_run_live_respects_max_frames_and_force_refresh_flag(tmp_path):
    src = tmp_path / "in.mp4"
    dets_all = build_video(src, lambda k: [PARKED_CAR], n=30)
    an, _ = analyzer_for(dets_all)
    s = an.run_live(src, tmp_path / "live", show=False, verbose=False, max_frames=12)
    assert s["frame_diproses"] == 12


def test_run_live_unopenable_source_gives_friendly_error(tmp_path):
    an = Analyzer(Config(), FakeSegmenter(lambda n, i: None), "classical")
    with pytest.raises(RuntimeError, match="Tidak bisa membuka sumber"):
        an.run_live(str(tmp_path / "tidak_ada.mp4"), tmp_path / "live", show=False, verbose=False)


# ------------------------------------------------------------------ CLI
def test_cli_live_headless(tmp_path, capsys):
    src = tmp_path / "in.mp4"
    dets_all = build_video(src, lambda k: [PARKED_CAR, ROAD_CAR])
    factory = lambda cfg: FakeSegmenter(lambda n, img: dets_all[n])
    main(["live", "--source", str(src), "--no-window", "--out", str(tmp_path / "o"), "--min-dwell", "6",
          "--set", "stationary_window_s=2"], segmenter_factory=factory)
    out = capsys.readouterr().out
    assert "alert: 1" in out and "mobil #1 parkir di trotoar" in out
    assert (tmp_path / "o" / "kejadian.csv").exists()


def test_cli_snapshot_from_file_and_error(tmp_path, capsys):
    src = tmp_path / "in.mp4"
    build_video(src, lambda k: [], n=20)
    main(["snapshot", "--source", str(src), "--out", str(tmp_path / "f.jpg"), "--warmup", "3"])
    assert cv2.imread(str(tmp_path / "f.jpg")).shape[:2] == (480, 640)
    assert "main.py polygon" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["snapshot", "--source", str(tmp_path / "tidak_ada.mp4"), "--out", str(tmp_path / "x.jpg")])
    assert "Galat" in capsys.readouterr().err


# ------------------------------------------------------------------ cabang live (thread + jam dinding)
class PacedCap:
    """Membungkus video sebagai 'kamera' berlaju tetap, agar cabang live (thread pembaca + jam dinding) terpakai."""

    def __init__(self, path, fps):
        self.cap, self.dt = cv2.VideoCapture(str(path)), 1.0 / fps

    def read(self):
        time.sleep(self.dt)
        return self.cap.read()

    def isOpened(self):
        return self.cap.isOpened()

    def get(self, prop):
        return self.cap.get(prop)

    def release(self):
        self.cap.release()


def test_run_live_threaded_wallclock_branch(tmp_path, monkeypatch):
    src = tmp_path / "cam.mp4"
    dets_all = build_video(src, lambda k: [PARKED_CAR], n=100)
    monkeypatch.setattr("trotoar.pipeline.is_live_source", lambda s: True)
    monkeypatch.setattr("trotoar.pipeline.open_capture", lambda s: PacedCap(s, 20))
    cfg = Config(min_dwell_s=2.0, stationary_window_s=1.0)
    an = Analyzer(cfg, FakeSegmenter(lambda n, img: dets_all[min(n, len(dets_all) - 1)]), "classical")
    s = an.run_live(src, tmp_path / "live", show=False, verbose=False)
    assert [a["track_id"] for a in s["alert"]] == [1]
    assert 2.0 <= s["alert"][0]["t_s"] <= 5.5            # waktu alert mengikuti jam dinding, bukan nomor frame
    assert s["durasi_s"] < 15

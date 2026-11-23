"""Pipeline utama: gambar tunggal dan video."""
from __future__ import annotations

import csv
import json
import math
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from .config import Config
from .decision import STATUS_CONFIRMED, VehicleResult, decide_frame
from .media import (LatestFrameReader, have_ffmpeg, is_live_source, open_capture, parse_source, to_h264)
from .sidewalk import segment_sidewalk_classical, sidewalk_from_polygon
from .temporal import CameraMotion, DwellTracker, shift_mask
from .vehicles import Detections, union_masks
from .visualize import draw_frame, draw_text

SIDEWALK_MODES = ("auto", "classical", "model", "polygon")

CSV_FIELDS = ["frame", "t_s", "track_id", "kelas", "conf", "contact_ratio", "ring_ratio", "overlap_ratio",
               "di_trotoar", "ada_pengendara", "status_frame", "status", "dwell_s", "x1", "y1", "x2", "y2"]


@dataclass
class FrameAnalysis:
    dets: Detections
    sidewalk: np.ndarray
    results: list
    exclude: np.ndarray
    info: dict


def result_row(r: VehicleResult, frame: int = 0, t: float = 0.0) -> dict:
    x1, y1, x2, y2 = r.obj.box
    nan = lambda v: "" if (isinstance(v, float) and math.isnan(v)) else round(v, 4)
    return {"frame": frame, "t_s": round(t, 3), "track_id": "" if r.track_id is None else r.track_id,
            "kelas": r.obj.label, "conf": round(r.obj.conf, 3), "contact_ratio": nan(r.contact_ratio),
            "ring_ratio": nan(r.ring_ratio), "overlap_ratio": nan(r.overlap_ratio),
            "di_trotoar": int(r.on_sidewalk), "ada_pengendara": "" if r.has_rider is None else int(r.has_rider),
            "status_frame": r.status_frame, "status": r.status, "dwell_s": round(r.dwell_s, 2),
            "x1": round(x1), "y1": round(y1), "x2": round(x2), "y2": round(y2)}


class StreamState:
    """Keadaan yang dibawa antar-frame pada video/live: tracker durasi, gerak kamera, cache mask trotoar."""

    def __init__(self, cfg: Config):
        self.tracker = DwellTracker(cfg)
        self.cam = CameraMotion(homography=cfg.camera_homography) if cfg.compensate_camera else None
        self.sw_ref = None
        self.sw_ref_cam = np.zeros(2)
        self.n = 0                       # jumlah frame yang sudah diproses
        self.force_refresh = False       # True -> hitung ulang mask trotoar pada frame berikutnya


class AlertSink:
    """ALERT satu kali per kendaraan (track ID): gambar bukti, baris CSV, dan (opsional) bunyi beep.

    Dipakai bersama oleh ``Analyzer.run_live`` (jendela OpenCV) dan dasbor web.
    """

    def __init__(self, out_dir, beep: bool = False, verbose: bool = True):
        self.out_dir = Path(out_dir)
        (self.out_dir / "alerts").mkdir(parents=True, exist_ok=True)
        self.csv_path = self.out_dir / "kejadian.csv"
        self.beep, self.verbose = beep, verbose
        self.events: dict[int, dict] = {}

    def reset(self) -> None:
        """Mulai sesi baru: ID tracker diulang dari awal, jadi kendaraan boleh memicu alert lagi."""
        self.events.clear()

    def submit(self, results: list, t: float, make_vis) -> list[dict]:
        """Catat alert baru dari ``results``. ``make_vis()`` -> gambar beranotasi, hanya dipanggil bila ada alert baru."""
        new = []
        for r in results:
            if r.status != STATUS_CONFIRMED or r.track_id is None or r.track_id in self.events:
                continue
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = self.out_dir / "alerts" / f"alert_{stamp}_id{r.track_id}.jpg"
            cv2.imwrite(str(path), make_vis(), [cv2.IMWRITE_JPEG_QUALITY, 92])
            ev = {"waktu": datetime.now().isoformat(timespec="seconds"), "t_s": round(t, 1),
                  "track_id": r.track_id, "kelas": r.obj.label, "dwell_s": round(r.dwell_s, 1), "gambar": str(path)}
            self.events[r.track_id] = ev
            write_header = not self.csv_path.exists()
            with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
                wr = csv.DictWriter(f, fieldnames=list(ev))
                if write_header:
                    wr.writeheader()
                wr.writerow(ev)
            if self.verbose:
                print(f"[ALERT {ev['waktu']}] {r.obj.label} #{r.track_id} parkir di trotoar "
                      f">= {r.dwell_s:.0f} s -> {path}", file=sys.stderr)
            if self.beep:
                _beep()
            new.append(ev)
        return new


class Analyzer:
    """Menggabungkan segmenter kendaraan, sumber mask trotoar, geometri, dan logika temporal.

    ``sidewalk_mode``:
        auto      -> model trotoar bila tersedia dan menemukan trotoar, selain itu klasik
        classical -> segmentasi klasik berbasis biji
        model     -> wajib dari model (sidewalk_weights atau kelas 'sidewalk' pada model utama)
        polygon   -> poligon manual (``polygon`` = daftar poligon ternormalisasi)
    """

    def __init__(self, cfg: Config, segmenter, sidewalk_mode: str = "auto", polygon=None, sidewalk_model=None):
        if sidewalk_mode not in SIDEWALK_MODES:
            raise ValueError(f"sidewalk_mode harus salah satu dari {SIDEWALK_MODES}")
        if sidewalk_mode == "polygon" and not polygon:
            raise ValueError("Mode 'polygon' membutuhkan poligon (--polygon file.json)")
        self.cfg, self.seg, self.mode = cfg, segmenter, sidewalk_mode
        self.polygon, self.sw_model = polygon, sidewalk_model
        self._base_mode = sidewalk_mode if sidewalk_mode != "polygon" else "auto"

    def set_polygon(self, polygon) -> None:
        """Pasang poligon trotoar manual (daftar poligon ternormalisasi); ``None`` = kembali ke mode sebelumnya.

        Dipakai dasbor web saat pengguna menggambar trotoar. Pemanggil perlu menyetel
        ``StreamState.force_refresh = True`` agar mask cadangan langsung dihitung ulang.
        """
        if polygon:
            self.mode, self.polygon = "polygon", polygon
        elif self.mode == "polygon":
            self.mode, self.polygon = self._base_mode, None

    # ------------------------------------------------------------------ mask trotoar
    def compute_sidewalk(self, img, dets: Detections, exclude):
        if self.mode == "polygon":
            return sidewalk_from_polygon(img.shape, self.polygon), {}
        if self.mode in ("auto", "model"):
            m = None
            if self.sw_model is not None:
                m = self.sw_model.segment(img)
            elif dets.sidewalk is not None:
                m = dets.sidewalk
            if m is not None and (self.mode == "model" or m.any()):
                return m, {}
            if self.mode == "model":
                raise RuntimeError("Mode 'model' tetapi tidak ada model trotoar (--sidewalk-weights) "
                                   "atau kelas 'sidewalk' pada model utama.")
        return segment_sidewalk_classical(img, self.cfg, exclude)

    # ------------------------------------------------------------------ gambar
    def analyze_image(self, img, sidewalk_override: np.ndarray | None = None) -> FrameAnalysis:
        dets = self.seg.detect(img, track=False)
        exclude = union_masks(dets, img.shape)
        if sidewalk_override is not None:
            sw, info = sidewalk_override.astype(bool), {"oracle": True}
        else:
            sw, info = self.compute_sidewalk(img, dets, exclude)
        results = decide_frame(dets, sw, self.cfg, img.shape)
        return FrameAnalysis(dets, sw, results, exclude, info)

    # ------------------------------------------------------------------ satu langkah aliran (video & live)
    def step(self, state: "StreamState", frame, t: float):
        """Proses satu frame pada aliran: deteksi+tracking, kompensasi kamera, mask trotoar, keputusan, durasi diam."""
        cfg = self.cfg
        dets = self.seg.detect(frame, track=True)
        exclude = union_masks(dets, frame.shape)
        cam_off = state.cam.update(frame, exclude, region=state.sw_ref) if state.cam is not None else np.zeros(2)
        cam_pose = state.cam.H if (state.cam is not None and cfg.camera_homography) else cam_off

        refresh = (state.sw_ref is None or state.force_refresh
                   or (self.mode != "polygon" and state.n % max(1, cfg.sidewalk_refresh) == 0))
        if refresh:
            state.sw_ref, _ = self.compute_sidewalk(frame, dets, exclude)
            state.sw_ref_cam = cam_off.copy()
            state.force_refresh = False
        sidewalk = shift_mask(state.sw_ref, cam_off - state.sw_ref_cam)

        results = decide_frame(dets, sidewalk, cfg, frame.shape)
        state.tracker.update(t, results, cam_pose)
        state.n += 1
        return sidewalk, results

    # ------------------------------------------------------------------ video
    def run_video(self, src, out_video, csv_path=None, summary_path=None, h264: bool = True,
                  max_frames: int | None = None, verbose: bool = True) -> dict:
        cfg = self.cfg
        cap = cv2.VideoCapture(str(src))
        if not cap.isOpened():
            raise RuntimeError(f"Tidak bisa membuka video: {src}")
        fps = cap.get(cv2.CAP_PROP_FPS)
        if not fps or fps <= 1 or fps > 240:
            fps = 30.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        stride = max(1, int(cfg.video_stride))

        out_video = Path(out_video)
        out_video.parent.mkdir(parents=True, exist_ok=True)
        raw_path = out_video.with_suffix(".raw.mp4") if (h264 and have_ffmpeg()) else out_video
        writer = None

        self.seg.reset_tracking()
        state = StreamState(cfg)
        rows, events = [], {}
        idx, n_proc = -1, 0

        while True:
            ok, frame = cap.read()
            if not ok:
                break
            idx += 1
            if idx % stride:
                continue
            if max_frames and n_proc >= max_frames:
                break
            t = idx / fps
            sidewalk, results = self.step(state, frame, t)

            for r in results:
                rows.append(result_row(r, idx, t))
                if r.status == STATUS_CONFIRMED and r.track_id is not None:
                    ev = events.setdefault(r.track_id, {"track_id": r.track_id, "kelas": r.obj.label,
                                                        "mulai_s": round(t - r.dwell_s, 2), "terakhir_s": t,
                                                        "durasi_s": r.dwell_s})
                    ev["terakhir_s"], ev["durasi_s"] = round(t, 2), round(r.dwell_s, 2)

            vis = draw_frame(frame, sidewalk, results)
            if writer is None:
                hh, ww = vis.shape[:2]
                writer = cv2.VideoWriter(str(raw_path), cv2.VideoWriter_fourcc(*"mp4v"), fps / stride, (ww, hh))
                if not writer.isOpened():
                    raise RuntimeError("VideoWriter gagal dibuka (codec mp4v tidak tersedia?)")
            writer.write(vis)
            n_proc += 1
            if verbose and n_proc % 50 == 0:
                print(f"  frame {idx + 1}/{total or '?'}  parkir terkonfirmasi: {len(events)}", file=sys.stderr)

        cap.release()
        if writer is not None:
            writer.release()
        if raw_path != out_video and raw_path.exists():
            to_h264(raw_path, out_video)
            raw_path.unlink()

        if csv_path:
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
                w.writeheader()
                w.writerows(rows)
        summary = {"video": str(src), "fps": fps, "frame_diproses": n_proc, "stride": stride,
                   "parkir_terkonfirmasi": sorted(events.values(), key=lambda e: e["mulai_s"]),
                   "config": cfg.to_dict()}
        if summary_path:
            Path(summary_path).write_text(json.dumps(summary, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        return summary

    # ------------------------------------------------------------------ live (ala CCTV)
    def run_live(self, source, out_dir, show: bool = True, record: bool = False, beep: bool = False,
                 max_frames: int | None = None, window: str = "Sidewalk Sentinel", verbose: bool = True) -> dict:
        """Pantau kamera/aliran secara real-time.

        ``source``: indeks webcam (0, 1, ...), URL kamera IP (rtsp://, http://...), atau path berkas video.
        Saat sebuah kendaraan terkonfirmasi parkir di trotoar (diam >= ``min_dwell_s``), program membuat ALERT
        satu kali per kendaraan: tulisan merah di layar, gambar bukti di ``out_dir/alerts/``, baris di
        ``out_dir/kejadian.csv``, dan (opsional) bunyi beep. Waktu pada sumber langsung memakai jam dinding,
        sehingga durasi diam tetap benar walau pemrosesan lebih lambat daripada kamera.
        Tombol: [q]/[Esc] keluar, [s] simpan gambar, [r] hitung ulang mask trotoar.
        """
        cfg = self.cfg
        source = parse_source(source)
        live = is_live_source(source)
        out_dir = Path(out_dir)
        sink = AlertSink(out_dir, beep=beep, verbose=verbose)

        cap = open_capture(source)
        if not cap.isOpened():
            raise RuntimeError(f"Tidak bisa membuka sumber: {source!r}. Webcam: coba indeks lain (--source 1). "
                               "Kamera IP: cek URL, jaringan Wi-Fi yang sama, dan aplikasi kameranya sedang aktif.")
        reader = LatestFrameReader(cap) if live else None
        file_fps = 30.0 if live else (cap.get(cv2.CAP_PROP_FPS) or 30.0)

        self.seg.reset_tracking()
        state = StreamState(cfg)
        alerts = sink.events
        writer, raw_path, rec_path = None, out_dir / "rekaman.raw.mp4", out_dir / "rekaman.mp4"
        t0 = time.monotonic()
        last_tick, fps_ema, idx, n = t0, 0.0, -1, 0

        try:
            while True:
                ok, frame = reader.read() if live else cap.read()
                if not ok:
                    if verbose:
                        print("Sumber berhenti mengirim gambar." if live else "Video selesai.", file=sys.stderr)
                    break
                idx += 1
                if max_frames and n >= max_frames:
                    break
                t = (time.monotonic() - t0) if live else idx / file_fps
                sidewalk, results = self.step(state, frame, t)

                now = time.monotonic()
                fps_ema = (1.0 / max(1e-6, now - last_tick)) if n == 0 else 0.9 * fps_ema + 0.1 / max(1e-6, now - last_tick)
                last_tick = now

                vis = draw_frame(frame, sidewalk, results)
                h, w = vis.shape[:2]
                scale = max(0.5, w / 1400.0)
                active = [r for r in results if r.status == STATUS_CONFIRMED]
                draw_text(vis, f"{fps_ema:4.1f} fps  |  {datetime.now():%H:%M:%S}", (8, h - 10), scale * 0.8, (255, 255, 255), 1)
                if active:
                    draw_text(vis, f"ALERT: {len(active)} kendaraan parkir di trotoar", (8, int(60 * scale)), scale * 1.1,
                              (0, 0, 255), max(2, int(scale * 2)))

                sink.submit(results, t, lambda: vis)

                if record and writer is None and (n >= 10 or not live):
                    rec_fps = file_fps if not live else max(1.0, n / max(1e-6, now - t0))
                    writer = cv2.VideoWriter(str(raw_path), cv2.VideoWriter_fourcc(*"mp4v"), rec_fps, (w, h))
                if writer is not None:
                    writer.write(vis)

                if show:
                    try:
                        cv2.imshow(window, vis)
                        key = cv2.waitKey(1) & 0xFF
                        closed = cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1
                    except cv2.error as e:
                        raise RuntimeError("OpenCV Anda tidak punya dukungan jendela (paket 'headless'?). "
                                           "Jalankan dengan --no-window, atau pasang opencv-python biasa.") from e
                    if key in (ord("q"), 27) or closed:
                        break
                    if key == ord("s"):
                        p = out_dir / f"snapshot_{datetime.now():%Y%m%d_%H%M%S}.jpg"
                        cv2.imwrite(str(p), vis)
                        print(f"Gambar disimpan: {p}", file=sys.stderr)
                    if key == ord("r"):
                        state.force_refresh = True
                n += 1
                if verbose and n % 60 == 0:
                    print(f"  {n} frame | {fps_ema:.1f} fps | alert: {len(alerts)}", file=sys.stderr)
        except KeyboardInterrupt:
            if verbose:
                print("Dihentikan (Ctrl+C).", file=sys.stderr)
        finally:
            if reader is not None:
                reader.stop()
            else:
                cap.release()
            if writer is not None:
                writer.release()
            if show:
                try:
                    cv2.destroyAllWindows()
                except cv2.error:                              # pragma: no cover
                    pass

        if writer is not None and raw_path.exists():
            if have_ffmpeg():
                to_h264(raw_path, rec_path)
                raw_path.unlink()
            else:
                raw_path.replace(rec_path)
        summary = {"sumber": str(source), "frame_diproses": n, "durasi_s": round(time.monotonic() - t0, 1),
                   "alert": list(alerts.values()), "config": cfg.to_dict()}
        (out_dir / "ringkasan_live.json").write_text(
            json.dumps(summary, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        return summary


def _beep() -> None:
    """Bunyi peringatan: beep Windows bila tersedia, selain itu bel terminal."""
    try:
        import winsound
        winsound.Beep(1200, 350)
    except Exception:
        print("\a", end="", flush=True)

"""Antarmuka baris perintah.  Pakai:  python main.py <perintah> --help"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import cv2
import numpy as np

from . import media
from .config import Config, apply_overrides
from .decision import decide_frame, image_level_positive
from .metrics import cohen_kappa, confusion, eval_seg_dirs, parse_label, prf  # noqa: F401
from .pipeline import CSV_FIELDS, Analyzer, result_row
from .sidewalk import load_polygon_file, save_polygon_file
from .visualize import draw_frame


# ----------------------------------------------------------------------------- util
def _add_common(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--weights", help="model YOLO-seg kendaraan+orang (default yolo11n-seg.pt)")
    sp.add_argument("--sidewalk-weights", help="model YOLO-seg trotoar hasil fine-tune (opsional)")
    sp.add_argument("--sidewalk", choices=["auto", "classical", "model", "polygon"], default="auto",
                    help="sumber mask trotoar (default: auto)")
    sp.add_argument("--polygon", help="file JSON poligon trotoar (untuk --sidewalk polygon)")
    sp.add_argument("--seed", help="kotak biji trotoar x1,y1,x2,y2 (pecahan 0-1), mode klasik")
    sp.add_argument("--tau", type=float, help="set tau_contact dan tau_ring sekaligus")
    sp.add_argument("--device", help="cpu / 0 / mps")
    sp.add_argument("--set", action="append", default=[], metavar="K=V", help="ubah parameter Config, bisa berulang")


def _make_cfg(args) -> Config:
    cfg = Config()
    if getattr(args, "weights", None):
        cfg.weights = args.weights
    if getattr(args, "sidewalk_weights", None):
        cfg.sidewalk_weights = args.sidewalk_weights
    if getattr(args, "device", None):
        cfg.device = args.device
    if getattr(args, "seed", None):
        cfg.seed_box = tuple(float(v) for v in args.seed.split(","))
        if len(cfg.seed_box) != 4:
            raise SystemExit("--seed harus 4 angka: x1,y1,x2,y2")
    if getattr(args, "tau", None) is not None:
        cfg.tau_contact = cfg.tau_ring = args.tau
    return apply_overrides(cfg, args.set)


def _make_analyzer(args, cfg: Config, segmenter_factory=None) -> Analyzer:
    if segmenter_factory is not None:
        seg = segmenter_factory(cfg)
    else:
        from .vehicles import YoloSegmenter
        seg = YoloSegmenter(cfg)
    sw_model = None
    if cfg.sidewalk_weights and args.sidewalk in ("auto", "model"):
        from .vehicles import YoloSidewalkSegmenter
        sw_model = YoloSidewalkSegmenter(cfg)
    polygon = load_polygon_file(args.polygon) if args.polygon else None
    mode = "polygon" if (args.polygon and args.sidewalk == "auto") else args.sidewalk
    return Analyzer(cfg, seg, mode, polygon, sw_model)


def _list_images(p: Path) -> list[Path]:
    if p.is_dir():
        return sorted(f for f in p.iterdir() if f.suffix.lower() in media.IMG_EXT)
    return [p]


def _write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


# ----------------------------------------------------------------------------- perintah
def cmd_prepare(args, **_):
    src, out = Path(args.src), Path(args.out)
    if src.is_dir():
        done = media.normalize_dir(src, out, args.max_side_img, args.fps, args.max_side_vid)
    elif src.suffix.lower() in media.VID_EXT:
        done = [media.normalize_video(src, out / (src.stem + ".mp4"), args.fps, args.max_side_vid)]
    else:
        done = [media.normalize_image(src, out, args.max_side_img)]
    for d in done:
        print("OK", d)
    print(f"{len(done)} berkas dinormalisasi -> {out}")


def cmd_frames(args, **_):
    n = media.extract_frames(args.video, args.out, args.fps)
    print(f"{n} frame disimpan di {args.out}")


def cmd_image(args, segmenter_factory=None):
    cfg = _make_cfg(args)
    an = _make_analyzer(args, cfg, segmenter_factory)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for p in _list_images(Path(args.input)):
        img = media.load_image(p)
        fa = an.analyze_image(img)
        vis = draw_frame(img, fa.sidewalk, fa.results, debug=args.debug)
        cv2.imwrite(str(out / f"{p.stem}_hasil.jpg"), vis, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if args.save_masks:
            cv2.imwrite(str(out / f"{p.stem}_sidewalk.png"), fa.sidewalk.astype(np.uint8) * 255)
        for r in fa.results:
            row = result_row(r)
            row["frame"] = p.name
            rows.append(row)
        n_park = sum(1 for r in fa.results if r.status_frame == "TERINDIKASI_PARKIR")
        warn = f"  [{fa.info['warning']}]" if "warning" in fa.info else ""
        print(f"{p.name}: {len(fa.results)} kendaraan, {n_park} terindikasi parkir di trotoar{warn}")
    _write_csv(out / "hasil_gambar.csv", rows, CSV_FIELDS)
    print(f"Hasil -> {out}")


def cmd_video(args, segmenter_factory=None):
    cfg = _make_cfg(args)
    if args.min_dwell is not None:
        cfg.min_dwell_s = args.min_dwell
    if args.stride:
        cfg.video_stride = args.stride
    if args.no_camera_comp:
        cfg.compensate_camera = False
    an = _make_analyzer(args, cfg, segmenter_factory)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = Path(args.input).stem
    s = an.run_video(args.input, out / f"{stem}_hasil.mp4", out / f"{stem}_hasil.csv", out / f"{stem}_ringkasan.json",
                     h264=not args.no_h264, max_frames=args.max_frames)
    ev = s["parkir_terkonfirmasi"]
    print(f"{s['frame_diproses']} frame diproses; parkir terkonfirmasi: {len(ev)}")
    for e in ev:
        print(f"  #{e['track_id']} {e['kelas']}: mulai {e['mulai_s']} s, durasi >= {e['durasi_s']} s")
    print(f"Hasil -> {out}")


def cmd_live(args, segmenter_factory=None):
    cfg = _make_cfg(args)
    if args.min_dwell is not None:
        cfg.min_dwell_s = args.min_dwell
    if args.no_camera_comp:
        cfg.compensate_camera = False
    an = _make_analyzer(args, cfg, segmenter_factory)
    print("Memantau kamera... tekan [q] atau [Esc] di jendela untuk berhenti ([s] simpan gambar, [r] hitung ulang trotoar)."
          if not args.no_window else "Memantau kamera tanpa jendela... tekan Ctrl+C untuk berhenti.")
    s = an.run_live(args.source, args.out, show=not args.no_window, record=args.record, beep=args.beep,
                    max_frames=args.max_frames)
    print(f"Selesai: {s['frame_diproses']} frame dalam {s['durasi_s']} detik; alert: {len(s['alert'])}")
    for e in s["alert"]:
        print(f"  [{e['waktu']}] {e['kelas']} #{e['track_id']} parkir di trotoar >= {e['dwell_s']} s -> {e['gambar']}")
    print(f"Hasil -> {args.out}")


def cmd_ui(args, segmenter_factory=None, phone=None):
    from . import webui
    cfg = _make_cfg(args)
    if args.min_dwell is not None:
        cfg.min_dwell_s = args.min_dwell
    polygon = load_polygon_file(args.polygon) if args.polygon else None
    engine = webui.Engine(cfg, lambda c: _make_analyzer(args, c, segmenter_factory), out_dir=args.out, polygon=polygon)
    webui.serve(engine, args.host, args.port, open_browser=not args.no_open, remote=args.remote, phone=phone)


def cmd_phone(args, segmenter_factory=None):
    """Dasbor untuk kamera HP: server mode remote + terowongan HTTPS + QR (tidak membuka jendela di laptop)."""
    from . import phone as phonemod
    from .webui import _phone_missing
    opts = phonemod.PhoneOptions(cloudflared=args.cloudflared, open_image=not args.no_open)
    if phonemod.find_cloudflared(args.cloudflared) is None:
        raise RuntimeError(_phone_missing(opts))             # cek sebelum memuat model yang makan waktu
    args.host, args.remote, args.no_open = "127.0.0.1", True, True
    cmd_ui(args, segmenter_factory, phone=opts)


def cmd_snapshot(args, **_):
    p = media.grab_snapshot(args.source, args.out, args.warmup)
    print(f"Gambar disimpan: {p}\nLanjut: python main.py polygon {p} --out trotoar_polygon.json")


def cmd_eval_seg(args, **_):
    rows = eval_seg_dirs(args.pred, args.gt, args.suffix)
    if not rows:
        raise SystemExit("Tidak ada pasangan mask prediksi/GT yang cocok (cek nama file & --suffix).")
    for r in rows:
        print(f"{r['gambar']:30s} IoU={r['iou']:.3f}  Dice={r['dice']:.3f}  PixAcc={r['pixel_acc']:.3f}")
    m = {k: float(np.mean([r[k] for r in rows])) for k in ("iou", "dice", "pixel_acc")}
    print(f"\nRata-rata ({len(rows)} gambar): IoU={m['iou']:.3f}  Dice={m['dice']:.3f}  PixAcc={m['pixel_acc']:.3f}")
    if args.out:
        _write_csv(Path(args.out), rows, ["gambar", "iou", "dice", "pixel_acc"])


def cmd_eval_decision(args, segmenter_factory=None):
    """Evaluasi keputusan tingkat gambar: positif = ada kendaraan terindikasi parkir di trotoar."""
    cfg = _make_cfg(args)
    an = _make_analyzer(args, cfg, segmenter_factory)
    labels = {}
    with open(args.labels, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            labels[Path(row["image"]).stem] = parse_label(row["label"])
    imgdir = Path(args.images)
    cache = []                                   # (stem, shape, dets, sidewalk)
    for p in _list_images(imgdir):
        if p.stem not in labels:
            continue
        img = media.load_image(p)
        override = None
        if args.oracle_sidewalk:
            gp = Path(args.oracle_sidewalk) / f"{p.stem}.png"
            if not gp.exists():
                print(f"[lewati] GT trotoar tidak ada: {gp}")
                continue
            override = cv2.imread(str(gp), cv2.IMREAD_GRAYSCALE) > 0
        fa = an.analyze_image(img, sidewalk_override=override)
        cache.append((p.stem, img.shape, fa.dets, fa.sidewalk))
    if not cache:
        raise SystemExit("Tidak ada gambar berlabel yang cocok dengan folder --images.")

    taus = [cfg.tau_contact] + ([float(v) for v in args.sweep_tau.split(",")] if args.sweep_tau else [])
    table = []
    for tau in dict.fromkeys(taus):
        c = Config(**{**cfg.to_dict()})
        c.tau_contact = c.tau_ring = tau
        y_true = [labels[s] for s, *_ in cache]
        y_pred = [image_level_positive(decide_frame(d, sw, c, shape)) for _, shape, d, sw in cache]
        table.append({"tau": tau, **prf(*confusion(y_true, y_pred))})
    print(f"Evaluasi tingkat gambar (n={len(cache)}){' [GT trotoar]' if args.oracle_sidewalk else ''}")
    print(f"{'tau':>5} {'TP':>4} {'FP':>4} {'FN':>4} {'TN':>4} {'Prec':>6} {'Rec':>6} {'F1':>6} {'Acc':>6}")
    for r in table:
        print(f"{r['tau']:5.2f} {r['tp']:4d} {r['fp']:4d} {r['fn']:4d} {r['tn']:4d} "
              f"{r['precision']:6.3f} {r['recall']:6.3f} {r['f1']:6.3f} {r['accuracy']:6.3f}")
    if args.out:
        _write_csv(Path(args.out), table, list(table[0].keys()))


def cmd_polygon(args, **_):
    """Gambar poligon trotoar secara interaktif di atas satu gambar/frame (butuh layar)."""
    img = media.load_image(args.image)
    h, w = img.shape[:2]
    scale = min(1.0, 1100 / max(h, w))
    disp = cv2.resize(img, None, fx=scale, fy=scale) if scale < 1 else img.copy()
    polys, cur = [], []

    def redraw():
        v = disp.copy()
        for poly in polys + [cur]:
            if len(poly) > 1:
                cv2.polylines(v, [np.array(poly, np.int32)], poly is not cur, (255, 170, 0), 2)
            for q in poly:
                cv2.circle(v, tuple(q), 3, (0, 0, 255), -1)
        cv2.imshow("Poligon trotoar", v)

    def on_mouse(ev, x, y, *_):
        if ev == cv2.EVENT_LBUTTONDOWN:
            cur.append([x, y])
            redraw()

    cv2.namedWindow("Poligon trotoar")
    cv2.setMouseCallback("Poligon trotoar", on_mouse)
    print("Klik titik-titik tepi trotoar. [n]=poligon baru  [u]=urungkan titik  [Enter/s]=simpan  [Esc]=batal")
    redraw()
    while True:
        k = cv2.waitKey(30) & 0xFF
        if k == 27:
            cv2.destroyAllWindows()
            return print("Dibatalkan.")
        if k == ord("u") and cur:
            cur.pop()
            redraw()
        if k == ord("n") and len(cur) >= 3:
            polys.append(cur[:])
            cur.clear()
            redraw()
        if k in (13, ord("s")):
            if len(cur) >= 3:
                polys.append(cur[:])
            break
    cv2.destroyAllWindows()
    norm = [[[x / disp.shape[1], y / disp.shape[0]] for x, y in poly] for poly in polys]
    save_polygon_file(args.out, norm)
    print(f"{len(norm)} poligon disimpan -> {args.out}")


def cmd_train(args, **_):
    try:
        from ultralytics import YOLO
    except ImportError:
        raise SystemExit("Butuh ultralytics: pip install ultralytics")
    model = YOLO(args.model)
    model.train(data=args.data, epochs=args.epochs, imgsz=args.imgsz, batch=args.batch,
                project=args.project, name=args.name, device=args.device or None, patience=args.patience)
    print(f"Selesai. Bobot terbaik: {Path(args.project) / args.name / 'weights' / 'best.pt'}")


def cmd_data(args, **_):
    from . import datasets as ds
    try:
        if args.data_cmd == "masks":
            opts = dict(ds.PRESETS.get(args.preset, {}))
            for k in ("image_dir", "mask_dir", "image_suffix", "mask_suffix", "label_id"):
                v = getattr(args, k)
                if v is not None:
                    opts[k] = v
            missing = [k for k in ("image_dir", "mask_dir", "image_suffix", "mask_suffix") if k not in opts]
            if missing:
                raise SystemExit("Pilih --preset cityscapes, atau isi " + ", ".join("--" + k.replace("_", "-") for k in missing) + ".")
            rgb = tuple(int(x) for x in args.label_rgb.split(",")) if args.label_rgb else None
            if rgb is not None and len(rgb) != 3:
                raise SystemExit("--label-rgb harus berbentuk R,G,B, mis. 244,35,232.")
            if rgb is not None:
                opts.pop("label_id", None)
            st = ds.masks_to_yolo(args.src, args.out, splits=tuple(args.splits.split(",")), max_side=args.max_side,
                                  class_name=args.class_name, limit=args.limit, label_rgb=rgb, **opts)
            print(f"Selesai: {st['gambar']} gambar ({st['dengan_kelas']} berisi {args.class_name}, {st['tanpa_kelas']} tanpa, "
                  f"{st['poligon']} poligon) per split {st['per_split']}.")
            if st["tanpa_gambar"] or st["mask_rusak"]:
                print(f"Dilewati: {st['tanpa_gambar']} mask tanpa gambar pasangannya, {st['mask_rusak']} berkas tak terbaca.")
            print(f"Latih dengan:  python main.py train --data {Path(args.out) / 'data.yaml'} --epochs 100")
        else:
            cmap = dict(m.split("=", 1) for m in args.map or [])
            st = ds.merge_yolo(args.src, args.out, class_map=cmap, names=args.names, keep_unmapped=args.keep_unmapped)
            print(f"Selesai: gambar per split {st['gambar']}; instans per kelas {st['instans']}; dilewati {st['dilewati']} (semua labelnya terbuang).")
    except (ValueError, FileNotFoundError) as e:
        raise SystemExit(str(e))


# ----------------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="trotoar", description="Deteksi kendaraan parkir di trotoar (segmentasi citra)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("prepare", help="normalisasi media HP (HEIC->JPG, EXIF, video->H.264 fps konstan)")
    sp.add_argument("src"); sp.add_argument("--out", default="data_bersih")
    sp.add_argument("--max-side-img", type=int); sp.add_argument("--max-side-vid", type=int, default=1280)
    sp.add_argument("--fps", type=float, default=30.0)
    sp.set_defaults(func=cmd_prepare)

    sp = sub.add_parser("frames", help="ekstrak frame video untuk anotasi")
    sp.add_argument("video"); sp.add_argument("--out", default="frames"); sp.add_argument("--fps", type=float, default=1.0)
    sp.set_defaults(func=cmd_frames)

    sp = sub.add_parser("image", help="analisis foto (berkas atau folder)")
    sp.add_argument("input"); sp.add_argument("--out", default="hasil")
    sp.add_argument("--save-masks", action="store_true", help="simpan mask trotoar PNG (untuk eval-seg)")
    sp.add_argument("--debug", action="store_true", help="tampilkan rasio kontak/cincin pada gambar")
    _add_common(sp); sp.set_defaults(func=cmd_image)

    sp = sub.add_parser("video", help="analisis video (tracking + durasi diam)")
    sp.add_argument("input"); sp.add_argument("--out", default="hasil")
    sp.add_argument("--min-dwell", type=float, help="detik diam minimal untuk 'parkir terkonfirmasi'")
    sp.add_argument("--stride", type=int, help="proses tiap N frame")
    sp.add_argument("--no-camera-comp", action="store_true", help="matikan kompensasi gerak kamera")
    sp.add_argument("--no-h264", action="store_true"); sp.add_argument("--max-frames", type=int)
    _add_common(sp); sp.set_defaults(func=cmd_video)

    sp = sub.add_parser("live", help="pantau kamera secara real-time (webcam / kamera IP HP / CCTV RTSP)")
    sp.add_argument("--source", default="0",
                    help="0 = webcam bawaan; 1,2.. = kamera lain; URL kamera IP (http://IP:8080/video, rtsp://...); atau path video")
    sp.add_argument("--out", default="live_hasil")
    sp.add_argument("--min-dwell", type=float, help="detik diam minimal sebelum ALERT (default 10)")
    sp.add_argument("--no-window", action="store_true", help="tanpa jendela tampilan (mis. di server)")
    sp.add_argument("--record", action="store_true", help="rekam tampilan beranotasi ke live_hasil/rekaman.mp4")
    sp.add_argument("--beep", action="store_true", help="bunyi beep saat ALERT")
    sp.add_argument("--no-camera-comp", action="store_true", help="matikan kompensasi gerak kamera (kamera tetap/tripod)")
    sp.add_argument("--max-frames", type=int, help="berhenti setelah N frame (untuk uji coba)")
    _add_common(sp); sp.set_defaults(func=cmd_live)

    sp = sub.add_parser("ui", help="dasbor web: kamera langsung + deteksi + panel kejadian (disarankan)")
    sp.add_argument("--host", default="127.0.0.1",
                    help="alamat server (default 127.0.0.1 = hanya laptop ini; 0.0.0.0 = bisa dibuka dari HP di Wi-Fi yang sama)")
    sp.add_argument("--port", type=int, default=8765)
    sp.add_argument("--no-open", action="store_true", help="jangan buka jendela otomatis")
    sp.add_argument("--remote", action="store_true",
                    help="untuk dibuka lewat terowongan HTTPS (kamera HP): wajib kunci akses, sumber di sisi server dimatikan")
    sp.add_argument("--out", default="live_hasil", help="folder foto bukti, kejadian.csv, dan poligon trotoar")
    sp.add_argument("--min-dwell", type=float, help="detik diam minimal sebelum ALERT (default 10; bisa diubah di UI)")
    _add_common(sp); sp.set_defaults(func=cmd_ui)

    sp = sub.add_parser("phone", help="pakai kamera HP (iPhone/Android): tautan HTTPS + kode QR untuk dipindai")
    sp.add_argument("--port", type=int, default=8765)
    sp.add_argument("--no-open", action="store_true", help="jangan membuka gambar QR otomatis di laptop")
    sp.add_argument("--cloudflared", help="path cloudflared bila tidak ada di PATH")
    sp.add_argument("--out", default="live_hasil", help="folder foto bukti, kejadian.csv, dan qr_iphone.png")
    sp.add_argument("--min-dwell", type=float, help="detik diam minimal sebelum ALERT (default 10)")
    _add_common(sp); sp.set_defaults(func=cmd_phone)

    sp = sub.add_parser("snapshot", help="ambil satu gambar dari kamera (untuk menggambar poligon trotoar)")
    sp.add_argument("--source", default="0"); sp.add_argument("--out", default="frame.jpg")
    sp.add_argument("--warmup", type=int, default=15, help="buang N frame pertama agar eksposur stabil")
    sp.set_defaults(func=cmd_snapshot)

    sp = sub.add_parser("eval-seg", help="IoU/Dice mask trotoar prediksi vs ground truth")
    sp.add_argument("--pred", required=True); sp.add_argument("--gt", required=True)
    sp.add_argument("--suffix", default="_sidewalk"); sp.add_argument("--out")
    sp.set_defaults(func=cmd_eval_seg)

    sp = sub.add_parser("eval-decision", help="evaluasi keputusan tingkat gambar + analisis sensitivitas ambang")
    sp.add_argument("--images", required=True); sp.add_argument("--labels", required=True, help="CSV: image,label")
    sp.add_argument("--sweep-tau", help="mis. 0.3,0.4,0.5,0.6,0.7")
    sp.add_argument("--oracle-sidewalk", help="folder mask GT trotoar (<stem>.png) untuk memisahkan galat segmentasi")
    sp.add_argument("--out")
    _add_common(sp); sp.set_defaults(func=cmd_eval_decision)

    sp = sub.add_parser("polygon", help="gambar poligon trotoar interaktif (untuk video tripod)")
    sp.add_argument("image"); sp.add_argument("--out", default="trotoar_polygon.json")
    sp.set_defaults(func=cmd_polygon)

    sp = sub.add_parser("train", help="fine-tune YOLO-seg pada dataset trotoar Anda")
    sp.add_argument("--data", required=True, help="data.yaml (format YOLO segmentation)")
    sp.add_argument("--model", default="yolo11n-seg.pt"); sp.add_argument("--epochs", type=int, default=100)
    sp.add_argument("--imgsz", type=int, default=640); sp.add_argument("--batch", type=int, default=8)
    sp.add_argument("--patience", type=int, default=30)
    sp.add_argument("--project", default="runs"); sp.add_argument("--name", default="trotoar")
    sp.add_argument("--device")
    sp.set_defaults(func=cmd_train)

    sp = sub.add_parser("data", help="siapkan dataset publik (Cityscapes/IDD/Mapillary/Roboflow) untuk latih YOLO")
    dsub = sp.add_subparsers(dest="data_cmd", required=True)
    m = dsub.add_parser("masks", help="mask piksel -> YOLO-seg satu kelas (mis. sidewalk)")
    m.add_argument("--src", required=True, help="folder dataset hasil unduhan/ekstrak")
    m.add_argument("--out", required=True, help="folder keluaran (berisi data.yaml)")
    m.add_argument("--preset", choices=["cityscapes"], help="tata letak dan id kelas bawaan")
    m.add_argument("--image-dir", help="folder gambar; boleh memuat {split}, mis. {split}/images")
    m.add_argument("--mask-dir", help="folder mask; boleh memuat {split}, mis. {split}/labels")
    m.add_argument("--image-suffix"); m.add_argument("--mask-suffix")
    m.add_argument("--label-id", type=int, help="id kelas pada mask 1 kanal (Cityscapes sidewalk = 8)")
    m.add_argument("--label-rgb", help="warna kelas pada mask berwarna (RGB penuh), R,G,B")
    m.add_argument("--splits", default="train,val", help="nama folder split pada dataset (Mapillary: training,validation)"); m.add_argument("--class-name", default="sidewalk")
    m.add_argument("--max-side", type=int, default=1280, help="sisi terpanjang gambar keluaran (hemat ruang)")
    m.add_argument("--limit", type=int, help="batasi gambar per split (untuk uji coba cepat)")
    g = dsub.add_parser("merge", help="gabung set berformat YOLO (ekspor Roboflow) dan ubah nama kelas")
    g.add_argument("--src", action="append", required=True, help="folder ekspor YOLO; boleh diulang")
    g.add_argument("--out", required=True)
    g.add_argument("--map", action="append", metavar="LAMA=BARU", help="ubah nama kelas; yang tak dipetakan dibuang; boleh diulang")
    g.add_argument("--names", nargs="+", help="urutan nama kelas akhir")
    g.add_argument("--keep-unmapped", action="store_true", help="pertahankan kelas yang tak ada di --map")
    sp.set_defaults(func=cmd_data)
    return p


def main(argv=None, segmenter_factory=None) -> None:
    if argv is None and len(sys.argv) <= 1:
        argv = ["ui"]                      # dijalankan tanpa argumen (mis. tombol Run di VS Code): buka dasbor
    args = build_parser().parse_args(argv)
    try:
        args.func(args, segmenter_factory=segmenter_factory)
    except (RuntimeError, ValueError, FileNotFoundError) as e:
        print(f"Galat: {e}", file=sys.stderr)
        raise SystemExit(1)

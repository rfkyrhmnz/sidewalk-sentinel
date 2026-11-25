#!/usr/bin/env python3
"""Sidewalk Sentinel: deteksi kendaraan parkir/naik trotoar dengan pengolahan citra klasik.

    python main.py                      # jalankan aplikasi web di http://localhost:8000
    python main.py serve --lan          # buka juga dari HP di Wi-Fi yang sama (tautan berkunci)
    python main.py serve --https        # sama, plus HTTPS: perlu agar kamera HP bisa jadi CCTV
    python main.py --cam "Depan=rtsp://user:sandi@192.168.1.20/stream1"   # kamera CCTV, dipantau terus
    python main.py analyze video.mp4    # analisis lewat baris perintah
    python main.py demo                 # buat video demo sintetis
"""
from __future__ import annotations

import argparse
import sys
import threading
import webbrowser
from pathlib import Path

DEFAULT_ROI = "0,0.62;1,0.62;1,0.98;0,0.98"


def parse_sets(items: list[str]) -> dict:
    out = {}
    for it in items or []:
        if "=" not in it:
            raise SystemExit(f"--set harus berbentuk kunci=nilai, bukan: {it}")
        k, v = it.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def cmd_serve(a) -> int:
    from sidewalk_sentinel.server import serve
    if not a.no_open and not (a.lan or a.https):
        threading.Timer(1.2, lambda: webbrowser.open(f"http://localhost:{a.port}/")).start()
    serve(host=a.host, port=a.port, data_dir=a.data, lan=a.lan, cams=a.cam, retention_days=a.retention_days,
          https=a.https)
    return 0


def cmd_analyze(a) -> int:
    from sidewalk_sentinel import Config
    from sidewalk_sentinel.analysis import parse_roi, run_analysis

    cfg = Config.from_overrides({"park_seconds": a.park_seconds, **parse_sets(a.set)})
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    def progress(frac, t, counts):
        print(f"\r  {frac * 100:5.1f}%  t={t:6.1f}s  parkir={counts['parkir']} naik={counts['naik_trotoar']}", end="", flush=True)

    res = run_analysis(a.video, parse_roi(a.roi), cfg, ref_time=a.ref_time, out_dir=out,
                       save_video=not a.no_video, progress_cb=progress)
    print()
    for e in res["events"]:
        print(f"  #{e['no']} {e['label']:20s} kendaraan {e['track_id']}  t={e['t']:.1f}s  ({e['reason']})")
    s, p = res["summary"], res["processing"]
    print(f"\nRingkasan: parkir={s['parkir']}  naik_trotoar={s['naik_trotoar']}  kendaraan_unik={s['kendaraan_unik']}")
    print(f"Diproses {p['frames']} frame dalam {p['seconds']} detik. Keluaran: {out.resolve()}")
    return 0


def cmd_demo(a) -> int:
    from sidewalk_sentinel.synthetic import default_scene, write_video
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    path = write_video(default_scene(shadow=a.shadow, gradient=a.gradient), str(out / "demo.mp4"))
    print(f"Video demo: {path}")
    print("Kebenaran: 1 parkir + 2 naik trotoar. Area trotoar: --roi \"" + DEFAULT_ROI + "\"")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sidewalk-sentinel", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("serve", help="jalankan aplikasi web (bawaan)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--lan", action="store_true", help="dengarkan di semua antarmuka, tautan berkunci untuk HP")
    s.add_argument("--https", action="store_true",
                   help="layani HTTPS (sertifikat dibuat sendiri) agar kamera HP bisa dipakai; otomatis ikut --lan")
    s.add_argument("--data", default="data", help="folder unggahan dan hasil (aman dihapus)")
    s.add_argument("--no-open", action="store_true", help="jangan buka browser otomatis")
    s.add_argument("--cam", action="append", metavar="NAMA=SUMBER",
                   help="daftarkan kamera: rtsp://..., http://..., device:0 (webcam USB), atau jalur berkas rekaman; boleh berulang")
    s.add_argument("--retention-days", type=int, default=30, help="simpan kejadian dan foto bukti selama ini (hari)")
    s.set_defaults(fn=cmd_serve)

    a = sub.add_parser("analyze", help="analisis satu video dari baris perintah")
    a.add_argument("video")
    a.add_argument("--roi", default=DEFAULT_ROI, help='area trotoar ternormalisasi: "x,y;x,y;..." atau berkas JSON')
    a.add_argument("--ref-time", type=float, default=0.0, help="detik frame trotoar kosong (referensi)")
    a.add_argument("--park-seconds", type=float, default=10.0)
    a.add_argument("--out", default="data/hasil")
    a.add_argument("--no-video", action="store_true", help="jangan buat video beranotasi")
    a.add_argument("--set", action="append", metavar="KUNCI=NILAI", help="timpa parameter Config, boleh berulang")
    a.set_defaults(fn=cmd_analyze)

    d = sub.add_parser("demo", help="buat video demo sintetis")
    d.add_argument("--out", default="data/demo")
    d.add_argument("--shadow", action="store_true")
    d.add_argument("--gradient", type=float, default=0.0)
    d.set_defaults(fn=cmd_demo)
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd is None:
        args = parser.parse_args(["serve"] + (argv if argv is not None else sys.argv[1:]))
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())

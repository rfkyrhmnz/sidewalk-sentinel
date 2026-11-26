#!/usr/bin/env python3
"""Sidewalk Sentinel: pemantau trotoar real-time. Kamera perangkat yang membuka aplikasi menjadi CCTV-nya.

    python main.py             # buka http://localhost:8000 di laptop; kamera laptop memantau
    python main.py --hp        # HP sebagai kamera: scan kode QR yang tampil, langsung masuk (butuh cloudflared)
    python main.py --https     # HP lewat Wi-Fi yang sama, tanpa tunnel (peringatan sertifikat sekali)

Kendaraan dikenali model YOLO (paket ultralytics). Zona trotoar bisa diusulkan otomatis oleh model segmentasi
trotoar (models/trotoar.pt) dan tetap bisa digambar atau diperbaiki sendiri.
"""
from __future__ import annotations

import argparse
import sys
import threading
import webbrowser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sidewalk-sentinel", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--hp", action="store_true",
                   help="pakai HP sebagai kamera: tunnel Cloudflare (bila cloudflared terpasang) dan kode QR di terminal "
                        "dan halaman /hp; tanpa cloudflared memakai HTTPS lokal")
    p.add_argument("--https", action="store_true",
                   help="layani HTTPS (sertifikat dibuat sendiri) agar kamera HP bisa dipakai; otomatis ikut --lan")
    p.add_argument("--lan", action="store_true", help="dengarkan di semua antarmuka, tautan berkunci untuk HP")
    p.add_argument("--data", default="data", help="folder kejadian dan foto bukti (aman dihapus)")
    p.add_argument("--no-open", action="store_true", help="jangan buka browser otomatis")
    p.add_argument("--weights", default="yolo11n.pt",
                   help="bobot YOLO untuk kendaraan (bawaan yolo11n.pt, diunduh sekali; boleh jalur ke berkas .pt sendiri)")
    p.add_argument("--sidewalk-weights", default=None,
                   help="bobot YOLO segmentasi trotoar untuk usulan zona (bawaan models/trotoar.pt bila ada)")
    p.add_argument("--device", default=None, help="perangkat inferensi, mis. cpu, 0 (GPU pertama), mps")
    p.add_argument("--retention-days", type=int, default=30, help="simpan kejadian dan foto bukti selama ini (hari)")
    return p


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "serve":                   # kebiasaan lama: `python main.py serve --https`
        argv = argv[1:]
    a = build_parser().parse_args(argv)
    from sidewalk_sentinel.server import serve
    if not a.no_open and not (a.lan or a.https or a.hp):
        threading.Timer(1.2, lambda: webbrowser.open(f"http://localhost:{a.port}/")).start()
    kw = {"weights": a.weights, "device": a.device}
    if a.sidewalk_weights:
        kw["sidewalk_weights"] = a.sidewalk_weights
    serve(host=a.host, port=a.port, data_dir=a.data, lan=a.lan, retention_days=a.retention_days, https=a.https,
          hp=a.hp, open_browser=not a.no_open, **kw)
    return 0


if __name__ == "__main__":
    sys.exit(main())

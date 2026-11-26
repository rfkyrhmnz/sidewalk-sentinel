#!/usr/bin/env python3
"""Sidewalk Sentinel: pemantau trotoar real-time. Kamera perangkat yang membuka aplikasi menjadi CCTV-nya.

    python main.py             # buka http://localhost:8000 di laptop; kamera laptop memantau
    python main.py --https     # sama, dan bisa dibuka dari HP di Wi-Fi yang sama; kamera HP memantau
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
    p.add_argument("--https", action="store_true",
                   help="layani HTTPS (sertifikat dibuat sendiri) agar kamera HP bisa dipakai; otomatis ikut --lan")
    p.add_argument("--lan", action="store_true", help="dengarkan di semua antarmuka, tautan berkunci untuk HP")
    p.add_argument("--data", default="data", help="folder kejadian dan foto bukti (aman dihapus)")
    p.add_argument("--no-open", action="store_true", help="jangan buka browser otomatis")
    p.add_argument("--retention-days", type=int, default=30, help="simpan kejadian dan foto bukti selama ini (hari)")
    return p


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "serve":                   # kebiasaan lama: `python main.py serve --https`
        argv = argv[1:]
    a = build_parser().parse_args(argv)
    from sidewalk_sentinel.server import serve
    if not a.no_open and not (a.lan or a.https):
        threading.Timer(1.2, lambda: webbrowser.open(f"http://localhost:{a.port}/")).start()
    serve(host=a.host, port=a.port, data_dir=a.data, lan=a.lan, retention_days=a.retention_days, https=a.https)
    return 0


if __name__ == "__main__":
    sys.exit(main())

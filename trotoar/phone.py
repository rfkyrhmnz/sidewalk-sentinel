"""Tautan HTTPS untuk kamera HP (terutama iPhone): terowongan Cloudflare + kode QR.

Safari (dan Chrome) hanya mengizinkan kamera di alamat ``https://`` atau ``http://localhost``. Karena dasbor berjalan
di laptop, HP perlu membukanya lewat terowongan HTTPS. Modul ini menjalankan ``cloudflared`` (Quick Tunnel, tanpa akun),
membaca alamat publiknya, lalu mencetak kode QR berisi alamat + kunci akses. Pengguna cukup memindai dengan aplikasi Kamera HP.

QR dibuat dengan ``cv2.QRCodeEncoder`` (OpenCV sudah menjadi dependensi), jadi tidak ada paket tambahan.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

import cv2
import numpy as np

INSTALL_HINT = (
    "cloudflared belum terpasang (dipakai untuk membuat tautan HTTPS ke iPhone).\n"
    "  Windows : winget install --id Cloudflare.cloudflared   (lalu tutup dan buka lagi terminalnya)\n"
    "  macOS   : brew install cloudflared\n"
    "  Linux   : https://github.com/cloudflare/cloudflared/releases\n"
    "Setelah terpasang, jalankan lagi:  python main.py phone"
)

_URL_RE = re.compile(r"https://([a-z0-9-]+)\.trycloudflare\.com", re.I)


class TunnelError(RuntimeError):
    pass


@dataclass
class PhoneOptions:
    cloudflared: str | None = None       # path cloudflared; None = cari di PATH
    open_image: bool = True              # buka qr_iphone.png otomatis di laptop


# ---------------------------------------------------------------------------- cloudflared
def find_cloudflared(explicit: str | None = None) -> str | None:
    if explicit:
        return explicit if (os.path.isfile(explicit) or shutil.which(explicit)) else None
    found = shutil.which("cloudflared")
    if found:
        return found
    if sys.platform.startswith("win"):
        for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                for sub in ("cloudflared", os.path.join("Programs", "cloudflared")):
                    p = os.path.join(base, sub, "cloudflared.exe")
                    if os.path.isfile(p):
                        return p
    return None


def find_url(line: str) -> str | None:
    """Alamat terowongan pada satu baris log cloudflared (host ``api`` adalah panggilan API, bukan terowongan)."""
    for m in _URL_RE.finditer(line):
        if m.group(1).lower() != "api":
            return m.group(0)
    return None


class Tunnel:
    def __init__(self, proc: subprocess.Popen, url: str, tail: deque):
        self.proc, self.url, self.tail = proc, url, tail

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:                  # pragma: no cover
                self.proc.kill()


def start_tunnel(base_cmd: Sequence[str], port: int, timeout: float = 45.0) -> Tunnel:
    """Jalankan ``<cloudflared> tunnel --url http://127.0.0.1:<port>`` dan tunggu alamat publiknya."""
    cmd = [*base_cmd, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace", bufsize=1)
    except OSError as e:
        raise TunnelError(f"cloudflared tidak bisa dijalankan: {e}") from e
    tail: deque = deque(maxlen=12)
    box: dict = {}
    found = threading.Event()

    def pump() -> None:                                        # terus menguras keluaran supaya pipa tidak penuh
        for line in proc.stdout:                               # type: ignore[union-attr]
            tail.append(line.rstrip())
            if "url" not in box:
                u = find_url(line)
                if u:
                    box["url"] = u
                    found.set()
        found.set()                                            # proses berakhir

    threading.Thread(target=pump, daemon=True).start()
    found.wait(timeout)
    if "url" not in box:
        proc.terminate()
        why = "berhenti sebelum memberi alamat" if proc.poll() is not None else f"tidak memberi alamat dalam {timeout:.0f} detik"
        raise TunnelError(f"cloudflared {why}. Keluaran terakhir:\n  " + "\n  ".join(tail or ["(kosong)"])
                          + "\nPeriksa koneksi internet; jaringan kampus kadang memblokir terowongan.")
    return Tunnel(proc, box["url"], tail)


# ---------------------------------------------------------------------------- kode QR
def qr_matrix(text: str) -> np.ndarray:
    """Matriks modul QR (True = gelap) tanpa zona hening."""
    params = cv2.QRCodeEncoder_Params()
    params.correction_level = cv2.QRCodeEncoder_CORRECT_LEVEL_M
    img = cv2.QRCodeEncoder.create(params).encode(text)
    if img is None or img.size == 0:
        raise ValueError("Teks terlalu panjang untuk kode QR.")
    dark = img < 128
    ys, xs = np.where(dark)
    return dark[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def qr_image(text: str, scale: int = 12, quiet: int = 4) -> np.ndarray:
    """Gambar QR hitam-putih (uint8) dengan zona hening, siap disimpan atau dipindai dari layar."""
    m = qr_matrix(text)
    img = np.kron(~m, np.ones((scale, scale), dtype=np.uint8)) * 255
    return np.pad(img, quiet * scale, constant_values=255).astype(np.uint8)


def save_qr_png(text: str, path: Path, scale: int = 12) -> None:
    ok, buf = cv2.imencode(".png", qr_image(text, scale))
    if not ok:                                                 # pragma: no cover
        raise OSError("Gagal membuat PNG.")
    buf.tofile(str(path))                                      # tofile: aman untuk path ber-Unicode di Windows


def qr_terminal(text: str, quiet: int = 2, half_blocks: bool = True) -> str:
    """QR untuk terminal. Warna hitam/putih dipaksa lewat ANSI sehingga polaritasnya benar di tema gelap maupun terang."""
    m = ~qr_matrix(text)                                       # True = terang
    m = np.pad(m, quiet, constant_values=True)
    black, white = "\x1b[40m", "\x1b[107m"
    lines: list[str] = []
    if half_blocks:
        if m.shape[0] % 2:
            m = np.vstack([m, np.ones((1, m.shape[1]), dtype=bool)])
        for r in range(0, m.shape[0], 2):
            out = []
            for top, bot in zip(m[r], m[r + 1]):
                fg = "97" if top else "30"
                bg = "107" if bot else "40"
                out.append(f"\x1b[{fg};{bg}m▀")
            lines.append("".join(out) + "\x1b[0m")
    else:
        for row in m:
            lines.append("".join((white if v else black) + "  " for v in row) + "\x1b[0m")
    return "\n".join(lines)


def _can_encode(ch: str) -> bool:
    try:
        ch.encode(getattr(sys.stdout, "encoding", None) or "ascii")
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def _open_file(path: Path) -> None:
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))                            # type: ignore[attr-defined]  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            opener = shutil.which("xdg-open")
            if opener:
                subprocess.Popen([opener, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


# ---------------------------------------------------------------------------- sesi
def _say(text: str) -> None:
    sys.stdout.write(text + "\n")                              # satu tulisan, supaya tidak terpotong cetakan thread lain
    sys.stdout.flush()


class PhoneSession:
    """Menyalakan terowongan di latar belakang, lalu mencetak QR dan tautannya."""

    def __init__(self, port: int, token: str, out_dir: Path, opts: PhoneOptions, base_cmd: Sequence[str],
                 out: Callable[[str], None] | None = None, on_fail: Callable[[], None] | None = None, timeout: float = 45.0):
        self.port, self.token, self.out_dir, self.opts = port, token, Path(out_dir), opts
        self.base_cmd, self.out, self.on_fail, self.timeout = list(base_cmd), out or _say, on_fail, timeout
        self.tunnel: Tunnel | None = None
        self.url: str | None = None
        self.done = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            self.out("Menyiapkan tautan HTTPS untuk HP (Cloudflare Tunnel)... biasanya 5-15 detik.")
            try:
                self.tunnel = start_tunnel(self.base_cmd, self.port, self.timeout)
            except TunnelError as e:
                self.out(f"Galat: {e}")
                if self.on_fail:
                    self.on_fail()
                return
            self.url = f"{self.tunnel.url}/?key={self.token}"
            if sys.platform.startswith("win"):
                os.system("")                                  # nyalakan kode warna ANSI di konsol Windows 10+
            png = None
            try:
                self.out_dir.mkdir(parents=True, exist_ok=True)
                png = self.out_dir / "qr_iphone.png"
                save_qr_png(self.url, png)
            except (OSError, ValueError):
                png = None
            self.out("")
            self.out("Di iPhone: buka aplikasi Kamera, arahkan ke kode di bawah, lalu ketuk tautan yang muncul.")
            self.out("")
            self.out(qr_terminal(self.url, half_blocks=_can_encode("▀")))
            self.out("")
            self.out(f"Atau salin tautan ini ke Safari:\n  {self.url}")
            if png:
                self.out(f"(Kode yang sama tersimpan di {png}; bisa dipindai langsung dari layar laptop.)")
                if self.opts.open_image:
                    _open_file(png)
            self.out("")
            self.out("Di Safari: ketuk Izinkan untuk kamera, maka deteksi mulai sendiri. Biarkan layar iPhone menyala dan Safari tetap di depan.\n"
                     "Kalau halaman belum terbuka, tunggu beberapa detik (alamat baru perlu waktu agar bisa dijangkau), lalu muat ulang.")
        finally:
            self.done.set()

    def close(self) -> None:
        if self.tunnel:
            self.tunnel.close()

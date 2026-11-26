"""Tunnel Cloudflare untuk membuka aplikasi dari HP lewat alamat https publik.

`cloudflared tunnel --url http://127.0.0.1:<port>` (tunnel cepat, tanpa akun) mencetak alamat acak
`https://....trycloudflare.com` di lognya. Modul ini menjalankannya sebagai proses anak, mengambil alamat itu dari
log, dan menghentikannya saat aplikasi berhenti. Alamat dari Cloudflare memakai sertifikat sah, jadi kamera HP
langsung boleh menyala tanpa peringatan sertifikat dan tanpa bergantung pada Wi-Fi atau firewall.
"""
from __future__ import annotations

import collections
import os
import re
import shutil
import subprocess
import sys
import threading
from typing import Callable

URL_RE = re.compile(r"https://([a-z0-9][a-z0-9-]*)\.trycloudflare\.com", re.IGNORECASE)
INSTALL_HINT = "winget install --id Cloudflare.cloudflared"


def parse_url(line: str) -> str | None:
    """Alamat tunnel dari satu baris log. `api.trycloudflare.com` muncul di pesan galat dan bukan alamat tunnel."""
    for m in URL_RE.finditer(line):
        if m.group(1).lower() != "api":
            return m.group(0).lower()
    return None


def find_cloudflared() -> str | None:
    """Jalur cloudflared: dari PATH, atau tempat pemasangan winget di Windows (PATH baru berlaku di terminal baru)."""
    exe = shutil.which("cloudflared")
    if exe:
        return exe
    if sys.platform == "win32":
        for var, sub in (("ProgramFiles(x86)", r"cloudflared\cloudflared.exe"), ("ProgramFiles", r"cloudflared\cloudflared.exe"),
                         ("LOCALAPPDATA", r"Microsoft\WinGet\Links\cloudflared.exe")):
            base = os.environ.get(var)
            if base and os.path.isfile(os.path.join(base, sub)):
                return os.path.join(base, sub)
    return None


def command(exe: str, port: int) -> list[str]:
    # 127.0.0.1, bukan localhost: di Windows localhost bisa menjadi ::1 sementara server hanya mendengar di IPv4.
    return [exe, "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"]


def explain(lines: list[str]) -> str:
    text = " ".join(lines).lower()
    if any(k in text for k in ("no such host", "dial tcp", "timeout", "failed to request quick tunnel", "i/o timeout")):
        return "Tidak bisa menghubungi Cloudflare. Periksa koneksi internet laptop, lalu jalankan ulang."
    last = lines[-1].strip() if lines else ""
    return f"cloudflared berhenti sebelum tunnel jadi. {last}".strip()


class Tunnel:
    """Proses cloudflared. `on_url(url)` dipanggil sekali saat alamat tersedia; `on_fail(pesan)` bila gagal atau putus."""

    def __init__(self, cmd: list[str], on_url: Callable[[str], None], on_fail: Callable[[str], None], timeout: float = 45.0):
        self.cmd, self.on_url, self.on_fail, self.timeout = cmd, on_url, on_fail, timeout
        self.proc: subprocess.Popen | None = None
        self.url: str | None = None
        self._stopping = False
        self._failed = False
        self._tail: collections.deque[str] = collections.deque(maxlen=12)
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None

    def start(self) -> None:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.proc = subprocess.Popen(self.cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         text=True, encoding="utf-8", errors="replace", bufsize=1, creationflags=flags)
        except OSError as e:
            self._fail(f"cloudflared tidak bisa dijalankan: {e}")
            return
        threading.Thread(target=self._read, name="tunnel", daemon=True).start()
        self._timer = threading.Timer(self.timeout, self._expired)
        self._timer.daemon = True
        self._timer.start()

    def _fail(self, message: str) -> None:
        with self._lock:
            if self._failed or self._stopping:
                return
            self._failed = True
        self.on_fail(message)

    def _read(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            self._tail.append(line.rstrip())
            if self.url is None:
                url = parse_url(line)
                if url:
                    self.url = url
                    if self._timer:
                        self._timer.cancel()
                    self.on_url(url)
        self.proc.wait()
        if self._timer:
            self._timer.cancel()
        if self.url is None:
            self._fail(explain(list(self._tail)))
        else:
            self._fail("Tunnel terputus. Jalankan ulang python main.py --hp untuk membuat kode baru.")

    def _expired(self) -> None:
        if self.url is None and not self._stopping:
            self._fail("Tunnel belum jadi setelah %d detik. Periksa koneksi internet laptop, lalu jalankan ulang." % self.timeout)
            self.stop(quiet=True)

    def stop(self, quiet: bool = False) -> None:
        with self._lock:
            self._stopping = self._stopping or not quiet
        if self._timer:
            self._timer.cancel()
        p = self.proc
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(3)
            except subprocess.TimeoutExpired:
                p.kill()

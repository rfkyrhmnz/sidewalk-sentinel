"""Siapa yang meminta: pusat (peramban di mesin server) atau perangkat.

Hak akses TIDAK ditentukan oleh alamat IP. IP berganti (Wi-Fi ke data seluler, DHCP), dipakai bersama (NAT), dan di
belakang tunnel semua permintaan tampak datang dari 127.0.0.1, jadi aturan "IP ini boleh melihat semuanya" justru akan
memberi hak pusat kepada setiap HP yang masuk lewat tunnel. Yang dipakai:

- pusat: sambungan benar-benar lokal. Ketiganya harus terpenuhi sekaligus, salah satu saja meleset berarti BUKAN pusat:
  (1) alamat penyambung loopback, (2) tidak ada header proxy (Cloudflare dan sejenisnya selalu menambahkannya),
  (3) header Host berupa nama loopback. Syarat (3) juga menolak serangan DNS rebinding.
- perangkat: pemegang kunci rahasia perangkat (cookie `ss_dev`, dipasang saat mendaftar). Hanya melihat datanya sendiri.
- IP tetap dicatat, tetapi hanya sebagai penanda yang ditampilkan di pusat, bukan sebagai kunci akses.
"""
from __future__ import annotations

import ipaddress
import re

PROXY_HEADERS = ("cf-connecting-ip", "cf-ray", "cf-visitor", "cf-ipcountry", "true-client-ip", "x-forwarded-for",
                 "x-forwarded-host", "x-forwarded-proto", "x-forwarded-port", "x-real-ip", "forwarded", "via")
LOCAL_NAMES = {"localhost"}


def parse_ip(text):
    """ipaddress.IPv4Address/IPv6Address, atau None bila bukan alamat. IPv4 yang dibungkus IPv6 dibuka bungkusnya."""
    try:
        addr = ipaddress.ip_address(str(text).strip().split("%")[0])
    except ValueError:
        return None
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr


def is_loopback(text) -> bool:
    addr = parse_ip(text)
    return bool(addr and addr.is_loopback)


_HOST = re.compile(r"(\[[0-9a-f:.]+\]|[^\s:/@\[\]]+)(?::(\d{1,5}))?")


def host_name(host_header: str) -> str:
    """Nama dari header Host tanpa port: 'localhost:8000' -> 'localhost', '[::1]:8000' -> '::1'.
    Bentuk yang tidak lazim ('localhost:8000@evil.com', 'localhost:abc') menghasilkan '' (bukan nama lokal)."""
    m = _HOST.fullmatch((host_header or "").strip().lower())
    if not m:
        return ""
    return m.group(1).strip("[]").rstrip(".")


def _lower(headers) -> dict:
    return {str(k).lower(): v for k, v in headers.items()}


def is_center(peer: str, headers) -> bool:
    """True hanya untuk sambungan lokal sungguhan (lihat dokumentasi modul). Ragu berarti bukan pusat."""
    if not is_loopback(peer):
        return False
    h = _lower(headers)
    if any(name in h for name in PROXY_HEADERS):
        return False
    name = host_name(h.get("host", ""))
    return name in LOCAL_NAMES or is_loopback(name)


def client_ip(peer: str, headers, trust_proxy: bool = False) -> str:
    """IP yang dicatat sebagai penanda perangkat ('' bila tidak dikenali).

    Di belakang tunnel, alamat asli ada di Cf-Connecting-Ip. Header itu dipercaya hanya bila sambungan datang dari
    loopback (cloudflared) dan server ini sendiri yang menjalankan tunnel (`trust_proxy`). Dari klien lain header itu
    gampang dipalsukan, jadi diabaikan."""
    addr = parse_ip(peer)
    if trust_proxy and addr is not None and addr.is_loopback:
        real = parse_ip(_lower(headers).get("cf-connecting-ip", ""))
        if real is not None:
            return str(real)
    return str(addr) if addr is not None else ""

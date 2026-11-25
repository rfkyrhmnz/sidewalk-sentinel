"""Sertifikat HTTPS ditandatangani sendiri untuk `serve --https`.

Kenapa perlu: browser hanya mengizinkan kamera (getUserMedia) di "konteks aman". `localhost` termasuk, tetapi alamat
Wi-Fi seperti `http://192.168.1.23:8000` tidak. Agar HP bisa menjadi kamera, server harus melayani HTTPS.

Sertifikat dibuat sekali, disimpan di folder data, dan dipakai ulang selama masih sah dan masih memuat alamat yang
dipakai sekarang. Karena ditandatangani sendiri, browser menampilkan peringatan sekali; setelah pengguna melanjutkan,
kamera bisa dipakai. Untuk menghindari peringatan, pakai terowongan HTTPS (mis. Cloudflare Tunnel) tanpa opsi ini.
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import os
import ssl
from pathlib import Path

CERT_NAME, KEY_NAME = "cert.pem", "key.pem"
VALID_DAYS = 825                     # batas yang masih diterima Safari/iOS untuk sertifikat pengguna
RENEW_BEFORE_DAYS = 30


class TlsUnavailable(RuntimeError):
    """Paket `cryptography` tidak ada atau sertifikat tidak bisa dibuat."""


def _crypto():
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    except ImportError as e:
        raise TlsUnavailable("Opsi --https butuh paket cryptography. Jalankan: pip install cryptography") from e
    return x509, hashes, serialization, ec, ExtendedKeyUsageOID, NameOID


def _wanted(hosts) -> tuple[set[str], set[str]]:
    """Pisahkan nama DNS dan alamat IP yang harus ada di sertifikat. `localhost` dan 127.0.0.1 selalu ikut."""
    dns, ips = {"localhost"}, {"127.0.0.1"}
    for h in hosts or []:
        h = str(h).strip()
        if not h:
            continue
        try:
            ips.add(str(ipaddress.ip_address(h)))
        except ValueError:
            dns.add(h.lower())
    return dns, ips


def _utc(t: dt.datetime) -> dt.datetime:
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def _still_good(cert_path: Path, key_path: Path, dns: set[str], ips: set[str]) -> bool:
    """Sertifikat lama boleh dipakai: terbaca, pasangan kuncinya cocok, memuat semua alamat, dan belum hampir habis."""
    x509, _, serialization, _, _, _ = _crypto()
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        fmt = (serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        if cert.public_key().public_bytes(*fmt) != key.public_key().public_bytes(*fmt):
            return False
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        have_dns = {n.lower() for n in san.get_values_for_type(x509.DNSName)}
        have_ips = {str(a) for a in san.get_values_for_type(x509.IPAddress)}
        now = dt.datetime.now(dt.timezone.utc)
        left = _utc(cert.not_valid_after_utc if hasattr(cert, "not_valid_after_utc") else cert.not_valid_after) - now
        return dns <= have_dns and ips <= have_ips and left > dt.timedelta(days=RENEW_BEFORE_DAYS)
    except (OSError, ValueError, x509.ExtensionNotFound):
        return False


def _write_private(path: Path, data: bytes) -> None:
    """Tulis berkas rahasia dengan izin 0600 sejak awal (tidak sempat terbaca pengguna lain), lalu ganti atomik."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def ensure_certificate(directory: str | Path, hosts=(), days: int = VALID_DAYS) -> tuple[Path, Path]:
    """Kembalikan (sertifikat, kunci). Dibuat bila belum ada, tidak cocok dengan alamat sekarang, atau hampir habis."""
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    cert_path, key_path = d / CERT_NAME, d / KEY_NAME
    dns, ips = _wanted(hosts)
    x509, hashes, serialization, ec, eku, name_oid = _crypto()
    if cert_path.is_file() and key_path.is_file() and _still_good(cert_path, key_path, dns, ips):
        return cert_path, key_path

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(name_oid.COMMON_NAME, "Sidewalk Sentinel (lokal)")])
    now = dt.datetime.now(dt.timezone.utc)
    san = [x509.DNSName(n) for n in sorted(dns)] + [x509.IPAddress(ipaddress.ip_address(a)) for a in sorted(ips)]
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1))       # toleransi jam perangkat yang sedikit mundur
            .not_valid_after(now + dt.timedelta(days=days))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=False, content_commitment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                         crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([eku.SERVER_AUTH]), critical=False)
            .sign(key, hashes.SHA256()))
    _write_private(key_path, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


def server_context(cert_path: Path, key_path: Path) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert_path, key_path)
    return ctx

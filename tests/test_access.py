"""Uji aturan siapa yang boleh melihat apa: pusat (sambungan lokal sungguhan) dan IP penanda. Fungsi murni, tanpa server.

Pegangan utama: ragu berarti BUKAN pusat. Kesalahan ke arah itu hanya membuat laptop pengelola terlihat seperti
perangkat biasa; kesalahan ke arah sebaliknya memberi semua data kepada orang asing."""
from __future__ import annotations

import pytest

from sidewalk_sentinel import access

LOCAL = {"Host": "localhost:8000"}


@pytest.mark.parametrize("peer,headers", [
    ("127.0.0.1", {"Host": "localhost:8000"}),
    ("127.0.0.1", {"Host": "127.0.0.1:8000"}),
    ("127.0.0.1", {"Host": "127.0.0.1"}),
    ("127.0.0.1", {"Host": "LOCALHOST:8000"}),
    ("127.0.0.1", {"Host": "localhost.:8000"}),
    ("127.0.0.2", {"Host": "localhost"}),
    ("::1", {"Host": "[::1]:8000"}),
    ("::ffff:127.0.0.1", {"Host": "localhost:8000"}),
    ("127.0.0.1", {"HOST": "localhost:8443"}),
])
def test_sambungan_lokal_sungguhan_adalah_pusat(peer, headers):
    assert access.is_center(peer, headers) is True


@pytest.mark.parametrize("peer,headers,why", [
    ("192.168.1.20", LOCAL, "penyambung dari LAN yang mengaku Host localhost"),
    ("203.0.113.9", LOCAL, "penyambung dari internet"),
    ("192.168.1.20", {"Host": "192.168.1.5:8443"}, "HP lewat Wi-Fi"),
    ("127.0.0.1", {"Host": "abc-def.trycloudflare.com"}, "cloudflared menyambung dari loopback dengan Host tunnel"),
    ("127.0.0.1", {}, "tanpa Host"),
    ("127.0.0.1", {"Host": ""}, "Host kosong"),
    ("127.0.0.1", {"Host": "localhost.evil.com"}, "DNS rebinding"),
    ("127.0.0.1", {"Host": "127.0.0.1.evil.com"}, "DNS rebinding dengan awalan IP"),
    ("127.0.0.1", {"Host": "evil.com"}, "nama lain yang diarahkan ke 127.0.0.1"),
    ("127.0.0.1", {"Host": "localhost:8000@evil.com"}, "Host bentuk tak lazim"),
    ("127.0.0.1", {"Host": "localhost@evil.com"}, "Host bentuk tak lazim"),
    ("127.0.0.1", {"Host": "localhost:abc"}, "port bukan angka"),
    ("127.0.0.1", {"Host": "localhost:"}, "port kosong"),
    ("127.0.0.1", {"Host": "localhost:8000/x"}, "ada jalur di Host"),
    ("", LOCAL, "penyambung tidak dikenal"),
    ("bukan-ip", LOCAL, "penyambung bukan IP"),
    ("0.0.0.0", LOCAL, "bukan loopback"),
])
def test_selain_itu_bukan_pusat(peer, headers, why):
    assert access.is_center(peer, headers) is False, why


@pytest.mark.parametrize("name", access.PROXY_HEADERS)
def test_header_proxy_apa_pun_membuat_sambungan_bukan_pusat(name):
    assert access.is_center("127.0.0.1", {**LOCAL, name: "1"}) is False
    assert access.is_center("127.0.0.1", {**LOCAL, name.upper(): "1"}) is False, "huruf besar kecil tidak menolong"


def test_header_kosong_pun_dihitung_ada():
    assert access.is_center("127.0.0.1", {**LOCAL, "X-Forwarded-For": ""}) is False


@pytest.mark.parametrize("peer,headers,trust,expected", [
    ("192.168.1.20", {}, False, "192.168.1.20"),
    ("192.168.1.20", {"Cf-Connecting-Ip": "6.6.6.6"}, False, "192.168.1.20"),
    ("192.168.1.20", {"Cf-Connecting-Ip": "6.6.6.6"}, True, "192.168.1.20"),          # dipalsukan dari LAN: diabaikan
    ("127.0.0.1", {"Cf-Connecting-Ip": "203.0.113.7"}, True, "203.0.113.7"),           # lewat tunnel milik server ini
    ("127.0.0.1", {"cf-connecting-ip": "203.0.113.7"}, True, "203.0.113.7"),
    ("127.0.0.1", {"Cf-Connecting-Ip": "203.0.113.7"}, False, "127.0.0.1"),            # tanpa tunnel: header tak dipercaya
    ("127.0.0.1", {"Cf-Connecting-Ip": "bukan ip"}, True, "127.0.0.1"),
    ("127.0.0.1", {"Cf-Connecting-Ip": "203.0.113.7, 10.0.0.1"}, True, "127.0.0.1"),    # daftar bukan satu IP
    ("127.0.0.1", {"Cf-Connecting-Ip": "2001:db8::1"}, True, "2001:db8::1"),
    ("::ffff:192.168.1.9", {}, False, "192.168.1.9"),
    ("127.0.0.1", {}, True, "127.0.0.1"),
    ("", {}, False, ""),
    ("bukan-ip", {}, True, ""),
])
def test_ip_penanda_hanya_memercayai_header_cloudflare_dari_tunnel_sendiri(peer, headers, trust, expected):
    assert access.client_ip(peer, headers, trust) == expected

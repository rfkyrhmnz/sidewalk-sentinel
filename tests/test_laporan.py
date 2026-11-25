"""Laporan ringkasan: agregasi dari kejadian yang sudah tercatat.

compute_report adalah fungsi murni, jadi batas periode, zona waktu, dan jam puncak diuji dengan data buatan dan
'sekarang' yang ditetapkan, tanpa kamera, utas, atau jam sungguhan. Uji API ada di test_cameras.py (memakai server).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sidewalk_sentinel.report import compute_report, duration_stats, period_start

WIB = timezone(timedelta(hours=7))
NOW = datetime(2026, 11, 26, 14, 30, tzinfo=WIB)


def rec(no, kind, when, durasi=None, snapshot=True):
    r = {"no": no, "ts": when.isoformat(timespec="seconds"), "kind": kind,
         "label": "PARKIR DI TROTOAR" if kind == "parkir" else "NAIK TROTOAR", "reason": "uji", "track_id": no,
         "snapshot": f"events/{no}.jpg" if snapshot else None}
    if durasi is not None:
        r["durasi_s"] = durasi
    return when, r


def at(day, hour=12, minute=0, second=0):
    return datetime(2026, 11, day, hour, minute, second, tzinfo=WIB)


def rep(pairs, periode="hari_ini", cid="cam1", name="Depan toko", now=NOW):
    return compute_report([(cid, name, pairs)], periode, now)


def test_hitungan_per_jenis_total_dan_per_kamera():
    a = [rec(1, "parkir", at(26, 8)), rec(2, "parkir", at(26, 9)), rec(3, "naik_trotoar", at(26, 10)),
         rec(4, "parkir", at(26, 11))]
    b = [rec(1, "naik_trotoar", at(26, 7)), rec(2, "parkir", at(26, 13))]
    r = compute_report([("a", "Depan toko", a), ("b", "Pojok", b)], "hari_ini", NOW)
    assert (r["total"], r["parkir"], r["naik_trotoar"]) == (6, 4, 2)
    assert [(c["nama"], c["parkir"], c["naik_trotoar"], c["total"]) for c in r["per_kamera"]] == [
        ("Depan toko", 3, 1, 4), ("Pojok", 1, 1, 2)], "diurutkan dari kejadian terbanyak"


def test_batas_hari_ini_tepat_tengah_malam_dan_masa_depan_diabaikan():
    pairs = [rec(1, "parkir", at(25, 23, 59, 59)), rec(2, "parkir", at(26, 0, 0, 0)),
             rec(3, "parkir", at(26, 14, 30, 0)), rec(4, "parkir", at(26, 14, 30, 1))]
    assert [e["no"] for e in rep(pairs)["terbaru"]] == [3, 2], "kemarin 23.59.59 keluar, 00.00.00 masuk, setelah sekarang keluar"
    assert period_start(NOW, "hari_ini") == at(26, 0)


@pytest.mark.parametrize("periode,awal,sehari_sebelum", [("7_hari", 20, 19), ("30_hari", None, None)])
def test_batas_periode_banyak_hari(periode, awal, sehari_sebelum):
    if periode == "7_hari":
        pairs = [rec(1, "parkir", at(awal, 0, 0, 0)), rec(2, "parkir", at(sehari_sebelum, 23, 59, 59))]
        r = rep(pairs, periode)
        assert r["total"] == 1 and r["dari"].startswith("2026-11-20T00:00:00")
        assert [d["tanggal"] for d in r["per_hari"]] == [f"2026-11-{d}" for d in range(20, 27)]
    else:
        earlier = datetime(2026, 10, 28, 0, 0, 0, tzinfo=WIB)
        pairs = [rec(1, "parkir", earlier), rec(2, "parkir", earlier - timedelta(seconds=1))]
        r = rep(pairs, periode)
        assert r["total"] == 1 and len(r["per_hari"]) == 30 and r["per_hari"][0]["tanggal"] == "2026-10-28"


def test_periode_tidak_dikenal_ditolak():
    with pytest.raises(ValueError, match="periode"):
        rep([], "tahun_ini")


def test_zona_waktu_rekaman_dinormalkan_ke_waktu_setempat():
    utc = datetime(2026, 11, 25, 17, 30, tzinfo=timezone.utc)            # = 26 Nov 00.30 WIB
    r = rep([rec(1, "parkir", utc)])
    assert r["total"] == 1 and r["per_jam"]["parkir"][0] == 1 and r["per_hari"][0]["tanggal"] == "2026-11-26"


def test_jam_puncak_sebaran_jam_dan_seri_memilih_jam_lebih_awal():
    pairs = [rec(1, "parkir", at(26, 7, 10)), rec(2, "naik_trotoar", at(26, 7, 50)), rec(3, "parkir", at(26, 8, 0)),
             rec(4, "parkir", at(26, 9, 0))]
    r = rep(pairs)
    assert r["per_jam"]["parkir"][7] == 1 and r["per_jam"]["naik_trotoar"][7] == 1 and r["per_jam"]["parkir"][8] == 1
    assert r["jam_puncak"] == {"jam": 7, "jumlah": 2}
    seri = rep([rec(1, "parkir", at(26, 10)), rec(2, "parkir", at(26, 6))])
    assert seri["jam_puncak"] == {"jam": 6, "jumlah": 1}


def test_per_hari_diisi_nol_dan_kejadian_masuk_hari_yang_benar():
    r = rep([rec(1, "parkir", at(22, 9)), rec(2, "naik_trotoar", at(22, 18)), rec(3, "parkir", at(26, 1))], "7_hari")
    por = {d["tanggal"]: (d["parkir"], d["naik_trotoar"]) for d in r["per_hari"]}
    assert por["2026-11-22"] == (1, 1) and por["2026-11-26"] == (1, 0) and por["2026-11-23"] == (0, 0)
    assert len(por) == 7


def test_statistik_lama_hanya_dari_kejadian_yang_punya_durasi():
    pairs = [rec(1, "parkir", at(26, 8), 10), rec(2, "parkir", at(26, 9), 20), rec(3, "parkir", at(26, 10), 30),
             rec(4, "parkir", at(26, 11)),                                   # belum selesai / tak terukur
             rec(5, "naik_trotoar", at(26, 12), 5)]
    lama = rep(pairs)["lama"]
    assert lama["parkir"] == {"n": 3, "rata": 20.0, "maks": 30.0, "min": 10.0, "total": 60.0}
    assert lama["naik_trotoar"]["n"] == 1 and lama["naik_trotoar"]["rata"] == 5.0
    assert lama["semua"]["n"] == 4 and lama["semua"]["rata"] == 16.2 and lama["semua"]["maks"] == 30.0


def test_tanpa_durasi_statistik_kosong_bukan_nol_atau_galat():
    r = rep([rec(1, "parkir", at(26, 8))])
    assert r["total"] == 1
    assert r["lama"]["parkir"] == {"n": 0, "rata": None, "maks": None, "min": None, "total": None}
    assert duration_stats([]) == {"n": 0, "rata": None, "maks": None, "min": None, "total": None}


def test_keadaan_kosong_semua_nol():
    r = compute_report([], "30_hari", NOW)
    assert (r["total"], r["parkir"], r["naik_trotoar"]) == (0, 0, 0)
    assert r["jam_puncak"] is None and r["terbaru"] == [] and r["per_kamera"] == []
    assert sum(sum(v) for v in r["per_jam"].values()) == 0 and all(d["parkir"] == d["naik_trotoar"] == 0 for d in r["per_hari"])


def test_kamera_tanpa_kejadian_tetap_tampil_dengan_nol():
    r = compute_report([("a", "Sepi", []), ("b", "Ramai", [rec(1, "parkir", at(26, 8))])], "hari_ini", NOW)
    assert [(c["nama"], c["total"]) for c in r["per_kamera"]] == [("Ramai", 1), ("Sepi", 0)]


def test_terbaru_terurut_dibatasi_enam_dan_url_foto():
    pairs = [rec(i, "parkir", at(26, 1 + i), snapshot=(i != 8)) for i in range(1, 9)]
    r = rep(pairs)
    assert [e["no"] for e in r["terbaru"]] == [8, 7, 6, 5, 4, 3]
    assert r["terbaru"][0]["snapshot_url"] is None
    assert r["terbaru"][1]["snapshot_url"] == "/api/cams/cam1/events/7.jpg"
    assert r["terbaru"][1]["camera"] == {"id": "cam1", "name": "Depan toko"}
    assert r["terbaru"][1]["reason"] == "uji", "baris kejadian di laporan menampilkan alasan"


def test_rekaman_asing_atau_rusak_dilewati_bukan_melempar():
    when = at(26, 9)
    pairs = [(when, {"no": 1, "ts": when.isoformat(), "kind": "lain", "label": "x"}),            # jenis tak dikenal
             (when, {"no": 2, "ts": when.isoformat(), "kind": "parkir", "durasi_s": "lama"}),    # durasi bukan angka
             (when, {"no": 3, "ts": when.isoformat(), "kind": "parkir", "durasi_s": True}),      # bool bukan durasi
             (when, {"no": 4, "ts": when.isoformat(), "kind": "parkir"}),                       # tanpa kolom snapshot
             (when, {"no": 5, "ts": when.isoformat(), "kind": "parkir", "durasi_s": float("nan")}),
             (when, {"no": 6, "ts": when.isoformat(), "kind": "parkir", "durasi_s": -4.0})]
    r = rep(pairs)
    assert r["total"] == 5 and r["lama"]["semua"]["n"] == 0
    assert all(e["durasi_s"] is None for e in r["terbaru"]), "durasi tak wajar tidak boleh sampai ke tampilan"

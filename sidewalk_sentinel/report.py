"""Laporan ringkasan kejadian: hitungan, lama di trotoar, sebaran jam dan hari, per kamera.

Fungsi murni: masukannya daftar (id kamera, nama, [(waktu, rekaman)]) dan waktu sekarang, sehingga batas periode dan
perhitungannya bisa diuji dengan data buatan tanpa kamera, utas, atau jam sungguhan.

Yang sengaja tidak ada: jenis kendaraan (mobil, motor, truk) dan kecepatan dalam km/jam. Pipeline klasik di sini
memisahkan kendaraan dari latar, bayangan, dan pejalan kaki, tetapi tidak mengenali jenisnya; ukuran blob bergantung
pada jarak kamera. Kecepatan nyata butuh kalibrasi jarak per kamera. Laporan hanya memuat apa yang benar-benar terukur.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

KINDS = ("parkir", "naik_trotoar")
KIND_LABEL = {"parkir": "Parkir di trotoar", "naik_trotoar": "Naik trotoar"}
# periode -> (jumlah hari sebelum hari ini yang ikut, label)
PERIODS = {"hari_ini": (0, "Hari ini"), "7_hari": (6, "7 hari terakhir"), "30_hari": (29, "30 hari terakhir")}
RECENT = 6
MAX_DURATION_S = 7 * 24 * 3600

Entry = tuple[str, str, list[tuple[datetime, dict]]]            # (id kamera, nama kamera, [(waktu, rekaman)])


def period_start(now: datetime, periode: str) -> datetime:
    """Awal periode: pukul 00.00 waktu setempat pada hari pertama yang ikut dihitung."""
    if periode not in PERIODS:
        raise ValueError(f"periode harus salah satu dari: {', '.join(PERIODS)}")
    return now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=PERIODS[periode][0])


def valid_duration(v) -> bool:
    """Angka sungguhan, terhingga, dan wajar. bool termasuk int di Python, dan NaN merusak rata-rata."""
    return (isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and 0 <= v <= MAX_DURATION_S)


def duration_stats(values: list[float]) -> dict:
    """Ringkasan lama (detik). Tanpa data: n = 0 dan sisanya None, bukan 0 yang menyesatkan."""
    if not values:
        return {"n": 0, "rata": None, "maks": None, "min": None, "total": None}
    return {"n": len(values), "rata": round(sum(values) / len(values), 1), "maks": round(max(values), 1),
            "min": round(min(values), 1), "total": round(sum(values), 1)}


def compute_report(entries: list[Entry], periode: str, now: datetime, camera_id: str | None = None) -> dict:
    since = period_start(now, periode)
    days = PERIODS[periode][0] + 1
    tz = now.tzinfo

    per_hour = {k: [0] * 24 for k in KINDS}
    per_day = {(since + timedelta(days=i)).date(): {k: 0 for k in KINDS} for i in range(days)}
    per_cam, lengths = [], {k: [] for k in KINDS}
    rows: list[tuple[datetime, int, dict]] = []

    for cid, name, pairs in entries:
        counts = {k: 0 for k in KINDS}
        for dt, rec in pairs:
            local = dt.astimezone(tz)
            if rec.get("kind") not in KINDS or not (since <= local <= now):
                continue
            kind = rec["kind"]
            counts[kind] += 1
            per_hour[kind][local.hour] += 1
            per_day[local.date()][kind] += 1
            if valid_duration(rec.get("durasi_s")):
                lengths[kind].append(float(rec["durasi_s"]))
            rows.append((local, rec["no"], {
                "no": rec["no"], "ts": rec["ts"], "kind": kind, "label": rec.get("label", ""),
                "reason": rec.get("reason", ""),
                "durasi_s": rec["durasi_s"] if valid_duration(rec.get("durasi_s")) else None, "camera": {"id": cid, "name": name},
                "snapshot_url": f"/api/cams/{cid}/{rec['snapshot']}" if rec.get("snapshot") else None}))
        per_cam.append({"id": cid, "nama": name, **counts, "total": sum(counts.values())})

    per_cam.sort(key=lambda c: (-c["total"], c["nama"].lower()))
    rows.sort(key=lambda r: (r[0], r[1]), reverse=True)
    by_hour = [sum(per_hour[k][h] for k in KINDS) for h in range(24)]
    peak = max(range(24), key=lambda h: (by_hour[h], -h)) if any(by_hour) else None
    parkir, naik = sum(per_hour["parkir"]), sum(per_hour["naik_trotoar"])
    return {
        "periode": periode, "periode_label": PERIODS[periode][1],
        "dari": since.isoformat(timespec="seconds"), "sampai": now.isoformat(timespec="seconds"),
        "kamera": camera_id or "semua",
        "total": parkir + naik, "parkir": parkir, "naik_trotoar": naik,
        "lama": {"semua": duration_stats(lengths["parkir"] + lengths["naik_trotoar"]),
                 "parkir": duration_stats(lengths["parkir"]), "naik_trotoar": duration_stats(lengths["naik_trotoar"])},
        "per_jam": per_hour,
        "jam_puncak": {"jam": peak, "jumlah": by_hour[peak]} if peak is not None else None,
        "per_hari": [{"tanggal": d.isoformat(), **v} for d, v in sorted(per_day.items())],
        "per_kamera": per_cam,
        "terbaru": [r[2] for r in rows[:RECENT]],
    }

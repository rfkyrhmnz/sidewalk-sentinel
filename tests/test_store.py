"""Uji penyimpanan kejadian dan penghitung lama kejadian (tanpa server, utas, atau kamera)."""
from __future__ import annotations

import json
from datetime import timedelta

import pytest

from sidewalk_sentinel.store import DurationTracker, EventStore, now

EV = {"kind": "parkir", "label": "PARKIR DI TROTOAR", "reason": "diam 5 detik", "track_id": 1}


def _ev(kind="parkir", track_id=1):
    return {"kind": kind, "label": kind, "reason": "uji", "track_id": track_id}


def _lines(folder):
    return (folder / "events.jsonl").read_text(encoding="utf-8").splitlines()


def test_event_store_menyimpan_dan_dipulihkan(tmp_path):
    s = EventStore(tmp_path / "c1")
    r1 = s.append(EV, b"\xff\xd8jpg1")
    r2 = s.append({**EV, "kind": "naik_trotoar", "label": "NAIK TROTOAR"}, None)
    assert (r1["no"], r2["no"]) == (1, 2) and r2["snapshot"] is None
    assert s.snapshot_path(1).read_bytes() == b"\xff\xd8jpg1" and s.snapshot_path(2) is None
    s2 = EventStore(tmp_path / "c1")                       # restart
    assert [r["no"] for r in s2.records] == [1, 2] and s2.seq == 2
    assert s2.append(EV, None)["no"] == 3                  # penomoran lanjut
    today = s2.counts_since(s2._dts[0].replace(hour=0, minute=0, second=0))
    assert today == {"parkir": 2, "naik_trotoar": 1}


def test_event_store_toleran_baris_rusak(tmp_path):
    s = EventStore(tmp_path / "c")
    s.append(EV, None)
    with open(s._file, "a", encoding="utf-8") as fh:
        fh.write('{"no": 2, "ts": "bukan-waktu"}\n{terpotong karena mati lis')
    again = EventStore(tmp_path / "c")
    assert [r["no"] for r in again.records] == [1]
    assert again.append(EV, None)["no"] == 2


def test_event_store_retensi_dan_nomor_tidak_dipakai_ulang(tmp_path):
    s = EventStore(tmp_path / "c")
    for _ in range(5):
        s.append(EV, b"jpg")
    assert s.prune(s._dts[0] - timedelta(days=1), max_events=3) == 2          # batas jumlah
    assert [r["no"] for r in s.records] == [3, 4, 5]
    assert s.snapshot_path(1) is None and s.snapshot_path(5) is not None
    assert s.prune(s._dts[-1] + timedelta(days=1), max_events=100) == 3        # lewat batas umur
    assert s.records == [] and list(s.events_dir.iterdir()) == []
    # setelah semuanya dipangkas dan server restart, nomor TIDAK mundur ke 1 (cache foto browser)
    assert EventStore(tmp_path / "c").append(EV, None)["no"] == 6


def test_event_store_halaman_dan_after(tmp_path):
    s = EventStore(tmp_path / "c")
    for i in range(6):
        s.append({**EV, "kind": "parkir" if i % 2 == 0 else "naik_trotoar"}, None)
    assert [r["no"] for r in s.latest(3)] == [6, 5, 4]
    assert [r["no"] for r in s.latest(3, before_no=4)] == [3, 2, 1]
    assert [r["no"] for r in s.latest(10, kind="parkir")] == [5, 3, 1]
    assert [r["no"] for r in s.after(4)] == [5, 6]



def test_patch_disimpan_ditambahkan_ke_berkas_dan_dimuat_ulang(tmp_path):
    store = EventStore(tmp_path)
    rec = store.append(_ev(), None)
    assert store.patch(rec["no"], durasi_s=12.34) is True
    assert store.latest(1)[0]["durasi_s"] == 12.3
    assert len(_lines(tmp_path)) == 2, "berkas hanya bertambah: satu baris kejadian, satu baris patch"
    assert EventStore(tmp_path).latest(1)[0]["durasi_s"] == 12.3


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), "12", True, None, 7 * 24 * 3600 + 1])
def test_patch_menolak_nilai_tak_wajar(tmp_path, value):
    store = EventStore(tmp_path)
    rec = store.append(_ev(), None)
    assert store.patch(rec["no"], durasi_s=value) is False
    assert "durasi_s" not in store.latest(1)[0] and len(_lines(tmp_path)) == 1


def test_patch_mengabaikan_kolom_asing_dan_kejadian_tak_dikenal(tmp_path):
    store = EventStore(tmp_path)
    rec = store.append(_ev(), None)
    assert store.patch(rec["no"], kind="naik_trotoar", reason="palsu") is False
    assert store.latest(1)[0]["kind"] == "parkir" and store.latest(1)[0]["reason"] == "uji"
    assert store.patch(999, durasi_s=5) is False


def test_baris_patch_rusak_di_berkas_dilewati_yang_sah_tetap_berlaku(tmp_path):
    store = EventStore(tmp_path)
    a, b = store.append(_ev(), None), store.append(_ev(track_id=2), None)
    with open(tmp_path / "events.jsonl", "a", encoding="utf-8") as fh:
        for bad in ('{"no": 1, "patch": "bukan kamus"}', '{"no": 77, "patch": {"durasi_s": 3}}',
                    '{"no": 1, "patch": {"durasi_s": -5}}', '{"no": "1", "patch": {"durasi_s": 4}}',
                    '{"no": 1, "patch": {"kind": "naik_trotoar"}}', '[1, 2]', '{"patch": {"durasi_s": 1}}', "{rusak"):
            fh.write(bad + "\n")
        fh.write(json.dumps({"no": b["no"], "patch": {"durasi_s": 8.0}}) + "\n")
    loaded = {r["no"]: r for r in EventStore(tmp_path).latest(10)}
    assert "durasi_s" not in loaded[a["no"]] and loaded[a["no"]]["kind"] == "parkir"
    assert loaded[b["no"]]["durasi_s"] == 8.0


def test_patch_tidak_mengubah_objek_yang_sedang_dipegang_pembaca(tmp_path):
    store = EventStore(tmp_path)
    rec = store.append(_ev(), None)
    held = store.latest(1)[0]                          # mis. API sedang menserialisasi objek ini di utas lain
    store.patch(rec["no"], durasi_s=9)
    assert "durasi_s" not in held, "mengubah kamus di tempat bisa memicu 'dictionary changed size' di utas pembaca"
    assert store.latest(1)[0]["durasi_s"] == 9.0


def test_patch_setelah_folder_kamera_dihapus_tidak_melempar(tmp_path):
    import shutil
    folder = tmp_path / "perangkat"
    store = EventStore(folder)
    rec = store.append(_ev(), None)
    shutil.rmtree(folder)
    assert store.patch(rec["no"], durasi_s=3) is False


def test_prune_melebur_patch_ke_berkas_baru(tmp_path):
    store = EventStore(tmp_path)
    for i in range(3):
        rec = store.append(_ev(track_id=i), None)
        store.patch(rec["no"], durasi_s=10 + i)
    assert store.prune(now() - timedelta(days=1), max_events=2) == 1
    assert len(_lines(tmp_path)) == 2, "setelah dipangkas, patch sudah menyatu ke baris kejadian"
    assert [r["durasi_s"] for r in EventStore(tmp_path).latest(5)] == [12.0, 11.0]


class Pelacak:
    """EventStore palsu: merekam patch yang diminta."""

    def __init__(self):
        self.patches = []

    def patch(self, no, **fields):
        self.patches.append((no, fields))
        return True


def test_durasi_dihitung_dari_mulai_hingga_terakhir_terlihat_setelah_jeda():
    st = Pelacak()
    d = DurationTracker(st, grace=3.0)
    d.begin(1, "parkir", no=5, t_start=10.0, t=20.0)
    d.update({1}, 25.0)
    d.update(set(), 26.0)                              # hilang 1 detik: belum dianggap pergi
    d.update(set(), 27.9)
    assert st.patches == []
    d.update(set(), 28.0)                              # tepat 3 detik sejak terakhir terlihat (25): pergi
    assert st.patches == [(5, {"durasi_s": 15.0})], "durasi = terakhir terlihat (25) - mulai (10), bukan saat dinyatakan pergi"
    d.update(set(), 99.0)
    assert len(st.patches) == 1, "tidak ditulis dua kali"


def test_kontak_trotoar_yang_sempat_putus_tidak_memotong_durasi():
    st = Pelacak()
    d = DurationTracker(st, grace=3.0)
    d.begin(1, "parkir", 5, 0.0, 10.0)
    d.update(set(), 11.0)
    d.update(set(), 12.5)                              # meleset 2,5 detik...
    d.update({1}, 13.0)                                # ...lalu terlihat lagi
    d.update({1}, 20.0)
    d.update(set(), 24.0)
    assert st.patches == [(5, {"durasi_s": 20.0})]


def test_satu_pelacak_dengan_dua_jenis_kejadian_dicatat_terpisah():
    st = Pelacak()
    d = DurationTracker(st)
    d.begin(1, "naik_trotoar", no=1, t_start=2.0, t=3.0)
    d.begin(1, "parkir", no=2, t_start=8.0, t=18.0)    # pengendara naik, lalu berhenti dan parkir
    d.update({1}, 30.0)
    d.finish_all()
    assert sorted(st.patches) == [(1, {"durasi_s": 28.0}), (2, {"durasi_s": 22.0})]


def test_pelacak_lain_tidak_memperpanjang_durasi_dan_finish_all_menutup_semua():
    st = Pelacak()
    d = DurationTracker(st)
    d.begin(1, "parkir", 1, 0.0, 10.0)
    d.begin(2, "parkir", 2, 5.0, 15.0)
    d.update({2}, 20.0)
    d.update({2}, 40.0)                                # 1 sudah pergi sejak 10; 2 masih terlihat
    d.finish_all()
    assert sorted(st.patches) == [(1, {"durasi_s": 10.0}), (2, {"durasi_s": 35.0})]


def test_append_dengan_waktu_tertentu_tercatat_dan_dimuat_ulang(tmp_path):
    when = now().replace(hour=7, minute=15, second=0, microsecond=0) - timedelta(days=3)
    store = EventStore(tmp_path)
    rec = store.append(_ev(), None, when=when)
    assert rec["ts"] == when.isoformat(timespec="seconds")
    assert store.counts_since(when - timedelta(minutes=1))["parkir"] == 1
    assert store.counts_since(when + timedelta(minutes=1))["parkir"] == 0, "waktu yang diberikan dipakai, bukan jam sekarang"
    assert EventStore(tmp_path).pairs()[0][0] == when


# ===================================================================== hapus kejadian
def test_hapus_kejadian_membuang_rekaman_foto_dan_bertahan_setelah_restart(tmp_path):
    s = EventStore(tmp_path / "c")
    for i in range(3):
        s.append(_ev(track_id=i), b"jpg%d" % i)
    s.patch(1, durasi_s=12.0)                              # keterangan susulan pada yang tidak dihapus
    assert s.delete(2) is True
    assert [r["no"] for r in s.records] == [1, 3] and len(s._dts) == 2
    assert s.snapshot_path(2) is None and s.snapshot_path(1) is not None and s.snapshot_path(3) is not None
    assert [json.loads(x)["no"] for x in _lines(tmp_path / "c")] == [1, 3], "berkas ditulis ulang tanpa kejadian itu"
    assert not (tmp_path / "c" / "events.tmp").exists()
    assert s.counts_since(s._dts[0] - timedelta(minutes=1))["parkir"] == 2
    assert [r["no"] for r in s.latest(10)] == [3, 1] and [r["no"] for r in s.after(0)] == [1, 3]
    back = EventStore(tmp_path / "c")                      # restart: tetap hilang, keterangan susulan tetap ada
    assert [r["no"] for r in back.records] == [1, 3] and back.records[0]["durasi_s"] == 12.0


def test_hapus_kejadian_yang_tidak_ada_mengembalikan_false_dan_tidak_mengubah_apa_pun(tmp_path):
    s = EventStore(tmp_path / "c")
    s.append(_ev(), b"jpg")
    before = _lines(tmp_path / "c")
    assert s.delete(99) is False and s.delete(0) is False
    assert s.delete(1) is True and s.delete(1) is False, "dihapus dua kali: yang kedua tidak ada"
    assert before and _lines(tmp_path / "c") == []


def test_nomor_kejadian_yang_dihapus_tidak_dipakai_ulang_bahkan_yang_terakhir(tmp_path):
    s = EventStore(tmp_path / "c")
    for _ in range(3):
        s.append(_ev(), b"jpg")
    assert s.delete(3) is True                             # yang terbaru: nomor 3 jangan dipakai lagi
    assert s.append(_ev(), None)["no"] == 4
    assert s.delete(4) is True
    assert EventStore(tmp_path / "c").append(_ev(), None)["no"] == 5, "setelah restart nomor tetap maju (cache foto peramban)"


def test_kejadian_yang_dihapus_saat_lamanya_masih_dihitung_tidak_hidup_lagi(tmp_path):
    s = EventStore(tmp_path / "c")
    rec = s.append(_ev(), b"jpg")
    d = DurationTracker(s, grace=3.0)
    d.begin(1, "parkir", rec["no"], t_start=0.0, t=0.0)
    d.update({1}, 4.0)
    assert s.delete(rec["no"]) is True
    d.update(set(), 20.0)                                  # lewat masa jeda: durasi hendak ditulis
    d.finish_all()
    assert s.records == [] and _lines(tmp_path / "c") == [], "patch untuk kejadian yang sudah dihapus diabaikan"


def test_hapus_kejadian_saat_folder_perangkat_sudah_dihapus_tidak_melempar(tmp_path):
    import shutil
    s = EventStore(tmp_path / "c")
    s.append(_ev(), b"jpg")
    shutil.rmtree(tmp_path / "c")
    assert s.delete(1) is False and len(s.records) == 1, "gagal menulis: ingatan tidak diubah"


def test_nomor_tidak_mundur_walau_meta_json_tidak_ada_pada_data_lama(tmp_path):
    s = EventStore(tmp_path / "c")
    for _ in range(3):
        s.append(_ev(), b"jpg")
    (tmp_path / "c" / "meta.json").unlink()                # data dari versi lama: belum ada penanda nomor berikutnya
    assert s.delete(3) is True
    assert EventStore(tmp_path / "c").append(_ev(), None)["no"] == 4, "tanpa meta.json nomor 3 akan dipakai ulang"

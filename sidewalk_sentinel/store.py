"""Penyimpanan kejadian per perangkat: events.jsonl, foto bukti, dan lama tiap kejadian."""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path


def now() -> datetime:
    return datetime.now().astimezone()


MAX_DURATION_S = 7 * 24 * 3600
PATCH_KEYS = ("durasi_s",)


def _clean_patch(fields: dict) -> dict:
    """Hanya kolom yang dikenal dan bernilai wajar. Berkas events.jsonl bisa rusak atau diedit tangan."""
    out = {}
    for k in PATCH_KEYS:
        v = fields.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= MAX_DURATION_S:
            out[k] = round(float(v), 1)
    return out


class EventStore:
    """Kejadian satu perangkat: events.jsonl (satu baris per kejadian) dan bukti foto di events/NNN.jpg.

    Kejadian dicatat sekali, saat terkonfirmasi. Hal yang baru diketahui kemudian (berapa lama kendaraan akhirnya
    berada di trotoar) ditambahkan sebagai baris `{"no": N, "patch": {...}}` di akhir berkas, lalu dilebur ke
    rekamannya saat dimuat. Berkas tetap hanya bertambah, jadi mati listrik tidak merusak catatan lama."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.events_dir = folder / "events"
        self.events_dir.mkdir(parents=True, exist_ok=True)
        self._file = folder / "events.jsonl"
        self._meta = folder / "meta.json"
        self._lock = threading.Lock()
        self.records: list[dict] = []
        self._dts: list[datetime] = []
        self._load()

    def _load(self) -> None:
        if self._file.exists():
            index: dict[int, int] = {}
            for line in self._file.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    rec = json.loads(line)
                    if isinstance(rec, dict) and isinstance(rec.get("patch"), dict):
                        i = index.get(rec.get("no"))
                        if i is not None:
                            self.records[i] = {**self.records[i], **_clean_patch(rec["patch"])}
                        continue
                    dt = datetime.fromisoformat(rec["ts"])
                    if not isinstance(rec.get("no"), int):
                        continue
                except (ValueError, KeyError, TypeError, AttributeError):   # baris rusak (mis. mati listrik): lewati
                    continue
                index[rec["no"]] = len(self.records)
                self.records.append(rec)
                self._dts.append(dt)
        nxt = 1 + max((r["no"] for r in self.records), default=0)
        try:
            nxt = max(nxt, int(json.loads(self._meta.read_text(encoding="utf-8"))["next_no"]))
        except (OSError, ValueError, KeyError, TypeError):
            pass
        self.next_no = nxt

    def append(self, ev: dict, snapshot: bytes | None, when: datetime | None = None) -> dict:
        """Catat satu kejadian. `when` (berzona waktu) hanya untuk mengimpor atau menyiapkan data uji; bawaannya sekarang."""
        with self._lock:
            no = self.next_no
            self.next_no += 1
            stamp = when or now()
            rec = {"no": no, "ts": stamp.isoformat(timespec="seconds"), "kind": ev["kind"], "label": ev["label"],
                   "reason": ev["reason"], "track_id": ev["track_id"],
                   "snapshot": f"events/{no}.jpg" if snapshot else None}
            if snapshot:
                (self.events_dir / f"{no}.jpg").write_bytes(snapshot)
            with open(self._file, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self._meta.write_text(json.dumps({"next_no": self.next_no}), encoding="utf-8")
            self.records.append(rec)
            self._dts.append(stamp)
            return rec

    def patch(self, no: int, **fields) -> bool:
        """Tambahkan keterangan baru (mis. durasi_s) ke kejadian yang sudah dicatat. False bila tidak ada yang
        diterapkan. Rekaman diganti dengan salinan, bukan diubah di tempat: pembaca di utas lain (API) masih memegang
        objek lama dan tidak boleh melihat kamus yang berubah ukuran saat sedang diserialisasi."""
        clean = _clean_patch(fields)
        if not clean:
            return False
        with self._lock:
            i = next((j for j in range(len(self.records) - 1, -1, -1) if self.records[j]["no"] == no), None)
            if i is None:
                return False
            try:
                with open(self._file, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"no": no, "patch": clean}) + "\n")
            except OSError:                                    # folder perangkat sudah dihapus
                return False
            self.records[i] = {**self.records[i], **clean}
            return True

    def delete(self, no: int) -> bool:
        """Hapus satu kejadian beserta foto buktinya. False bila nomornya tidak ada.

        Berkas ditulis ulang utuh lewat berkas sementara lalu diganti (seperti `prune`), jadi mati listrik di tengah
        jalan meninggalkan berkas lama yang masih utuh. Nomor berikutnya tidak mundur: `next_no` dicatat dulu di
        meta.json, sehingga kejadian baru tidak memakai ulang nomor (dan foto di cache peramban) milik yang dihapus.
        Kejadian yang lamanya masih dihitung `DurationTracker` aman: `patch` untuk nomor yang sudah tidak ada diabaikan."""
        with self._lock:
            i = next((j for j, r in enumerate(self.records) if r["no"] == no), None)
            if i is None:
                return False
            keep = [j for j in range(len(self.records)) if j != i]
            tmp = self._file.with_suffix(".tmp")
            try:
                self._meta.write_text(json.dumps({"next_no": self.next_no}), encoding="utf-8")
                tmp.write_text("".join(json.dumps(self.records[j], ensure_ascii=False) + "\n" for j in keep),
                               encoding="utf-8")
                tmp.replace(self._file)
            except OSError:                                    # folder perangkat sudah dihapus
                return False
            (self.events_dir / f"{no}.jpg").unlink(missing_ok=True)
            self.records = [self.records[j] for j in keep]
            self._dts = [self._dts[j] for j in keep]
            return True

    def snapshot_path(self, no: int) -> Path | None:
        p = self.events_dir / f"{int(no)}.jpg"
        return p if p.is_file() else None

    def after(self, no: int, limit: int = 200) -> list[dict]:
        with self._lock:
            return [r for r in self.records if r["no"] > no][:limit]

    def latest(self, limit: int = 50, before_no: int | None = None, kind: str | None = None) -> list[dict]:
        with self._lock:
            rows = [r for r in reversed(self.records)
                    if (before_no is None or r["no"] < before_no) and (kind is None or r["kind"] == kind)]
        return rows[:limit]

    def counts_since(self, since: datetime) -> dict:
        out = {"parkir": 0, "naik_trotoar": 0}
        with self._lock:
            for r, dt in zip(self.records, self._dts):
                if dt >= since and r["kind"] in out:
                    out[r["kind"]] += 1
        return out

    @property
    def empty(self) -> bool:
        with self._lock:
            return not self.records

    @property
    def seq(self) -> int:
        with self._lock:
            return self.records[-1]["no"] if self.records else 0

    def pairs(self) -> list[tuple[datetime, dict]]:
        with self._lock:
            return list(zip(self._dts, self.records))

    def prune(self, cutoff: datetime, max_events: int) -> int:
        """Hapus kejadian lebih tua dari `cutoff` dan sisakan paling banyak `max_events` terbaru."""
        with self._lock:
            keep = [i for i, d in enumerate(self._dts) if d >= cutoff][-max_events:]
            if len(keep) == len(self.records):
                return 0
            keep_set = set(keep)
            for i, r in enumerate(self.records):
                if i not in keep_set:
                    (self.events_dir / f"{r['no']}.jpg").unlink(missing_ok=True)
            tmp = self._file.with_suffix(".tmp")
            tmp.write_text("".join(json.dumps(self.records[i], ensure_ascii=False) + "\n" for i in keep),
                           encoding="utf-8")
            tmp.replace(self._file)
            removed = len(self.records) - len(keep)
            self.records = [self.records[i] for i in keep]
            self._dts = [self._dts[i] for i in keep]
            return removed



# ================================================================ lama kejadian
GRACE_S = 3.0           # kendaraan dianggap sudah pergi bila tidak terlihat di trotoar selama ini (waktu mesin)


class DurationTracker:
    """Mencatat berapa lama tiap kejadian berlangsung. Kejadian dibuka saat terkonfirmasi; selama pelacaknya masih
    terlihat di zona trotoar, waktu terakhir terlihat diperbarui. Lepas dari zona lebih dari GRACE_S (atau sesi
    berakhir), durasi = terakhir terlihat - mulai kondisi terpenuhi, lalu ditulis ke kejadian.

    Jeda GRACE_S mencegah satu-dua frame yang meleset (kontak dengan trotoar sempat di bawah ambang) memotong
    durasi kendaraan yang sebenarnya masih ada. Kuncinya (track, jenis): satu pelacak bisa punya dua kejadian,
    misalnya pengendara naik ke trotoar lalu berhenti di sana."""

    def __init__(self, store: EventStore, grace: float = GRACE_S):
        self.store, self.grace = store, grace
        self._open: dict[tuple[int, str], list] = {}      # (track, jenis) -> [no, mulai, terakhir terlihat]

    def begin(self, track_id: int, kind: str, no: int, t_start: float, t: float) -> None:
        self._open[(track_id, kind)] = [no, t_start, t]

    def update(self, present: set[int], t: float) -> None:
        for key in list(self._open):
            o = self._open[key]
            if key[0] in present:
                o[2] = t
            elif t - o[2] >= self.grace:
                self._finish(key)

    def finish_all(self) -> None:
        for key in list(self._open):
            self._finish(key)

    def _finish(self, key: tuple[int, str]) -> None:
        no, t0, last = self._open.pop(key)
        self.store.patch(no, durasi_s=max(0.0, last - t0))

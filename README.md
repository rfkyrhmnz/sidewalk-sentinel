# Sidewalk Sentinel

Pemantau trotoar waktu nyata yang berjalan seperti kamera CCTV, tetapi memakai kamera perangkat yang membukanya.
Buka aplikasi di laptop dan kamera laptop menjadi CCTV-nya. Buka di HP dan kamera HP yang memantau. Tidak ada
alamat kamera, tidak ada berkas video, dan tidak ada mode contoh: begitu halaman terbuka, kamera menyala dan
pemantauan dimulai.

Yang dideteksi hanya **kendaraan** (mobil, motor, bus, truk, sepeda) dan **trotoar**. Tangan, orang, dan benda lain
tidak pernah dihitung, karena model mengenali kendaraan menurut kelasnya, bukan sekadar "ada yang berubah di gambar".

- **Parkir di trotoar**: kendaraan diam di atas area trotoar selama batas waktu tertentu (bawaan 10 detik).
- **Naik trotoar**: kendaraan bergerak yang masuk ke area trotoar dari luar (dari jalan).

Setiap kejadian disimpan dengan foto bukti beranotasi, waktu, jenis kendaraan, dan lamanya berada di trotoar.
Semuanya diringkas pada halaman **Laporan**.

## Menjalankan

Butuh Python 3.10 atau lebih baru. Di Windows (terminal PowerShell di VS Code):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

`pip install` memasang `ultralytics` (YOLO) beserta PyTorch, jadi unduhannya besar. Pada **jalan pertama**, model
kendaraan `yolo11n.pt` diunduh sekali oleh ultralytics dan butuh internet; sesudahnya tersimpan di komputer. Selama model
dimuat, layar pantau berstatus **Memuat model**, lalu pemantauan mulai sendiri.

Browser terbuka di `http://localhost:8000/`. Izinkan kamera. Zona trotoar diusulkan otomatis dari gambar kamera
(lihat di bawah); periksa, seret titik bila belum pas, lalu tekan **Mulai pantau**.

### Memakai HP sebagai kamera

```powershell
python main.py --https
```

Terminal mencetak tautan `https://<IP-laptop>:8000/?k=...`. Buka tautan itu di HP yang tersambung ke Wi-Fi yang sama
dengan laptop. Kunci pada tautan berlaku selama server berjalan; permintaan tanpa kunci ditolak.

Browser hanya mengizinkan kamera pada HTTPS atau `localhost`, sehingga alamat Wi-Fi biasa seperti
`http://192.168.1.23:8000` tidak bisa dipakai. Sertifikat dibuat otomatis dan ditandatangani sendiri
(tersimpan di `data/tls/`, memuat IP Wi-Fi saat itu, dibuat ulang bila IP berubah), jadi browser memberi
peringatan satu kali:

- Chrome: **Lanjutan**, lalu **Lanjutkan ke situs**.
- Safari: **Tampilkan Detail**, lalu **buka situs web ini**.

Sesudah itu izinkan kamera, tambahkan ke layar utama bila perlu, dan taruh HP di dudukan.

### Model sendiri

```powershell
python main.py --weights yolo11s.pt                      # model kendaraan yang lebih besar: lebih akurat, lebih lambat
python main.py --weights runs\detect\train\weights\best.pt   # hasil fine-tune sendiri (harus punya kelas kendaraan COCO)
python main.py --sidewalk-weights models\trotoar.pt      # model segmentasi trotoar untuk usulan zona
python main.py --device 0                                # GPU pertama (bawaan: ditentukan ultralytics)
```

## Cara memakai

1. **Pantau**: kamera menyala sendiri. Model trotoar mengusulkan zona dari gambar; **Usulkan otomatis** meminta
   usulan ulang, misalnya setelah kamera digeser. Seret titik kuning sampai garis menutupi trotoar, lalu tekan
   **Mulai pantau**. Bila model trotoar tidak ada atau trotoar tidak terlihat, tandai zona sendiri.
   Di HP, kamera belakang dipakai lebih dulu dan **Balik kamera** menukar depan dan belakang.
2. Status langsung **Memantau**. Kendaraan yang parkir atau naik trotoar diberi kotak berlabel di gambar,
   dihitung pada meter, dan masuk ke **Kejadian**. Pemberitahuan muncul di semua halaman, disertai getar di HP.
3. **Laporan** merangkum hari ini, 7 hari, atau 30 hari: total kejadian per jenis, lama di trotoar (rata-rata,
   terlama, tersingkat, total), grafik per jam dan per hari, bukti foto terbaru, unduhan CSV, dan cetak ke PDF.
4. **Setelan**: nama perangkat, lama diam sampai dianggap parkir, kepekaan (ambang keyakinan YOLO), ukuran minimum
   kendaraan, dan status model.

Bila halaman dimuat ulang atau dibuka lagi, perangkat yang sama melanjutkan sendiri. Satu perangkat hanya bisa
mengirim dari satu tab; tab kedua menawarkan untuk mengambil alih.

## Cara kerja

```
kamera perangkat -> browser (JPEG ~5 fps, lebar 640 px) -> server -> YOLO -> pelacak -> keputusan -> kejadian -> laporan
                                                                       zona trotoar ^
```

1. **Deteksi kendaraan (YOLO)**: model COCO dijalankan dengan filter kelas `bicycle`, `motorcycle`, `car`, `bus`,
   `truck`. Kelas lain (orang, ponsel, anjing, kursi, dan seterusnya) tidak diminta dari model dan dibuang lagi saat
   hasilnya dibaca. Kotak yang luasnya di bawah ukuran minimum dibuang.
2. **Zona trotoar**: poligon yang disimpan per perangkat. Usulannya berasal dari model segmentasi trotoar
   (`models/trotoar.pt`, YOLO11n-seg satu kelas `sidewalk`): mask komponen terbesar disederhanakan menjadi paling banyak
   10 titik. Model ini hanya dipakai saat zona ditandai, bukan di tiap frame, dan hasilnya selalu bisa diubah.
3. **Pelacak**: kotak dicocokkan antar-frame menurut jarak pusat terdekat. Kotak diperhalus (rata-rata bergerak
   eksponensial) supaya getar kotak YOLO beberapa piksel tidak terbaca sebagai gerak, dan jejak bertahan sampai 2 detik
   tanpa deteksi supaya satu frame yang terlewat tidak mengulang hitungan diam.
4. **Keputusan**: bagian bawah kotak (tempat roda menyentuh tanah) harus berada di zona minimal 50 persen.
   Diam sejak `park_seconds` menghasilkan **parkir**. Bergerak dan masuk dari luar zona selama minimal 0,6 detik
   menghasilkan **naik trotoar** (1,5 detik bila muncul langsung di dalam zona). Satu jenis kejadian dicatat sekali
   per kendaraan.

Semua ambang ada di `sidewalk_sentinel/config.py` sebagai pecahan frame atau detik, sehingga tidak bergantung pada
resolusi kamera. Laju inferensi dibatasi `target_fps` (bawaan 8) agar CPU tidak kewalahan.

**Arti "lama di trotoar".** Dihitung sejak syarat kejadian terpenuhi sampai kendaraan terakhir terlihat di zona.
Kendaraan dianggap pergi bila tidak terlihat selama 3 detik. Lama ditulis sebagai baris tambahan pada
`events.jsonl` (berkas hanya bertambah, aman dari mati listrik). Kejadian yang kendaraannya belum pergi belum punya
lama, dan laporan menyebut berapa kejadian yang terukur.

## Lisensi model

`ultralytics` berlisensi **AGPL-3.0**. Untuk tugas kuliah dan penelitian pribadi ini tidak jadi masalah. Bila aplikasi
ini dibagikan atau dilayankan ke orang lain, AGPL mewajibkan kode sumbernya (termasuk perubahan) tersedia dengan
lisensi yang sama, atau memakai lisensi Enterprise dari Ultralytics. Bobot hasil latihan dengan ultralytics, termasuk
`models/trotoar.pt`, ikut berada di bawah ketentuan itu. Ini ringkasan teknis, bukan nasihat hukum.

## Batasan

- **YOLO sungguhan belum diuji end-to-end di lingkungan pengembangan.** Lingkungan itu tidak punya PyTorch, jadi
  seluruh tes memakai detektor tiruan berbentuk sama. Yang teruji: pembacaan keluaran `Results`, penyaring kelas,
  konversi mask ke poligon, mesin, kamera, server, dan antarmuka. Yang belum: kecepatan, ketepatan kotak, dan perilaku
  model asli pada trotoar nyata. Jalan pertama di komputer Anda adalah ujian sesungguhnya.
- **Model trotoar bawaan masih lemah.** `models/trotoar.pt` hasil latihan 3 epoch dari YOLO11n-seg; pada data validasi
  saat itu mAP50 mask sekitar 0,56 dan recall sekitar 0,49, dan nilainya masih naik di epoch terakhir. Anggap
  usulan zona sebagai titik awal yang harus diperiksa, terutama bila sudut kamera berbeda dari data latih. Latih lebih
  lama dan tambah data trotoar dari sudut kamera Anda untuk hasil yang lebih baik.
- **Belum ada pengukuran pada rekaman trotoar sungguhan.** Angka presisi dan recall belum ada, dan parameter peredam
  getar (`smooth`, `track_max_missed_s`) belum disetel pada keluaran YOLO asli. Langkah yang menentukan: rekam
  trotoar nyata, beri label kejadian secara manual, lalu hitung presisi, recall, dan galat waktu.
- **Model kecil (`yolo11n`) bisa melewatkan kendaraan yang jauh, kecil, atau gelap**, dan kecepatannya di CPU laptop
  belum diukur. Bila akurasi kurang, coba `--weights yolo11s.pt`; bila terlalu lambat, turunkan `target_fps`
  atau pakai GPU (`--device 0`).
- **Sepeda ikut dihitung sebagai kendaraan.** Bila hanya kendaraan bermotor yang dimaksud, hapus `bicycle` dari
  `VEHICLES` di `sidewalk_sentinel/detector.py`. Pengendara motor terdeteksi sebagai orang dan motor sekaligus; yang
  dihitung hanya kotak motornya.
- **Kamera harus diam.** Gerak kamera terbaca sebagai gerak kendaraan. Setelah perangkat digeser atau diputar,
  gunakan **Usulkan otomatis** atau **Atur zona** lagi, karena zona disimpan relatif terhadap gambar.
- **Kamera hidup selama halaman terbuka di layar.** Web tidak bisa menjaga kamera menyala saat HP terkunci atau
  aplikasi pindah ke latar. Aplikasi meminta layar tetap menyala, tetapi HP sebaiknya dicolok ke pengisi daya.
- Satu kendaraan yang jejaknya terputus lebih dari 2 detik lalu terdeteksi lagi mendapat ID baru dan bisa tercatat
  dua kali.
- Jalur HTTPS baru diuji pada Chrome di Linux dengan kamera tiruan, belum pada HP sungguhan. Pemberitahuan hanya
  muncul selama halaman terbuka; notifikasi saat aplikasi tertutup belum ada.

## Antarmuka server

Aplikasi web dan API dilayani pustaka standar Python. Titik akhir utama:

| Titik akhir | Fungsi |
|---|---|
| `GET /api/info` | versi, nilai bawaan, dan status model (`state`: belum, memuat, siap, galat) |
| `POST /api/detector/retry` | memuat ulang model kendaraan setelah galat |
| `POST /api/device` | mendaftarkan perangkat (kunci acak dari browser) atau mengambil kembali kameranya |
| `POST /api/cams/<id>/frame` | satu frame JPEG; dijawab status, kotak deteksi, dan hitungan hari ini |
| `POST /api/cams/<id>/propose-zone` | usulan poligon trotoar dari gambar terbaru perangkat |
| `GET/POST/DELETE /api/cams/<id>` | informasi, setelan, dan penghapusan data perangkat |
| `GET /api/cams/<id>/events` | kejadian perangkat (`?after=<no>`) |
| `GET /api/events` | semua kejadian (`kind`, `limit`, `offset`) |
| `GET /api/events.csv` | unduhan CSV, kolom `durasi_detik` ikut |
| `GET /api/report` | laporan (`periode=hari_ini\|7_hari\|30_hari`, `kamera=<id>` opsional) |

Server menolak frame yang bukan JPEG, lebih dari 8 megapiksel, atau berukuran di atas 3 MB, tanpa mendekodenya lebih
dulu. Sel CSV yang berawalan `=`, `+`, `-`, atau `@` diberi tanda petik agar tidak dijalankan sebagai rumus oleh
Excel, dan semua teks dari pengguna ditampilkan sebagai teks, bukan HTML.

Opsi baris perintah:

```powershell
python main.py [--host H] [--port P] [--https] [--lan] [--data DIR] [--no-open] [--retention-days 30]
               [--weights W] [--sidewalk-weights W] [--device D]
```

`--https` otomatis ikut `--lan`. Kejadian yang lebih tua dari `--retention-days` dan yang melebihi 5000 per perangkat
dihapus bersama fotonya.

## Struktur

```
main.py                     titik masuk
models/trotoar.pt           model segmentasi trotoar (YOLO11n-seg) untuk usulan zona
sidewalk_sentinel/          detector (YOLO kendaraan, model trotoar, mask ke poligon); engine (kotak -> pelacak ->
                            keputusan); config, features, tracker, decision, render; cameras (kamera perangkat,
                            manajer); store (kejadian, lama kejadian); report (laporan); server; tls (sertifikat
                            untuk --https); util
web/                        aplikasi web (PWA): index.html, app.css, app.js (kerangka dan navigasi), device.js (kamera),
                            pantau.js (layar pantau), overlay.js (zona dan kotak deteksi), views.js (Kejadian, Laporan,
                            Setelan), laporan.js (grafik dan ringkasan), api.js, core.js, sw.js, manifest, ikon
tools/make_icons.py         membuat ikon PNG dari desain SVG
tests/                      pytest
```

## Pengujian

```powershell
pip install -r requirements-dev.txt
python -m pytest -q
```

Tes tidak pernah memuat atau mengunduh model YOLO: `conftest.py` membuat `ultralytics` tidak bisa diimpor selama tes,
dan detektor diganti tiruan (`tests/support.py`).

- `test_detector.py`: penyaring kelas (tangan, orang, ponsel, dan benda lain dibuang), konversi mask ke poligon, status
  pemuatan model (memuat, galat, coba lagi), dan usulan zona dari keluaran model trotoar tiruan.
- `test_engine.py`: mesin dengan detektor berskrip: parkir, naik trotoar, kendaraan di luar zona, getar kotak, deteksi
  yang terlewat sebentar, kendaraan yang pergi, ukuran minimum, dan pengaturan yang diteruskan ke detektor.
- `test_cameras.py` dan `test_store.py`: kamera perangkat, pendaftaran, pemulihan setelah restart (termasuk setelan
  versi lama), validasi frame, status model, usulan zona, penyimpanan kejadian, dan pelacak lama kejadian.
- `test_server.py` dan `test_tls.py`: API, kunci LAN, keamanan masukan, sertifikat HTTPS, dan cookie `Secure`.
- `test_laporan.py`: agregasi laporan dengan data buatan (batas tengah malam, zona waktu, jam puncak, lama yang belum
  terukur, keadaan kosong).
- `test_ui_pantau.py`: antarmuka di Chrome lewat Playwright dengan kamera tiruan (kanvas yang menggambar jalan dan
  kendaraan): kamera menyala sendiri, usulan zona otomatis, alur dari zona sampai parkir terdeteksi dan lamanya masuk
  Laporan, model memuat atau galat, izin kamera ditolak, konteks tidak aman, tab kedua, jalur HTTPS, grafik, tata letak
  HP dan desktop, serta hasil cetak PDF. Tes ini dilewati otomatis bila Playwright atau Chrome tidak terpasang.

## Privasi

Gambar kamera dan foto bukti hanya diproses dan disimpan di folder `data/` pada komputer yang menjalankan server;
aplikasi ini tidak mengirimnya ke layanan lain. Dua hal di luar kendali aplikasi: ultralytics mengunduh bobot model
pada jalan pertama, dan secara bawaan (`sync=True`) mengirim statistik pemakaian anonim ke Ultralytics (bukan gambar).
Matikan statistik itu dengan `yolo settings sync=False` di terminal yang memakai lingkungan Python proyek ini.
Foto bukti memuat kendaraan dan orang di sekitarnya, jadi atur `--retention-days` sesuai kebutuhan dan jangan
arahkan kamera ke area yang tidak boleh direkam. Tanpa `--lan`, server hanya menerima koneksi dari komputer itu
sendiri. Folder `data/` dari versi lama aplikasi tidak dipakai lagi dan aman dihapus.

## Riwayat repositori

Repositori dibersihkan pada 25 November 2026. Riwayat git tidak dihapus.

- Isi sebelum dibersihkan: `git checkout c4ecf80`
- Isi sebelum reset 24 November: `git checkout 05542b4`
- Versi dengan deteksi pengurangan latar (tanpa YOLO): `git checkout 1d6d25c`

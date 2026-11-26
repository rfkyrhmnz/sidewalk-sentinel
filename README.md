# Sidewalk Sentinel

Pemantau trotoar waktu nyata yang berjalan seperti kamera CCTV, tetapi memakai kamera perangkat yang membukanya.
Buka aplikasi di laptop dan kamera laptop menjadi CCTV-nya. Buka di HP dan kamera HP yang memantau. Tidak ada
alamat kamera, tidak ada berkas video, dan tidak ada mode contoh: begitu halaman terbuka, kamera menyala dan
pemantauan dimulai.

Yang dideteksi pada area trotoar:

- **Parkir di trotoar**: kendaraan diam di atas area trotoar selama batas waktu tertentu (bawaan 10 detik).
- **Naik trotoar**: kendaraan bergerak yang masuk ke area trotoar dari luar (dari jalan).

Setiap kejadian disimpan dengan foto bukti beranotasi, waktu, dan lamanya kendaraan berada di trotoar. Semuanya
diringkas pada halaman **Laporan**.

## Menjalankan

Butuh Python 3.10 atau lebih baru (diuji pada 3.13). Di Windows (terminal PowerShell di VS Code):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

Browser terbuka di `http://localhost:8000/`. Izinkan kamera, tandai area trotoar pada gambar, lalu biarkan.

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

## Cara memakai

1. **Pantau**: kamera menyala sendiri. Seret titik kuning sampai garis menutupi trotoar, lalu tekan **Mulai pantau**.
   Di HP, kamera belakang dipakai lebih dulu dan **Ganti kamera** menukar depan dan belakang.
2. Aplikasi **mempelajari latar sendiri** selama sekitar 12 detik (median dari beberapa cuplikan berjarak waktu), jadi
   kendaraan yang kebetulan lewat tidak ikut tersimpan sebagai latar. Jaga agar perangkat tidak bergerak.
3. Status berubah menjadi **Memantau**. Kendaraan yang parkir atau naik trotoar diberi kotak berlabel di gambar,
   dihitung pada meter, dan masuk ke **Kejadian**. Pemberitahuan muncul di semua halaman, disertai getar di HP.
4. **Laporan** merangkum hari ini, 7 hari, atau 30 hari: total kejadian per jenis, lama di trotoar (rata-rata,
   terlama, tersingkat, total), grafik per jam dan per hari, bukti foto terbaru, unduhan CSV, dan cetak ke PDF.
5. **Setelan**: nama perangkat, lama diam sampai dianggap parkir, kepekaan, ukuran minimum kendaraan, dan
   pengabaian pejalan kaki.

Bila halaman dimuat ulang atau dibuka lagi, perangkat yang sama melanjutkan sendiri tanpa belajar ulang. Satu
perangkat hanya bisa mengirim dari satu tab; tab kedua menawarkan untuk mengambil alih.

## Cara kerja deteksi

Seluruhnya pengolahan citra klasik dengan NumPy dan OpenCV, tanpa model pembelajaran mesin. Tiap tahap bisa diuji
dan disetel sendiri-sendiri.

```
kamera perangkat -> browser (JPEG ~5 fps, lebar 640 px) -> server -> mesin deteksi -> kejadian -> laporan
```

1. **Praproses**: Gaussian untuk meredam derau, koreksi gamma, CLAHE, dan filter homomorfik (log, FFT, penekanan
   frekuensi rendah, invers FFT) untuk meredam gradien cahaya yang pelan.
2. **Segmentasi**: selisih terhadap citra latar yang dipelajari, ambang Otsu yang dijepit, dan penyaringan per area
   pada zona trotoar. Bayangan dibuang lewat ruang warna CIELAB dan HSV (kecerahan turun, warna tetap).
3. **Morfologi**: opening untuk membuang bintik, closing untuk menyambung kendaraan yang maskernya terbelah, dan
   pengisian lubang.
4. **Deskripsi objek**: kontur, luas, keliling, centroid, rasio aspek, extent, solidity, dan momen Hu; pelacak centroid
   memberi ID dan kecepatan pada tiap kendaraan.
5. **Keputusan**: bagian bawah kotak objek harus menyentuh zona minimal 50 persen. Diam sejak `park_seconds`
   menghasilkan **parkir**. Bergerak dan masuk dari luar zona selama minimal 0,6 detik menghasilkan **naik trotoar**
   (1,5 detik bila muncul langsung di dalam zona). Satu jenis kejadian dicatat sekali per kendaraan.

Latar diperbarui pelan-pelan hanya pada piksel yang bukan objek, supaya kendaraan yang parkir tidak terserap ke
latar. Bila cahaya berubah drastis atau perangkat bergeser, penjaga adegan mengulang pembelajaran latar hanya jika dua
syarat terpenuhi bersamaan selama 3 detik: gambar berbeda di mana-mana dan zona trotoar dibanjiri foreground.

Semua ambang ada di `sidewalk_sentinel/config.py` sebagai pecahan frame atau detik, sehingga tidak bergantung pada
resolusi kamera.

**Arti "lama di trotoar".** Dihitung sejak syarat kejadian terpenuhi sampai kendaraan terakhir terlihat di zona.
Kendaraan dianggap pergi bila tidak terlihat selama 3 detik. Lama ditulis sebagai baris tambahan pada
`events.jsonl` (berkas hanya bertambah, aman dari mati listrik). Kejadian yang kendaraannya belum pergi belum punya
lama, dan laporan menyebut berapa kejadian yang terukur.

## Batasan

- **Validasi baru memakai adegan sintetis dan kamera tiruan.** Belum ada pengukuran pada rekaman trotoar sungguhan,
  jadi angka presisi dan recall belum ada. Langkah yang menentukan: rekam trotoar nyata, beri label kejadian secara
  manual, lalu hitung presisi, recall, dan galat waktu.
- **Tidak ada jenis kendaraan dan kecepatan.** Pipeline klasik memisahkan kendaraan dari latar, bayangan, dan
  pejalan kaki, tetapi tidak mengenali jenisnya. Kecepatan dalam km/jam butuh kalibrasi jarak untuk tiap kamera.
- **Kamera harus diam.** Gerak kamera terbaca sebagai gerak kendaraan. Pergeseran besar memicu pembelajaran latar
  ulang; pakai **Pelajari ulang latar** setelah perangkat disentuh. Memutar HP mengubah rasio gambar, jadi tandai
  zona lagi lewat **Atur zona**.
- **Kamera hidup selama halaman terbuka di layar.** Web tidak bisa menjaga kamera menyala saat HP terkunci atau
  aplikasi pindah ke latar. Aplikasi meminta layar tetap menyala, tetapi HP sebaiknya dicolok ke pengisi daya.
- Kendaraan yang warnanya hampir sama dengan latar pada gambar sangat gelap atau hampir monokrom bisa lolos.
  Kendaraan gelap di bawah bayangan pekat bisa terbaca sebagai bayangan. Pengendara yang tampak dari depan bisa mirip
  pejalan kaki dan tersaring.
- Kendaraan yang diam sepanjang jendela belajar latar menjadi bagian latar. Saat ia pergi, bekasnya bisa terbaca
  sebagai objek sampai latar dipelajari ulang.
- Satu kendaraan yang pelacaknya terputus lalu dilacak ulang dengan ID baru bisa tercatat dua kali.
- Jalur HTTPS baru diuji pada Chrome di Linux dengan kamera tiruan, belum pada HP sungguhan. Pemberitahuan hanya
  muncul selama halaman terbuka; notifikasi saat aplikasi tertutup belum ada.

## Antarmuka server

Aplikasi web dan API dilayani pustaka standar Python. Titik akhir utama:

| Titik akhir | Fungsi |
|---|---|
| `POST /api/device` | mendaftarkan perangkat (kunci acak dari browser) atau mengambil kembali kameranya |
| `POST /api/cams/<id>/frame` | satu frame JPEG; dijawab status, kotak deteksi, dan hitungan hari ini |
| `GET/POST/DELETE /api/cams/<id>` | informasi, setelan, dan penghapusan data perangkat |
| `POST /api/cams/<id>/recalibrate` | mengulang pembelajaran latar |
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
```

`--https` otomatis ikut `--lan`. Kejadian yang lebih tua dari `--retention-days` dan yang melebihi 5000 per perangkat
dihapus bersama fotonya.

## Struktur

```
main.py                     titik masuk
sidewalk_sentinel/          config, preprocess, segmentation, morphology, features, tracker, decision, render, engine
                            (mesin deteksi); monitor (belajar latar otomatis dan penjaga perubahan adegan);
                            cameras (kamera perangkat, manajer); store (kejadian, lama kejadian); report (laporan);
                            server; tls (sertifikat untuk --https); util
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

- `test_engine.py` dan `test_monitor.py`: mesin deteksi pada adegan sintetis (termasuk pejalan kaki, bayangan, dan
  gradien cahaya), pengaruh tiap tahap, serta belajar latar otomatis dan penjaga perubahan adegan.
- `test_cameras.py` dan `test_store.py`: kamera perangkat, pendaftaran, pemulihan setelah restart, validasi frame,
  penyimpanan kejadian, dan pelacak lama kejadian.
- `test_server.py` dan `test_tls.py`: API, kunci LAN, keamanan masukan, sertifikat HTTPS, dan cookie `Secure`.
- `test_laporan.py`: agregasi laporan dengan data buatan (batas tengah malam, zona waktu, jam puncak, lama yang belum
  terukur, keadaan kosong).
- `test_ui_pantau.py`: antarmuka di Chrome lewat Playwright dengan kamera tiruan (kanvas yang menggambar jalan dan
  kendaraan): kamera menyala sendiri, alur dari zona sampai parkir terdeteksi dan lamanya masuk Laporan, izin kamera
  ditolak, konteks tidak aman, tab kedua, jalur HTTPS, grafik, tata letak HP dan desktop, serta hasil cetak PDF. Tes ini
  dilewati otomatis bila Playwright atau Chrome tidak terpasang.

## Privasi

Foto bukti hanya disimpan di folder `data/` pada komputer yang menjalankan server dan tidak dikirim ke layanan lain.
Foto memuat kendaraan dan orang di sekitarnya, jadi atur `--retention-days` sesuai kebutuhan dan jangan arahkan kamera
ke area yang tidak boleh direkam. Tanpa `--lan`, server hanya menerima koneksi dari komputer itu sendiri. Folder
`data/` dari versi lama aplikasi tidak dipakai lagi dan aman dihapus.

## Riwayat repositori

Repositori dibersihkan pada 25 November 2026. Riwayat git tidak dihapus.

- Isi sebelum dibersihkan: `git checkout c4ecf80`
- Isi sebelum reset 24 November: `git checkout 05542b4`

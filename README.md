# Sidewalk Sentinel

Aplikasi web (bisa dipasang di layar utama HP) yang mendeteksi dua hal pada video trotoar:

1. **Kendaraan parkir di trotoar**: kendaraan diam di atas area trotoar selama sekian detik (bawaan 10 detik).
2. **Pengendara naik ke trotoar**: kendaraan bergerak yang masuk ke area trotoar dari luar (jalan).

Dibuat untuk tugas akhir mata kuliah **Citra Komputer** (S1 Pendidikan Teknik Informatika, Universitas Negeri Malang).
Seluruh deteksi memakai pengolahan citra klasik (NumPy dan OpenCV) tanpa model pembelajaran mesin, sehingga
setiap tahap bisa ditunjuk ke materi kuliah dan diuji satu per satu.

## Menjalankan

Butuh Python 3.10 atau lebih baru (diuji pada 3.13). Di Windows (VS Code, terminal PowerShell):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

Browser terbuka di `http://localhost:8000/`. Folder `data/` menyimpan unggahan dan hasil; aman dihapus kapan saja.
`ffmpeg` bersifat opsional: bila ada, video hasil diberi anotasi dalam H.264 yang bisa diputar di HP; bila tidak, hasil
tetap lengkap (hitungan, foto bukti, CSV, JSON) tanpa video beranotasi.

Coba cepat tanpa video sendiri: tekan **Coba dengan video demo** di beranda. Hasil yang benar untuk adegan itu
adalah 1 parkir dan 2 naik trotoar.

## Membuka dari HP

```powershell
python main.py --lan
```

Terminal mencetak tautan `http://<IP-laptop>:8000/?k=...`. HP dan laptop harus berada di Wi-Fi yang sama. Tautan itu
memuat kunci acak yang berlaku untuk satu kali jalan server; tanpa kunci, server menolak permintaan.

- **Rekam video** dan **Pilih video** bekerja lewat HTTP biasa: HP membuka kamera bawaannya, hasil rekaman diunggah.
- **Kamera langsung** memakai `getUserMedia`, yang hanya diizinkan browser pada HTTPS atau `localhost`. Di HP lewat
  `http://IP-laptop` fitur ini menampilkan penjelasan, bukan galat. Untuk mengaktifkannya, buat terowongan HTTPS ke
  server (misalnya `cloudflared tunnel --url http://localhost:8000`) dan buka alamat HTTPS-nya dengan tambahan `?k=<kunci>`.
  Cara ini belum diuji pada perangkat sungguhan; ingat bahwa terowongan membuat server terjangkau dari internet.
- Manifest dan ikon PWA sudah disediakan. Lewat HTTP biasa, menu **Tambahkan ke layar utama** hanya membuat pintasan;
  pemasangan PWA penuh (service worker) butuh HTTPS.

## Alur penggunaan

1. **Video**: rekam, pilih berkas, atau pakai demo.
2. **Area**: ketuk tepi trotoar pada satu frame untuk membuat poligon, lalu pilih frame **trotoar kosong** sebagai referensi.
3. **Atur**: lama diam sampai dihitung parkir, kepekaan, ukuran objek minimum, dan saklar tiap tahap pengolahan.
4. **Proses**: pratinjau hidup, penghitung, dan kejadian muncul saat terdeteksi. Tab **Tahapan pipeline** menampilkan
   keluaran tiap langkah (6 panel).
5. **Hasil**: linimasa kejadian, foto bukti, video beranotasi, unduh CSV dan JSON.

## Pemetaan ke enam materi

| Materi | Dipakai untuk | Modul |
|---|---|---|
| 1. Transformasi intensitas dan filter spasial | Gaussian sebelum selisih, penyetaraan gain terhadap referensi, LUT gamma, CLAHE (opsional) | `preprocess.py` |
| 2. Filter domain frekuensi | Filter homomorfik: log, FFT, high-emphasis Gaussian, IFFT, exp, untuk meredam gradien cahaya yang pelan | `preprocess.py` (`homomorphic`) |
| 3. Morfologi citra | Opening (buang bintik), closing (sambung objek terbelah), pengisian lubang pada mask | `morphology.py` |
| 4. Segmentasi citra | Selisih terhadap citra referensi, ambang Otsu yang dijepit, penyaringan per area | `segmentation.py` |
| 5. Representasi dan deskripsi | Kontur; luas, keliling, centroid, rasio aspek, extent, solidity, compactness, momen Hu; pelacakan centroid dan kecepatan | `features.py`, `tracker.py`, `decision.py` |
| 6. Pengolahan citra warna | CIELAB (selisih kroma), HSV (deteksi bayangan: kecerahan turun, warna tetap) | `preprocess.py`, `segmentation.py` |

## Cara kerja deteksi

Dua mekanisme terpisah, lalu satu aturan keputusan:

- **Keberadaan** (untuk parkir): selisih terhadap referensi trotoar kosong menjawab "ada benda di sini?".
  Referensi diperbarui pelan-pelan hanya pada piksel yang bukan objek, supaya kendaraan yang parkir tidak terserap
  ke latar.
- **Gerak** (untuk naik trotoar): perpindahan centroid antar frame, dinormalkan terhadap lebar frame, menjawab
  "diam atau bergerak?".
- **Keputusan**: bagian bawah kotak objek (30 persen) harus menyentuh area trotoar minimal 50 persen. Diam sejak
  `park_seconds` menghasilkan **parkir**. Bergerak dan masuk dari luar area selama minimal 0,6 detik menghasilkan
  **naik trotoar** (1,5 detik bila muncul langsung di dalam area). Kendaraan yang naik lalu parkir menghasilkan kedua
  kejadian; satu jenis kejadian dicatat sekali per kendaraan.

Semua ambang ada di `sidewalk_sentinel/config.py` sebagai pecahan frame atau detik, sehingga tidak bergantung resolusi.
Nilai bawaan dipilih dari eksperimen ablasi di bawah, bukan ditebak.

## Hasil eksperimen

Diuji pada adegan **sintetis** (enam kondisi cahaya, tiga benih acak; lulus bila tepat 1 parkir dan 2 naik trotoar,
termasuk pejalan kaki yang tidak boleh terhitung):

| Konfigurasi | Benar |
|---|---|
| Pipeline lengkap | 18/18 |
| Tanpa Materi 2 (homomorfik) | 12/18 |
| Tanpa Materi 6 (buang bayangan) | 15/18 |
| Tanpa bukti citra asli | 15/18 |
| Tanpa Materi 3 (morfologi) | 0/18 |

Baris morfologi gagal total karena adegan sengaja memuat kendaraan yang maskernya terbelah; itu membuktikan
mekanismenya, bukan seberapa sering hal itu terjadi di lapangan. **Angka ini belum membuktikan akurasi pada rekaman
nyata.** Langkah berikutnya yang menentukan: rekam trotoar sungguhan, beri label kejadian secara manual, lalu
hitung presisi, recall, dan galat waktu.

## Batasan yang perlu diketahui

- Kamera harus diam. Gerak kamera terbaca sebagai gerak kendaraan.
- Referensi harus frame trotoar kosong. Kendaraan yang sudah ada di referensi tidak terdeteksi, dan bila ia pergi,
  bekasnya bisa terbaca sebagai objek.
- Kendaraan gelap di bawah bayangan pekat bisa terbaca sebagai bayangan.
- Pengendara yang tampak dari depan bisa mirip pejalan kaki dan tersaring.
- Kamera langsung di HP butuh HTTPS (lihat di atas).

## Antarmuka baris perintah

```powershell
python main.py serve [--host H --port P --lan --data DIR --no-open]   # bawaan bila tanpa perintah
python main.py analyze video.mp4 --roi "0,0.64;1,0.64;1,0.99;0,0.99" --ref-time 0 --park-seconds 10 --out data/hasil
python main.py analyze video.mp4 --set diff_floor=17 --set min_area_frac=0.004   # timpa parameter Config
python main.py demo --out data/demo [--shadow] [--gradient 0.3]                  # buat video sintetis
```

`analyze` menulis `result.json`, `result.csv`, foto bukti, dan (bila ada ffmpeg) `annotated.mp4`.

## Struktur

```
main.py                     titik masuk (serve, analyze, demo)
sidewalk_sentinel/          engine (config, preprocess, segmentation, morphology, features,
                            tracker, decision, render, engine), synthetic, video, analysis, jobs, server
web/                        PWA: index.html, app.css, app.js, roi.js, live.js, api.js, sw.js, manifest, ikon
tools/make_icons.py         membuat ikon PNG dari desain SVG
tests/                      pytest: engine, server, antarmuka kamera langsung
```

## Pengujian

```powershell
pip install -r requirements-dev.txt
python -m pytest -q
```

`tests/test_engine.py` menguji pipeline pada adegan sintetis, ablasi tiap materi, dan validasi konfigurasi.
`tests/test_server.py` menguji API, Range untuk video, kunci LAN, dan keamanan jalur berkas.
`tests/test_ui_live.py` memakai Playwright dan Chrome untuk mengecek kamera langsung; otomatis dilewati bila
keduanya tidak ada (`pip install playwright` dan Chrome terpasang).

## Privasi

Video dan foto bukti hanya disimpan di folder `data/` pada komputer yang menjalankan server dan tidak dikirim ke
layanan lain. Tanpa `--lan`, server hanya menerima koneksi dari komputer itu sendiri.

## Riwayat repositori

Repositori dibersihkan pada 25 November 2026. Riwayat git tidak dihapus.

- Isi sebelum dibersihkan: `git checkout c4ecf80`
- Isi sebelum reset 24 November: `git checkout 05542b4`

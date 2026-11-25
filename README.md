# Sidewalk Sentinel

Aplikasi web (bisa dipasang di layar utama HP) yang **menjadikan HP atau laptop sebagai kamera CCTV**: kamera
dinyalakan, diarahkan ke trotoar, dan deteksi berjalan langsung serta otomatis. Aplikasi juga bisa membaca kamera CCTV
lain (RTSP, webcam USB) atau menganalisis video yang diunggah. Dua hal yang dideteksi pada trotoar:

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

Coba cepat dengan kamera laptop: di halaman **Pantau** tekan **Jadikan perangkat ini CCTV**, izinkan kamera, tandai
area trotoar, lalu biarkan. Aplikasi mempelajari latar sendiri dan mencatat kendaraan yang parkir atau naik ke trotoar.
(`http://localhost` termasuk konteks aman, jadi di laptop tidak perlu HTTPS.)

Coba cepat tanpa kamera atau video sendiri: di halaman **Pantau** tekan **Coba demo**. Server memutar adegan
sintetis seperti kamera sungguhan, belajar latarnya sendiri, lalu mencatat kejadian. Hasil yang benar untuk adegan itu
adalah 1 parkir dan 2 naik trotoar. Untuk jalur analisis video, buka **Analisis** lalu **Coba dengan video demo**.

## Membuka dari HP

```powershell
python main.py --https
```

`--https` otomatis mengaktifkan `--lan`. Terminal mencetak tautan `https://<IP-laptop>:8000/?k=...`. HP dan laptop harus
berada di Wi-Fi yang sama. Tautan itu memuat kunci acak yang berlaku untuk satu kali jalan server; tanpa kunci, server
menolak permintaan.

**Kenapa HTTPS:** browser hanya mengizinkan kamera (`getUserMedia`) pada HTTPS atau `localhost`. Alamat Wi-Fi seperti
`http://192.168.1.23:8000` tidak termasuk, sehingga kamera HP tidak bisa dibuka tanpa HTTPS.

**Peringatan sertifikat.** Sertifikat dibuat sendiri (ditandatangani sendiri, tersimpan di `data/tls/`, memuat IP Wi-Fi
saat itu, dibuat ulang otomatis bila IP berubah atau hampir habis), sehingga browser memberi peringatan sekali.
Di Chrome: **Lanjutan → Lanjutkan ke situs**. Di Safari: **Tampilkan Detail → buka situs web ini**. Sesudah itu
izinkan kamera. Paket `cryptography` (ada di `requirements.txt`) dipakai untuk membuat sertifikat.

Cara lain tanpa peringatan: terowongan HTTPS ke server (misalnya `cloudflared tunnel --url http://localhost:8000`) dan
buka alamat HTTPS-nya dengan tambahan `?k=<kunci>`. Terowongan membuat server terjangkau dari internet; kunci acak
tetap berlaku, tetapi pertimbangkan risikonya.

- **Pantau dari sumber lain** (RTSP, webcam USB di laptop, rekaman) tidak butuh HTTPS: server yang membaca kamera, HP
  hanya menampilkan hasilnya.
- **Rekam video** dan **Pilih video** (halaman Analisis) bekerja lewat HTTP biasa: HP membuka kamera bawaannya, hasil rekaman diunggah.
- Manifest dan ikon PWA sudah disediakan. Dengan sertifikat yang belum dipercaya, browser menolak mendaftarkan service
  worker, sehingga **Tambahkan ke layar utama** hanya membuat pintasan. Kamera tetap bisa dipakai.
- Tanpa `--https`, tombol kamera perangkat di HP menampilkan penjelasan cara memperbaikinya, bukan galat.

## Mode pantau (kamera CCTV)

### Perangkat ini jadi CCTV

1. **Pantau → Jadikan perangkat ini CCTV.** Izinkan kamera. Kamera belakang dipakai lebih dulu di HP; **Ganti kamera**
   bertukar depan dan belakang.
2. **Tandai area trotoar** pada gambar langsung dari kamera itu. Tidak perlu memilih frame kosong: aplikasi **belajar
   latar sendiri** dengan median piksel dari beberapa cuplikan berjarak waktu (sekitar 22 detik pada kamera langsung),
   sehingga kendaraan yang kebetulan lewat tidak ikut tersimpan.
3. Status berubah menjadi **Memantau**. Kendaraan yang parkir atau naik ke trotoar langsung diberi kotak di gambar
   dan masuk ke **Kejadian** dengan foto bukti. Pita merah di atas layar menandakan perangkat ini sedang jadi CCTV dan
   tetap tampil di semua halaman.

Cara kerjanya: browser mengambil gambar dari `<video>` sekitar 5 kali per detik (lebar 640 px, JPEG) dan mengirimnya ke
`POST /api/cams/<id>/frame`. Server menyimpan hanya frame terbaru, lalu menjalankan mesin pantau yang sama persis dengan
sumber lain. Kotak deteksi digambar di browser dari hasil yang dikembalikan tiap kiriman. Server menolak frame yang
bukan JPEG, lebih dari 8 megapiksel, atau badannya melebihi 3 MB, tanpa mendekodenya lebih dulu.

Bila halaman dimuat ulang atau ditutup lalu dibuka lagi, perangkat yang sama melanjutkan sendiri tanpa belajar ulang
(latar tersimpan di server). Bila tidak ada frame lebih dari 3 detik, status menjadi **Menunggu perangkat**.

### Sumber lain

Server membaca tiap kamera di utas sendiri, tanpa menunggu ada yang membuka halaman. Laptop dan HP hanya penampil.
**Pantau → Tambah kamera → Sumber lain** menerima alamat RTSP dari CCTV atau NVR, tautan http(s) berupa video
(misalnya MJPEG), webcam USB di komputer server (`device:0`), rekaman video yang diunggah (diputar seperti siaran
kamera, bisa diulang terus), dan kamera demo. Langkah belajar latar dan penandaan area sama; belajar latar memakan
sekitar 3 detik untuk rekaman dan demo.

Kejadian masuk ke halaman kamera dan ke **Kejadian** (semua kamera, bisa disaring per jenis, unduh CSV) lengkap dengan
foto bukti beranotasi nama kamera dan jam. Selama halaman terbuka, kejadian baru memunculkan pemberitahuan, getar di HP,
dan lencana pada menu Kejadian.

Status kamera: *Perlu area*, *Menghubungkan*, *Menunggu perangkat*, *Belajar latar*, *Memantau*, *Terputus* (mencoba lagi sendiri dengan
jeda yang membesar sampai 30 detik), *Dijeda*, *Selesai* (rekaman yang tidak diulang; tombol **Putar ulang**).
Di halaman kamera tersedia **Kalibrasi lagi**, **Ubah area**, **Jeda**, pengaturan (lama diam, kepekaan, ukuran objek
minimum, nama, kecepatan putar rekaman), dan **Hapus** (dua ketukan).

**Belajar ulang otomatis.** Bila cahaya berubah drastis (matahari tertutup awan tebal, lampu menyala), engine bisa
buta. Penjaga adegan mengulang belajar latar hanya jika dua syarat terpenuhi bersamaan selama 3 detik: frame berbeda di
mana-mana **dan** zona trotoar dibanjiri foreground (`use_scene_guard`, `scene_change_frac`, `scene_change_seconds`).
Syarat kedua penting: dalam eksperimen, engine ternyata tahan terhadap pergeseran warna sedang, dan belajar ulang pada
saat itu justru menyerap kendaraan yang sedang diam ke latar.

**Keamanan sumber.** API hanya menerima `demo`, unggahan, `browser` (kamera perangkat), `device:0` sampai `device:9`,
`rtsp(s)://`, dan `http(s)://`.
Jalur berkas lokal hanya lewat `--cam` (operator). Sandi pada alamat kamera disamarkan di API, log, dan layar;
`cameras.json` disimpan dengan izin 600 (berlaku di Linux dan macOS; di Windows andalkan izin akun). Batas: 8 kamera dan 12 penonton siaran serentak. Kejadian lebih tua dari
`--retention-days` (bawaan 30) dan di atas 5000 per kamera dihapus bersama fotonya.

Kamera bisa didaftarkan dari baris perintah supaya langsung berjalan saat server hidup (idempoten menurut nama):

```powershell
python main.py --cam "Depan=rtsp://user:sandi@192.168.1.20:554/stream1" --cam "Meja=device:0"
```

Titik akhir utama: `GET/POST /api/cams`, `GET/POST/DELETE /api/cams/<id>`, `POST /api/cams/<id>/recalibrate`,
`POST /api/cams/<id>/frame` (satu frame JPEG dari kamera perangkat; dijawab status, kotak deteksi, dan hitungan hari ini),
`GET /api/cams/<id>/snapshot.jpg` (`?raw=1` tanpa anotasi), `GET /api/cams/<id>/stream.mjpg`,
`GET /api/cams/<id>/events?after=<no>`, `GET /api/events?kind=&limit=&offset=`, dan `GET /api/events.csv`.

## Alur analisis video

Untuk video yang sudah direkam (halaman **Analisis**):

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

- Kamera harus diam. Gerak kamera terbaca sebagai gerak kendaraan, dan pergeseran kamera **tidak** terdeteksi
  otomatis (korelasi fase tidak andal pada tekstur ubin yang berulang); pakai **Kalibrasi lagi** setelah kamera
  disentuh.
- Referensi harus frame trotoar kosong. Pada mode pantau, kendaraan yang diam sepanjang jendela belajar latar menjadi
  bagian latar, dan saat ia pergi, "hantu"-nya bisa terbaca sebagai objek sampai kamera dikalibrasi ulang.
- Gambar hampir monokrom (mode inframerah malam) atau sangat gelap berkontras rendah: kendaraan yang warnanya
  menyerupai latar bisa lolos. Ini teramati pada adegan sintetis; belum diukur pada kamera malam sungguhan. Kendaraan yang sudah ada di referensi tidak terdeteksi, dan bila ia pergi,
  bekasnya bisa terbaca sebagai objek.
- Kendaraan gelap di bawah bayangan pekat bisa terbaca sebagai bayangan.
- Pengendara yang tampak dari depan bisa mirip pejalan kaki dan tersaring.
- **Kamera perangkat hanya hidup selama halaman aplikasi terbuka di layar.** Web tidak bisa menjaga kamera tetap
  menyala saat HP terkunci atau aplikasi berpindah ke latar; aplikasi meminta layar tetap menyala (Wake Lock) selama
  kamera aktif, tetapi HP sebaiknya dicolok ke pengisi daya dan ditaruh di dudukan. Untuk pemantauan 24 jam,
  pakai kamera IP (RTSP) yang dibaca server.
- Satu kamera perangkat sebaiknya dikirim dari satu perangkat saja. Server tidak membedakan pengirim, jadi dua
  perangkat ke kamera yang sama saling menimpa frame.
- Memutar HP (potret ke lanskap) mengubah rasio gambar: aplikasi belajar latar ulang, tetapi area trotoar yang sudah
  ditandai tidak ikut bergeser, jadi tandai ulang lewat **Ubah area**.
- Dengan `--https`, browser memberi peringatan sertifikat sekali (lihat "Membuka dari HP"). Cara ini baru diuji pada
  Chrome di Linux (sertifikat dan alur kamera lewat kamera tiruan), belum pada HP sungguhan.
- Pemberitahuan kejadian hanya muncul selama halaman terbuka. Web Push (notifikasi saat aplikasi tertutup) belum ada.
- **Seluruh validasi memakai adegan sintetis.** Alamat RTSP belum diuji pada kamera sungguhan; jalur sumber URL
  diuji lewat siaran MJPEG http dari kamera lain. Beban CPU untuk banyak kamera belum diukur pada perangkat lemah.

## Antarmuka baris perintah

```powershell
python main.py serve [--host H --port P --lan --data DIR --no-open]   # bawaan bila tanpa perintah
python main.py --cam "Nama=rtsp://..." [--cam "Nama2=device:0"] [--retention-days 30]   # kamera langsung dipantau
python main.py analyze video.mp4 --roi "0,0.64;1,0.64;1,0.99;0,0.99" --ref-time 0 --park-seconds 10 --out data/hasil
python main.py analyze video.mp4 --set diff_floor=17 --set min_area_frac=0.004   # timpa parameter Config
python main.py demo --out data/demo [--shadow] [--gradient 0.3]                  # buat video sintetis
```

`analyze` menulis `result.json`, `result.csv`, foto bukti, dan (bila ada ffmpeg) `annotated.mp4`.

## Struktur

```
main.py                     titik masuk (serve, analyze, demo)
sidewalk_sentinel/          engine (config, preprocess, segmentation, morphology, features,
                            tracker, decision, render, engine), synthetic, video, analysis, jobs, server;
                            monitor (belajar latar otomatis + penjaga perubahan adegan),
                            cameras (sumber, kamera, penyimpanan kejadian, manajer)
web/                        PWA: index.html, app.css, app.js, roi.js, live.js, api.js, sw.js, manifest, ikon
tools/make_icons.py         membuat ikon PNG dari desain SVG
tests/                      pytest: engine, server, monitor, kamera, antarmuka kamera langsung dan pantau
```

## Pengujian

```powershell
pip install -r requirements-dev.txt
python -m pytest -q
```

`tests/test_engine.py` menguji pipeline pada adegan sintetis, ablasi tiap materi, dan validasi konfigurasi.
`tests/test_server.py` menguji API, Range untuk video, kunci LAN, dan keamanan jalur berkas.
`tests/test_monitor.py` menguji belajar latar otomatis dan penjaga perubahan adegan. `tests/test_cameras.py` menguji
validasi sumber (termasuk penolakan sumber berbahaya dan penyamaran sandi), penyimpanan kejadian, siklus kamera
(demo, unggahan, jeda, hapus, pemulihan setelah restart), siaran MJPEG, dan API.
`tests/test_tls.py` menguji sertifikat HTTPS (alamat LAN, pemakaian ulang, pembuatan ulang, izin kunci) dan server TLS
(termasuk koneksi yang diam saat jabat tangan dan cookie `Secure`).
`tests/test_ui_live.py` dan `tests/test_ui_pantau.py` memakai Playwright dan Chrome untuk mengecek kamera langsung
dan seluruh alur Pantau, termasuk perangkat jadi CCTV dengan kamera tiruan (kanvas yang menggambar jalan dan kendaraan),
juga lewat HTTPS; otomatis dilewati bila keduanya tidak ada (`pip install playwright` dan Chrome terpasang).
Seluruh rangkaian butuh sekitar 5 menit karena kamera demo diputar sungguhan.

## Privasi

Video dan foto bukti hanya disimpan di folder `data/` pada komputer yang menjalankan server dan tidak dikirim ke
layanan lain. Foto bukti kamera pantau memuat kendaraan dan orang di sekitarnya; atur `--retention-days` sesuai
kebutuhan dan jangan arahkan kamera ke area yang tidak boleh direkam. Tanpa `--lan`, server hanya menerima koneksi dari komputer itu sendiri.

## Riwayat repositori

Repositori dibersihkan pada 25 November 2026. Riwayat git tidak dihapus.

- Isi sebelum dibersihkan: `git checkout c4ecf80`
- Isi sebelum reset 24 November: `git checkout 05542b4`

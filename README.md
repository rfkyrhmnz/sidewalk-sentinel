# Deteksi Kendaraan Parkir di Trotoar (Segmentasi Citra) — input kamera HP

Program ini menerima **foto/video dari HP (Android/iPhone)** dan menandai kendaraan yang berada di trotoar.

## Cara kerja

```
foto/frame ─► YOLO-seg: mask kendaraan + orang (+ ID lewat ByteTrack pada video)
          └─► mask trotoar: klasik (K-Lab + Mahalanobis + morfologi) | model fine-tune | poligon manual
                        │
        geometri: footprint (titik kontak roda) + cincin tanah di sekitarnya
                        │
        keputusan: di trotoar? ada pengendara? (foto)  →  diam ≥ T detik? (video)
```

Inti geometri: kendaraan di atas trotoar menutupi piksel trotoar, sehingga mask trotoar "bolong" di bawahnya dan
irisan mask penuh ≈ 0. Karena itu dipakai **`contact_ratio`** (footprint ∩ trotoar, untuk mask yang menutupi area di
bawah kendaraan) dan **`ring_ratio`** (porsi trotoar pada tanah di kiri/kanan/depan kendaraan, tahan terhadap lubang).

## Instalasi

```bash
pip install -r requirements.txt      # numpy, opencv-python, pillow, pillow-heif, ultralytics
```
Model default `yolo11n-seg.pt` diunduh otomatis saat pertama dijalankan (perlu internet). Bila `ultralytics` Anda
terbaru, bisa memakai `--weights yolo26n-seg.pt`. Untuk video, ffmpeg dianjurkan (hasil video H.264 agar bisa diputar di HP).

## Alur kerja

```bash
# 1. Bersihkan media HP: HEIC→JPG, orientasi EXIF diterapkan, GPS dihapus, video→H.264 fps konstan
python main.py prepare folder_dari_hp --out data_bersih

# 2. (untuk anotasi) ambil frame dari video, 1 fps
python main.py frames data_bersih/video/rekaman.mp4 --out frames --fps 1

# 3. Analisis foto (berkas atau folder)
python main.py image data_bersih/foto --out hasil --save-masks --debug

# 4. Analisis video (tracking + durasi diam; kompensasi gerak kamera aktif)
python main.py video data_bersih/video/rekaman.mp4 --out hasil --min-dwell 10
```
Keluaran: gambar/video beranotasi, `hasil_gambar.csv` / `*_hasil.csv`, dan `*_ringkasan.json` (daftar parkir terkonfirmasi).

## Aplikasi (laptop dan HP)

```bash
python main.py            # sama dengan: python main.py ui   (juga bisa lewat tombol Run di VS Code)
```
**Buka, lalu langsung mendeteksi.** Layarnya hanya kamera. Begitu jendela terbuka, kamera menyala (di HP: kamera belakang) dan
pemantauan mulai sendiri setelah model siap; satu-satunya yang perlu kamu lakukan adalah menekan *Izinkan* saat browser meminta akses
kamera. Trotoar dideteksi otomatis. Satu kapsul di bawah selalu menjawab satu pertanyaan: *ada kendaraan di atas trotoar atau tidak?*

| Kapsul | Artinya |
|---|---|
| Hijau, **Trotoar aman** | Tidak ada kendaraan di trotoar |
| Kuning, **Mobil berhenti di trotoar** | Sedang dipastikan; cincin terisi sampai lama diam tercapai |
| Merah, **Mobil parkir di trotoar** | Terkonfirmasi: layar berpendar, bunyi dan getar, foto bukti tersimpan |

Hanya yang relevan yang tampil: ikon jeda dan pengaturan di kanan atas, serta penghitung **Kejadian** di bawah. Semuanya melebur saat
memantau supaya gambar tampil penuh; gerakkan pointer atau sentuh layar untuk memunculkannya lagi.

- **Kejadian** (`E`): foto bukti terbaru lebih dulu, klik untuk memperbesar. Tandai tiap kejadian *Benar* atau *Salah*; presisi alert
  dihitung otomatis dan tersimpan di `live_hasil/kejadian_tinjauan.csv` (juga di `ringkasan_live.json`), bahan langsung untuk tabel hasil pengujian di laporan.
- **Pengaturan** (ikon kanan atas): sumber gambar (kamera, video, atau stream), lama diam sebelum alert, gambar area trotoar manual (`D`),
  jenis peringatan (bip, suara Bahasa Indonesia, atau senyap), notifikasi saat di latar belakang, simpan foto layar, layar penuh (`F`).
  Pintasan lain: `Spasi` jeda atau lanjut. Di HP layar dijaga tetap menyala selama memantau (bila browser mendukung).
- **Sumber gambar**: *Kamera* (otomatis), *Video* (pilih berkas, langsung dianalisis; gunakan `.mp4` H.264, kodek lain sering tidak bisa
  diputar browser), *Stream* (indeks webcam `0`, kamera IP HP `http://192.168.1.20:8080/video`, atau CCTV `rtsp://...`; server yang membuka sumbernya).
- **Kamera terputus** (dicabut, atau HP mematikannya saat di latar belakang): dinyalakan lagi sendiri. Menjeda memberhentikan mode langsung
  sampai kamu menekan lanjut; matikan *Langsung memantau saat dibuka* di Pengaturan bila tidak mau kamera menyala otomatis.
- Jendela terbuka sendiri (Edge/Chrome mode aplikasi; bila tak ada, browser biasa). Tanpa paket tambahan: servernya memakai pustaka standar Python.
  Model dimuat di latar belakang; kapsul menampilkan *Menyiapkan model…* sampai siap.
- Opsi: `--port 8765`, `--out live_hasil`, `--min-dwell 10`, `--no-open`, serta `--weights`, `--polygon`, `--set`, dst. seperti perintah lain.

### Pasang sebagai aplikasi
Antarmukanya adalah aplikasi web yang bisa dipasang (PWA): ikon sendiri, tanpa bilah alamat, layar penuh.

- **Laptop (Chrome atau Edge)**: jalankan `python main.py`, lalu klik *Pasang aplikasi* di Pengaturan (atau ikon pasang di bilah alamat).
  Aplikasi terpasang membuka `http://localhost:8765`, jadi **servernya tetap harus menyala** (`python main.py`); bila belum, aplikasi menampilkan pesan itu.
- **HP**: pemasangan hanya bermakna bila alamatnya tetap. Alamat dari `python main.py phone` berganti tiap dijalankan, jadi untuk uji coba cukup
  buka tautannya di browser (lihat di bawah). Di iPhone: Safari → *Bagikan* → *Tambah ke Layar Utama* bila alamatnya tetap. Memasang di Android lewat Chrome → *Pasang aplikasi*.
- Yang **belum** dilakukan aplikasi ini: berjalan sepenuhnya di dalam HP tanpa laptop. Deteksi (YOLO) dijalankan di server, bukan di HP.

### Testing dengan kamera HP (iPhone atau Android)
Browser hanya mengizinkan kamera di `http://localhost` atau `https://`, jadi dari HP dasbor harus dibuka lewat terowongan HTTPS.
Semuanya sudah diotomatiskan:
```bash
winget install --id Cloudflare.cloudflared    # sekali saja (Windows; macOS: brew install cloudflared), lalu buka ulang terminal
python main.py phone
```
Terminal mencetak **kode QR** (dan menyimpannya sebagai `live_hasil/qr_iphone.png`, yang dibuka otomatis di laptop). Di iPhone:
buka aplikasi **Kamera**, arahkan ke kode, ketuk tautan yang muncul, ketuk **Izinkan** di Safari. Deteksi langsung berjalan; hasilnya
(kapsul status, foto bukti, tinjauan Benar/Salah) tampil di layar iPhone. Frame dikirim dari HP ke laptop, jadi YOLO tetap dijalankan di laptop.

- Biarkan layar iPhone menyala dan Safari tetap di depan: iOS menghentikan kamera saat Safari di latar belakang atau layar terkunci.
- Kamera belakang dipakai lebih dulu. Notifikasi browser tidak tersedia di Safari biasa; bunyi/suara baru aktif setelah layar disentuh sekali.
- Tautan berisi kunci acak dan berubah tiap `python main.py phone`; sumber di sisi server (Stream) dimatikan pada mode ini.
- Bila tautan belum terbuka, tunggu beberapa detik lalu muat ulang (alamat baru perlu waktu agar bisa dijangkau). Jaringan kampus kadang memblokir terowongan; coba hotspot HP.
- Tanpa terowongan: aplikasi yang menjadikan HP sebagai webcam laptop (misalnya Iriun Webcam atau Camo) membuat HP muncul di daftar kamera di Pengaturan,
  atau pakai aplikasi kamera IP di HP dengan sumber **Stream** (Wi-Fi yang sama).
- Manual (tanpa `phone`): `python main.py ui --remote`, lalu `cloudflared tunnel --url http://127.0.0.1:8765` dan buka `https://<alamat>/?key=<kunci>`.

## Mode live (ala CCTV)

*(Versi baris perintah dengan jendela OpenCV; dasbor di atas lebih nyaman.)*

Memantau kamera secara real-time; saat ada kendaraan **diam di trotoar tanpa pengendara ≥ 10 detik** muncul tulisan
`ALERT` merah di layar, gambar bukti disimpan, dan kejadian dicatat.

```bash
python main.py live --source 0 --beep --record
```

| Sumber (`--source`) | Contoh |
|---|---|
| Webcam laptop | `0` (kamera lain: `1`, `2`) |
| HP sebagai kamera IP (Android, aplikasi sejenis *IP Webcam*) | `http://192.168.1.20:8080/video` — HP dan laptop harus di Wi-Fi yang sama; alamat persisnya ditampilkan aplikasinya |
| HP sebagai webcam (aplikasi sejenis DroidCam/EpocCam) | biasanya muncul sebagai `1` atau `2` |
| CCTV / kamera IP RTSP | `rtsp://user:pass@192.168.1.30:554/stream` |

Untuk kamera yang **tetap** (tripod/ditempel), hasil paling stabil memakai poligon trotoar:
```bash
python main.py snapshot --source 0 --out frame.jpg          # ambil satu gambar dari kamera
python main.py polygon frame.jpg --out poligon.json         # klik tepi trotoar, tekan Enter
python main.py live --source 0 --polygon poligon.json --no-camera-comp --beep
```
Tombol di jendela: **q / Esc** keluar, **s** simpan gambar, **r** hitung ulang mask trotoar.
Keluaran di `live_hasil/`: `alerts/*.jpg` (bukti), `kejadian.csv` (log), `ringkasan_live.json`, dan `rekaman.mp4` bila `--record`.
Satu kendaraan hanya memicu satu ALERT. Bila CPU lambat (FPS rendah), turunkan resolusi model: `--set imgsz=640`.
Tanpa layar (server): tambahkan `--no-window`.

### Sumber mask trotoar (`--sidewalk`)

| Mode | Kapan dipakai |
|---|---|
| `classical` | Baseline tanpa training. Mengasumsikan **bagian bawah-tengah gambar adalah trotoar** (HP dipegang dari atas trotoar). Bila trotoar ada di sisi lain, atur `--seed x1,y1,x2,y2` (pecahan 0–1), mis. `--seed 0.02,0.8,0.2,0.98` untuk kiri-bawah. |
| `polygon` | Video tripod: `python main.py polygon frame.jpg --out poligon.json`, lalu `--polygon poligon.json`. Poligon ikut bergeser mengikuti gerak kamera. |
| `model` / `auto` | Model YOLO-seg khusus trotoar (`--sidewalk-weights best.pt`). `auto` memakai model bila ada, selain itu klasik. |

Melatih model trotoar: anotasi **satu kelas** (`sidewalk`) berformat YOLO-segmentation (Roboflow/CVAT), lalu
`python main.py train --data data.yaml --epochs 100`. Pisahkan train/val/test **per lokasi**, bukan acak per frame.

**Dataset publik** (Cityscapes, IDD, Mapillary Vistas untuk trotoar; set Roboflow untuk kendaraan di trotoar) tidak disimpan di repo.
`python main.py data masks|merge` mengubah hasil unduhan menjadi data siap latih; panduan langkah demi langkah ada di [`datasets/README.md`](datasets/README.md).

## Evaluasi

```bash
# segmentasi trotoar: IoU / Dice (GT = PNG biner bernama <nama_gambar>.png)
python main.py eval-seg --pred hasil --gt gt_trotoar

# keputusan tingkat gambar + analisis sensitivitas ambang (labels.csv: image,label  → 1/0 atau parkir/bukan)
python main.py eval-decision --images data_uji --labels labels.csv --sweep-tau 0.3,0.4,0.5,0.6,0.7 --out eval.csv
# tambahkan --oracle-sidewalk gt_trotoar untuk memisahkan galat segmentasi dari galat logika keputusan
```
`trotoar.metrics.cohen_kappa` tersedia untuk melaporkan kesepakatan antar-anotator.

## Parameter yang paling berpengaruh (ubah dengan `--set nama=nilai`)

`tau_contact`, `tau_ring` (ambang keputusan; `--tau` mengatur keduanya), `maha_thresh` (kelonggaran segmentasi klasik),
`min_dwell_s`, `max_move_frac`, `stationary_window_s`, `vote_window`, `footprint_frac`, `ring_scale`, `ring_below_frac`,
`compensate_camera`, `video_stride`. Semua ada di `trotoar/config.py`. **Nilai default hanyalah titik awal**; tetapkan
dari data validasi Anda.

## Status verifikasi — baca sebelum dipakai di laporan

- **Teruji (101 tes otomatis, data SINTETIS)**: segmentasi klasik, geometri kontak/cincin, heuristik pengendara,
  dwell time, kompensasi gerak kamera, pipeline video, mode live (sumber berkas + kamera tiruan berlaju tetap),
  CLI, evaluasi, penanganan EXIF/video, konversi dataset (mask piksel ke YOLO-seg, gabung/ubah kelas set YOLO; data buatan), serta server dasbor (frame JPEG lewat HTTP, siaran MJPEG, poligon manual,
  pengaturan, tinjauan benar/salah dan CSV-nya, manifes aplikasi, service worker, dan ikon (termasuk di mode `--remote`), kunci akses `--remote`, pembuatan kode QR yang di-decode ulang, pembacaan alamat terowongan dari log, galat bila cloudflared tak ada). Jalankan: `pytest tests -q`.
- **Tampilan aplikasi** dicoba di Chrome headless dengan kamera palsu bawaan Chrome dan deteksi tiruan, di layar laptop (1440×900) dan HP (390×844):
  buka halaman tanpa klik apa pun lalu kamera menyala dan pemantauan mulai sendiri, kapsul berganti aman/memastikan/parkir, daftar kejadian dan tinjauan
  Benar/Salah, pengaturan, gambar trotoar manual, sumber Stream dan Video (`.mp4` H.264), jeda lalu lanjut, kamera ditolak (pesan + tombol coba lagi),
  tanpa galat konsol. Chrome melaporkan manifes tanpa galat, tanpa hambatan pemasangan, dan service worker aktif. Belum dicoba: Edge/Firefox/Safari,
  kamera HP sungguhan, pemasangan sungguhan ke laptop/HP, notifikasi dan suara di browser sungguhan, terowongan HTTPS, dan memutar video dengan model YOLO asli.
  Font memakai yang ada di sistem (Bahnschrift/Segoe UI di Windows), jadi tampilan sedikit berbeda antar-OS.
- **Tautan HP (`python main.py phone`)** diuji dengan `cloudflared` tiruan (skrip yang mencetak log mirip aslinya) dan QR yang tercetak di terminal di-decode kembali menjadi alamat yang benar. **`cloudflared` asli dan iPhone sungguhan belum dicoba**: format log Cloudflare bisa berubah, dan jaringan kampus/kantor kadang memblokir terowongan.
- **Belum teruji dengan model asli**: pemanggilan YOLO sebenarnya (`vehicles.py`: `YoloSegmenter`, `YoloSidewalkSegmenter`),
  perintah `train`, `polygon` dan jendela `live` OpenCV (butuh layar), serta kamera sungguhan (webcam, kamera IP HP, RTSP):
  tes live memakai video sintetis sebagai sumber. Parser hasil Ultralytics diuji dengan objek tiruan; versi
  Ultralytics yang berbeda bisa mengubah detail API. Jika ada galat, mulai dari `parse_ultralytics_result`.
- **Akurasi pada trotoar nyata belum diukur.** Angka pada data sintetis hanya membuktikan program berjalan sesuai desain.
- Keterbatasan yang diketahui: segmentasi klasik rapuh terhadap bayangan dan trotoar yang warnanya mirip jalan;
  heuristik pengendara bisa keliru bila pejalan kaki berdiri tepat di belakang motor; "parkir" pada foto hanyalah
  *indikasi* (tidak ada dimensi waktu); kompensasi kamera hanya translasi (bukan rotasi/zoom); tracker yang kehilangan
  ID mereset hitungan durasi; kendaraan yang separuh di trotoar berada dekat ambang sehingga sensitif terhadap `tau`.

## Struktur

```
main.py                 titik masuk CLI
trotoar/                config, sidewalk, vehicles, geometry, decision, temporal, pipeline, visualize, media, metrics, datasets, cli, webui
datasets/               panduan dan manifes dataset publik (datanya diunduh sendiri, tidak ikut git)
trotoar/web/            antarmuka aplikasi (index.html, app.css, app.js, sw.js, ikon PNG)
tests/                  tes otomatis + generator adegan sintetis (synthetic.py)
```

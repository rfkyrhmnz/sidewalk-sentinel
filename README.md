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

## Dasbor web (cara termudah)

```bash
python main.py            # sama dengan: python main.py ui   (juga bisa lewat tombol Run di VS Code)
```
**Langsung jalan tanpa setelan**: begitu jendela terbuka, kamera menyala (di HP: kamera belakang) dan pemantauan mulai sendiri setelah model siap. Satu-satunya yang perlu kamu lakukan adalah menekan *Izinkan* saat browser meminta akses kamera. Trotoar dideteksi otomatis. Kalau kamera terputus (dicabut, atau HP mematikannya saat tab di latar belakang), dasbor menyalakannya lagi sendiri. Tombol *Berhenti* menghentikan mode langsung sampai kamu menekan *Mulai pantau*; matikan di Pengaturan bila tidak mau kamera menyala otomatis.

Jendela dasbor terbuka sendiri (Edge/Chrome mode aplikasi; bila tak ada, browser biasa). Tanpa paket tambahan: servernya
memakai pustaka standar Python. Model dimuat di latar belakang; saat chip di kanan atas berubah menjadi **Model siap**, kamu bisa mulai.

| Tab | Untuk apa |
|---|---|
| **Kamera** | Kamera laptop atau HP lewat browser. Menyala dan memantau otomatis; tombolnya hanya dipakai bila izin sempat ditolak. |
| **Stream** | Diisi indeks webcam (`0`), alamat kamera IP HP (`http://192.168.1.20:8080/video`), atau CCTV (`rtsp://...`). Server yang membuka sumbernya. |
| **Video** | Pilih berkas video dari komputer; diputar di dasbor dan dianalisis sambil diputar. |

- **Gambar trotoar** (`D`): klik titik-titik tepi trotoar langsung di atas video, lalu *Terapkan*. Paling stabil untuk kamera yang diam
  (tersimpan juga di `live_hasil/trotoar_polygon.json`). *Pakai otomatis* mengembalikan ke segmentasi otomatis.
- **Tampilan**: tema gelap ala pusat kendali. Tiap kendaraan diberi kurung sudut HUD yang bergerak mulus, cincin kemajuan lama diam
  (penuh = alert), dan jejak gerak (`T`). **Peta panas** (`H`) menumpuk posisi kendaraan di trotoar selama sesi. **Foto** (`S`) menyimpan
  gambar lengkap dengan overlay. Dok kendali melebur saat memantau; gerakkan pointer atau sentuh layar untuk memunculkannya lagi.
- **Telemetri**: jumlah kendaraan / di trotoar / parkir dengan grafik mini, fps dan latensi, serta rincian jenis kendaraan.
- Saat kendaraan terkonfirmasi parkir: seluruh antarmuka berpendar merah, muncul banner, dan foto bukti masuk ke daftar **Kejadian**
  (klik untuk memperbesar; *Unduh CSV* untuk log). Pilihan peringatan di **Pengaturan**: bip, suara Bahasa Indonesia, atau senyap,
  plus notifikasi browser bila tab sedang di latar belakang.
- **Tinjau kejadian**: tandai tiap kejadian *Benar* atau *Salah*. Presisi alert dihitung otomatis dan hasilnya tersimpan di
  `live_hasil/kejadian_tinjauan.csv` (juga ada di `ringkasan_live.json`), bahan langsung untuk tabel hasil pengujian di laporan.
- **Pengaturan** (ikon kanan atas): lama diam sebelum alert, ambang kontak trotoar, kompensasi gerak kamera. Berlaku langsung.
- **Di HP**: tata letak menyesuaikan layar tegak, dan layar dijaga tetap menyala selama memantau (bila browser mendukung).
- Pintasan papan ketik: `Spasi` mulai/berhenti, `D`, `H`, `T`, `S`, `F` layar penuh, `?` daftar pintasan.
- Opsi: `--port 8765`, `--out live_hasil`, `--min-dwell 10`, `--no-open`, serta `--weights`, `--polygon`, `--set`, dst. seperti perintah lain.

### Testing dengan kamera HP (iPhone atau Android)
Browser hanya mengizinkan kamera di `http://localhost` atau `https://`, jadi dari HP dasbor harus dibuka lewat terowongan HTTPS.
Semuanya sudah diotomatiskan:
```bash
winget install --id Cloudflare.cloudflared    # sekali saja (Windows; macOS: brew install cloudflared), lalu buka ulang terminal
python main.py phone
```
Terminal mencetak **kode QR** (dan menyimpannya sebagai `live_hasil/qr_iphone.png`, yang dibuka otomatis di laptop). Di iPhone:
buka aplikasi **Kamera**, arahkan ke kode, ketuk tautan yang muncul, ketuk **Izinkan** di Safari. Deteksi langsung berjalan; hasilnya
(banner, foto bukti, tinjauan Benar/Salah) tampil di layar iPhone. Frame dikirim dari HP ke laptop, jadi YOLO tetap dijalankan di laptop.

- Biarkan layar iPhone menyala dan Safari tetap di depan: iOS menghentikan kamera saat Safari di latar belakang atau layar terkunci.
- Kamera belakang dipakai lebih dulu. Notifikasi browser tidak tersedia di Safari biasa; bunyi/suara baru aktif setelah layar disentuh sekali.
- Tautan berisi kunci acak dan berubah tiap `python main.py phone`; sumber di sisi server (tab Stream) dimatikan pada mode ini.
- Bila tautan belum terbuka, tunggu beberapa detik lalu muat ulang (alamat baru perlu waktu agar bisa dijangkau). Jaringan kampus kadang memblokir terowongan; coba hotspot HP.
- Tanpa terowongan: aplikasi yang menjadikan HP sebagai webcam laptop (misalnya Iriun Webcam atau Camo) membuat HP muncul di daftar kamera tab **Kamera**,
  atau pakai aplikasi kamera IP di HP dengan tab **Stream** (Wi-Fi yang sama).
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

- **Teruji (87 tes otomatis, data SINTETIS)**: segmentasi klasik, geometri kontak/cincin, heuristik pengendara,
  dwell time, kompensasi gerak kamera, pipeline video, mode live (sumber berkas + kamera tiruan berlaju tetap),
  CLI, evaluasi, penanganan EXIF/video, serta server dasbor (frame JPEG lewat HTTP, siaran MJPEG, poligon manual,
  pengaturan, tinjauan benar/salah dan CSV-nya, kunci akses `--remote`, pembuatan kode QR yang di-decode ulang, pembacaan alamat terowongan dari log, galat bila cloudflared tak ada). Jalankan: `pytest tests -q`.
- **Tampilan dasbor** dicoba di Chrome headless dengan kamera palsu bawaan Chrome dan deteksi tiruan (alur tab Stream dan Kamera,
  menggambar poligon, kurung HUD/banner/peta panas, tinjauan Benar/Salah, laci pengaturan, pintasan papan ketik, tata letak
  layar HP 390 px, serta alur tanpa klik: kamera menyala dan pemantauan mulai sendiri, Berhenti manual tidak memulai ulang, galat izin kamera + tombol coba lagi) tanpa galat konsol. Belum dicoba: Edge/Firefox/Safari, kamera HP sungguhan, notifikasi dan suara di browser
  sungguhan, terowongan HTTPS, dan memutar video dengan model YOLO asli. Font memakai yang ada di sistem (Bahnschrift/Segoe UI di Windows),
  jadi tampilan sedikit berbeda antar-OS.
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
trotoar/                config, sidewalk, vehicles, geometry, decision, temporal, pipeline, visualize, media, metrics, cli, webui
trotoar/web/            antarmuka dasbor (index.html, app.css, app.js)
tests/                  tes otomatis + generator adegan sintetis (synthetic.py)
```

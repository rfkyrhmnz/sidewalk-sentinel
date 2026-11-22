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

- **Teruji (44 tes otomatis, data SINTETIS)**: segmentasi klasik, geometri kontak/cincin, heuristik pengendara,
  dwell time, kompensasi gerak kamera, pipeline video, CLI, evaluasi, penanganan EXIF/video. Jalankan: `pytest tests -q`.
- **Belum teruji dengan model asli**: pemanggilan YOLO sebenarnya (`vehicles.py`: `YoloSegmenter`, `YoloSidewalkSegmenter`),
  perintah `train`, dan `polygon` (butuh layar). Parser hasil Ultralytics diuji dengan objek tiruan; versi
  Ultralytics yang berbeda bisa mengubah detail API. Jika ada galat, mulai dari `parse_ultralytics_result`.
- **Akurasi pada trotoar nyata belum diukur.** Angka pada data sintetis hanya membuktikan program berjalan sesuai desain.
- Keterbatasan yang diketahui: segmentasi klasik rapuh terhadap bayangan dan trotoar yang warnanya mirip jalan;
  heuristik pengendara bisa keliru bila pejalan kaki berdiri tepat di belakang motor; "parkir" pada foto hanyalah
  *indikasi* (tidak ada dimensi waktu); kompensasi kamera hanya translasi (bukan rotasi/zoom); tracker yang kehilangan
  ID mereset hitungan durasi; kendaraan yang separuh di trotoar berada dekat ambang sehingga sensitif terhadap `tau`.

## Struktur

```
main.py                 titik masuk CLI
trotoar/                config, sidewalk, vehicles, geometry, decision, temporal, pipeline, visualize, media, metrics, cli
tests/                  tes otomatis + generator adegan sintetis (synthetic.py)
```

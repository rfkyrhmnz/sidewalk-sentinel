# Dataset untuk melatih YOLO

Data **tidak** disimpan di repo: ukurannya besar dan sebagian lisensinya melarang membagikan ulang (Cityscapes). Alurnya:
unduh ke `datasets/raw/<nama>/` → jalankan `python main.py data ...` → hasil siap latih di `datasets/yolo/`. Kedua folder itu diabaikan git.
Daftar lengkap sumber, akses, dan lisensi ada di [`manifest.json`](manifest.json).

Dua kategori yang ingin dikenali program:
1. Motor di trotoar **dengan pengendara**: *menyalip lewat trotoar*.
2. Kendaraan di trotoar **tanpa pengendara** dan diam: *parkir di atas trotoar*.

Pembagian kerjanya: aturan di program yang menentukan kategori (ada pengendara atau tidak, diam berapa lama). Data dipakai untuk dua hal:

| Tujuan | Dataset | Hasil |
|---|---|---|
| **A. Model trotoar** (YOLO-seg satu kelas `sidewalk`) | Cityscapes, IDD, Mapillary Vistas | `datasets/yolo/sidewalk/` |
| **B. Set uji kendaraan di trotoar** (presisi/recall tiap kategori) | Set Roboflow di manifes + foto sendiri | `datasets/yolo/uji/` |

Kendaraan dan pengendara tidak perlu dilatih dulu: YOLO bawaan (COCO) sudah mengenali car, motorcycle, dan person.

## A. Model trotoar

### Cityscapes (dasar yang paling siap)
1. Daftar di https://www.cityscapes-dataset.com/register/ dengan email UM. Aktivasinya manual, bisa beberapa hari.
2. Unduh `leftImg8bit_trainvaltest.zip` dan `gtFine_trainvaltest.zip` (sekitar 11 GB gabungan), ekstrak keduanya ke `datasets/raw/cityscapes/` sampai ada folder `leftImg8bit/` dan `gtFine/`.
3. Coba dulu dengan sedikit gambar:
   ```bash
   python main.py data masks --src datasets/raw/cityscapes --out datasets/yolo/sidewalk --preset cityscapes --limit 50
   ```
   Lalu jalankan tanpa `--limit` untuk semuanya (2.975 latih + 500 validasi; gambar dikecilkan ke sisi terpanjang 1280 px).
   Pembagian train/val mengikuti bawaan Cityscapes, yaitu per kota, sesuai anjuran memisahkan data per lokasi.

### IDD (paling mirip lalu lintas Indonesia: banyak motor)
Daftar di http://idd.insaan.iiit.ac.in/ dan unduh paket segmentasinya. Saya belum melihat berkas aslinya, jadi nama berkas mask dan id kelas
trotoar harus kamu cek sendiri (dokumentasi IDD, atau buka satu mask dan lihat nilai pikselnya), lalu:
```bash
python main.py data masks --src datasets/raw/idd --out datasets/yolo/sidewalk_idd \
  --image-dir leftImg8bit --mask-dir gtFine --image-suffix _leftImg8bit.png --mask-suffix <akhiran mask> --label-id <id trotoar>
```
Bila folder splitnya bernama lain, atur `--splits`. Untuk menggabung dengan Cityscapes, jalankan ke folder terpisah lalu gunakan keduanya bergantian atau gabungkan gambar dan labelnya.

### Mapillary Vistas
Unduh versi riset dari situs Mapillary (cari "Mapillary Vistas"), ekstrak ke `datasets/raw/mapillary/`. Untuk v1.2, dengan mask palet (nilai piksel = id kelas, Sidewalk = 15):
```bash
python main.py data masks --src datasets/raw/mapillary --out datasets/yolo/sidewalk_mapillary \
  --image-dir "{split}/images" --mask-dir "{split}/labels" --splits training,validation \
  --image-suffix .jpg --mask-suffix .png --label-id 15
```
Id 15 saya ambil dari daftar kelas v1.2 dan belum saya cek pada berkas asli. Untuk v2.0 atau mask berwarna, lihat `config.json` bawaan dataset
(id kelas Sidewalk, atau warnanya lalu pakai `--label-rgb R,G,B`).

### Melatih
```bash
python main.py train --data datasets/yolo/sidewalk/data.yaml --epochs 100 --imgsz 640 --batch 8
python main.py live --sidewalk auto --sidewalk-weights runs/trotoar/weights/best.pt
```
Butuh GPU supaya praktis. Tanpa GPU, pakai Google Colab: zip folder `datasets/yolo/sidewalk`, unggah, lalu jalankan perintah `train` yang sama
(`pip install ultralytics` dulu). Hasilnya `runs/trotoar/weights/best.pt`.

## B. Set uji kendaraan di trotoar
Dari tiap halaman Roboflow di manifes: **Download Dataset**, format **YOLOv8** (YOLO TXT), ekstrak ke `datasets/raw/<nama>/`. Buka `data.yaml` tiap set untuk
melihat nama kelasnya persis, lalu petakan ke kategori yang kamu pakai (nama lama yang salah ketik membuat perintah berhenti dengan pesan jelas):
```bash
python main.py data merge --src datasets/raw/toolkit-sidewalk --src datasets/raw/footpath-anomaly \
  --map car-on-sidewalk=kendaraan_di_trotoar --map motorcycle-on-sidewalk=motor_di_trotoar --map illegal-parking=kendaraan_di_trotoar \
  --names kendaraan_di_trotoar motor_di_trotoar --out datasets/yolo/uji
```
Kelas yang tidak dipetakan dibuang, dan gambar yang seluruh labelnya terbuang tidak disalin. **Periksa dulu mutu tiap set**: jumlah gambar dan ketepatan label di halaman Roboflow belum saya verifikasi.
Set ini sebaiknya dipakai sebagai himpunan uji/penyesuaian dan dilengkapi foto sendiri; 100 sampai 200 gambar berlabel sudah berguna untuk menghitung presisi dan recall.

## Batasan yang perlu diketahui
- Alat konversi diuji dengan data **buatan** yang meniru tata letak Cityscapes, Mapillary (mask palet), dan ekspor Roboflow. Belum dicoba pada unduhan asli, dan pelatihan YOLO-nya sendiri belum dijalankan.
- Cityscapes, IDD, dan Mapillary diambil di jalan Eropa/India, bukan Indonesia. Setelah pelatihan awal, tambahkan 100 sampai 200 foto trotoar lokal untuk penyesuaian dan ukur hasilnya di foto lokal.
- Konverter memakai kontur luar mask: kendaraan yang berdiri di tengah trotoar (terkelilingi trotoar di semua sisi) ikut terisi sebagai trotoar. Pada data jalan raya seperti ini jarang terjadi.
- Sitasi tiap dataset di laporan. CC BY 4.0 mewajibkan atribusi, dan Cityscapes/Mapillary hanya untuk riset non-komersial.

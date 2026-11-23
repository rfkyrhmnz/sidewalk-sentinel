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

## Yang paling gampang diunduh (tanpa akun)
Hanya GitHub yang bisa dijangkau dari sandbox pengembangan, jadi dataset di GitHub (nomor 1 dan 2) sudah ditarik dan dikonversi di sana. Nomor 3 dan 4 belum pernah dicoba diunduh.

1. **Trotoar: SidewalkDetection (GitHub, MIT)** https://github.com/charbel-a-hC/SidewalkDetection : 276 gambar latih + 45 validasi 1920x1080, mask biner (putih = trotoar).
   ```bash
   git clone --depth 1 https://github.com/charbel-a-hC/SidewalkDetection datasets/raw/SidewalkDetection
   python main.py data masks --src datasets/raw/SidewalkDetection/dataset --out datasets/yolo/sidewalk_a --preset sidewalkdetection
   python main.py train --data datasets/yolo/sidewalk_a/data.yaml --model yolo11n-seg.pt --epochs 100
   ```
   Hasil uji pada berkas asli: 321 gambar (276 latih, 45 val), 372 poligon, sekitar 30 detik; hamparan poligon pada 6 contoh sudah dilihat dan menutup trotoar dengan benar, termasuk takik di sekitar sepeda yang parkir.
   Catatan: clone-nya sekitar 1,2 GB (folder `dataset/` 552 MB, sisanya riwayat git dan bobot). Folder `Train_set/` dan `test/` tidak dipakai. Semua gambar berisi trotoar (tidak ada contoh tanpa trotoar), README tidak menyebut lokasi pengambilan, dan lisensi MIT tertulis untuk repositorinya (hak cipta Charbel Abi Hana 2022); tidak ada lisensi terpisah untuk gambarnya. Pelatihan YOLO belum dicoba.
2. **Trotoar: WalkScapes (GitHub, CC BY-NC 4.0)** https://github.com/KNSyNAPS-org/walkscapes-dataset (panduan) dan https://github.com/KNSyNAPS-org/walkscapes-dataset-data (data): 1.503 foto HP dari sudut pandang pejalan kaki di 10 kota Polandia, 14 kelas (trotoar = id 1). Pembagian bawaan 1.057 latih / 224 val / 222 uji.
   ```bash
   git clone --depth 1 https://github.com/KNSyNAPS-org/walkscapes-dataset-data datasets/raw/walkscapes
   python main.py data masks --src datasets/raw/walkscapes/dataset --out datasets/yolo/sidewalk_b --preset walkscapes
   ```
   **Clone-nya sekitar 11 GB** (foto HP resolusi asli, sampai 3213x5712); di Colab lebih nyaman daripada di laptop. Hasil uji pada berkas asli: 1.281 gambar (1.057 latih + 224 val), 3.169 poligon, 2 menit 33 detik; 6 contoh dicek visual dan selaras dengan fotonya. Lisensi non-komersial plus Terms of Use tambahan (belum saya baca isinya): cukup untuk tugas kuliah, jangan dipakai produk komersial.
   Gabungkan dengan nomor 1 (keduanya satu kelas `sidewalk`, jadi tidak perlu `--map`):
   ```bash
   python main.py data merge --src datasets/yolo/sidewalk_a --src datasets/yolo/sidewalk_b --out datasets/yolo/sidewalk_all --names sidewalk
   ```
   Hasil uji: 1.333 latih + 269 val, 3.541 poligon, 565 MB. Belum diukur berapa banyak foto yang memuat kendaraan di atas trotoar; mask WalkScapes memisahkan kelas kendaraan dari trotoar, jadi logika "lubang di bawah kendaraan" hanya terlatih sebanyak contoh seperti itu ada.
3. **Zenodo: Dataset for Detecting Motorcyclists in Pedestrian Areas** https://zenodo.org/records/7935299 (CC BY 4.0, `PedestrianDataSet.zip` 643 MB, 6.324 gambar CCTV 1280x720).
   Kelasnya persis kasus kita: `MMAP` motor **dengan** pengendara di area pejalan kaki (= menyalip lewat trotoar), `MNAP` motor **tanpa** pengendara di area pejalan kaki (= parkir),
   `MMNAP` motor dengan pengendara di luar area pejalan kaki, `PCP` pejalan kaki di zebra cross. Catatan: hanya motor (tanpa mobil); halamannya tidak mendefinisikan "area pejalan kaki"
   dan tidak menyebut format anotasi di folder `etiquetas/`, jadi **belum ada konverter**. Setelah diunduh, lihat isi satu berkas di `etiquetas/` dan sesuaikan.
4. **ADE20K SceneParsing** http://data.csail.mit.edu/places/ADEchallenge/ADEChallengeData2016.zip (sekitar 1 GB, kelas `sidewalk` di antara 150 kelas). Ekstrak ke `datasets/raw/` (hasilnya `datasets/raw/ADEChallengeData2016/`), lalu:
   ```bash
   python main.py data masks --src datasets/raw/ADEChallengeData2016 --out datasets/yolo/sidewalk_ade \
     --image-dir images --mask-dir annotations --splits training,validation \
     --image-suffix .jpg --mask-suffix .png --label-id 12 --skip-empty
   ```
   `--skip-empty` wajib di sini: sebagian besar gambar ADE20K adalah dalam ruangan tanpa trotoar. Id 12 diturunkan dari daftar kelas Ultralytics (trotoar = indeks 11, id mask = indeks + 1), belum dicek pada berkas asli.

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

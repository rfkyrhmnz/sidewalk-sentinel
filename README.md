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

Browser terbuka di `http://localhost:8000/`. Izinkan kamera, selesai: zona trotoar dicari sendiri oleh model dari
gambar kamera dan pemantauan langsung berjalan. Tidak ada langkah menandai trotoar.

### Memakai HP sebagai kamera

```powershell
python main.py --hp
```

Satu perintah. Browser laptop terbuka di halaman **Hubungkan HP** yang menampilkan kode QR besar (kode yang sama juga
tercetak di terminal). Buka kamera HP, arahkan ke kode, ketuk tautan yang muncul, izinkan kamera. Selesai: HP langsung
memantau, dan halaman di laptop berubah menjadi "HP tersambung".

Cara ini memakai tunnel Cloudflare, jadi tidak bergantung pada Wi-Fi, firewall, atau peringatan sertifikat, dan HP bisa
memakai data seluler. Syaratnya `cloudflared` terpasang sekali:

```powershell
winget install --id Cloudflare.cloudflared
```

Buka terminal baru sesudah memasang agar `cloudflared` dikenali. Server tetap hanya terbuka di laptop
(`127.0.0.1`); HP masuk lewat tunnel dengan tautan berkunci acak, dan permintaan tanpa kunci ditolak. Alamat tunnel dan
kuncinya berganti setiap `--hp` dijalankan ulang.

Kunci tetap ada di alamat halaman (`...?k=...`) dan tidak dihapus setelah halaman terbuka. Itu disengaja: pratinjau
pemindai QR di iPhone dan Safari menyimpan cookie secara terpisah, jadi bila alamat dipindah dengan "Buka di Safari" hanya
alamatnya yang ikut. Selama alamatnya lengkap, browser mana pun yang membukanya langsung masuk. Akibatnya kunci terlihat
di bilah alamat dan riwayat HP: jangan bagikan tangkapan layarnya, dan jangan menyalin alamat itu ke tempat lain.
Setiap browser yang dipakai terdaftar sebagai perangkat sendiri, jadi pindah browser menambah satu perangkat di Setelan
(hapus yang lama di sana). Alamat tunnel berganti tiap `--hp` dijalankan, dan tiap alamat dihitung sebagai browser baru.
Server menerima paling banyak 8 perangkat. Saat penuh, perangkat kosong yang paling lama dipakai ulang otomatis: tidak
sedang mengirim, belum punya kejadian, tanpa zona atau pengaturan buatan pemilik, tidak ditutup atau dijeda pemilik, dan
terdaftar lebih dari 10 menit. Bila tidak ada yang memenuhi syarat itu, layar menulis alasannya ("Server menolak perangkat
ini"), kamera dimatikan, dan tombol **Coba lagi** muncul setelah pusat menghapus perangkat di Setelan. Gangguan sementara
(jaringan putus, server sibuk) tidak menghentikan kamera: alasannya tampil di layar dan sambungan dicoba lagi tiap 3 detik.
Permintaan yang tidak dijawab juga diputus: 20 detik untuk permintaan biasa, 10 detik untuk satu gambar, 120 detik untuk usulan
zona (model trotoar bisa lama di CPU), lalu dianggap gangguan sementara.
Halaman "Akses ditolak" muncul bila alamat tidak memuat kunci yang berlaku, dan menunjuk cara memperbaikinya.

Bila `cloudflared` tidak ditemukan, `--hp` otomatis memakai jalur Wi-Fi: HTTPS lokal dengan kode QR di terminal. HP dan
laptop harus satu Wi-Fi, firewall Windows harus mengizinkan Python (pilih **Allow** saat ditanya), dan browser memberi
peringatan sertifikat sekali (sertifikat dibuat otomatis dan ditandatangani sendiri, tersimpan di `data/tls/`):

- Chrome: **Lanjutan**, lalu **Lanjutkan ke situs**.
- Safari: **Tampilkan Detail**, lalu **buka situs web ini**.

Jalur Wi-Fi yang sama bisa dipanggil langsung dengan `python main.py --https`; tautannya dicetak di terminal tanpa kode QR.
Di HP, taruh di dudukan dan colok ke pengisi daya: layar harus tetap menyala agar kamera tidak berhenti.

### Model sendiri

```powershell
python main.py --weights yolo11s.pt                      # model kendaraan yang lebih besar: lebih akurat, lebih lambat
python main.py --weights runs\detect\train\weights\best.pt   # hasil fine-tune sendiri (harus punya kelas kendaraan COCO)
python main.py --sidewalk-weights models\trotoar.pt      # model segmentasi trotoar untuk zona otomatis
python main.py --device 0                                # GPU pertama (bawaan: ditentukan ultralytics)
```

## Cara memakai

1. **Pantau**: kamera menyala sendiri dan zona trotoar dicari otomatis. Selama trotoar belum terlihat, status
   **Mencari trotoar** dengan berkas cahaya menyapu gambar; begitu ketemu, zona (garis biru) muncul dan status
   berubah menjadi **Memantau**. Zona ikut menyesuaikan bila kamera digeser. Bila model salah, tekan **Zona manual**
   untuk menggambar sendiri (garis kuning), dan **Kembali ke otomatis** untuk menyerahkannya lagi ke model.
   Tanpa model trotoar, layar pantau langsung meminta zona manual.
   Di HP, kamera belakang dipakai lebih dulu dan **Balik kamera** menukar depan dan belakang.
2. Kendaraan yang parkir atau naik trotoar diberi kotak berlabel di gambar, dihitung pada meter, dan masuk ke
   **Kejadian**. Pemberitahuan muncul di semua halaman, disertai getar di HP. Kejadian yang keliru (misalnya
   terpicu benda lain di depan kamera) bisa dihapus dengan ikon tempat sampah, dua ketukan; foto buktinya ikut terhapus.
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
2. **Zona trotoar**: dibuat otomatis oleh model segmentasi trotoar (`models/trotoar.pt`, YOLO11n-seg satu kelas
   `sidewalk`). Model dijalankan berkala (tiap detik pada enam pembaruan pertama, lalu tiap 4 detik), bukan di tiap
   frame. Mask dirata-ratakan antar-pembaruan (rata-rata bergerak eksponensial) supaya batas zona tidak bergetar,
   diubah menjadi poligon (cembung bila hanya terpotong kendaraan, cekung bila memang berbentuk L), dan hanya
   diterbitkan ulang bila bentuknya berubah nyata. Pembaruan ditunda selama ada kendaraan di zona karena kendaraan
   menutupi trotoar, dan zona dilepas setelah tiga pembaruan berturut-turut tidak menemukan trotoar. Zona manual
   (disimpan per perangkat) selalu didahulukan; zona otomatis hanya ada di memori.
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
  zona otomatis bisa meleset atau tidak ditemukan, terutama bila sudut kamera berbeda dari data latih (tombol
  **Zona manual** ada untuk itu). Zona otomatis sepenuhnya bergantung pada model ini. Latih lebih
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
  tunggu zona otomatis menyesuaikan diri. Zona manual tidak ikut bergeser: gambar ulang atau pilih
  **Kembali ke otomatis**, karena zona disimpan relatif terhadap gambar.
- **Kamera hidup selama halaman terbuka di layar.** Web tidak bisa menjaga kamera menyala saat HP terkunci atau
  aplikasi pindah ke latar. Aplikasi meminta layar tetap menyala, tetapi HP sebaiknya dicolok ke pengisi daya.
- Satu kendaraan yang jejaknya terputus lebih dari 2 detik lalu terdeteksi lagi mendapat ID baru dan bisa tercatat
  dua kali.
- Jalur HTTPS baru diuji pada Chrome di Linux dengan kamera tiruan, belum pada HP sungguhan. Pemberitahuan hanya
  muncul selama halaman terbuka; notifikasi saat aplikasi tertutup belum ada.

## Tampilan

Bahasa visualnya kaca cair: permukaan tembus pandang yang mengaburkan cahaya di belakangnya, bertepi bercahaya, dengan
kilau tipis di sisi atas. Kamera adalah tokoh utamanya. Bingkainya menyala sesuai keadaan (hijau saat memantau, biru saat
mencari atau memuat, kuning saat perlu tindakan, merah saat galat), dan warna yang sama menyalakan latar. Pengguna yang
mengaktifkan "kurangi transparansi" di sistemnya mendapat permukaan padat (bila peramban mendukung pengaturan itu), dan hasil cetak tetap putih tanpa kaca.

Tata letak menyesuaikan layar, tanpa gulir mendatar di ukuran apa pun:

| Layar | Menu | Halaman Pantau |
|---|---|---|
| HP tegak (320 px ke atas) | bawah | satu kolom |
| tablet tegak | bawah | kamera di atas, hitungan dan kejadian terbaru berdampingan di bawahnya |
| HP mendatar | samping, ramping | kamera di kiri, hitungan dan kejadian di kanan |
| laptop, tablet mendatar | samping | sama, kolom kanan lebih lebar |
| layar sangat lebar | samping | isi dibatasi 1480 px dan diletakkan di tengah |

Area aman (poni dan bilah gestur) dihormati, dan sasaran ketuk menu minimal 44 px.

## Antarmuka server

Aplikasi web dan API dilayani pustaka standar Python. Semua titik akhir data dibatasi menurut peran (lihat **Privasi**):
perangkat hanya mendapat datanya sendiri, pusat mendapat semuanya, dan perangkat orang lain dijawab `404` persis seperti
perangkat yang tidak ada. Titik akhir utama:

| Titik akhir | Fungsi |
|---|---|
| `GET /hp` | halaman kode QR di laptop (mode `--hp`) |
| `GET /api/connect` | (hanya pusat) keadaan sambungan HP: tautan berkunci, gambar QR (SVG), dan perangkat yang sedang mengirim |
| `GET /api/info` | versi, peran peminta (`pusat` atau `perangkat`), nilai bawaan, dan status model |
| `POST /api/detector/retry` | memuat ulang model kendaraan setelah galat |
| `POST /api/device` | mendaftarkan perangkat (kunci acak dari browser) atau mengambil kembali kameranya; menjawab dengan cookie `ss_dev` |
| `POST /api/cams/<id>/frame` | satu frame JPEG; dijawab status, kotak deteksi, dan hitungan hari ini. Dijawab `409` bila kamera ditutup (isinya tidak dibaca) |
| `POST /api/cams/<id>/propose-zone` | usulan poligon trotoar dari gambar terbaru perangkat (titik awal penyunting zona manual) |
| `GET/POST/DELETE /api/cams/<id>` | informasi, setelan (`name`, `roi`, `settings`, `enabled` untuk Jeda, `closed` untuk Tutup kamera), dan penghapusan data perangkat |
| `GET /api/cams/<id>/events` | kejadian perangkat (`?after=<no>`) |
| `DELETE /api/cams/<id>/events/<no>` | hapus satu kejadian beserta foto buktinya (nomor tidak dipakai ulang) |
| `GET /api/events` | semua kejadian (`kind`, `limit`, `offset`) |
| `GET /api/events.csv` | unduhan CSV, kolom `durasi_detik` ikut |
| `GET /api/report` | laporan (`periode=hari_ini\|7_hari\|30_hari`, `kamera=<id>` opsional) |

Server menolak frame yang bukan JPEG, lebih dari 8 megapiksel, atau berukuran di atas 3 MB, tanpa mendekodenya lebih
dulu. Sel CSV yang berawalan `=`, `+`, `-`, atau `@` diberi tanda petik agar tidak dijalankan sebagai rumus oleh
Excel, dan semua teks dari pengguna ditampilkan sebagai teks, bukan HTML.

Opsi baris perintah:

```powershell
python main.py [--host H] [--port P] [--hp] [--https] [--lan] [--data DIR] [--no-open] [--retention-days 30]
               [--weights W] [--sidewalk-weights W] [--device D]
```

`--hp` memakai tunnel Cloudflare bila `cloudflared` ada, selain itu `--https`. `--https` otomatis ikut `--lan`. Kejadian yang lebih tua dari `--retention-days` dan yang melebihi 5000 per perangkat
dihapus bersama fotonya.

## Struktur

```
main.py                     titik masuk
models/trotoar.pt           model segmentasi trotoar (YOLO11n-seg) untuk zona otomatis
sidewalk_sentinel/          detector (YOLO kendaraan, model trotoar, mask ke poligon); engine (kotak -> pelacak ->
                            keputusan); zone (zona trotoar otomatis); config, features, tracker, decision, render;
                            cameras (kamera perangkat, manajer); store (kejadian, lama kejadian); report (laporan);
                            server; access (peran pusat atau perangkat, IP penanda); tls (sertifikat untuk jalur Wi-Fi);
                            tunnel (cloudflared); qr (pembuat kode QR tanpa pustaka luar); util
web/                        aplikasi web (PWA): index.html, app.css, app.js (kerangka dan navigasi), device.js (kamera),
                            pantau.js (layar pantau), overlay.js (zona dan kotak deteksi), views.js (Kejadian, Laporan,
                            Setelan), laporan.js (grafik dan ringkasan), hp.html dan hp.js (halaman kode QR),
                            api.js, core.js, sw.js, manifest, ikon
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
- `test_zone.py`: zona otomatis: perataan mask, jadwal pembaruan, ditunda saat ada kendaraan, dilepas setelah beberapa
  kali tak terlihat, dan tidak diterbitkan ulang untuk perubahan kecil.
- `test_engine.py`: mesin dengan detektor berskrip: parkir, naik trotoar, kendaraan di luar zona, getar kotak, deteksi
  yang terlewat sebentar, kendaraan yang pergi, ukuran minimum, dan pengaturan yang diteruskan ke detektor.
- `test_cameras.py` dan `test_store.py`: kamera perangkat, pendaftaran, pemulihan setelah restart (termasuk setelan
  versi lama), validasi frame, status model, usulan zona, penyimpanan kejadian, pelacak lama kejadian, dan pemakaian ulang slot
  perangkat (hanya yang kosong, tua, dan tidak dipakai pemilik yang boleh tergusur).
- `test_access.py` dan `test_isolation.py`: aturan peran (sambungan lokal sungguhan, header proxy, Host, DNS rebinding,
  IP penanda) dan pemisahan data di setiap jalur API: perangkat A tidak bisa membaca, mengubah, atau menghapus milik B,
  tamu tanpa perangkat tidak melihat apa pun, pusat melihat semuanya, dan IP tidak pernah sampai ke perangkat.
- `test_qr.py`: pembuat QR dibaca dekoder QR OpenCV, dan semua panjang teks dibaca balik oleh pembaca mandiri yang
  memeriksa syndrome Reed-Solomon tiap blok.
- `test_hp.py`: tunnel dengan `cloudflared` tiruan (alamat dari log, gagal, putus, batas waktu), `--hp` dari ujung ke
  ujung (server hanya di laptop, berkunci, kode QR terminal terbaca kembali menjadi tautan, cadangan jalur Wi-Fi), dan
  API `/api/connect`.
- `test_ui_hp.py`: halaman `/hp` di Chrome; kode QR di layar dibaca kembali dari tangkapan layar dan harus sama dengan
  tautan berkunci.
- `test_server.py` dan `test_tls.py`: API, kunci LAN (termasuk pindah browser hanya dengan alamat, dan kunci berisi karakter
  aneh), keamanan masukan, sertifikat HTTPS, dan cookie `Secure`.
- `test_laporan.py`: agregasi laporan dengan data buatan (batas tengah malam, zona waktu, jam puncak, lama yang belum
  terukur, keadaan kosong).
- `test_ui_pantau.py`: antarmuka di Chrome lewat Playwright dengan kamera tiruan (kanvas yang menggambar jalan dan
  kendaraan): kamera menyala sendiri, zona ditemukan tanpa menandai apa pun, zona manual dan kembali ke otomatis, status
  mencari trotoar, alur sampai parkir terdeteksi dan lamanya masuk
  Laporan, model memuat atau galat, izin kamera ditolak, konteks tidak aman, tab kedua, jalur HTTPS, grafik, tata letak
  HP dan desktop, 12 ukuran layar dari 320 px sampai 2560 px (tanpa gulir mendatar, menu tidak menutupi isi, teks tidak
  keluar dari tombol), bahan kaca, tutup kamera (semua aliran berhenti, tidak ada gambar terkirim, bertahan setelah muat ulang,
  ditutup dari pusat, kartu muat di kotak bidik), sambungan ditolak (perangkat penuh, kunci salah: alasan tampil dan
  kamera mati) atau terganggu sementara (alasan tampil, tersambung sendiri, permintaan yang menggantung diputus), serta hasil cetak PDF. Tes ini dilewati otomatis bila Playwright atau Chrome tidak terpasang.

## Privasi

Gambar kamera dan foto bukti hanya diproses dan disimpan di folder `data/` pada komputer yang menjalankan server;
aplikasi ini tidak mengirimnya ke layanan lain. Pengecualian: dengan `--hp` lewat tunnel, gambar dari HP melewati
server Cloudflare dalam perjalanan ke laptop; jalur Wi-Fi tidak melewati pihak luar. Dua hal di luar kendali aplikasi: ultralytics mengunduh bobot model
pada jalan pertama, dan secara bawaan (`sync=True`) mengirim statistik pemakaian anonim ke Ultralytics (bukan gambar).
Matikan statistik itu dengan `yolo settings sync=False` di terminal yang memakai lingkungan Python proyek ini.
Foto bukti memuat kendaraan dan orang di sekitarnya, jadi atur `--retention-days` sesuai kebutuhan dan jangan
arahkan kamera ke area yang tidak boleh direkam. Tanpa `--lan` atau `--https`, server hanya menerima koneksi dari komputer itu
sendiri; dengan tunnel (`--hp`) server tetap hanya di komputer itu dan siapa pun yang memegang tautan berkunci bisa
masuk lewat tunnel, jadi jangan bagikan kode QR atau tautannya. Folder `data/` dari versi lama aplikasi tidak dipakai lagi dan aman dihapus.

### Tutup kamera

Tombol **Tutup kamera** di layar Pantau mematikan kamera sepenuhnya, berbeda dari **Jeda** yang hanya berhenti mencatat
sementara kamera dan pengiriman gambar tetap berjalan. Selama ditutup:

- aliran kamera dimatikan seketika (lampu kamera padam) dan halaman tidak mengirim gambar apa pun;
- server menolak frame dengan `409` tanpa membaca isinya, dan gambar terakhir yang tersimpan di memori dibuang;
- status perangkat menjadi **Ditutup**, juga di pusat (Setelan, Perangkat lain);
- kamera tetap tertutup setelah halaman dimuat ulang, aplikasi dibuka lagi, atau server dimulai ulang, sampai pemilik menekan
  **Buka kamera**. Nilai yang meragukan dibaca sebagai tertutup.

Pusat juga bisa menutup kamera perangkat lain lewat API (`closed: true`); perangkat itu mematikan kameranya pada gambar
berikutnya dan tetap tertutup. Hanya perangkat itu sendiri yang menyalakan kameranya lagi, dengan menekan Buka kamera.
Kejadian dan foto bukti yang sudah tercatat tidak ikut terhapus; hapus lewat halaman Kejadian bila perlu.

### Pusat dan perangkat

Ada dua peran. **Pusat** adalah peramban di komputer yang menjalankan server (buka lewat `localhost` atau `127.0.0.1`):
ia melihat semua perangkat, termasuk foto bukti dan IP-nya, dan boleh menghapus kejadian atau perangkat mana pun.
**Perangkat** (HP, atau peramban lain) hanya melihat dan mengubah datanya sendiri: umpan, laporan, CSV, dan foto bukti
perangkat lain dijawab seolah tidak ada. Setelan perangkat memberi tahu pemiliknya bahwa pusat bisa melihat datanya.

Hak akses sengaja **tidak** ditentukan oleh alamat IP:

- Perangkat dikenali dari kunci rahasia acak di browsernya (cookie `ss_dev`, `HttpOnly`, `SameSite=Strict`, hanya untuk `/api`).
  Kunci itu tidak pernah ikut di jawaban API mana pun.
- Pusat dikenali dari sambungan yang benar-benar lokal. Tiga syarat harus terpenuhi bersama: penyambung loopback, tidak
  ada header proxy (Cloudflare selalu menambahkan `Cf-*` dan `X-Forwarded-*`), dan header `Host` berupa `localhost` atau
  IP loopback. Ragu berarti bukan pusat. Syarat `Host` sekaligus menolak serangan DNS rebinding.
- IP dicatat sebagai **penanda** dan hanya ditampilkan di pusat (Setelan, Perangkat lain). Di belakang tunnel, IP asli HP
  dibaca dari `Cf-Connecting-Ip`, dan header itu hanya dipercaya bila server sendiri yang menjalankan tunnel dan
  sambungannya dari loopback. Bila IP sebuah perangkat tiba-tiba berganti, pusat bisa melihatnya.

Kenapa bukan IP: di belakang tunnel semua permintaan tampak datang dari `127.0.0.1`, jadi aturan "IP laptop boleh melihat
semuanya" memberi hak pusat kepada setiap HP yang masuk lewat tunnel. IP juga berganti (Wi-Fi ke data seluler) dan dipakai
bersama (NAT), sehingga HP yang sah bisa terkunci keluar atau orang lain ikut terbuka.

Batas yang perlu diketahui:

- Kunci perangkat bersifat pembawa (bearer): siapa pun yang menyalin kunci dari penyimpanan lokal sebuah HP menjadi
  perangkat itu. IP penanda hanya membantu pusat melihat kejadian semacam itu, tidak mencegahnya.
- Program lain di laptop pusat tampak sebagai pusat. Pemisahan ini melindungi perangkat satu dari yang lain, bukan laptop
  dari pemiliknya sendiri.
- Laptop yang membuka aplikasi lewat alamat LAN-nya sendiri (bukan `localhost`) dihitung sebagai perangkat biasa.
- Nama perangkat harus unik, sehingga mengganti nama menjadi nama perangkat lain ditolak: itu membocorkan bahwa nama
  tersebut ada. Isi datanya tetap tertutup.
- Aturan pusat bergantung pada `cloudflared` yang meneruskan header `Host` asli dan menambahkan header `Cf-*`. Perilaku
  itu belum diuji dengan tunnel Cloudflare sungguhan di sini (uji memakai peminta tiruan dengan header yang sama). Bila
  tunnel lain menulis ulang `Host` menjadi `localhost` dan membuang semua header, sambungannya akan dianggap pusat.
- IP termasuk data pribadi. Disimpan di `data/devices.json`, ikut terhapus bersama perangkatnya, dan hanya terlihat di
  pusat. Beri tahu pemilik perangkat sebelum memakainya.

## Riwayat repositori

Repositori dibersihkan pada 25 November 2026. Riwayat git tidak dihapus.

- Isi sebelum dibersihkan: `git checkout c4ecf80`
- Isi sebelum reset 24 November: `git checkout 05542b4`
- Versi dengan deteksi pengurangan latar (tanpa YOLO): `git checkout 1d6d25c`

// Halaman /hp: kode QR untuk menyambungkan HP. Server memberi tautan berkunci (alamat tunnel atau Wi-Fi) dan gambar QR-nya;
// halaman ini hanya menampilkannya dan memberi tahu kapan HP sudah tersambung.
import { getJSON } from './api.js';
import { $id, setText } from './core.js';

const qrEl = $id('qr');
const statusEl = $id('status');
const copyBtn = $id('copy');
let url = '';
let svg = '';
let timer = 0;

const AMBIENT = { memulai: 'mencari_trotoar', siap: 'mencari_trotoar', tersambung: 'memantau', gagal: 'model_galat', off: 'perlu_area' };

function show(state, text) {
  statusEl.dataset.st = state;
  document.documentElement.dataset.state = AMBIENT[state] || 'idle';
  setText($id('statusText'), text);
}

function draw(data) {
  const next = data.state === 'siap' || data.state === 'tersambung' ? (data.svg || '') : '';
  if (next !== svg) {                                   // SVG dibuat server dari tautan sendiri; hanya dipasang saat berubah
    svg = next;
    qrEl.innerHTML = svg || '<span class="qr-wait" aria-hidden="true"></span>';
  }
  qrEl.dataset.ready = String(!!svg);
}

function names(devices) {
  return devices.map((d) => d.name).join(', ');
}

async function poll() {
  try {
    const data = await getJSON('/api/connect');
    url = data.url || '';
    copyBtn.disabled = !url || data.state === 'gagal';
    if (data.state === 'off') {
      draw(data);
      show('off', 'Mode HP belum aktif. Jalankan python main.py --hp di terminal.');
    } else if (data.state === 'gagal') {
      draw({ state: 'gagal' });
      show('gagal', data.message || 'Tunnel gagal dibuat.');
    } else if (data.devices.length) {
      draw(data);
      qrEl.dataset.done = 'true';
      show('tersambung', `HP tersambung: ${names(data.devices)}. Pemantauan berjalan di HP.`);
    } else if (data.state === 'siap') {
      draw(data);
      qrEl.dataset.done = 'false';
      show('siap', 'Menunggu HP. Scan kode di atas.');
    } else {
      draw(data);
      show('memulai', data.message || 'Membuat tunnel, tunggu beberapa detik.');
    }
  } catch {
    show('gagal', 'Server tidak terjangkau. Pastikan python main.py masih berjalan.');
  }
  timer = setTimeout(poll, 1500);
}

copyBtn.addEventListener('click', async () => {
  if (!url) return;
  try {
    await navigator.clipboard.writeText(url);
  } catch {                                             // konteks tanpa izin papan klip: pilih lewat kolom sementara
    const box = document.createElement('textarea');
    box.value = url;
    document.body.append(box);
    box.select();
    document.execCommand('copy');
    box.remove();
  }
  copyBtn.textContent = 'Tautan disalin';
  setTimeout(() => { copyBtn.textContent = 'Salin tautan'; }, 2000);
});

window.addEventListener('pagehide', () => clearTimeout(timer));
poll();

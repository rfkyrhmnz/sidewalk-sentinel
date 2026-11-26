// Sidewalk Sentinel: titik masuk. Menyalakan kamera perangkat ini begitu aplikasi dibuka, mengatur navigasi, dan
// mengumumkan kejadian baru di halaman mana pun.
import { getJSON } from './api.js';
import { $id, ICON, KIND, setText, toast } from './core.js';
import { Device } from './device.js';
import { viewPantau } from './pantau.js';
import { viewKejadian, viewLaporan, viewSetelan } from './views.js';

const view = $id('view');
const D = new Device();                                 // kamera perangkat ini; hidup selama aplikasi terbuka
let info = { version: '', defaults: { park_seconds: 10, min_area_frac: 0.0015 } };
let current = { dispose() {} };
const P = { unseen: 0, lastSeq: null, camId: null };    // kejadian yang belum dilihat dan batas yang sudah diumumkan
const listeners = new Set();
const onEvent = (fn) => { listeners.add(fn); return () => listeners.delete(fn); };

// --------------------------------------------------------------------------- navigasi
const NAV = [
  ['#/', 'Pantau', ICON.pantau, /^#\/?$/, false],
  ['#/laporan', 'Laporan', ICON.laporan, /^#\/laporan/, false],
  ['#/kejadian', 'Kejadian', ICON.kejadian, /^#\/kejadian/, true],
  ['#/setelan', 'Setelan', ICON.setelan, /^#\/setelan/, false],
];

function renderNav(hash) {
  $id('nav').innerHTML = NAV.map(([href, label, icon, re, badge]) => {
    const n = badge ? P.unseen : 0;
    const tag = n ? `<i class="nav-badge" aria-hidden="true">${n > 99 ? '99+' : n}</i>` : '';
    const sr = n ? `<span class="sr-only">, ${n} baru</span>` : '';
    return `<a href="${href}" ${re.test(hash) ? 'aria-current="page"' : ''}>${icon}<span>${label}${sr}</span>${tag}</a>`;
  }).join('');
}

const markSeen = () => { P.unseen = 0; renderNav(location.hash || '#/'); };

const ROUTES = [
  [/^#\/?$/, () => viewPantau({ view, device: D, info, onEvent })],
  [/^#\/laporan$/, () => viewLaporan({ view })],
  [/^#\/kejadian$/, () => viewKejadian({ view, markSeen })],
  [/^#\/setelan$/, () => viewSetelan({ view, device: D, info })],
];

function route() {
  try { current.dispose(); } catch { /* abaikan */ }
  current = { dispose() {} };
  const hash = location.hash || '#/';
  if (/^#\/kejadian/.test(hash)) P.unseen = 0;
  renderNav(hash);
  for (const [re, fn] of ROUTES) {
    if (re.test(hash)) { current = fn() || current; window.scrollTo(0, 0); view.focus({ preventScroll: true }); return; }
  }
  location.hash = '#/';
}
window.addEventListener('hashchange', route);

// ------------------------------------------------------------------ penanda status
const CHIP = {
  memantau: 'Memantau', belajar: 'Belajar', perlu_area: 'Tandai zona', jeda: 'Dijeda', menunggu: 'Menunggu',
};

function renderChip() {
  let code = 'idle', text = 'Standby';
  if (D.phase === 'asking') [code, text] = ['asking', 'Izin kamera'];
  else if (D.phase === 'connecting') [code, text] = ['connecting', 'Menyambung'];
  else if (['insecure', 'denied', 'error', 'taken'].includes(D.phase)) [code, text] = ['off', 'Kamera mati'];
  else if (D.live) {
    const status = D.state ? D.state.status : D.cam.status;
    if (D.error) [code, text] = ['offline', 'Terputus'];
    else [code, text] = [status, CHIP[status] || 'Standby'];
  }
  const chip = $id('conn');
  chip.dataset.st = code;
  setText(chip.querySelector('span'), text);
}

// ------------------------------------------------------------------ kejadian baru
async function announce(from) {
  try {
    const { events } = await getJSON(`/api/cams/${D.id}/events?after=${from}&limit=20`);
    if (!events.length) return;
    const onFeed = /^#\/kejadian/.test(location.hash);
    if (!onFeed) P.unseen += events.length;
    renderNav(location.hash || '#/');
    for (const e of events.slice(-2)) toast(KIND[e.kind] || e.label, e.reason, e.kind);
    if (navigator.vibrate) navigator.vibrate(events.some((e) => e.kind === 'parkir') ? [160, 80, 160] : 160);
    for (const fn of [...listeners]) fn(events);
  } catch { /* pengumuman berikutnya akan menyusul */ }
}

let wasLive = false;
function onDevice() {
  renderChip();
  if (D.live && !wasLive && /^#\/setelan/.test(location.hash)) route();     // setelan menunggu perangkat tersambung
  wasLive = D.live;
  const st = D.state;
  if (!st || !D.cam) return;
  if (P.camId !== D.cam.id || P.lastSeq === null) {      // pertama tersambung: kejadian lama tidak diumumkan ulang
    P.camId = D.cam.id;
    P.lastSeq = st.seq;
    return;
  }
  if (st.seq > P.lastSeq) {
    const from = P.lastSeq;
    P.lastSeq = st.seq;
    announce(from);
  }
}

// ------------------------------------------------------------------------- mulai
async function boot() {
  try { info = await getJSON('/api/info'); } catch { /* galat koneksi ditampilkan oleh perangkat */ }
  D.on(onDevice);
  route();
  renderChip();
  D.start();                                             // langsung menyala: tanpa tombol mulai, tanpa pilihan sumber
  if ('serviceWorker' in navigator && window.isSecureContext) navigator.serviceWorker.register('/sw.js').catch(() => {});
}
boot();

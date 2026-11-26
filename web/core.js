// Pembantu bersama: pelolos HTML, penyimpanan lokal, ikon, toast, lightbox, format waktu.

export const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const $id = (id) => document.getElementById(id);

/** Ubah teks hanya bila berbeda: wilayah aria-live tidak diumumkan ulang untuk isi yang sama. */
export const setText = (el, text) => { if (el && el.textContent !== text) el.textContent = text; };

export const keep = {                                  // localStorage bisa melempar galat (mode privat)
  get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* abaikan */ } },
  del: (k) => { try { localStorage.removeItem(k); } catch { /* abaikan */ } },
};

export const KIND = { parkir: 'Parkir di trotoar', naik_trotoar: 'Naik trotoar' };

const I = (d) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
export const ICON = {
  pantau: I('<path d="M4 8V5h3M20 8V5h-3M4 16v3h3M20 16v3h-3"/><circle cx="12" cy="12" r="3.2"/>'),
  laporan: I('<path d="M4 20h16"/><path d="M6 20v-7M11 20V5M16 20v-10M21 20v-4" transform="translate(-1.5 0)"/>'),
  kejadian: I('<path d="M8 6h12M8 12h12M8 18h12"/><path d="M4 6h.01M4 12h.01M4 18h.01"/>'),
  setelan: I('<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>'),
  pause: I('<path d="M9 5v14M15 5v14"/>'),
  play: I('<path d="M8 5l11 7-11 7z"/>'),
  zone: I('<path d="M4 7l8-4 8 4v9l-8 5-8-5z"/>'),
  sync: I('<path d="M20 12a8 8 0 01-14 5M4 12a8 8 0 0114-5"/><path d="M18 3v4h-4M6 21v-4h4"/>'),
  auto: I('<path d="M5 19L15 9"/><path d="M14 4v3M18 8h3M17 5l2-2M10 6l-1.5-1.5M20 12l1.5 1.5"/>'),
  flip: I('<path d="M4 8h13l-3-3M20 16H7l3 3"/>'),
  dl: I('<path d="M12 4v12M7 11l5 5 5-5"/><path d="M4 20h16"/>'),
  print: I('<path d="M7 9V4h10v5"/><rect x="4" y="9" width="16" height="8" rx="1.5"/><path d="M7 14h10v6H7z"/>'),
  trash: I('<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>'),
  check: I('<path d="M5 12.5l4.5 4.5L19 7.5"/>'),
  close: I('<path d="M6 6l12 12M18 6L6 18"/>'),
  undo: I('<path d="M9 14L4 9l5-5"/><path d="M4 9h10a6 6 0 010 12h-3"/>'),
};

export function toast(title, text = '', kind = '') {
  const el = document.createElement('div');
  el.className = 'toast';
  el.setAttribute('role', kind === 'error' ? 'alert' : 'status');
  if (kind) el.dataset.k = kind;
  const b = document.createElement('b');
  b.textContent = title;
  el.append(b);
  if (text) { const s = document.createElement('span'); s.textContent = text; el.append(s); }
  const wrap = $id('toasts');
  while (wrap.children.length >= 2) wrap.firstElementChild.remove();   // maksimal dua; jangan menutupi layar
  el.addEventListener('click', () => el.remove());
  wrap.appendChild(el);
  setTimeout(() => el.remove(), kind === 'error' ? 7000 : 4500);
}

export function lightbox(src) {
  const el = document.createElement('div');
  el.className = 'lightbox';
  el.tabIndex = -1;
  const img = document.createElement('img');
  img.src = src;
  img.alt = 'Bukti kejadian, ukuran penuh';
  el.append(img);
  const close = () => { el.remove(); document.removeEventListener('keydown', onKey); };
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  el.addEventListener('click', close);
  document.addEventListener('keydown', onKey);
  document.body.appendChild(el);
}

export function showError(host, err, tone = 'bad') {
  host.textContent = '';
  const box = document.createElement('div');
  box.className = 'notice';
  box.dataset.tone = tone;
  box.setAttribute('role', 'alert');
  const b = document.createElement('b');
  b.textContent = 'Gagal';
  box.append(b, document.createTextNode(err.message || String(err)));
  host.append(box);
}

const pad = (n) => String(n).padStart(2, '0');
export const clock = (d = new Date()) => `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
const MONTH = new Intl.DateTimeFormat('id-ID', { day: 'numeric', month: 'short' });

/** "Baru saja", "5 mnt lalu", "Hari ini 14.32", "Kemarin 09.10", "12 Nov 09.10". */
export function ago(iso, now = new Date()) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const diff = (now - d) / 1000;
  const hm = `${pad(d.getHours())}.${pad(d.getMinutes())}`;
  if (diff < 45) return 'Baru saja';
  if (diff < 3600) return `${Math.max(1, Math.round(diff / 60))} mnt lalu`;
  const day = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((day(now) - day(d)) / 86400000);
  if (days === 0) return `Hari ini ${hm}`;
  if (days === 1) return `Kemarin ${hm}`;
  return `${MONTH.format(d)} ${hm}`;
}

/** Lama dalam bahasa sehari-hari, mis. "2 mnt 24 dtk". Angka dan satuan tidak dipisah saat baris dipatah. */
export function dur(s) {
  if (s == null || !Number.isFinite(s)) return '';
  s = Math.round(s);
  const N = ' ';
  if (s < 60) return `${s}${N}dtk`;
  const m = Math.floor(s / 60), d = s % 60;
  if (s < 3600) return d ? `${m}${N}mnt ${d}${N}dtk` : `${m}${N}mnt`;
  const j = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60);
  return mm ? `${j}${N}jam ${mm}${N}mnt` : `${j}${N}jam`;
}

/** Satu baris kejadian: foto bukti, jenis, waktu, perangkat, dan lamanya bila sudah terukur. */
export function eventRow(e, { fresh = false } = {}) {
  const row = document.createElement('button');
  row.type = 'button';
  row.className = `event${fresh ? ' fresh' : ''}`;
  row.dataset.k = e.kind;
  row.dataset.no = e.no;
  const thumb = e.snapshot_url
    ? `<img src="${esc(e.snapshot_url)}" alt="" loading="lazy" width="320" height="180">`
    : '<span class="noimg" aria-hidden="true"></span>';
  const d = dur(e.durasi_s);
  row.innerHTML = `${thumb}<span class="ev-body">
    <b>${esc(KIND[e.kind] || e.label || '')}</b>
    <span class="ev-meta"><span>${esc(ago(e.ts))}</span>${e.camera ? `<span>${esc(e.camera.name)}</span>` : ''}</span>
    ${d ? `<span class="ev-dur">Berlangsung ${esc(d)}</span>` : ''}
    <span class="ev-why">${esc(e.reason || '')}</span></span>`;
  if (e.snapshot_url) row.addEventListener('click', () => lightbox(e.snapshot_url));
  else row.disabled = true;
  return row;
}

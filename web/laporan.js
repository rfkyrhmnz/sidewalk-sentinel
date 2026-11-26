// Halaman Laporan: ringkasan angka dan grafik batang bertumpuk. Modul ini hanya merender data dari /api/report;
// tidak menyentuh keadaan aplikasi lain. Semua teks dari server dimasukkan lewat textContent atau esc().

const NS = 'http://www.w3.org/2000/svg';
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

export const SERIES = [
  { key: 'parkir', label: 'Parkir di trotoar', short: 'Parkir', color: 'var(--k-parkir)' },
  { key: 'naik_trotoar', label: 'Naik trotoar', short: 'Naik trotoar', color: 'var(--k-naik)' },
];

export function fmtDur(s) {
  if (s == null || !Number.isFinite(s)) return '—';
  s = Math.round(s);
  const N = '\u00a0';                                   // angka dan satuannya tidak dipisah saat baris dipatah
  if (s < 60) return `${s}${N}dtk`;
  const m = Math.floor(s / 60), d = s % 60;
  if (s < 3600) return d ? `${m}${N}mnt ${d}${N}dtk` : `${m}${N}mnt`;
  const j = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60);
  return mm ? `${j}${N}jam ${mm}${N}mnt` : `${j}${N}jam`;
}

export const hourLabel = (h) => `${String(h).padStart(2, '0')}.00`;

const DATE = new Intl.DateTimeFormat('id-ID', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
const SHORT = new Intl.DateTimeFormat('id-ID', { day: 'numeric', month: 'short' });
const FULL = new Intl.DateTimeFormat('id-ID', { day: 'numeric', month: 'long', year: 'numeric' });

export function periodText(r) {
  const a = new Date(r.dari), b = new Date(r.sampai);
  if (r.periode === 'hari_ini') return DATE.format(b);
  return `${SHORT.format(a)} sampai ${FULL.format(b)}`;
}

/** Sumbu y bilangan bulat: paling banyak 4 selang, batas atas kelipatan langkah yang rapi. */
export function niceTicks(max) {
  if (max <= 4) return { top: Math.max(max, 1), ticks: Array.from({ length: Math.max(max, 1) + 1 }, (_, i) => i) };
  const raw = max / 4, mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= raw);
  const top = Math.ceil(max / step) * step;
  return { top, ticks: Array.from({ length: top / step + 1 }, (_, i) => i * step) };
}

const el = (tag, attrs = {}, text) => {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (text != null) n.textContent = text;
  return n;
};

/** Jalur batang: pangkal rata, ujung atas membulat (r <= 4). */
function topRounded(x, y, w, h, r) {
  r = Math.max(0, Math.min(r, w / 2, h));
  return `M${x} ${y + h}V${y + r}${r ? `a${r} ${r} 0 0 1 ${r} ${-r}` : ''}H${x + w - r}${r ? `a${r} ${r} 0 0 1 ${r} ${r}` : ''}V${y + h}Z`;
}

/**
 * Batang bertumpuk. slots: [{ label, title, values: { parkir, naik_trotoar } }], xTicks: [{ i, text }].
 * Menggambar legenda, grafik (celah 2 px antar segmen, tooltip untuk pointer/sentuh/fokus papan ketik), dan
 * tampilan tabel yang sama datanya (tooltip tidak pernah menjadi satu-satunya jalan ke sebuah angka).
 */
export function stackedColumns(host, { slots, xTicks, caption, unit = 'kejadian', maxBar = 24 }) {
  host.textContent = '';
  host.classList.add('rc');
  const W = 336, H = 158, M = { l: 26, r: 4, t: 18, b: 22 };
  const plotW = W - M.l - M.r, plotH = H - M.t - M.b;
  const totals = slots.map((s) => SERIES.reduce((n, k) => n + (s.values[k.key] || 0), 0));
  const max = Math.max(0, ...totals);
  const { top, ticks } = niceTicks(max);
  const slotW = plotW / slots.length, bw = Math.min(maxBar, Math.max(3, slotW * 0.62));
  const y = (v) => M.t + plotH - (v / top) * plotH;

  const legend = document.createElement('div');
  legend.className = 'rc-legend';
  for (const s of SERIES) {
    const item = document.createElement('span');
    const sw = document.createElement('i');
    sw.style.background = s.color;
    sw.setAttribute('aria-hidden', 'true');
    item.append(sw, s.label);
    legend.append(item);
  }

  const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, class: 'rc-svg', role: 'group', 'aria-label': caption });
  for (const t of ticks) {
    svg.append(el('line', { x1: M.l, x2: W - M.r, y1: y(t), y2: y(t), class: t === 0 ? 'rc-base' : 'rc-grid' }));
    svg.append(el('text', { x: M.l - 5, y: y(t) + 3.5, 'text-anchor': 'end', class: 'rc-tick' }, String(t)));
  }
  for (const { i, text } of xTicks) {
    svg.append(el('text', { x: M.l + (i + 0.5) * slotW, y: H - 6, 'text-anchor': 'middle', class: 'rc-tick' }, text));
  }

  const GAP = 2;
  let peakDrawn = false;
  slots.forEach((s, i) => {
    const cx = M.l + (i + 0.5) * slotW, x = cx - bw / 2;
    let base = y(0);
    const live = SERIES.filter((k) => (s.values[k.key] || 0) > 0);
    live.forEach((k, j) => {
      const full = ((s.values[k.key] || 0) / top) * plotH;
      const h = j === 0 ? full : Math.max(full - GAP, 2);        // celah dipotong dari segmen atas
      const yy = base - h - (j === 0 ? 0 : GAP);
      const isTop = j === live.length - 1;
      svg.append(el('path', { d: isTop ? topRounded(x, yy, bw, h, 4) : `M${x} ${yy}h${bw}v${h}h${-bw}Z`, fill: k.color, class: 'rc-bar' }));
      base = yy;
    });
    if (!peakDrawn && max > 0 && totals[i] === max) {            // satu label langsung: puncaknya saja
      peakDrawn = true;
      svg.append(el('text', { x: cx, y: base - 5, 'text-anchor': 'middle', class: 'rc-peak' }, String(max)));
    }
  });

  const tip = document.createElement('div');
  tip.className = 'rc-tip';
  tip.hidden = true;
  tip.setAttribute('aria-hidden', 'true');

  const show = (i) => {
    const s = slots[i];
    tip.textContent = '';
    const head = document.createElement('b');
    head.textContent = s.title;
    tip.append(head);
    for (const k of SERIES) {
      const row = document.createElement('span');
      const key = document.createElement('i');
      key.style.background = k.color;
      const v = document.createElement('strong');
      v.textContent = String(s.values[k.key] || 0);
      row.append(key, v, ` ${k.short.toLowerCase()}`);
      tip.append(row);
    }
    tip.hidden = false;
    const box = host.getBoundingClientRect(), sv = svg.getBoundingClientRect();
    const px = (M.l + (i + 0.5) * slotW) * (sv.width / W) + (sv.left - box.left);
    tip.style.left = `${Math.max(0, Math.min(px - tip.offsetWidth / 2, box.width - tip.offsetWidth))}px`;
    svg.querySelectorAll('.rc-hit').forEach((h, n) => h.classList.toggle('on', n === i));
  };
  const hide = () => {
    tip.hidden = true;
    svg.querySelectorAll('.rc-hit.on').forEach((h) => h.classList.remove('on'));
  };

  slots.forEach((s, i) => {
    const hit = el('rect', {
      x: M.l + i * slotW, y: 0, width: slotW, height: H - M.b, class: 'rc-hit', tabindex: '0', role: 'img',
      'aria-label': `${s.title}: ${SERIES.map((k) => `${s.values[k.key] || 0} ${k.short.toLowerCase()}`).join(', ')}`,
    });
    hit.addEventListener('pointerenter', () => show(i));
    hit.addEventListener('pointerdown', () => show(i));
    hit.addEventListener('pointerleave', (e) => { if (e.pointerType !== 'touch') hide(); });
    hit.addEventListener('focus', () => show(i));
    hit.addEventListener('blur', hide);
    svg.append(hit);
  });

  const onDoc = (e) => {                                         // sentuhan di luar grafik menutup tooltip
    if (!host.isConnected) document.removeEventListener('pointerdown', onDoc);
    else if (!host.contains(e.target)) hide();
  };
  document.addEventListener('pointerdown', onDoc);

  // tampilan tabel: setiap angka bisa dibaca tanpa hover, dan ikut tercetak
  const toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.className = 'btn line rc-toggle';
  toggle.setAttribute('aria-expanded', 'false');
  toggle.textContent = 'Lihat sebagai tabel';
  const table = document.createElement('div');
  table.className = 'rc-table';
  table.hidden = true;
  const t = document.createElement('table');
  t.className = 'table';
  const cap = document.createElement('caption');
  cap.className = 'sr-only';
  cap.textContent = caption;
  const head = t.createTHead().insertRow();
  for (const label of ['Waktu', ...SERIES.map((k) => k.short), 'Total']) {
    const th = document.createElement('th');
    th.scope = 'col';
    th.textContent = label;
    head.append(th);
  }
  const body = t.createTBody();
  slots.forEach((s, i) => {
    if (!totals[i]) return;
    const r = body.insertRow();
    r.insertCell().textContent = s.title;
    for (const k of SERIES) { const c = r.insertCell(); c.className = 'num'; c.textContent = String(s.values[k.key] || 0); }
    const c = r.insertCell(); c.className = 'num'; c.textContent = String(totals[i]);
  });
  t.prepend(cap);
  table.append(t);
  toggle.onclick = () => {
    table.hidden = !table.hidden;
    toggle.setAttribute('aria-expanded', String(!table.hidden));
    toggle.textContent = table.hidden ? 'Lihat sebagai tabel' : 'Sembunyikan tabel';
  };

  const stage = document.createElement('div');
  stage.className = 'rc-stage';
  stage.append(svg, tip);
  host.append(legend, stage, toggle, table);
  return { setTableOpen(open) { table.hidden = !open; toggle.setAttribute('aria-expanded', String(open)); toggle.textContent = open ? 'Sembunyikan tabel' : 'Lihat sebagai tabel'; }, isTableOpen: () => !table.hidden };
}

/** Irisan data untuk dua grafik dari satu laporan. */
export function hourSlots(r) {
  return Array.from({ length: 24 }, (_, h) => ({
    title: `Pukul ${hourLabel(h)}`, values: { parkir: r.per_jam.parkir[h], naik_trotoar: r.per_jam.naik_trotoar[h] },
  }));
}

export function daySlots(r) {
  return r.per_hari.map((d) => {
    const dt = new Date(`${d.tanggal}T12:00:00`);
    return { title: DATE.format(dt), values: { parkir: d.parkir, naik_trotoar: d.naik_trotoar }, short: SHORT.format(dt) };
  });
}

export function dayTicks(slots) {
  const n = slots.length;
  const at = n <= 7 ? slots.map((_, i) => i) : [0, Math.floor(n / 3), Math.floor((2 * n) / 3), n - 1];
  return at.map((i) => ({ i, text: slots[i].short }));
}

export const HOUR_TICKS = [0, 6, 12, 18, 23].map((h) => ({ i: h, text: String(h) }));

// ---------------------------------------------------------------------------- bagian ringkasan
const tile = (value, label, extra = '') => `<div class="rp-tile" ${extra}><b class="num">${value}</b><span>${label}</span></div>`;

/** Kartu ringkasan: total, per jenis, lama di trotoar. Semua angka berasal dari server (bilangan), label tetap. */
export function summaryHTML(r) {
  const l = r.lama, all = l.semua;
  const measured = all.n === 0
    ? 'Belum ada kejadian yang kendaraannya sudah pergi, jadi lamanya belum bisa dihitung.'
    : `Lama diukur dari ${all.n} dari ${r.total} kejadian. Sisanya belum selesai atau tercatat sebelum pengukuran lama ada.`;
  const peak = r.jam_puncak
    ? `Paling ramai pukul ${hourLabel(r.jam_puncak.jam)}, ${r.jam_puncak.jumlah} kejadian.` : '';
  return `
  <section class="rp-hero" aria-label="Total kejadian">
    <b class="num rp-total">${r.total}</b>
    <div class="stack"><span class="rp-total-l">kejadian di trotoar</span><span class="dim small">${esc(periodText(r))}</span></div>
  </section>
  <div class="rp-kinds">
    ${tile(r.parkir, 'Parkir di trotoar', 'data-k="parkir"')}
    ${tile(r.naik_trotoar, 'Naik trotoar', 'data-k="naik_trotoar"')}
  </div>
  <section class="stack" aria-labelledby="rpLama">
    <h2 class="h3" id="rpLama">Lama di trotoar</h2>
    <div class="rp-stats">
      ${tile(esc(fmtDur(all.rata)), 'Rata-rata', 'data-d="1"')}
      ${tile(esc(fmtDur(all.maks)), 'Terlama', 'data-d="1"')}
      ${tile(esc(fmtDur(all.min)), 'Tersingkat', 'data-d="1"')}
      ${tile(esc(fmtDur(all.total)), 'Total waktu', 'data-d="1"')}
    </div>
    <div class="rp-stats">
      ${tile(esc(fmtDur(l.parkir.rata)), 'Rata-rata parkir', 'data-d="1" data-k="parkir"')}
      ${tile(esc(fmtDur(l.naik_trotoar.rata)), 'Rata-rata naik trotoar', 'data-d="1" data-k="naik_trotoar"')}
    </div>
    <p class="faint small">Lama dihitung sejak kendaraan mulai diam atau bergerak di trotoar sampai ia meninggalkannya. ${esc(measured)}</p>
  </section>
  ${peak ? `<p class="rp-peak-note">${esc(peak)}</p>` : ''}`;
}

export function perCameraHTML(r) {
  if (r.per_kamera.length < 2) return '';
  const rows = r.per_kamera.map((c) => `<tr><th scope="row">${esc(c.nama)}</th>
    <td class="num">${c.parkir}</td><td class="num">${c.naik_trotoar}</td><td class="num"><b>${c.total}</b></td></tr>`).join('');
  return `<section class="stack" aria-labelledby="rpCam"><h2 class="h3" id="rpCam">Per perangkat</h2>
    <div class="panel rp-cams"><table class="table"><thead><tr><th scope="col">Perangkat</th><th scope="col">Parkir</th>
    <th scope="col">Naik trotoar</th><th scope="col">Total</th></tr></thead><tbody>${rows}</tbody></table></div></section>`;
}

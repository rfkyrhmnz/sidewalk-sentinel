import { ApiError, getJSON, postJSON, upload } from './api.js';
import { PRESET, RoiEditor } from './roi.js';
import { LiveLoop, Pusher, cameraSupported, drawTracks, openCamera, snapshot, mountVideo } from './live.js';
import { HOUR_TICKS, dayTicks, daySlots, fmtDur, hourSlots, perCameraHTML, periodText, stackedColumns, summaryHTML } from './laporan.js';

const view = document.getElementById('view');
const S = {
  info: null,
  upload: null,           // {upload_id, info, name}
  roi: [],
  refT: 0,
  settings: {
    park_seconds: 10, sens: 3, size: 'sedang', filter_pedestrians: true,
    use_homomorphic: true, use_shadow_removal: true, use_morphology: true, use_clahe: false, adaptive_ref: true,
  },
};
let current = { dispose() {} };
const pusher = new Pusher();                           // kamera perangkat ini; hidup selama aplikasi terbuka
const keep = {                                         // localStorage bisa melempar galat (mode privat)
  get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* abaikan */ } },
  del: (k) => { try { localStorage.removeItem(k); } catch { /* abaikan */ } },
};

// ---------------------------------------------------------------------------- util
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = (s) => { s = Math.max(0, Math.round(s)); return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`; };
const KIND = { parkir: 'Parkir di trotoar', naik_trotoar: 'Naik trotoar' };
const go = (hash) => { location.hash = hash; };

const I = (d, extra = '') => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}${extra}</svg>`;
const ICON = {
  home: I('<path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/>'),
  camera: I('<path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>'),
  history: I('<path d="M3 12a9 9 0 109-9"/><path d="M3 4v5h5"/><path d="M12 8v5l3 2"/>'),
  layers: I('<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>'),
  rec: I('<circle cx="12" cy="12" r="8.5"/>', '<circle cx="12" cy="12" r="3.2" fill="currentColor" stroke="none"/>'),
  up: I('<path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 16v4h16v-4"/>'),
  undo: I('<path d="M9 14L4 9l5-5"/><path d="M4 9h10a6 6 0 010 12h-3"/>'),
  trash: I('<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>'),
  dl: I('<path d="M12 4v12M7 11l5 5 5-5"/><path d="M4 20h16"/>'),
  flip: I('<path d="M4 8h13l-3-3M20 16H7l3 3"/>'),
  stop: I('<rect x="6" y="6" width="12" height="12" rx="2"/>'),
  sync: I('<path d="M20 12a8 8 0 01-14 5M4 12a8 8 0 0114-5"/><path d="M18 3v4h-4M6 21v-4h4"/>'),
  radar: I('<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4.5"/><path d="M12 12l6.5-5.5"/>'),
  report: I('<path d="M4 20h16"/><path d="M6 20v-7M11 20V5M16 20v-10M21 20v-4" transform="translate(-1.5 0)"/>'),
  print: I('<path d="M7 9V4h10v5"/><rect x="4" y="9" width="16" height="8" rx="2"/><path d="M7 14h10v6H7z"/>'),
  list: I('<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1.2"/><circle cx="4.5" cy="12" r="1.2"/><circle cx="4.5" cy="18" r="1.2"/>'),
  film: I('<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 9.5h18M3 14.5h18M8 5v14M16 5v14"/>'),
  plus: I('<path d="M12 5v14M5 12h14"/>'),
  pause: I('<path d="M9 5v14M15 5v14"/>'),
  play: I('<path d="M7 4.5l12 7.5-12 7.5z"/>'),
  area: I('<path d="M4 7l8-4 8 4v9l-8 5-8-5z"/>'),
  sliders: I('<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>'),
  back: I('<path d="M15 5l-7 7 7 7"/>'),
};

function toast(title, text = '', kind = '') {
  const el = document.createElement('div');
  el.className = 'toast';
  if (kind) el.dataset.k = kind;
  el.innerHTML = `<b>${esc(title)}</b>${text ? `<span class="dim small">${esc(text)}</span>` : ''}`;
  const wrap = document.getElementById('toasts');
  while (wrap.children.length >= 2) wrap.firstElementChild.remove();   // maksimal dua; jangan menutupi layar
  el.addEventListener('click', () => el.remove());
  wrap.appendChild(el);
  setTimeout(() => el.remove(), kind === 'error' ? 7000 : 4500);
}

function lightbox(src) {
  const el = document.createElement('div');
  el.className = 'lightbox';
  el.innerHTML = `<img src="${esc(src)}" alt="Bukti kejadian, ukuran penuh">`;
  el.addEventListener('click', () => el.remove());
  document.body.appendChild(el);
}

function stepper(n) {
  const names = ['Video', 'Area', 'Atur', 'Proses'];
  return `<ol class="steps" aria-label="Langkah">${names.map((t, i) =>
    `<li data-s="${i + 1 < n ? 'done' : i + 1 === n ? 'now' : ''}" ${i + 1 === n ? 'aria-current="step"' : ''}><b>${i + 1}</b>${t}</li>`).join('')}</ol>`;
}

const SENS = { 1: 34, 2: 28, 3: 22, 4: 17, 5: 13 };                  // kepekaan -> diff_floor
const SIZE = { kecil: 0.0008, sedang: 0.0015, besar: 0.004 };         // ukuran minimum -> min_area_frac
const nearest = (map, v, dflt) => {
  const n = Number(v);
  if (!Number.isFinite(n)) return dflt;
  return Object.keys(map).reduce((best, k) => (Math.abs(map[k] - n) < Math.abs(map[best] - n) ? k : best));
};

function overrides() {
  const s = S.settings;
  return {
    park_seconds: Number(s.park_seconds),
    diff_floor: SENS[s.sens],
    min_area_frac: SIZE[s.size],
    filter_pedestrians: s.filter_pedestrians,
    use_homomorphic: s.use_homomorphic,
    use_shadow_removal: s.use_shadow_removal,
    use_morphology: s.use_morphology,
    use_clahe: s.use_clahe,
    ref_update_rate: s.adaptive_ref ? 0.002 : 0,
  };
}

function showError(host, err, tone = 'bad') {
  host.innerHTML = `<div class="notice" data-tone="${tone}" role="alert"><b>Gagal</b>${esc(err.message || err)}</div>`;
}

// --------------------------------------------------------------------------- router
const NAV = [
  ['#/', 'Pantau', ICON.radar, /^#\/?$|^#\/(pantau|tambah)/, false],
  ['#/laporan', 'Laporan', ICON.report, /^#\/laporan/, false],
  ['#/kejadian', 'Kejadian', ICON.list, /^#\/kejadian/, true],
  ['#/analisis', 'Analisis', ICON.film, /^#\/(analisis|area|atur|proses|hasil|live|riwayat)/, false],
  ['#/tahapan', 'Tahapan', ICON.layers, /^#\/tahapan/, false],
];

function renderNav(hash) {
  document.getElementById('nav').innerHTML = NAV.map(([href, label, icon, re, badge]) => {
    const n = badge ? P.unseen : 0;
    const tag = n ? `<i class="nav-badge" aria-hidden="true">${n > 99 ? '99+' : n}</i>` : '';
    const sr = n ? `<span class="sr-only">, ${n} baru</span>` : '';
    return `<a href="${href}" ${re.test(hash) ? 'aria-current="page"' : ''}>${icon}<span>${label}${sr}</span>${tag}</a>`;
  }).join('');
}

const ROUTES = [
  [/^#\/?$/, viewPantau],
  [/^#\/tambah$/, viewAddCam],
  [/^#\/pantau\/([0-9a-f]{12})$/, viewCam],
  [/^#\/pantau\/([0-9a-f]{12})\/area$/, viewCamArea],
  [/^#\/laporan$/, viewLaporan],
  [/^#\/kejadian$/, viewFeed],
  [/^#\/analisis$/, viewHome],
  [/^#\/area$/, viewArea],
  [/^#\/atur$/, viewSettings],
  [/^#\/proses\/([0-9a-f]{12})$/, viewProcess],
  [/^#\/hasil\/([0-9a-f]{12})$/, viewResult],
  [/^#\/live$/, viewLive],
  [/^#\/riwayat$/, viewHistory],
  [/^#\/tahapan$/, viewPipeline],
];

function route() {
  try { current.dispose(); } catch { /* abaikan */ }
  current = { dispose() {} };
  const hash = location.hash || '#/';
  if (/^#\/kejadian/.test(hash)) P.unseen = 0;
  renderNav(hash);
  for (const [re, fn] of ROUTES) {
    const m = hash.match(re);
    if (m) { fn(...m.slice(1)); window.scrollTo(0, 0); view.focus({ preventScroll: true }); return; }
  }
  go('#/');
}
window.addEventListener('hashchange', route);

// ---------------------------------------------------------------------------- beranda
const HERO = `
<svg viewBox="0 0 400 230" role="img" aria-label="Animasi: sepeda motor naik dari jalan ke trotoar, dibingkai, lalu ditandai parkir">
  <rect width="400" height="230" fill="#0d1930"/>
  <rect width="400" height="112" fill="#0c1528"/>
  <line x1="0" y1="52" x2="400" y2="52" stroke="#2b4066" stroke-width="3" stroke-dasharray="26 18"/>
  <rect y="112" width="400" height="9" fill="#6a7fa3"/>
  <rect y="121" width="400" height="109" fill="#13223d"/>
  <g stroke="#1b2e4d" stroke-width="1">
    <path d="M40 121v109M80 121v109M120 121v109M160 121v109M200 121v109M240 121v109M280 121v109M320 121v109M360 121v109"/>
    <path d="M0 148h400M0 175h400M0 202h400"/>
  </g>
  <line x1="0" y1="216" x2="400" y2="216" stroke="#ffd31f" stroke-width="6" stroke-dasharray="1 9" stroke-linecap="round"/>
  <polygon points="2,126 398,126 398,224 2,224" fill="rgba(56,225,255,.07)" stroke="#38e1ff" stroke-width="1.5" stroke-dasharray="6 5"/>
  <text x="10" y="143" fill="#38e1ff" font-size="12">Area trotoar</text>
  <g class="veh">
    <rect x="0" y="0" width="54" height="30" rx="7" fill="#dce6f7"/>
    <rect x="35" y="4" width="14" height="22" rx="3" fill="#6f8bb5"/>
    <rect x="5" y="-3" width="14" height="5" rx="2" fill="#050a12"/><rect x="5" y="28" width="14" height="5" rx="2" fill="#050a12"/>
    <rect x="34" y="-3" width="14" height="5" rx="2" fill="#050a12"/><rect x="34" y="28" width="14" height="5" rx="2" fill="#050a12"/>
    <g class="tag-naik">
      <path d="M-7 8v-15h15M61 -7h15v15M-7 22v15h15M61 37h15v-15" transform="translate(-4 0)" fill="none" stroke="#e056ff" stroke-width="3"/>
      <rect x="-10" y="-32" width="96" height="19" rx="3" fill="#0a1424" opacity=".88"/>
      <text x="-4" y="-18" fill="#e056ff" font-size="14">Naik trotoar</text>
    </g>
    <g class="tag-parkir">
      <path d="M-7 8v-15h15M61 -7h15v15M-7 22v15h15M61 37h15v-15" transform="translate(-4 0)" fill="none" stroke="#ff5a5f" stroke-width="3"/>
      <rect x="-10" y="-32" width="64" height="19" rx="3" fill="#0a1424" opacity=".88"/>
      <text x="-4" y="-18" fill="#ff5a5f" font-size="14">Parkir</text>
      <rect x="-11" y="41" width="86" height="4" fill="#3a2a33"/>
      <rect class="bar-dwell" x="-11" y="41" width="86" height="4" fill="#ff5a5f"/>
    </g>
  </g>
</svg>`;

async function viewHome() {
  const ffmpegNote = S.info && !S.info.ffmpeg
    ? `<div class="notice" data-tone="warn"><b>ffmpeg belum terpasang</b>Analisis tetap jalan. Video beranotasi dinonaktifkan; bukti tetap berupa foto.</div>` : '';
  view.innerHTML = `
  <div class="stack-lg" style="max-width:640px">
    <div class="stack">
      <h1 class="h2">Analisis rekaman video</h1>
      <p class="lead">Punya video trotoar? Unggah, tandai area, dan dapatkan laporan kejadian lengkap dengan foto bukti. Untuk pemantauan terus-menerus, tambahkan kamera di halaman Pantau.</p>
    </div>
    <div class="actions">
      <label class="btn primary block" id="recBtn" tabindex="0" role="button">${ICON.rec}<span>Rekam video</span>
        <input id="rec" type="file" accept="video/*" capture="environment" hidden></label>
      <label class="btn line block" tabindex="0" role="button">${ICON.up}<span>Pilih video</span>
        <input id="pick" type="file" accept="video/*,.mov,.mp4,.m4v,.webm,.mkv,.avi" hidden></label>
      <div class="actions row">
        <button class="btn quiet" id="demoBtn" type="button">Coba dengan video demo</button>
        <a class="btn quiet" href="#/riwayat">${ICON.history}<span>Riwayat analisis</span></a>
      </div>
    </div>
    <div id="homeMsg"></div>
    ${ffmpegNote}
  </div>`;

  const msg = document.getElementById('homeMsg');
  const onFile = async (file) => {
    if (!file) return;
    msg.innerHTML = `<div class="panel stack"><b>Mengunggah ${esc(file.name)}</b>
      <div class="kerb" aria-hidden="true"><i id="upBar"></i></div><span class="dim small" id="upTxt">0%</span></div>`;
    try {
      const res = await upload(file, (f) => {
        document.getElementById('upBar').style.width = `${Math.round(f * 100)}%`;
        document.getElementById('upTxt').textContent = `${Math.round(f * 100)}%`;
      });
      S.upload = res; S.roi = []; S.refT = 0;
      go('#/area');
    } catch (e) { showError(msg, e); }
  };
  document.getElementById('rec').addEventListener('change', (e) => onFile(e.target.files[0]));
  document.getElementById('pick').addEventListener('change', (e) => onFile(e.target.files[0]));
  for (const lab of view.querySelectorAll('label.btn')) {
    lab.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); lab.querySelector('input').click(); } });
  }
  document.getElementById('demoBtn').addEventListener('click', async () => {
    msg.innerHTML = `<div class="panel dim">Menyiapkan video demo…</div>`;
    try {
      const res = await postJSON('/api/demo');
      S.upload = res; S.roi = res.roi; S.refT = res.ref_time || 0;
      go('#/area');
    } catch (e) { showError(msg, e); }
  });
}

// ------------------------------------------------------------------------------ area
function viewArea() {
  if (!S.upload) return go('#/analisis');
  const dur = S.upload.info.duration || 0;
  view.innerHTML = `${stepper(2)}
  <div class="split">
    <div class="stack">
      <div class="frame" id="frameHost"><span class="badge chip" data-tone="scan" id="refChip">Referensi di ${S.refT.toFixed(1)} dtk</span></div>
      ${dur ? `<div class="field"><label for="tpos">Pilih frame (${fmt(0)} sampai ${fmt(dur)})</label>
        <div class="slider-row"><input id="tpos" type="range" min="0" max="${dur}" step="0.1" value="${S.refT}"><output id="tout">${S.refT.toFixed(1)} dtk</output></div></div>` : ''}
    </div>
    <div class="stack sticky">
      <h1 class="h2">Tandai area trotoar</h1>
      <p class="dim">Ketuk di sepanjang tepi trotoar untuk membuat area. Seret titik untuk memperbaiki. Kamera harus diam selama video.</p>
      <div class="actions row">
        <button class="btn line" id="undo" type="button">${ICON.undo}<span>Urungkan</span></button>
        <button class="btn line" id="clear" type="button">${ICON.trash}<span>Hapus</span></button>
      </div>
      <button class="btn line block" id="preset" type="button">Isi sepertiga bawah</button>
      <div class="panel stack">
        <b>Frame referensi</b>
        <p class="dim small">Pilih frame saat trotoar kosong, lalu tandai sebagai referensi. Kendaraan yang sudah ada di referensi tidak akan terdeteksi.</p>
        <button class="btn line block" id="setRef" type="button">Jadikan frame ini referensi</button>
      </div>
      <button class="btn primary block" id="next" type="button" disabled>Lanjut ke pengaturan</button>
      <p class="faint small" id="ptCount"></p>
    </div>
  </div>`;

  const host = document.getElementById('frameHost');
  const next = document.getElementById('next');
  const count = document.getElementById('ptCount');
  const editor = new RoiEditor(host, {
    onChange: (pts) => {
      next.disabled = pts.length < 3;
      count.textContent = pts.length < 3 ? `Butuh minimal 3 titik (sekarang ${pts.length}).` : `${pts.length} titik.`;
    },
  });
  if (S.roi.length) editor.setPoints(S.roi); else editor.onChange(editor.points);

  let t = S.refT;
  const loadFrame = (time) => {
    const img = new Image();
    img.onload = () => { editor.setImage(img); };
    img.onerror = () => toast('Frame tidak bisa dimuat', 'Coba pilih posisi lain.', 'error');
    img.src = `/api/uploads/${S.upload.upload_id}/frame.jpg?t=${time}&w=960`;
  };
  loadFrame(t);
  const slider = document.getElementById('tpos');
  let deb;
  if (slider) slider.addEventListener('input', () => {
    t = Number(slider.value);
    document.getElementById('tout').textContent = `${t.toFixed(1)} dtk`;
    clearTimeout(deb); deb = setTimeout(() => loadFrame(t), 120);
  });
  document.getElementById('undo').onclick = () => editor.undo();
  document.getElementById('clear').onclick = () => editor.clear();
  document.getElementById('preset').onclick = () => editor.setPoints(PRESET);
  document.getElementById('setRef').onclick = () => {
    S.refT = t;
    document.getElementById('refChip').textContent = `Referensi di ${t.toFixed(1)} dtk`;
    toast('Referensi disimpan', `Frame di ${t.toFixed(1)} detik dipakai sebagai trotoar kosong.`);
  };
  next.onclick = () => { S.roi = editor.points.map((p) => [p[0], p[1]]); go('#/atur'); };
  window.addEventListener('resize', () => editor.draw());
}

// -------------------------------------------------------------------------- pengaturan
function sw(id, title, hint, checked) {
  return `<label class="switch"><span>${title}<small>${hint}</small></span><input type="checkbox" id="${id}" ${checked ? 'checked' : ''}></label>`;
}

function viewSettings() {
  if (!S.upload || S.roi.length < 3) return go('#/analisis');
  const s = S.settings;
  const info = S.upload.info;
  view.innerHTML = `${stepper(3)}
  <div class="split">
    <div class="stack-lg">
      <div class="stack"><h1 class="h2">Atur deteksi</h1>
        <p class="dim">${esc(S.upload.name)}: ${info.width}×${info.height}, ${info.duration ? fmt(info.duration) : 'durasi tidak diketahui'}. Area ${S.roi.length} titik, referensi di ${S.refT.toFixed(1)} dtk.</p></div>
      <div class="field"><label for="park">Lama diam sampai dihitung parkir</label>
        <div class="slider-row"><input id="park" type="range" min="3" max="60" step="1" value="${s.park_seconds}"><output id="parkOut">${s.park_seconds} dtk</output></div>
        <span class="hint">Kendaraan yang diam di atas trotoar selama ini masuk kategori parkir.</span></div>
      <div class="field"><label for="sens">Kepekaan</label>
        <div class="slider-row"><input id="sens" type="range" min="1" max="5" step="1" value="${s.sens}"><output id="sensOut">${s.sens}</output></div>
        <span class="hint">Naikkan bila kendaraan gelap atau kecil tidak terdeteksi. Turunkan bila banyak deteksi palsu.</span></div>
      <div class="field"><span class="label">Ukuran objek minimum</span>
        <div class="seg" role="group" aria-label="Ukuran objek minimum">
          ${['kecil', 'sedang', 'besar'].map((k) => `<button type="button" data-size="${k}" aria-pressed="${s.size === k}">${k[0].toUpperCase() + k.slice(1)}</button>`).join('')}
        </div>
      </div>
    </div>
    <div class="stack">
      <h2 class="h3">Tahapan pengolahan citra</h2>
      <div class="panel">
        ${sw('f_ped', 'Saring pejalan kaki', 'Objek tinggi dan sempit tidak dihitung kendaraan.', s.filter_pedestrians)}
        ${sw('f_morph', 'Morfologi (Materi 3)', 'Opening, closing, dan isi lubang pada mask.', s.use_morphology)}
        ${sw('f_homo', 'Filter homomorfik (Materi 2)', 'Meredam perubahan cahaya yang pelan di domain frekuensi.', s.use_homomorphic)}
        ${sw('f_shadow', 'Buang bayangan (Materi 6)', 'Memisahkan bayangan dari objek lewat kecerahan dan warna.', s.use_shadow_removal)}
        ${sw('f_clahe', 'CLAHE (Materi 1)', 'Equalisasi histogram adaptif; bisa mengganggu perbandingan dengan referensi.', s.use_clahe)}
        ${sw('f_adapt', 'Perbarui referensi pelan-pelan', 'Mengikuti perubahan cahaya; kendaraan diam tidak ikut terserap.', s.adaptive_ref)}
      </div>
      <button class="btn primary block" id="start" type="button">Mulai analisis</button>
      <div id="startMsg"></div>
    </div>
  </div>`;

  const bind = (id, key, bool = true) => { document.getElementById(id).addEventListener('change', (e) => { s[key] = bool ? e.target.checked : e.target.value; }); };
  bind('f_ped', 'filter_pedestrians'); bind('f_morph', 'use_morphology'); bind('f_homo', 'use_homomorphic');
  bind('f_shadow', 'use_shadow_removal'); bind('f_clahe', 'use_clahe'); bind('f_adapt', 'adaptive_ref');
  document.getElementById('park').addEventListener('input', (e) => { s.park_seconds = Number(e.target.value); document.getElementById('parkOut').textContent = `${e.target.value} dtk`; });
  document.getElementById('sens').addEventListener('input', (e) => { s.sens = Number(e.target.value); document.getElementById('sensOut').textContent = e.target.value; });
  for (const b of view.querySelectorAll('[data-size]')) b.addEventListener('click', () => {
    s.size = b.dataset.size;
    for (const o of view.querySelectorAll('[data-size]')) o.setAttribute('aria-pressed', String(o === b));
  });
  document.getElementById('start').addEventListener('click', async (e) => {
    e.target.disabled = true;
    try {
      const r = await postJSON('/api/analyze', {
        upload_id: S.upload.upload_id, roi: S.roi, ref_time: S.refT, settings: overrides(), save_video: true,
      });
      localStorage.setItem('ss_last', r.job_id);
      go(`#/proses/${r.job_id}`);
    } catch (err) { e.target.disabled = false; showError(document.getElementById('startMsg'), err); }
  });
}

// ----------------------------------------------------------------------------- proses
function eventRow(jobId, ev, fresh = false, base = null) {
  const src = ev.snapshot ? (base ? `${base}/${ev.snapshot}` : `/api/jobs/${jobId}/${ev.snapshot}`) : '';
  return `<button class="event ${fresh ? 'fresh' : ''}" type="button" data-k="${ev.kind}" data-src="${esc(src)}">
    ${src ? `<img src="${esc(src)}" alt="Bukti ${esc(KIND[ev.kind])}" loading="lazy">` : '<span></span>'}
    <span class="stack"><b>${KIND[ev.kind]}</b>
      <span class="dim small">Terkonfirmasi di ${fmt(ev.t)}. Kendaraan #${ev.track_id}.</span>
      <span class="dim small">${esc(ev.reason)}.</span></span></button>`;
}

function wireEvents(root) {
  for (const b of root.querySelectorAll('.event[data-src]')) {
    b.onclick = () => { if (b.dataset.src) lightbox(b.dataset.src); };
  }
}

function viewProcess(id) {
  let mode = 'detect', stop = false, timer, redirect;
  const shown = new Set();
  view.innerHTML = `${stepper(4)}
  <div class="split">
    <div class="stack">
      <div class="frame"><img id="pv" alt="Pratinjau analisis" width="640" height="360"></div>
      <div class="seg" role="group" aria-label="Tampilan"><button type="button" data-m="detect" aria-pressed="true">Deteksi</button><button type="button" data-m="stages" aria-pressed="false">Tahapan pipeline</button></div>
    </div>
    <div class="stack-lg">
      <div class="stack"><h1 class="h2" id="ptitle">Menganalisis…</h1>
        <div class="kerb" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0" id="pbar"><i id="pfill"></i></div>
        <p class="dim small" id="pinfo">Menyiapkan</p></div>
      <div class="tally" aria-live="polite">
        <div data-k="parkir"><div class="n" id="cPark">0</div><div class="l">Parkir</div></div><div class="sep"></div>
        <div data-k="naik_trotoar"><div class="n" id="cRide">0</div><div class="l">Naik trotoar</div></div></div>
      <div class="events" id="feed"></div>
      <div class="actions"><button class="btn line danger" id="cancel" type="button">${ICON.stop}<span>Batalkan</span></button>
        <a class="btn primary" id="toRes" href="#/hasil/${id}" hidden>Lihat hasil</a></div>
      <div id="perr"></div>
    </div>
  </div>`;
  const $ = (q) => document.getElementById(q);
  for (const b of view.querySelectorAll('[data-m]')) b.onclick = () => {
    mode = b.dataset.m;
    for (const o of view.querySelectorAll('[data-m]')) o.setAttribute('aria-pressed', String(o === b));
  };
  $('cancel').onclick = async () => { try { await postJSON(`/api/jobs/${id}/cancel`); } catch (e) { toast('Gagal membatalkan', e.message, 'error'); } };

  const refreshPreview = () => {
    const probe = new Image();
    probe.onload = () => { $('pv').src = probe.src; };
    probe.src = `/api/jobs/${id}/preview.jpg?view=${mode}&_=${Date.now()}`;
  };

  const poll = async () => {
    if (stop) return;
    try {
      const j = await getJSON(`/api/jobs/${id}`);
      const pct = Math.round(j.progress * 100);
      $('pfill').style.width = `${pct}%`;
      $('pbar').setAttribute('aria-valuenow', String(pct));
      $('cPark').textContent = j.counts.parkir;
      $('cRide').textContent = j.counts.naik_trotoar;
      $('pinfo').textContent = j.state === 'queued' ? 'Menunggu giliran' : `${pct}%, detik ke-${Math.round(j.t)}${j.video && j.video.duration ? ` dari ${Math.round(j.video.duration)}` : ''}`;
      for (const ev of j.events) {
        if (shown.has(ev.no)) continue;
        shown.add(ev.no);
        $('feed').insertAdjacentHTML('afterbegin', eventRow(id, ev, true));
        toast(KIND[ev.kind], `Kendaraan #${ev.track_id} di detik ${fmt(ev.t)}.`, ev.kind);
      }
      wireEvents($('feed'));
      refreshPreview();
      if (j.state === 'done') {
        $('ptitle').textContent = 'Analisis selesai';
        $('cancel').hidden = true; $('toRes').hidden = false;
        redirect = setTimeout(() => go(`#/hasil/${id}`), 1600);
        return;
      }
      if (j.state === 'error' || j.state === 'cancelled') {
        $('ptitle').textContent = j.state === 'error' ? 'Analisis gagal' : 'Analisis dibatalkan';
        $('cancel').hidden = true;
        if (j.error) showError($('perr'), new Error(j.error));
        else $('perr').innerHTML = `<a class="btn line block" href="#/atur">Ubah pengaturan dan ulangi</a>`;
        return;
      }
    } catch (e) { showError($('perr'), e); }
    timer = setTimeout(poll, 700);
  };
  poll();
  current = { dispose() { stop = true; clearTimeout(timer); clearTimeout(redirect); } };
}

// ------------------------------------------------------------------------------ hasil
async function viewResult(id) {
  view.innerHTML = `<p class="dim">Memuat hasil…</p>`;
  let j;
  try { j = await getJSON(`/api/jobs/${id}`); } catch (e) { return showError(view, e); }
  if (j.state === 'queued' || j.state === 'running') return go(`#/proses/${id}`);
  if (j.state !== 'done') {
    view.innerHTML = `<div class="stack"><h1 class="h2">Analisis tidak selesai</h1><div id="e"></div><a class="btn line" href="#/analisis">Kembali ke analisis</a></div>`;
    return showError(document.getElementById('e'), new Error(j.error || 'Analisis dibatalkan.'), 'warn');
  }
  const dur = (j.video && j.video.duration) || Math.max(1, ...j.events.map((e) => e.t));
  const sum = j.summary || { parkir: 0, naik_trotoar: 0, kendaraan_unik: 0 };
  const ticks = j.events.map((e) => {
    const c = e.kind === 'parkir' ? 'var(--flare)' : 'var(--rider)';
    return `<button type="button" style="left:${Math.min(100, (e.t / dur) * 100)}%;--c:${c}" data-no="${e.no}" aria-label="${KIND[e.kind]} di ${fmt(e.t)}"></button>`;
  }).join('');
  view.innerHTML = `
  <div class="split">
    <div class="stack-lg">
      <div class="stack"><h1 class="h2">Hasil analisis</h1>
        <p class="dim">${sum.kendaraan_unik} kendaraan memicu peringatan. Diproses ${j.processing ? `${j.processing.seconds} detik (${j.processing.fps} frame/detik)` : ''}.</p></div>
      <div class="tally"><div data-k="parkir"><div class="n">${sum.parkir}</div><div class="l">Parkir di trotoar</div></div><div class="sep"></div>
        <div data-k="naik_trotoar"><div class="n">${sum.naik_trotoar}</div><div class="l">Naik trotoar</div></div></div>
      ${j.events.length ? `<div><div class="timeline" role="group" aria-label="Linimasa kejadian">${ticks}</div>
        <div class="timeline-scale"><span>00:00</span><span>${fmt(dur)}</span></div></div>` : ''}
      ${j.has_video ? `<div class="frame"><video id="vid" controls playsinline preload="metadata" src="/api/jobs/${id}/annotated.mp4"></video></div>` :
        `<div class="notice"><b>Video beranotasi tidak dibuat</b>Butuh ffmpeg di komputer server. Bukti tetap tersedia sebagai foto.</div>`}
    </div>
    <div class="stack-lg">
      <div class="stack"><h2 class="h3">Kejadian</h2>
        <div class="events" id="evs">${j.events.length ? j.events.map((e) => eventRow(id, e)).join('') : '<p class="dim">Tidak ada kendaraan yang terdeteksi parkir atau naik trotoar.</p>'}</div></div>
      <div class="actions">
        <a class="btn line" href="/api/jobs/${id}/result.csv?download=1">${ICON.dl}<span>Unduh CSV</span></a>
        <a class="btn line" href="/api/jobs/${id}/result.json?download=1">${ICON.dl}<span>Unduh JSON</span></a>
        <a class="btn primary" href="#/atur" id="again">${ICON.sync}<span>Ubah pengaturan dan ulangi</span></a>
      </div>
    </div>
  </div>`;
  wireEvents(document.getElementById('evs'));
  const again = document.getElementById('again');
  if (!S.upload || (j.upload_id && S.upload.upload_id !== j.upload_id)) again.setAttribute('href', '#/analisis');
  const vid = document.getElementById('vid');
  for (const b of view.querySelectorAll('.timeline button')) b.onclick = () => {
    const ev = j.events.find((e) => String(e.no) === b.dataset.no);
    if (vid && ev) { vid.currentTime = Math.max(0, ev.t - 2); vid.play().catch(() => {}); vid.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
  };
}

// ---------------------------------------------------------------------------- riwayat
async function viewHistory() {
  view.innerHTML = `<div class="stack-lg"><h1 class="h2">Riwayat analisis</h1><div id="hist" class="list"><p class="dim">Memuat…</p></div></div>`;
  const host = document.getElementById('hist');
  try {
    const { jobs } = await getJSON('/api/jobs');
    host.innerHTML = jobs.length ? jobs.map((j) => {
      const sum = j.summary || { parkir: 0, naik_trotoar: 0 };
      const when = new Date(j.created * 1000).toLocaleString('id-ID', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
      return `<a class="list-item" href="#/hasil/${j.id}"><b>${sum.parkir} parkir, ${sum.naik_trotoar} naik trotoar</b>
        <span class="chip" data-tone="${j.state === 'done' ? 'ok' : ''}">${j.state === 'done' ? 'Selesai' : j.state}</span>
        <span class="dim small">${when}${j.video && j.video.duration ? `, video ${fmt(j.video.duration)}` : ''}</span></a>`;
    }).join('') : `<div class="dropzone"><b>Belum ada analisis</b><span>Rekam atau unggah video dari halaman Analisis untuk memulai.</span><a class="btn primary" href="#/analisis">Ke analisis</a></div>`;
  } catch (e) { showError(host, e); }
}

// ---------------------------------------------------------------------------- tahapan
function viewPipeline() {
  const mats = [
    ['M1', 'Transformasi intensitas dan filter spasial', ['Penghalusan Gaussian sebelum selisih', 'Penyetaraan gain kecerahan terhadap referensi', 'Transformasi gamma dan CLAHE (opsional)']],
    ['M2', 'Filter di domain frekuensi', ['Filter homomorfik: log, FFT, high-emphasis, IFFT', 'Meredam gradien cahaya yang pelan, misalnya matahari bergeser']],
    ['M3', 'Morfologi citra', ['Opening membuang bintik derau', 'Closing menyambung objek yang maskernya terbelah', 'Pengisian lubang pada blob']],
    ['M4', 'Segmentasi citra', ['Selisih terhadap citra referensi trotoar kosong', 'Ambang global Otsu, dijepit antara batas bawah dan atas', 'Zona perhatian di sekitar area trotoar']],
    ['M5', 'Representasi dan deskripsi', ['Kontur, luas, keliling, centroid, rasio aspek', 'Solidity, compactness, momen Hu', 'Pelacakan centroid lalu kecepatan: diam atau bergerak']],
    ['M6', 'Pengolahan citra warna', ['CIELAB untuk memisahkan kecerahan dari warna', 'HSV untuk mendeteksi bayangan: kecerahan turun, warna tetap']],
  ];
  const abl = [
    ['Pipeline lengkap', '18/18'], ['Tanpa Materi 2 (homomorfik)', '12/18'], ['Tanpa Materi 6 (buang bayangan)', '15/18'],
    ['Tanpa bukti citra asli', '15/18'], ['Tanpa Materi 3 (morfologi)', '0/18'],
  ];
  view.innerHTML = `<div class="split">
    <div class="stack-lg"><div class="stack"><h1 class="h2">Enam materi, satu alur</h1>
      <p class="dim">Tiap frame melewati tahapan ini. Pilih "Tahapan pipeline" saat analisis berjalan untuk melihat keluaran tiap langkah.</p></div>
      <ol class="pipeline" style="list-style:none;margin:0;padding:0">${mats.map(([m, t, pts]) => `<li><span class="m">${m}</span><div><b>${t}</b><ul>${pts.map((p) => `<li>${p}</li>`).join('')}</ul></div></li>`).join('')}</ol></div>
    <div class="stack-lg">
      <div class="stack"><h2 class="h3">Seberapa berguna tiap materi</h2>
        <p class="dim small">Diuji pada adegan sintetis: 6 kondisi cahaya, 3 pengacakan derau. Nilai benar bila tepat 1 parkir dan 2 naik trotoar. Belum diuji pada rekaman lapangan.</p>
        <table class="table"><thead><tr><th scope="col">Konfigurasi</th><th scope="col" style="text-align:right">Benar</th></tr></thead>
        <tbody>${abl.map(([k, v]) => `<tr><td>${k}</td><td class="num">${v}</td></tr>`).join('')}</tbody></table>
        <p class="faint small">Baris morfologi gagal total karena adegan sengaja memuat kendaraan yang maskernya terbelah. Itu membuktikan mekanismenya, bukan seberapa sering terjadi di lapangan.</p></div>
      <div class="stack"><h2 class="h3">Batasan yang perlu diketahui</h2>
        <ul class="dim" style="margin:0;padding-left:20px;display:grid;gap:8px">
          <li>Kamera harus diam. Gerak kamera dibaca sebagai gerak kendaraan.</li>
          <li>Referensi harus frame trotoar kosong. Kendaraan yang sudah ada di referensi tidak terdeteksi, dan bila ia pergi, bekasnya bisa terbaca sebagai objek.</li>
          <li>Kendaraan gelap di bawah bayangan pekat bisa terbaca sebagai bayangan.</li>
          <li>Pengendara yang tampak dari depan bisa mirip pejalan kaki dan tersaring.</li>
        </ul></div>
    </div>
  </div>`;
}

// ------------------------------------------------------------------------------- live
function viewLive() {
  view.innerHTML = `<div class="stack-lg"><div class="stack"><h1 class="h2">Kamera langsung</h1>
    <p class="dim">Pantau trotoar dari kamera perangkat ini. Taruh perangkat di tempat yang stabil.</p></div><div id="liveBody"></div></div>`;
  const body = document.getElementById('liveBody');
  if (!cameraSupported()) {
    body.innerHTML = `<div class="notice" data-tone="warn"><b>Kamera langsung butuh koneksi aman</b>
      Browser hanya mengizinkan kamera di HTTPS atau localhost. Di HP, pakai Rekam video dari halaman Analisis, atau tambahkan kamera CCTV di halaman Pantau (server yang membaca kameranya, jadi tidak perlu HTTPS).</div>
      <div class="actions" style="margin-top:16px"><a class="btn primary" href="#/analisis">Rekam video dari halaman Analisis</a></div>`;
    return;
  }

  const video = document.createElement('video');
  let stream = null, facing = 'environment', loop = null, sid = null, roi = [];
  let phase = 'intro';
  const shown = new Set();
  const dispose = async () => {
    if (loop) loop.stop();
    if (stream) stream.getTracks().forEach((t) => t.stop());
    if (sid) { try { await postJSON(`/api/live/${sid}`, {}, 'DELETE'); } catch { /* sudah berakhir */ } }
  };
  current = { dispose() { dispose(); } };

  const frameWith = () => { const f = document.createElement('div'); f.className = 'frame'; return f; };

  const intro = () => {
    phase = 'intro';
    body.innerHTML = `<div class="actions"><button class="btn primary" id="on" type="button">${ICON.camera}<span>Nyalakan kamera</span></button></div><div id="lerr"></div>`;
    document.getElementById('on').onclick = async () => {
      try { stream = await openCamera(video, facing); preview(); }
      catch (e) { showError(document.getElementById('lerr'), new Error(e.name === 'NotAllowedError' ? 'Izin kamera ditolak. Izinkan kamera di pengaturan browser, lalu coba lagi.' : `Kamera tidak bisa dinyalakan: ${e.message}`)); }
    };
  };

  const preview = () => {
    phase = 'camera';
    body.innerHTML = `<div class="stack"><div id="fh"></div>
      <div class="actions row"><button class="btn line" id="flip" type="button">${ICON.flip}<span>Ganti kamera</span></button>
      <button class="btn primary" id="freeze" type="button">Bekukan dan tandai area</button></div></div>`;
    const f = frameWith(); document.getElementById('fh').appendChild(f); mountVideo(f, video);
    document.getElementById('flip').onclick = async () => {
      stream.getTracks().forEach((t) => t.stop());
      facing = facing === 'environment' ? 'user' : 'environment';
      try { stream = await openCamera(video, facing); } catch (e) { toast('Kamera tidak bisa diganti', e.message, 'error'); }
    };
    document.getElementById('freeze').onclick = () => drawArea(snapshot(video));
  };

  const drawArea = (snap) => {
    phase = 'area';
    body.innerHTML = `<div class="split"><div class="frame" id="fh"></div><div class="stack">
      <p class="dim">Ketuk di sepanjang tepi trotoar. Pastikan trotoar kosong saat kalibrasi dimulai.</p>
      <div class="actions row"><button class="btn line" id="undo" type="button">${ICON.undo}<span>Urungkan</span></button><button class="btn line" id="clear" type="button">${ICON.trash}<span>Hapus</span></button></div>
      <button class="btn line block" id="preset" type="button">Isi sepertiga bawah</button>
      <button class="btn primary block" id="go" type="button" disabled>Mulai kalibrasi</button>
      <button class="btn quiet block" id="back" type="button">Kembali ke kamera</button></div></div>`;
    const go_ = document.getElementById('go');
    const ed = new RoiEditor(document.getElementById('fh'), { onChange: (p) => { go_.disabled = p.length < 3; } });
    ed.setImage(snap); ed.setPoints(roi.length ? roi : []);
    document.getElementById('undo').onclick = () => ed.undo();
    document.getElementById('clear').onclick = () => ed.clear();
    document.getElementById('preset').onclick = () => ed.setPoints(PRESET);
    document.getElementById('back').onclick = preview;
    go_.onclick = () => { roi = ed.points.map((p) => [p[0], p[1]]); run(); };
  };

  const run = async () => {
    phase = 'run';
    const ov = overrides();
    try { const r = await postJSON('/api/live', { roi, settings: ov }); sid = r.sid; }
    catch (e) { return showError(body, e); }
    body.innerHTML = `<div class="split"><div class="stack"><div id="fh"></div>
      <div class="chip" id="lstat" data-tone="scan" role="status">Menyiapkan kalibrasi</div></div>
      <div class="stack-lg"><div class="tally"><div data-k="parkir"><div class="n" id="lp">0</div><div class="l">Parkir</div></div><div class="sep"></div>
      <div data-k="naik_trotoar"><div class="n" id="lr">0</div><div class="l">Naik trotoar</div></div></div>
      <div class="events" id="lfeed"></div>
      <div class="actions row"><button class="btn line" id="recal" type="button">${ICON.sync}<span>Kalibrasi lagi</span></button>
      <button class="btn line danger" id="halt" type="button">${ICON.stop}<span>Berhenti</span></button></div><div id="lerr"></div></div></div>`;
    const f = frameWith();
    const ov_ = document.createElement('canvas'); ov_.className = 'overlay';
    document.getElementById('fh').appendChild(f); mountVideo(f, video); f.appendChild(ov_);
    const sizeOverlay = () => { ov_.width = 640; ov_.height = Math.round(640 * (video.videoHeight || 360) / (video.videoWidth || 640)); };
    sizeOverlay();
    const stat = document.getElementById('lstat');
    loop = new LiveLoop({
      video, sid,
      onState: (d) => {
        if (d.state === 'calibrating') {
          stat.dataset.tone = 'paint';
          stat.textContent = `Kalibrasi ${d.calib.have} dari ${d.calib.need}. Jaga trotoar tetap kosong.`;
          drawTracks(ov_, [], ov.park_seconds);
        } else {
          stat.dataset.tone = 'ok'; stat.textContent = 'Memantau';
          drawTracks(ov_, d.tracks, ov.park_seconds);
        }
        document.getElementById('lp').textContent = d.counts.parkir;
        document.getElementById('lr').textContent = d.counts.naik_trotoar;
        for (const ev of d.new_events) {
          if (shown.has(ev.no)) continue;
          shown.add(ev.no);
          document.getElementById('lfeed').insertAdjacentHTML('afterbegin', eventRow(null, ev, true, `/api/live/${sid}`));
          wireEvents(document.getElementById('lfeed'));
          toast(KIND[ev.kind], `Kendaraan #${ev.track_id}.`, ev.kind);
          if (navigator.vibrate) navigator.vibrate(ev.kind === 'parkir' ? [200, 80, 200] : 120);
        }
      },
      onError: (e) => {
        if (e instanceof ApiError && e.status === 404) showError(document.getElementById('lerr'), new Error('Sesi berakhir. Mulai ulang kamera langsung.'));
        else if (e instanceof ApiError && e.status === 400) showError(document.getElementById('lerr'), e);
        else stat.textContent = 'Koneksi bermasalah, mencoba lagi';
      },
    });
    loop.start();
    document.getElementById('recal').onclick = async () => { try { await postJSON(`/api/live/${sid}/recalibrate`); } catch (e) { toast('Gagal', e.message, 'error'); } };
    document.getElementById('halt').onclick = async () => { await dispose(); sid = null; stream = null; intro(); };
    try { if (navigator.wakeLock) await navigator.wakeLock.request('screen'); } catch { /* tidak wajib */ }
  };
  intro();
}

// ============================================================================ pantau (mode CCTV)
// Server memantau kamera terus-menerus. Halaman ini hanya penampil: ia mengambil keadaan dari /api/cams
// tiap beberapa detik dan memberi tahu bila ada kejadian baru, di halaman mana pun pengguna berada.
const MONTH = ['Jan', 'Feb', 'Mar', 'Apr', 'Mei', 'Jun', 'Jul', 'Agu', 'Sep', 'Okt', 'Nov', 'Des'];
const STATUS = {
  perlu_area: ['paint', 'Perlu area'],
  menghubungkan: ['scan', 'Menghubungkan'],
  belajar: ['paint', 'Belajar latar'],
  memantau: ['ok', 'Memantau'],
  terputus: ['flare', 'Terputus'],
  jeda: ['', 'Dijeda'],
  selesai: ['', 'Selesai'],
};

const P = { cams: [], max: 8, now: '', unseen: 0, seen: new Map(), subs: new Set(), timer: 0, gen: 0 };
const camById = (id) => P.cams.find((c) => c.id === id);
const subscribe = (fn) => { P.subs.add(fn); return () => P.subs.delete(fn); };
const hhmm = (ts) => String(ts).slice(11, 16);

function whenLabel(ts) {
  ts = String(ts);
  if (ts.slice(0, 10) === String(P.now).slice(0, 10)) return `${hhmm(ts)} hari ini`;
  return `${hhmm(ts)}, ${Number(ts.slice(8, 10))} ${MONTH[Number(ts.slice(5, 7)) - 1] || ''}`;
}

function fmtSpan(sec) {
  sec = Math.max(0, Math.round(sec));
  if (sec < 60) return `${sec} detik`;
  if (sec < 3600) return `${Math.floor(sec / 60)} menit`;
  if (sec < 86400) return `${Math.floor(sec / 3600)} jam ${Math.floor((sec % 3600) / 60)} menit`;
  return `${Math.floor(sec / 86400)} hari ${Math.floor((sec % 86400) / 3600)} jam`;
}

const statusChip = (c) => {
  const [tone, label] = STATUS[c.status] || ['', c.status];
  return `<span class="chip" data-tone="${tone}" data-st="${esc(c.status)}"><i class="dot" aria-hidden="true"></i>${label}</span>`;
};

function swapImage(img, url) {                      // ganti gambar hanya setelah yang baru siap: tanpa kedip
  const probe = new Image();
  probe.onload = () => { if (img.isConnected) img.src = probe.src; };
  probe.src = url;
}

async function announce(cam, since) {
  try {
    const { events } = await getJSON(`/api/cams/${cam.id}/events?after=${since}&limit=5`);
    for (const ev of events) toast(KIND[ev.kind], `${cam.name}, ${whenLabel(ev.ts)}`, ev.kind);
    if (events.length && navigator.vibrate) navigator.vibrate(events.some((e) => e.kind === 'parkir') ? [200, 80, 200] : 120);
  } catch { /* pemberitahuan tidak wajib */ }
}

async function pollCams() {
  const conn = document.getElementById('conn');
  try {
    const r = await getJSON('/api/cams');
    P.cams = r.cams; P.max = r.max; P.now = r.now;
    conn.dataset.ok = '1'; conn.lastElementChild.textContent = 'Server siap';
    let fresh = 0;
    const ids = new Set();
    for (const c of r.cams) {
      ids.add(c.id);
      const prev = P.seen.get(c.id);
      if (prev === undefined || c.seq < prev) { P.seen.set(c.id, c.seq); continue; }   // pertama terlihat: jadi acuan
      if (c.seq > prev) { P.seen.set(c.id, c.seq); fresh += c.seq - prev; announce(c, prev); }
    }
    for (const id of [...P.seen.keys()]) if (!ids.has(id)) P.seen.delete(id);
    if (fresh && !/^#\/kejadian/.test(location.hash)) { P.unseen += fresh; renderNav(location.hash || '#/'); }
    for (const fn of [...P.subs]) { try { fn(); } catch (e) { console.error(e); } }
  } catch {
    conn.dataset.ok = '0'; conn.lastElementChild.textContent = 'Server tidak terjangkau';
  }
}

function startWatcher() {
  const loop = async () => {
    const mine = ++P.gen;
    await pollCams();
    if (mine === P.gen) P.timer = setTimeout(loop, document.hidden ? 8000 : 2000);
  };
  P.timer = setTimeout(loop, 2000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { clearTimeout(P.timer); loop(); } });
}

async function createCam(body) {
  const cam = await postJSON('/api/cams', body);
  await pollCams();                                  // jadikan acuan sebelum kejadian pertama
  return cam;
}

function secureNotice() {
  return `<div class="notice" data-tone="warn"><b>Kamera butuh koneksi aman</b>
    Browser hanya mengizinkan kamera di HTTPS atau localhost. Di laptop, buka <code>http://localhost:8000</code>.
    Di HP, jalankan server dengan <code>python main.py --lan --https</code>, buka tautan https yang dicetak,
    lalu terima peringatan sertifikat satu kali.</div>`;
}

// Jadikan perangkat ini kamera: minta izin dulu (agar penolakan tidak meninggalkan kamera kosong), buat kamera
// bersumber `browser` di server, mulai mengirim frame, lalu minta pengguna menandai area.
async function startDeviceCamera(host, name = '') {
  if (!cameraSupported()) { host.innerHTML = secureNotice(); return false; }
  const label = String(name).trim() || (matchMedia('(pointer: coarse)').matches ? 'Kamera HP' : 'Kamera laptop');
  try { await pusher.open(); } catch { showError(host, new Error(pusher.error)); return false; }
  let cam;
  try { cam = await createCam({ source: 'browser', name: label.slice(0, 40) }); }
  catch (e) { pusher.stop(); showError(host, e); return false; }
  pusher.begin(cam.id);
  keep.set('ss_push', cam.id);
  go(`#/pantau/${cam.id}/area`);
  return true;
}

// Penanda global: selama perangkat ini mengirim gambar, tampilkan di setiap halaman dan tautkan ke kameranya.
function renderCamLive() {
  const el = $id('camlive');
  const cam = pusher.active ? camById(pusher.camId) : null;
  if (!cam) { el.hidden = true; el.dataset.h = ''; return; }
  const st = (pusher.state && pusher.state.status) || cam.status;
  const markup = `<i class="dot" aria-hidden="true"></i><span>Perangkat ini sedang jadi CCTV: <b>${esc(cam.name)}</b>, ${esc((STATUS[st] || ['', st])[1].toLowerCase())}</span>`;
  el.hidden = false;
  el.href = `#/pantau/${cam.id}`;
  if (el.dataset.h !== markup) { el.dataset.h = markup; el.innerHTML = markup; }
}

function camEventRow(ev, camName, fresh = false) {
  const src = ev.snapshot_url || '';
  return `<button class="event ${fresh ? 'fresh' : ''}" type="button" data-k="${ev.kind}" data-src="${esc(src)}">
    ${src ? `<img src="${esc(src)}" alt="Bukti ${esc(KIND[ev.kind])}" loading="lazy">` : '<span></span>'}
    <span class="stack"><b>${KIND[ev.kind]}</b>
      <span class="dim small">${camName ? `${esc(camName)}, ` : ''}${whenLabel(ev.ts)}</span>
      <span class="dim small">${esc(ev.reason)}.</span>
      ${typeof ev.durasi_s === 'number' ? `<span class="dim small">Berlangsung ${fmtDur(ev.durasi_s)}.</span>` : ''}</span></button>`;
}

const $id = (q) => document.getElementById(q);
// Isi ulang teks hanya bila berubah: <main> adalah wilayah aria-live, jadi menulis nilai yang sama
// membuat pembaca layar mengumumkannya lagi tiap polling.
const setText = (el, v) => { v = String(v); if (el.textContent !== v) el.textContent = v; };

// -------------------------------------------------------------------------- daftar kamera
function viewPantau() {
  view.innerHTML = '<div id="pantau"></div>';
  const root = $id('pantau');
  let mode = '', sig = '';

  const camTile = (c) => `<a class="cam-tile" href="#/pantau/${c.id}" data-id="${c.id}">
    <div class="frame"><img class="thumb" alt="" width="640" height="360" src="/api/cams/${c.id}/snapshot.jpg?_=${Date.now()}"><span class="badge end" data-r="chip"></span></div>
    <div class="cam-meta"><b class="cam-name" data-r="name"></b>
      <span class="cam-counts"><span data-k="parkir"><b data-r="park">0</b> parkir</span><span data-k="naik_trotoar"><b data-r="ride">0</b> naik trotoar</span></span>
      <span class="dim small" data-r="last"></span></div></a>`;

  const buildEmpty = () => {
    mode = 'empty'; sig = '';
    root.innerHTML = `<div class="hero">
      <div class="hero-scene">${HERO}</div>
      <div class="hero-copy stack">
        <h1 class="h1">Perangkat ini jadi CCTV.</h1>
        <p class="lead">Nyalakan kamera HP atau laptop, arahkan ke trotoar, dan tandai area sekali saja. Deteksi berjalan langsung dan otomatis: kendaraan yang parkir atau naik ke trotoar tercatat lengkap dengan foto.</p>
        <div class="actions">
          <button class="btn primary block" id="useDevice" type="button">${ICON.camera}<span>Jadikan perangkat ini CCTV</span></button>
          <div class="actions row">
            <button class="btn line" id="demoCam" type="button">${ICON.play}<span>Coba demo</span></button>
            <a class="btn line" href="#/tambah">${ICON.plus}<span>Sumber lain</span></a></div>
        </div>
        <div id="pmsg"></div>
      </div></div>`;
    $id('useDevice').onclick = async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      if (!(await startDeviceCamera($id('pmsg')))) btn.disabled = false;
    };
    $id('demoCam').onclick = async (e) => {
      e.currentTarget.disabled = true;
      try { const cam = await createCam({ source: 'demo' }); go(`#/pantau/${cam.id}`); }
      catch (err) { e.currentTarget.disabled = false; showError($id('pmsg'), err); }
    };
  };

  const buildList = (idsig) => {
    mode = 'list'; sig = idsig;
    root.innerHTML = `<div class="stack-lg">
      <div class="head-row">
        <div class="stack"><h1 class="h2">Pantau trotoar</h1><p class="dim" id="psub"></p></div>
        <a class="btn primary" id="addBtn" href="#/tambah">${ICON.plus}<span>Tambah kamera</span></a>
      </div>
      <div class="tally" aria-live="off">
        <div data-k="parkir"><div class="n" id="tp">0</div><div class="l">Parkir hari ini</div></div><div class="sep"></div>
        <div data-k="naik_trotoar"><div class="n" id="tr">0</div><div class="l">Naik trotoar hari ini</div></div></div>
      <div class="cam-grid" id="grid">${P.cams.map(camTile).join('')}</div>
    </div>`;
  };

  const patch = () => {
    const cams = P.cams;
    if (!cams.length) { if (mode !== 'empty') buildEmpty(); return; }
    const idsig = cams.map((c) => c.id).join(',');
    if (mode !== 'list' || idsig !== sig) buildList(idsig);
    let park = 0, ride = 0, on = 0;
    for (const c of cams) {
      park += c.counts_today.parkir; ride += c.counts_today.naik_trotoar; if (c.status === 'memantau') on++;
      const t = root.querySelector(`.cam-tile[data-id="${c.id}"]`);
      if (!t) continue;
      const set = (k, v) => { const el = t.querySelector(`[data-r="${k}"]`); if (el && el.textContent !== String(v)) el.textContent = v; };
      const chip = statusChip(c);
      const chipEl = t.querySelector('[data-r="chip"]');
      if (chipEl.dataset.h !== chip) { chipEl.innerHTML = chip; chipEl.dataset.h = chip; }
      set('name', c.name); set('park', c.counts_today.parkir); set('ride', c.counts_today.naik_trotoar);
      set('last', c.last_event ? `${KIND[c.last_event.kind]}, ${whenLabel(c.last_event.ts)}` : 'Belum ada kejadian');
    }
    setText($id('tp'), park); setText($id('tr'), ride);
    setText($id('psub'), `${cams.length} kamera, ${on} sedang memantau. Server bekerja terus; halaman ini hanya menampilkan.`);
    const full = cams.length >= P.max;
    $id('addBtn').setAttribute('aria-disabled', String(full));
    if (full) $id('addBtn').setAttribute('tabindex', '-1'); else $id('addBtn').removeAttribute('tabindex');
  };

  patch();
  const off = subscribe(patch);
  const thumbs = setInterval(() => {
    if (document.hidden) return;
    for (const img of root.querySelectorAll('img.thumb')) {
      swapImage(img, `/api/cams/${img.closest('.cam-tile').dataset.id}/snapshot.jpg?_=${Date.now()}`);
    }
  }, 2000);
  current = { dispose() { off(); clearInterval(thumbs); } };
}

// ----------------------------------------------------------------------- tambah kamera
function viewAddCam() {
  let type = 'url', file = null;
  const KINDS = [['url', 'Kamera IP'], ['device', 'Webcam'], ['upload', 'Rekaman']];
  view.innerHTML = `<div class="stack-lg" style="max-width:640px">
    <div class="stack"><a class="back" href="#/">${ICON.back}<span>Semua kamera</span></a>
      <h1 class="h2">Tambah kamera</h1>
      <p class="dim">Kamera bisa berasal dari perangkat ini, atau dari sumber lain yang dibaca server.</p></div>
    <div class="field"><label for="cname">Nama kamera</label>
      <input class="input" id="cname" type="text" maxlength="40" autocomplete="off" placeholder="Contoh: Depan toko"></div>
    <div class="panel stack"><b>Perangkat ini</b>
      <p class="dim small">Pakai kamera HP atau laptop yang sedang membuka aplikasi. Taruh di tempat yang stabil menghadap trotoar dan biarkan layar menyala.</p>
      <button class="btn primary block" id="useDevice" type="button">${ICON.camera}<span>Jadikan perangkat ini CCTV</span></button>
      <div id="devMsg"></div></div>
    <div class="stack"><h2 class="h3">Sumber lain</h2>
      <p class="dim small">Server membaca kamera ini sendiri. HP atau laptop hanya menampilkan hasilnya.</p>
      <div class="seg fill" role="group" aria-label="Jenis sumber">${KINDS.map(([k, t]) => `<button type="button" data-t="${k}" aria-pressed="${k === type}">${t}</button>`).join('')}</div>
      <div id="src"></div>
      <button class="btn line block" id="add" type="button">Tambah sumber dan tandai area</button>
      <div id="addMsg"></div>
    </div>
    <div class="panel stack"><b>Belum punya kamera?</b>
      <p class="dim small">Kamera demo memutar adegan sintetis dan mencatat kejadiannya, supaya Anda bisa melihat seluruh alurnya.</p>
      <button class="btn line block" id="demo" type="button">${ICON.play}<span>Coba kamera demo</span></button></div>
  </div>`;

  const src = $id('src');
  const draw = () => {
    for (const b of view.querySelectorAll('[data-t]')) b.setAttribute('aria-pressed', String(b.dataset.t === type));
    if (type === 'url') {
      src.innerHTML = `<div class="field"><label for="url">Alamat kamera</label>
        <input class="input" id="url" type="text" inputmode="url" autocapitalize="off" autocorrect="off" spellcheck="false" autocomplete="off" placeholder="rtsp://user:sandi@192.168.1.20:554/stream1">
        <span class="hint">Alamat RTSP dari CCTV atau NVR, atau tautan video http(s) seperti MJPEG. Server harus bisa menjangkau alamat ini. Sandi disamarkan di layar.</span></div>`;
    } else if (type === 'device') {
      src.innerHTML = `<div class="field"><label for="dev">Webcam</label>
        <select class="input" id="dev">${[0, 1, 2, 3].map((n) => `<option value="${n}">Webcam ${n + 1}</option>`).join('')}</select>
        <span class="hint">Webcam yang tercolok di komputer server, bukan kamera di perangkat ini.</span></div>`;
    } else {
      src.innerHTML = `<div class="stack"><div class="field"><span class="label">Video rekaman</span>
        <label class="btn line block" tabindex="0" role="button">${ICON.up}<span id="fname">${file ? esc(file.name) : 'Pilih video'}</span>
          <input id="vfile" type="file" accept="video/*,.mov,.mp4,.m4v,.webm,.mkv,.avi" hidden></label>
        <span class="hint">Diputar seperti kamera sungguhan. Cocok untuk mencoba tanpa CCTV.</span></div>
        <div class="panel">${sw('loop', 'Ulangi terus', 'Mulai lagi dari awal setiap selesai, seperti siaran kamera.', false)}</div></div>`;
      const lab = src.querySelector('label.btn');
      lab.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $id('vfile').click(); } });
      $id('vfile').addEventListener('change', (e) => {
        file = e.target.files[0] || null;
        $id('fname').textContent = file ? file.name : 'Pilih video';
        if (file && !$id('cname').value.trim()) $id('cname').value = file.name.replace(/\.[^.]+$/, '').slice(0, 40);
      });
    }
  };
  for (const b of view.querySelectorAll('[data-t]')) b.onclick = () => { type = b.dataset.t; draw(); };
  draw();

  $id('useDevice').onclick = async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    if (!(await startDeviceCamera($id('devMsg'), $id('cname').value))) btn.disabled = false;
  };
  $id('demo').onclick = async (e) => {
    e.currentTarget.disabled = true;
    try { const cam = await createCam({ source: 'demo' }); go(`#/pantau/${cam.id}`); }
    catch (err) { e.currentTarget.disabled = false; showError($id('addMsg'), err); }
  };

  $id('add').onclick = async (e) => {
    const btn = e.currentTarget;
    const msg = $id('addMsg');
    let name = $id('cname').value.trim();
    let source;
    if (type === 'url') {
      source = $id('url').value.trim();
      if (!source) return showError(msg, new Error('Isi alamat kamera lebih dulu.'));
      if (!name) { try { name = new URL(source).hostname; } catch { name = 'Kamera IP'; } }
    } else if (type === 'device') {
      source = `device:${$id('dev').value}`;
      if (!name) name = `Webcam ${Number($id('dev').value) + 1}`;
    } else if (!file) {
      return showError(msg, new Error('Pilih video rekaman lebih dulu.'));
    }
    btn.disabled = true;
    try {
      const body = { name: name.slice(0, 40) };
      if (type === 'upload') {
        msg.innerHTML = `<div class="panel stack"><b>Mengunggah ${esc(file.name)}</b>
          <div class="kerb" aria-hidden="true"><i id="upBar"></i></div><span class="dim small" id="upTxt">0%</span></div>`;
        const res = await upload(file, (f) => {
          $id('upBar').style.width = `${Math.round(f * 100)}%`; $id('upTxt').textContent = `${Math.round(f * 100)}%`;
        });
        source = `upload:${res.upload_id}`;
        if ($id('loop').checked) body.max_loops = null;
        if (!body.name) body.name = 'Rekaman';
      }
      body.source = source;
      const cam = await createCam(body);
      go(`#/pantau/${cam.id}/area`);
    } catch (err) { btn.disabled = false; showError(msg, err); }
  };
}

// ------------------------------------------------------------------------ satu kamera
function viewCam(id) {
  let cam = camById(id);
  if (!cam) {
    view.innerHTML = `<div class="stack"><a class="back" href="#/">${ICON.back}<span>Semua kamera</span></a>
      <h1 class="h2">Kamera tidak ditemukan</h1><p class="dim">Kamera ini mungkin sudah dihapus.</p></div>`;
    return;
  }
  const isDevice = cam.kind === 'browser';
  let dead = false, lastSeq = -1, known = null, snapTimer = 0, confirmDel = 0, mode = '', offPush = () => {};
  const stream = document.createElement('img');
  stream.id = 'stream';
  const detach = () => { stream.onerror = null; clearInterval(snapTimer); stream.removeAttribute('src'); };
  const attach = () => {
    clearInterval(snapTimer);
    stream.onerror = () => {                          // siaran ditolak (penonton penuh) atau terputus: pakai gambar diam per detik
      stream.onerror = null;
      snapTimer = setInterval(() => swapImage(stream, `/api/cams/${id}/snapshot.jpg?_=${Date.now()}`), 1000);
    };
    stream.src = `/api/cams/${id}/stream.mjpg?_=${Date.now()}`;
  };
  const onVis = () => { if (document.hidden) detach(); else if (!dead && mode === 'stream') attach(); };
  const off = subscribe(() => tick());
  current = {
    dispose() {
      dead = true; clearTimeout(confirmDel); document.removeEventListener('visibilitychange', onVis);
      off(); offPush(); detach();
      if (mode === 'local') pusher.park();               // video kamera kembali ke tempat tersembunyi, tetap berjalan
    },
  };

  view.innerHTML = `<div class="stack-lg">
    <a class="back" href="#/">${ICON.back}<span>Semua kamera</span></a>
    <div class="split">
      <div class="stack">
        <div class="frame" id="live"><span class="badge end" id="chip"></span></div>
        <div class="panel stack" id="state"></div>
        <p class="sr-only" role="status" id="stLive"></p>
      </div>
      <div class="stack-lg">
        <div class="stack"><h1 class="h2" id="cname"></h1><p class="dim small" id="csrc"></p></div>
        <div class="tally">
          <div data-k="parkir"><div class="n" id="tp">0</div><div class="l">Parkir hari ini</div></div><div class="sep"></div>
          <div data-k="naik_trotoar"><div class="n" id="tr">0</div><div class="l">Naik trotoar hari ini</div></div></div>
        <div class="actions row">
          <button class="btn line" id="recal" type="button"></button>
          <a class="btn line" id="area" href="#/pantau/${id}/area">${ICON.area}<span>Ubah area</span></a></div>
        <div class="actions row">
          <button class="btn line" id="toggle" type="button"></button>
          <button class="btn line" id="cfg" type="button" aria-expanded="false" aria-controls="cfgPanel">${ICON.sliders}<span>Atur</span></button></div>
        <div id="devctl" class="stack"></div>
        <div id="cfgPanel" hidden></div>
      </div>
    </div>
    <div class="stack"><h2 class="h3">Kejadian terbaru</h2><div class="events" id="evs"><p class="dim">Memuat…</p></div></div>
  </div>`;
  const live = $id('live');
  stream.alt = `Siaran langsung kamera ${cam.name}`; stream.width = 640; stream.height = 360;
  const overlay = document.createElement('canvas');
  overlay.className = 'overlay';
  const showFeed = () => {                             // video lokal bila perangkat ini kameranya, selain itu siaran server
    const want = isDevice && pusher.active && pusher.camId === id ? 'local' : 'stream';
    if (want === mode) return;
    mode = want;
    if (want === 'local') { detach(); stream.remove(); pusher.mount(live); live.appendChild(overlay); }
    else { overlay.remove(); pusher.park(); live.prepend(stream); attach(); }
  };
  const drawLive = () => {
    if (mode !== 'local') return;
    const v = pusher.video;
    const h = Math.round((640 * (v.videoHeight || 360)) / (v.videoWidth || 640));
    if (overlay.width !== 640 || overlay.height !== h) { overlay.width = 640; overlay.height = h; }
    const st = pusher.state || {};
    drawTracks(overlay, st.tracks || [], Number((cam.settings || {}).park_seconds) || 10, st.counts_today || null);
  };
  document.addEventListener('visibilitychange', onVis);
  showFeed();

  const html = {};                                     // isi terakhir tiap bagian: hindari menyusun ulang DOM yang sama
  const put = (key, el, markup) => { if (html[key] !== markup) { html[key] = markup; el.innerHTML = markup; } };

  const patch = () => {
    const c = cam;
    put('chip', $id('chip'), statusChip(c));
    const label = (STATUS[c.status] || ['', c.status])[1];
    if (html.live !== label) { html.live = label; $id('stLive').textContent = `${c.name}: ${label}`; }   // hanya saat status berganti
    setText($id('cname'), c.name);
    setText($id('csrc'), c.kind === 'demo' ? 'Adegan sintetis bawaan' : c.kind === 'upload' ? 'Rekaman video yang diunggah'
      : c.kind === 'browser' ? 'Kamera perangkat (HP atau laptop)'
      : `${{ file: 'Rekaman video', device: 'Webcam USB' }[c.kind] || 'Kamera IP'}: ${c.source}`);
    setText($id('tp'), c.counts_today.parkir); setText($id('tr'), c.counts_today.naik_trotoar);
    const lp = c.learn_progress || { have: 0, need: 1 };
    let body = `<b>${esc(c.message || (STATUS[c.status] || ['', c.status])[1])}</b>`;
    if (c.status === 'belajar') {
      body += `<div class="kerb" role="progressbar" aria-valuemin="0" aria-valuemax="${lp.need}" aria-valuenow="${lp.have}"><i style="width:${Math.round((lp.have / Math.max(1, lp.need)) * 100)}%;transition:none"></i></div>
        <span class="dim small">Cuplikan ${lp.have} dari ${lp.need}. Biarkan kamera tetap diam; kendaraan yang lewat tidak ikut tersimpan sebagai latar.</span>`;
    } else if (c.status === 'menunggu' && !(pusher.active && pusher.camId === id)) {
      body += `<span class="dim small">Buka halaman ini di HP atau laptop yang akan jadi kamera, lalu pilih Pakai perangkat ini sebagai kamera.</span>`;
    } else if (c.status === 'perlu_area') {
      body += `<a class="btn primary block" href="#/pantau/${id}/area">${ICON.area}<span>Tandai area trotoar</span></a>`;
    } else if (c.status === 'memantau') {
      const facts = [c.fps ? `${c.fps} gambar/detik` : '', c.size ? `${c.size[0]}×${c.size[1]}` : '', c.uptime_s != null ? `tersambung ${fmtSpan(c.uptime_s)}` : ''].filter(Boolean);
      body += `<span class="dim small">${facts.join(', ')}.</span>`;
      if (c.relearns) body += `<span class="faint small">Belajar latar ulang otomatis ${c.relearns} kali karena perubahan cahaya besar.</span>`;
    }
    put('state', $id('state'), body);
    put('recal', $id('recal'), c.status === 'selesai' ? `${ICON.play}<span>Putar ulang</span>` : `${ICON.sync}<span>Kalibrasi lagi</span>`);
    $id('recal').disabled = c.status === 'perlu_area';
    put('toggle', $id('toggle'), c.enabled ? `${ICON.pause}<span>Jeda</span>` : `${ICON.play}<span>Lanjutkan</span>`);
    renderDev();
  };

  const renderDev = () => {                            // kontrol khusus kamera perangkat
    if (!isDevice) return;
    const here = pusher.active && pusher.camId === id;
    const warn = pusher.error ? `<div class="notice" data-tone="warn" role="alert">${esc(pusher.error)}</div>` : '';
    let markup;
    if (here) {
      markup = `<div class="actions row"><button class="btn line" data-a="flip" type="button">${ICON.flip}<span>Ganti kamera</span></button>
        <button class="btn line" data-a="stop" type="button">${ICON.stop}<span>Berhenti</span></button></div>
        <p class="faint small">Biarkan layar menyala dan halaman aplikasi terbuka. HP yang terkunci menghentikan kamera.</p>${warn}`;
    } else if (!cameraSupported()) {
      markup = secureNotice();
    } else {
      markup = `<button class="btn primary block" data-a="here" type="button">${ICON.camera}<span>Pakai perangkat ini sebagai kamera</span></button>${warn}`;
    }
    put('dev', $id('devctl'), markup);
  };
  $id('devctl').onclick = async (e) => {
    const b = e.target.closest('[data-a]');
    if (!b) return;
    if (b.dataset.a === 'flip') pusher.flip().catch((err) => toast('Kamera tidak bisa diganti', err.message, 'error'));
    if (b.dataset.a === 'stop') { pusher.stop(); keep.del('ss_push'); }
    if (b.dataset.a === 'here') {
      try { await pusher.start(id); keep.set('ss_push', id); } catch { renderDev(); }
    }
  };
  offPush = pusher.on(() => { showFeed(); drawLive(); renderDev(); });

  const loadEvents = async () => {
    try {
      const { events } = await getJSON(`/api/cams/${id}/events?limit=30`);
      if (dead) return;
      const first = known === null;
      const prev = known || new Set();
      known = new Set(events.map((e) => e.no));
      $id('evs').innerHTML = events.length
        ? events.map((e) => camEventRow(e, '', !first && !prev.has(e.no))).join('')
        : '<p class="dim">Belum ada kejadian. Kendaraan yang parkir atau naik ke trotoar akan muncul di sini.</p>';
      wireEvents($id('evs'));
    } catch (e) { if (!dead) showError($id('evs'), e); }
  };

  const tick = () => {
    const c = camById(id);
    if (!c) { toast('Kamera dihapus', cam.name); go('#/'); return; }
    cam = c;
    patch();
    if (c.seq !== lastSeq) { lastSeq = c.seq; loadEvents(); }
  };

  const act = async (fn, okText) => {
    try { await fn(); await pollCams(); if (okText) toast(okText); } catch (e) { toast('Gagal', e.message, 'error'); }
  };
  $id('recal').onclick = () => act(() => postJSON(`/api/cams/${id}/recalibrate`), cam.status === 'selesai' ? 'Rekaman diputar ulang' : 'Kamera belajar latar ulang');
  $id('toggle').onclick = () => act(() => postJSON(`/api/cams/${id}`, { enabled: !cam.enabled }), cam.enabled ? 'Pemantauan dijeda' : 'Pemantauan dilanjutkan');
  $id('cfg').onclick = () => {
    const panel = $id('cfgPanel');
    const open = panel.hidden;
    panel.hidden = !open;
    $id('cfg').setAttribute('aria-expanded', String(open));
    if (open) { buildSettings(panel); panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }
  };

  const buildSettings = (panel) => {
    const st = cam.settings || {};
    let park = Number(st.park_seconds) || 10;
    let sens = Number(nearest(SENS, st.diff_floor ?? SENS[3], '3'));
    let size = nearest(SIZE, st.min_area_frac ?? SIZE.sedang, 'sedang');
    const rec = ['demo', 'upload', 'file'].includes(cam.kind);
    let speed = [1, 2, 4, 8].reduce((b, v) => (Math.abs(v - cam.speed) < Math.abs(b - cam.speed) ? v : b), 1);
    panel.innerHTML = `<div class="panel stack-lg">
      <div class="field"><label for="sName">Nama kamera</label>
        <input class="input" id="sName" type="text" maxlength="40" autocomplete="off" value="${esc(cam.name)}"></div>
      <div class="field"><label for="sPark">Lama diam sampai dihitung parkir</label>
        <div class="slider-row"><input id="sPark" type="range" min="3" max="60" step="1" value="${park}"><output id="sParkOut">${park} dtk</output></div></div>
      <div class="field"><label for="sSens">Kepekaan</label>
        <div class="slider-row"><input id="sSens" type="range" min="1" max="5" step="1" value="${sens}"><output id="sSensOut">${sens}</output></div>
        <span class="hint">Naikkan bila kendaraan gelap atau kecil terlewat. Turunkan bila banyak deteksi palsu.</span></div>
      <div class="field"><span class="label">Ukuran objek minimum</span>
        <div class="seg" role="group" aria-label="Ukuran objek minimum">${['kecil', 'sedang', 'besar'].map((k) => `<button type="button" data-size="${k}" aria-pressed="${k === size}">${k[0].toUpperCase() + k.slice(1)}</button>`).join('')}</div></div>
      ${rec ? `<div class="field"><span class="label">Kecepatan putar rekaman</span>
        <div class="seg" role="group" aria-label="Kecepatan putar">${[1, 2, 4, 8].map((v) => `<button type="button" data-speed="${v}" aria-pressed="${v === speed}">${v}×</button>`).join('')}</div>
        ${sw('sLoop', 'Ulangi terus', 'Mulai lagi dari awal setiap selesai.', cam.max_loops === null)}</div>` : ''}
      <div class="stack">
        <button class="btn primary block" id="sSave" type="button">Simpan</button>
        <p class="faint small">Menyimpan pengaturan membuat kamera belajar latar ulang beberapa detik.</p>
        <div id="sMsg"></div>
        <hr class="rule">
        <button class="btn line danger block" id="sDel" type="button">${ICON.trash}<span>Hapus kamera dan kejadiannya</span></button>
      </div></div>`;
    $id('sPark').oninput = (e) => { park = Number(e.target.value); $id('sParkOut').textContent = `${park} dtk`; };
    $id('sSens').oninput = (e) => { sens = Number(e.target.value); $id('sSensOut').textContent = sens; };
    for (const b of panel.querySelectorAll('[data-size]')) b.onclick = () => {
      size = b.dataset.size; for (const o of panel.querySelectorAll('[data-size]')) o.setAttribute('aria-pressed', String(o === b));
    };
    for (const b of panel.querySelectorAll('[data-speed]')) b.onclick = () => {
      speed = Number(b.dataset.speed); for (const o of panel.querySelectorAll('[data-speed]')) o.setAttribute('aria-pressed', String(o === b));
    };
    $id('sSave').onclick = async (e) => {
      e.currentTarget.disabled = true;
      const body = { name: $id('sName').value.trim(), settings: { ...(cam.settings || {}), park_seconds: park, diff_floor: SENS[sens], min_area_frac: SIZE[size] } };
      if (rec) { body.speed = speed; body.max_loops = $id('sLoop').checked ? null : (cam.max_loops ?? 1); }
      try {
        await postJSON(`/api/cams/${id}`, body);
        await pollCams();
        toast('Pengaturan disimpan', 'Kamera belajar latar ulang.');
        panel.hidden = true; $id('cfg').setAttribute('aria-expanded', 'false');
      } catch (err) { e.currentTarget.disabled = false; showError($id('sMsg'), err); }
    };
    $id('sDel').onclick = async (e) => {
      const btn = e.currentTarget;
      if (!confirmDel) {                                // dua ketukan: cegah salah hapus
        btn.querySelector('span').textContent = 'Ketuk lagi untuk menghapus';
        confirmDel = setTimeout(() => { confirmDel = 0; if (btn.isConnected) btn.querySelector('span').textContent = 'Hapus kamera dan kejadiannya'; }, 4000);
        return;
      }
      clearTimeout(confirmDel); btn.disabled = true;
      try { await postJSON(`/api/cams/${id}`, {}, 'DELETE'); await pollCams(); toast('Kamera dihapus', cam.name); go('#/'); }
      catch (err) { btn.disabled = false; showError($id('sMsg'), err); }
    };
  };

  tick();
}

// ---------------------------------------------------------------------- tandai area kamera
function viewCamArea(id) {
  const cam = camById(id);
  if (!cam) return go('#/');
  let dead = false, retry = 0, loaded = false;
  current = { dispose() { dead = true; clearTimeout(retry); } };
  view.innerHTML = `<div class="stack-lg">
    <a class="back" href="#/pantau/${id}">${ICON.back}<span>${esc(cam.name)}</span></a>
    <div class="split">
      <div class="stack"><div class="frame" id="frameHost"><span class="badge chip" data-tone="scan">Gambar langsung dari kamera</span></div>
        <p class="dim small" id="aMsg" role="status">Menunggu gambar pertama dari kamera…</p></div>
      <div class="stack sticky">
        <h1 class="h2">Tandai area trotoar</h1>
        <p class="dim">Ketuk di sepanjang tepi trotoar untuk membuat area. Seret titik untuk memperbaiki. Kamera belajar sendiri seperti apa trotoar kosong, jadi tidak perlu menunggu trotoar kosong.</p>
        <div class="actions row">
          <button class="btn line" id="undo" type="button">${ICON.undo}<span>Urungkan</span></button>
          <button class="btn line" id="clear" type="button">${ICON.trash}<span>Hapus</span></button></div>
        <button class="btn line block" id="preset" type="button">Isi sepertiga bawah</button>
        <button class="btn line block" id="reload" type="button">${ICON.sync}<span>Ambil gambar baru</span></button>
        <button class="btn primary block" id="save" type="button" disabled>Simpan dan mulai memantau</button>
        <div id="aErr"></div>
      </div>
    </div></div>`;
  const save = $id('save');
  const editor = new RoiEditor($id('frameHost'), {
    onChange: (pts) => { save.disabled = pts.length < 3 || !loaded; },
  });
  if (cam.roi && cam.roi.length) editor.setPoints(cam.roi);

  const load = () => {
    clearTimeout(retry);
    if (cam.kind === 'browser' && pusher.active && pusher.camId === id && pusher.video.videoWidth) {
      loaded = true;                                   // kamera ada di perangkat ini: pakai frame lokal, tanpa lewat server
      editor.setImage(snapshot(pusher.video, 960));
      editor.onChange(editor.points);
      setText($id('aMsg'), 'Gambar dari kamera perangkat ini.');
      return;
    }
    const img = new Image();
    img.onload = () => {
      if (dead) return;
      loaded = true; editor.setImage(img); editor.onChange(editor.points);
      $id('aMsg').textContent = `Gambar ${img.naturalWidth}×${img.naturalHeight}.`;
    };
    img.onerror = () => {                              // 503 = kamera belum mengirim frame
      if (dead) return;
      const c = camById(id);
      $id('aMsg').textContent = c && c.status === 'terputus' ? `Kamera belum tersambung. ${c.message}` : 'Menunggu gambar pertama dari kamera…';
      retry = setTimeout(load, 1500);
    };
    img.src = `/api/cams/${id}/snapshot.jpg?raw=1&_=${Date.now()}`;
  };
  load();

  $id('undo').onclick = () => editor.undo();
  $id('clear').onclick = () => editor.clear();
  $id('preset').onclick = () => editor.setPoints(PRESET);
  $id('reload').onclick = load;
  save.onclick = async () => {
    save.disabled = true;
    try {
      await postJSON(`/api/cams/${id}`, { roi: editor.points.map((p) => [p[0], p[1]]) });
      await pollCams();
      toast('Area disimpan', 'Kamera mulai belajar latar.');
      go(`#/pantau/${id}`);
    } catch (err) { save.disabled = false; showError($id('aErr'), err); }
  };
  const redraw = () => editor.draw();
  window.addEventListener('resize', redraw);
  const prev = current.dispose;
  current = { dispose() { prev(); window.removeEventListener('resize', redraw); } };
}

// ------------------------------------------------------------------------ kejadian semua kamera
function viewFeed() {
  const PAGE = 30;
  let kind = '', offset = 0, dead = false, more = false, known = new Set(), firstLoad = true;
  view.innerHTML = `<div class="stack-lg">
    <div class="head-row"><div class="stack"><h1 class="h2">Kejadian</h1>
      <p class="dim">Semua kendaraan yang parkir atau naik ke trotoar, dari semua kamera.</p></div>
      <a class="btn line" href="/api/events.csv" download>${ICON.dl}<span>Unduh CSV</span></a></div>
    <div class="seg fill" role="group" aria-label="Jenis kejadian">
      <button type="button" data-f="" aria-pressed="true">Semua</button>
      <button type="button" data-f="parkir" aria-pressed="false">Parkir</button>
      <button type="button" data-f="naik_trotoar" aria-pressed="false">Naik trotoar</button></div>
    <div class="events" id="feed"></div>
    <button class="btn line block" id="more" type="button" hidden>Muat lebih banyak</button>
    <div id="fErr"></div></div>`;
  const feed = $id('feed');

  const load = async (append) => {
    if (!append) offset = 0;
    try {
      const { events } = await getJSON(`/api/events?limit=${PAGE}&offset=${offset}${kind ? `&kind=${kind}` : ''}`);
      if (dead) return;
      offset += events.length; more = events.length === PAGE;
      const rows = events.map((e) => camEventRow(e, e.camera.name, !firstLoad && !append && !known.has(`${e.camera.id}:${e.no}`)));
      if (append) feed.insertAdjacentHTML('beforeend', rows.join(''));
      else feed.innerHTML = rows.length ? rows.join('') : `<div class="dropzone"><b>Belum ada kejadian</b>
        <span>Kejadian muncul di sini begitu ada kendaraan yang parkir atau naik ke trotoar di salah satu kamera.</span>
        <a class="btn primary" href="#/">Lihat kamera</a></div>`;
      for (const e of events) known.add(`${e.camera.id}:${e.no}`);
      firstLoad = false;
      $id('more').hidden = !more;
      wireEvents(feed);
      $id('fErr').innerHTML = '';
    } catch (e) { if (!dead) showError($id('fErr'), e); }
  };

  for (const b of view.querySelectorAll('[data-f]')) b.onclick = () => {
    kind = b.dataset.f;
    for (const o of view.querySelectorAll('[data-f]')) o.setAttribute('aria-pressed', String(o === b));
    firstLoad = true; known = new Set(); load(false);
  };
  $id('more').onclick = () => load(true);

  let total = P.cams.reduce((n, c) => n + c.seq, 0);   // ada kejadian baru: muat ulang halaman pertama saja
  const off = subscribe(() => {
    const now = P.cams.reduce((n, c) => n + c.seq, 0);
    if (now !== total) { total = now; if (offset <= PAGE) load(false); }
  });
  current = { dispose() { dead = true; off(); } };
  load(false);
}

// ---------------------------------------------------------------------------- laporan
const PERIODS = [['hari_ini', 'Hari ini'], ['7_hari', '7 hari'], ['30_hari', '30 hari']];

function viewLaporan() {
  let periode = keep.get('ss_rp_periode');
  if (!PERIODS.some(([k]) => k === periode)) periode = 'hari_ini';
  let kamera = keep.get('ss_rp_kamera') || '';
  let dead = false, ticket = 0, sig = '', charts = {}, camSig = '';

  view.innerHTML = `<div class="rp stack-lg" id="rp">
    <div class="rp-print"><b>Laporan Sidewalk Sentinel</b><div class="small" id="rpMeta"></div></div>
    <div class="stack rp-noprint"><h1 class="h2">Laporan</h1>
      <p class="dim">Ringkasan kendaraan yang parkir atau naik ke trotoar.</p></div>
    <div class="rp-filters rp-noprint">
      <div class="seg fill" role="group" aria-label="Periode">${PERIODS.map(([k, t]) =>
        `<button type="button" data-p="${k}" aria-pressed="${k === periode}">${t}</button>`).join('')}</div>
      <div class="field"><label class="sr-only" for="rpCam">Kamera</label><select class="input" id="rpCam"></select></div>
    </div>
    <div id="rpBody" class="stack-lg"></div>
    <div id="rpErr"></div></div>`;
  const root = $id('rp'), body = $id('rpBody'), select = $id('rpCam');

  const fillCameras = () => {                          // daftar kamera mengikuti penyimpanan pemantau
    const next = P.cams.map((c) => `${c.id}:${c.name}`).join('|');
    if (next === camSig) return;
    camSig = next;
    select.textContent = '';
    for (const [value, label] of [['', 'Semua kamera'], ...P.cams.map((c) => [c.id, c.name])]) {
      const o = document.createElement('option');
      o.value = value; o.textContent = label;
      select.append(o);
    }
    if (kamera && !camById(kamera)) { kamera = ''; keep.del('ss_rp_kamera'); }
    select.value = kamera;
  };

  const empty = (r) => {
    if (!P.cams.length) return `<div class="dropzone"><b>Belum ada kamera</b>
      <span>Laporan dibuat dari kejadian yang dideteksi kamera. Nyalakan kamera HP atau laptop ini sebagai CCTV untuk mulai.</span>
      <a class="btn primary" href="#/">Buka halaman Pantau</a></div>`;
    return `<div class="dropzone"><b>Belum ada kejadian pada periode ini</b>
      <span>${esc(periodText(r))}. Kejadian muncul di sini begitu ada kendaraan yang parkir atau naik ke trotoar.</span>
      <a class="btn primary" href="#/">Buka halaman Pantau</a></div>`;
  };

  const render = (r) => {
    const open = {};                                   // tabel yang sedang terbuka tetap terbuka setelah data diperbarui
    for (const k of Object.keys(charts)) open[k] = charts[k].isTableOpen();
    charts = {};
    const camName = r.kamera === 'semua' ? 'semua kamera' : ((r.per_kamera[0] || {}).nama || '');
    setText($id('rpMeta'), `${periodText(r)}. Kamera: ${camName}. Dibuat ${new Date().toLocaleString('id-ID')}.`);
    if (r.total === 0) { body.innerHTML = empty(r); return; }
    const days = r.per_hari.length > 1;
    body.innerHTML = `${summaryHTML(r)}
      <section class="stack" aria-labelledby="rpJamH"><h2 class="h3" id="rpJamH">Kejadian per jam</h2>
        <div class="panel"><div id="rpJam"></div></div></section>
      ${days ? `<section class="stack" aria-labelledby="rpHariH"><h2 class="h3" id="rpHariH">Kejadian per hari</h2>
        <div class="panel"><div id="rpHari"></div></div></section>` : ''}
      ${perCameraHTML(r)}
      <section class="stack" aria-labelledby="rpBukti"><div class="head-row"><h2 class="h3" id="rpBukti">Bukti terbaru</h2>
        <a class="btn quiet rp-noprint" href="#/kejadian">Lihat semua</a></div>
        <div class="panel"><div class="events" id="rpEv">${r.terbaru.map((e) => camEventRow(e, e.camera.name)).join('')}</div></div></section>
      <div class="actions row rp-noprint">
        <a class="btn line" href="/api/events.csv" download>${ICON.dl}<span>Unduh CSV</span></a>
        <button class="btn primary" id="rpPrint" type="button">${ICON.print}<span>Cetak laporan</span></button></div>`;
    charts.jam = stackedColumns($id('rpJam'), { slots: hourSlots(r), xTicks: HOUR_TICKS, caption: `Kejadian per jam, ${periodText(r)}` });
    if (open.jam) charts.jam.setTableOpen(true);
    if (days) {
      const slots = daySlots(r);
      charts.hari = stackedColumns($id('rpHari'), { slots, xTicks: dayTicks(slots), caption: `Kejadian per hari, ${periodText(r)}` });
      if (open.hari) charts.hari.setTableOpen(true);
    }
    wireEvents($id('rpEv'));
    $id('rpPrint').onclick = () => window.print();
  };

  const load = async () => {
    const mine = ++ticket;
    try {
      const r = await getJSON(`/api/report?periode=${periode}${kamera ? `&kamera=${kamera}` : ''}`);
      if (dead || mine !== ticket) return;
      root.classList.remove('rp-loading');
      $id('rpErr').textContent = '';
      const next = JSON.stringify({ ...r, sampai: 0 });
      if (next !== sig) { sig = next; render(r); }
    } catch (e) {
      if (dead || mine !== ticket) return;
      root.classList.remove('rp-loading');
      if (e.status === 404 && kamera) { kamera = ''; keep.del('ss_rp_kamera'); select.value = ''; sig = ''; load(); return; }
      showError($id('rpErr'), e);
    }
  };
  const reload = () => { sig = ''; root.classList.add('rp-loading'); load(); };

  for (const b of view.querySelectorAll('[data-p]')) b.onclick = () => {
    periode = b.dataset.p;
    keep.set('ss_rp_periode', periode);
    for (const o of view.querySelectorAll('[data-p]')) o.setAttribute('aria-pressed', String(o === b));
    reload();
  };
  select.onchange = () => { kamera = select.value; if (kamera) keep.set('ss_rp_kamera', kamera); else keep.del('ss_rp_kamera'); reload(); };

  fillCameras();
  const off = subscribe(fillCameras);
  const timer = setInterval(() => { if (!document.hidden) load(); }, 10000);   // kejadian baru dan lama yang baru terukur
  current = { dispose() { dead = true; clearInterval(timer); off(); } };
  load();
}

// ------------------------------------------------------------------------------ mulai
async function boot() {
  try { S.info = await getJSON('/api/info'); } catch { /* penanda koneksi diurus pemantau */ }
  await pollCams();                                  // isi daftar kamera sebelum halaman pertama digambar
  pusher.on(renderCamLive);
  subscribe(renderCamLive);
  const saved = keep.get('ss_push');                 // perangkat ini sebelumnya dipakai sebagai kamera: lanjutkan
  const savedCam = saved && camById(saved);
  if (saved && (!savedCam || savedCam.kind !== 'browser')) keep.del('ss_push');
  else if (savedCam && cameraSupported()) pusher.start(saved).catch(() => { /* halaman kamera menawarkan tombol */ });
  route();
  startWatcher();
  if ('serviceWorker' in navigator && window.isSecureContext) navigator.serviceWorker.register('/sw.js').catch(() => {});
}
boot();

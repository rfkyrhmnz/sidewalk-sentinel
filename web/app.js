import { ApiError, getJSON, postJSON, upload } from './api.js';
import { PRESET, RoiEditor } from './roi.js';
import { LiveLoop, cameraSupported, drawTracks, openCamera, snapshot, mountVideo } from './live.js';

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

function overrides() {
  const s = S.settings;
  return {
    park_seconds: Number(s.park_seconds),
    diff_floor: { 1: 34, 2: 28, 3: 22, 4: 17, 5: 13 }[s.sens],
    min_area_frac: { kecil: 0.0008, sedang: 0.0015, besar: 0.004 }[s.size],
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
  ['#/', 'Beranda', ICON.home, /^#\/?$|^#\/(area|atur|proses|hasil)/],
  ['#/live', 'Kamera', ICON.camera, /^#\/live/],
  ['#/riwayat', 'Riwayat', ICON.history, /^#\/riwayat/],
  ['#/tahapan', 'Tahapan', ICON.layers, /^#\/tahapan/],
];

function renderNav(hash) {
  document.getElementById('nav').innerHTML = NAV.map(([href, label, icon, re]) =>
    `<a href="${href}" ${re.test(hash) ? 'aria-current="page"' : ''}>${icon}<span>${label}</span></a>`).join('');
}

const ROUTES = [
  [/^#\/?$/, viewHome],
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
  <div class="hero">
    <div class="hero-scene">${HERO}</div>
    <div class="hero-copy stack">
      <h1 class="h1">Kendaraan di trotoar, terdeteksi otomatis.</h1>
      <p class="lead">Rekam atau unggah video trotoar. Aplikasi menandai kendaraan yang parkir di atasnya dan pengendara yang naik dari jalan, memakai pengolahan citra klasik.</p>
      <div class="actions">
        <label class="btn primary block" id="recBtn" tabindex="0" role="button">${ICON.rec}<span>Rekam video</span>
          <input id="rec" type="file" accept="video/*" capture="environment" hidden></label>
        <div class="actions row">
          <label class="btn line" tabindex="0" role="button">${ICON.up}<span>Pilih video</span>
            <input id="pick" type="file" accept="video/*,.mov,.mp4,.m4v,.webm,.mkv,.avi" hidden></label>
          <a class="btn line" href="#/live">${ICON.camera}<span>Kamera langsung</span></a>
        </div>
        <button class="btn quiet" id="demoBtn" type="button">Coba dengan video demo</button>
      </div>
      <div id="homeMsg"></div>
      ${ffmpegNote}
    </div>
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
  if (!S.upload) return go('#/');
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
  if (!S.upload || S.roi.length < 3) return go('#/');
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
    view.innerHTML = `<div class="stack"><h1 class="h2">Analisis tidak selesai</h1><div id="e"></div><a class="btn line" href="#/">Kembali ke beranda</a></div>`;
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
  if (!S.upload || (j.upload_id && S.upload.upload_id !== j.upload_id)) again.setAttribute('href', '#/');
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
    }).join('') : `<div class="dropzone"><b>Belum ada analisis</b><span>Rekam atau unggah video dari beranda untuk memulai.</span><a class="btn primary" href="#/">Ke beranda</a></div>`;
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
      Browser hanya mengizinkan kamera di HTTPS atau localhost. Di HP, pakai Rekam video dari beranda, atau buka aplikasi lewat tautan HTTPS (misalnya cloudflared tunnel).</div>
      <div class="actions" style="margin-top:16px"><a class="btn primary" href="#/">Rekam video dari beranda</a></div>`;
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

// ------------------------------------------------------------------------------ mulai
async function boot() {
  const conn = document.getElementById('conn');
  try {
    S.info = await getJSON('/api/info');
    conn.dataset.ok = '1'; conn.lastElementChild.textContent = 'Server siap';
  } catch {
    conn.dataset.ok = '0'; conn.lastElementChild.textContent = 'Server tidak terjangkau';
  }
  route();
  if ('serviceWorker' in navigator && window.isSecureContext) navigator.serviceWorker.register('/sw.js').catch(() => {});
}
boot();

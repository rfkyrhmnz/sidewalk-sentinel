// Halaman Laporan, Kejadian, dan Setelan.
import { ApiError, del, getJSON } from './api.js';
import { $id, ICON, KIND, esc, eventRow, ipLabel, keep, setText, showError, toast } from './core.js';
import { HOUR_TICKS, dayTicks, daySlots, hourSlots, perCameraHTML, periodText, stackedColumns, summaryHTML } from './laporan.js';

const PERIODS = [['hari_ini', 'Hari ini'], ['7_hari', '7 hari'], ['30_hari', '30 hari']];

/** Hapus satu kejadian di server. 404 berarti sudah tidak ada (mis. dihapus dari perangkat lain): tujuannya tercapai. */
async function deleteEvent(e) {
  try { await del(`/api/cams/${e.camera.id}/events/${e.no}`); }
  catch (err) { if (!(err instanceof ApiError && err.status === 404)) throw err; }
}

// ======================================================================== laporan
export function viewLaporan({ view }) {
  let periode = keep.get('ss_rp_periode');
  if (!PERIODS.some(([k]) => k === periode)) periode = 'hari_ini';
  let kamera = keep.get('ss_rp_kamera') || '';
  let dead = false, ticket = 0, sig = '', charts = {}, camSig = '', cams = [];

  view.innerHTML = `<div class="rp stack-lg" id="rp">
    <div class="rp-print"><b>Laporan Sidewalk Sentinel</b><div class="small" id="rpMeta"></div></div>
    <div class="stack rp-noprint"><h1 class="h2">Laporan</h1>
      <p class="dim">Ringkasan kendaraan yang parkir atau naik ke trotoar.</p></div>
    <div class="rp-filters rp-noprint">
      <div class="seg fill" role="group" aria-label="Periode">${PERIODS.map(([k, t]) =>
        `<button type="button" data-p="${k}" aria-pressed="${k === periode}">${t}</button>`).join('')}</div>
      <div class="field"><label class="sr-only" for="rpDev">Perangkat</label><select class="input" id="rpDev"></select></div>
    </div>
    <div id="rpBody" class="stack-lg"></div>
    <div id="rpErr"></div></div>`;
  const root = $id('rp'), body = $id('rpBody'), select = $id('rpDev');

  const fillDevices = () => {                          // daftar perangkat mengikuti server
    const next = cams.map((c) => `${c.id}:${c.name}`).join('|');
    if (next === camSig) return;
    camSig = next;
    select.textContent = '';
    for (const [value, label] of [['', 'Semua perangkat'], ...cams.map((c) => [c.id, c.name])]) {
      const o = document.createElement('option');
      o.value = value; o.textContent = label;
      select.append(o);
    }
    if (kamera && !cams.some((c) => c.id === kamera)) { kamera = ''; keep.del('ss_rp_kamera'); }
    select.value = kamera;
    select.hidden = cams.length < 2;
  };

  const empty = (r) => `<div class="empty"><b>Belum ada kejadian pada periode ini</b>
    <span>${esc(periodText(r))}. Kejadian muncul di sini begitu ada kendaraan yang parkir atau naik ke trotoar di depan kamera.</span>
    <a class="btn primary" href="#/">Buka layar pantau</a></div>`;

  const remove = async (e) => {                        // laporan dihitung ulang dari server, bukan diakali di layar
    await deleteEvent(e);
    toast('Kejadian dihapus', 'Hitungan dan grafik sudah diperbarui.');
    reload();
  };

  const render = (r) => {
    const open = {};                                   // tabel yang sedang terbuka tetap terbuka setelah data diperbarui
    for (const k of Object.keys(charts)) open[k] = charts[k].isTableOpen();
    charts = {};
    const who = r.kamera === 'semua' ? 'semua perangkat' : ((r.per_kamera[0] || {}).nama || '');
    setText($id('rpMeta'), `${periodText(r)}. Perangkat: ${who}. Dibuat ${new Date().toLocaleString('id-ID')}.`);
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
        <div class="events" id="rpEv"></div></section>
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
    for (const e of r.terbaru) $id('rpEv').append(eventRow(e, { onDelete: remove }));
    $id('rpPrint').onclick = () => window.print();
  };

  const load = async () => {
    const mine = ++ticket;
    try {
      const [r, list] = await Promise.all([
        getJSON(`/api/report?periode=${periode}${kamera ? `&kamera=${kamera}` : ''}`),
        getJSON('/api/cams'),
      ]);
      if (dead || mine !== ticket) return;
      cams = list.cams;
      fillDevices();
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

  select.hidden = true;
  const timer = setInterval(() => { if (!document.hidden) load(); }, 10000);   // kejadian baru dan lama yang baru terukur
  load();
  return { dispose() { dead = true; clearInterval(timer); } };
}

// ======================================================================= kejadian
const PAGE = 30;

export function viewKejadian({ view, markSeen }) {
  let kind = '';
  let rows = [];
  let more = false;
  let dead = false;
  let ticket = 0;

  view.innerHTML = `<div class="stack-lg">
    <div class="stack"><h1 class="h2">Kejadian</h1>
      <p class="dim">Setiap kendaraan yang parkir atau naik ke trotoar, lengkap dengan foto bukti. Ketuk untuk memperbesar.</p></div>
    <div class="seg fill" role="group" aria-label="Jenis kejadian">
      <button type="button" data-f="" aria-pressed="true">Semua</button>
      <button type="button" data-f="parkir" aria-pressed="false">Parkir</button>
      <button type="button" data-f="naik_trotoar" aria-pressed="false">Naik trotoar</button></div>
    <div class="events" id="feed" aria-live="polite"></div>
    <div id="feedEmpty"></div>
    <button type="button" class="btn line block" id="more" hidden>Muat lebih banyak</button>
    <div id="feedErr"></div></div>`;
  const feed = $id('feed');
  const key = (e) => `${e.camera.id}:${e.no}`;
  const gone = new Set();                              // yang baru dihapus: jawaban lama yang masih di jalan tidak boleh memunculkannya lagi

  const live = (events) => events.filter((e) => !gone.has(key(e)));

  const remove = async (e) => {
    await deleteEvent(e);
    gone.add(key(e));
    rows = rows.filter((r) => key(r) !== key(e));
    toast('Kejadian dihapus');
    draw();
  };

  const draw = (freshKeys = new Set()) => {
    feed.textContent = '';
    for (const e of rows) feed.append(eventRow(e, { fresh: freshKeys.has(key(e)), onDelete: remove }));
    $id('more').hidden = !more;
    $id('feedEmpty').innerHTML = rows.length ? '' : `<div class="empty"><b>Belum ada kejadian${kind ? ` ${KIND[kind].toLowerCase()}` : ''}</b>
      <span>Kejadian muncul di sini begitu kamera melihat kendaraan parkir atau naik ke trotoar.</span>
      <a class="btn primary" href="#/">Buka layar pantau</a></div>`;
  };

  const fetchPage = async (offset) => {
    const { events } = await getJSON(`/api/events?limit=${PAGE}&offset=${offset}${kind ? `&kind=${kind}` : ''}`);
    return events;
  };

  const reset = async () => {
    const mine = ++ticket;
    try {
      const events = await fetchPage(0);
      if (dead || mine !== ticket) return;
      rows = live(events);
      more = events.length === PAGE;
      $id('feedErr').textContent = '';
      draw();
    } catch (e) { if (!dead && mine === ticket) showError($id('feedErr'), e); }
  };

  const poll = async () => {                           // kejadian baru masuk di atas tanpa membuang halaman yang sudah dimuat
    const mine = ticket;
    try {
      const events = await fetchPage(0);
      if (dead || mine !== ticket) return;
      const have = new Set(rows.map(key));
      const fresh = live(events).filter((e) => !have.has(key(e)));
      const changed = events.some((e) => { const r = rows.find((x) => key(x) === key(e)); return r && r.durasi_s !== e.durasi_s; });
      if (!fresh.length && !changed) return;
      const byKey = new Map(events.map((e) => [key(e), e]));
      rows = [...fresh, ...rows.map((r) => byKey.get(key(r)) || r)];
      draw(new Set(fresh.map(key)));
    } catch { /* percobaan berikutnya */ }
  };

  $id('more').onclick = async (ev) => {
    const btn = ev.currentTarget;
    btn.disabled = true;
    try {
      const events = await fetchPage(rows.length);
      const have = new Set(rows.map(key));
      rows = [...rows, ...live(events).filter((e) => !have.has(key(e)))];
      more = events.length === PAGE;
      draw();
    } catch (e) { showError($id('feedErr'), e); }
    btn.disabled = false;
  };

  for (const b of view.querySelectorAll('[data-f]')) b.onclick = () => {
    kind = b.dataset.f;
    for (const o of view.querySelectorAll('[data-f]')) o.setAttribute('aria-pressed', String(o === b));
    reset();
  };

  markSeen();
  const timer = setInterval(() => { if (!document.hidden) poll(); }, 4000);
  reset();
  return { dispose() { dead = true; clearInterval(timer); } };
}

// ======================================================================== setelan
const SENS = { 1: 0.55, 2: 0.42, 3: 0.30, 4: 0.22, 5: 0.15 };         // kepekaan -> conf (ambang keyakinan YOLO; makin rendah makin peka)
const SENS_LABEL = { 1: 'Sangat rendah', 2: 'Rendah', 3: 'Sedang', 4: 'Tinggi', 5: 'Sangat tinggi' };
const SIZE = { kecil: 0.0008, sedang: 0.0015, besar: 0.004 };         // ukuran minimum -> min_box_frac
const nearest = (map, v, dflt) => {
  const n = Number(v);
  if (!Number.isFinite(n)) return dflt;
  return Object.keys(map).reduce((best, k) => (Math.abs(map[k] - n) < Math.abs(map[best] - n) ? k : best));
};

export function viewSetelan({ view, device: D, info }) {
  const cam = D.cam;
  if (!cam) {
    view.innerHTML = `<div class="stack-lg"><div class="stack"><h1 class="h2">Setelan</h1></div>
      <div class="empty"><b>Perangkat ini belum tersambung</b>
      <span>Setelan tersedia setelah kamera menyala dan tersambung ke server.</span>
      <a class="btn primary" href="#/">Buka layar pantau</a></div></div>`;
    return { dispose() {} };
  }
  const center = info.role === 'pusat';
  const s = cam.settings || {};
  const d = info.defaults;
  const det = info.detector || {};
  const st = {
    park: Number(s.park_seconds ?? d.park_seconds),
    sens: Number(nearest(SENS, s.conf ?? d.conf, 3)),
    size: nearest(SIZE, s.min_box_frac ?? d.min_box_frac, 'sedang'),
  };
  let dead = false;

  view.innerHTML = `<div class="stack-lg">
    <div class="stack"><h1 class="h2">Setelan</h1><p class="dim">Pengaturan untuk perangkat ini. Mengubah deteksi mengulang pelacakan kendaraan yang sedang dipantau.</p></div>

    <section class="stack" aria-labelledby="sPerangkat"><h2 class="h3" id="sPerangkat">Perangkat ini</h2>
      <div class="panel stack">
        <div class="field"><label for="sName">Nama</label><input class="input" id="sName" maxlength="40" autocomplete="off" value="${esc(cam.name)}"></div>
        <div class="row"><button class="btn line" id="sNameSave" type="button">Simpan nama</button></div>
        <div id="sNameMsg"></div>
      </div></section>

    <section class="stack" aria-labelledby="sDeteksi"><h2 class="h3" id="sDeteksi">Deteksi</h2>
      <div class="panel stack-lg">
        <div class="field"><label for="sPark">Dianggap parkir setelah diam <b class="num" id="sParkV"></b></label>
          <input type="range" id="sPark" min="5" max="120" step="5" value="${st.park}">
          <span class="faint small">Kendaraan yang diam lebih lama dari ini di zona trotoar dicatat sebagai parkir.</span></div>
        <div class="field"><label for="sSens">Kepekaan <b id="sSensV"></b></label>
          <input type="range" id="sSens" min="1" max="5" step="1" value="${st.sens}">
          <span class="faint small">Naikkan bila kendaraan sering terlewat. Turunkan bila benda lain sering salah dikenali sebagai kendaraan.</span></div>
        <div class="field"><span class="label" id="sSizeL">Ukuran minimum kendaraan</span>
          <div class="seg fill" role="group" aria-labelledby="sSizeL">${['kecil', 'sedang', 'besar'].map((k) =>
            `<button type="button" data-size="${k}" aria-pressed="${k === st.size}">${k[0].toUpperCase() + k.slice(1)}</button>`).join('')}</div>
          <span class="faint small">Kendaraan yang tampak lebih kecil dari ini diabaikan. Pilih Besar bila hanya kendaraan yang dekat dengan kamera yang perlu dihitung.</span></div>
        <div class="row"><button class="btn primary" id="sSave" type="button">Simpan setelan</button></div>
        <div id="sMsg"></div>
      </div></section>

    <section class="stack" aria-labelledby="sZona"><h2 class="h3" id="sZona">Zona trotoar</h2>
      <div class="panel stack">
        <p class="small" id="sZoneText"></p>
        <div class="row" id="sZoneRow"></div>
        <div id="sZoneMsg"></div>
      </div></section>

    <section class="stack" aria-labelledby="sModel"><h2 class="h3" id="sModel">Model deteksi</h2>
      <div class="panel stack">
        <p class="small"><b>Kendaraan</b> (mobil, motor, bus, truk, sepeda): YOLO <span class="num">${esc(det.weights || 'yolo11n.pt')}</span>. <span id="sModelState"></span></p>
        <p class="small"><b>Trotoar</b>: ${det.sidewalk_model ? 'model segmentasi trotoar ditemukan, zona dicari otomatis dari gambar kamera.' : 'model segmentasi tidak ditemukan. Letakkan di <span class="num">models/trotoar.pt</span> agar zona dicari otomatis, atau gambar zona sendiri di layar pantau.'}</p>
        <p class="faint small">Tangan, orang, dan benda lain tidak pernah dihitung sebagai kendaraan karena bukan kelas kendaraan model.</p>
      </div></section>

    <section class="stack" aria-labelledby="sPriv"><h2 class="h3" id="sPriv">Privasi</h2>
      <div class="panel stack" id="sPrivText">${center
        ? `<p class="small">Anda membuka aplikasi dari komputer pusat, jadi Anda melihat semua perangkat: kejadian, foto bukti, dan alamat IP masing-masing.</p>
           <p class="faint small">Setiap perangkat lain hanya melihat datanya sendiri.</p>`
        : `<p class="small">Anda hanya melihat data perangkat ini. Perangkat lain tidak bisa melihat kejadian atau foto bukti Anda.</p>
           <p class="faint small">Komputer pusat pemantauan melihat semua perangkat, termasuk kejadian, foto bukti, dan alamat IP perangkat ini.</p>`}
      </div></section>

    <section class="stack" aria-labelledby="sData"><h2 class="h3" id="sData">Data</h2>
      <div class="panel stack">
        <p class="dim small">Kejadian dan foto bukti disimpan di komputer yang menjalankan aplikasi, bukan di layanan lain.</p>
        <div class="row"><a class="btn line" href="/api/events.csv" download>${ICON.dl}<span>${center ? 'Unduh semua kejadian (CSV)' : 'Unduh kejadian perangkat ini (CSV)'}</span></a></div>
        <div class="row"><button class="btn danger" id="sDel" type="button">${ICON.trash}<span>Hapus data perangkat ini</span></button></div>
      </div></section>

    <section class="stack" id="sOthers" aria-labelledby="sOth" hidden><h2 class="h3" id="sOth">Perangkat lain</h2>
      <p class="dim small">Hanya terlihat dari komputer pusat.</p>
      <div class="panel"><ul class="devs" id="devs"></ul></div></section>

    <p class="faint small">Sidewalk Sentinel ${esc(info.version)}. Gambar kamera diproses di server milik Anda dan tidak dikirim ke layanan lain.</p>
  </div>`;

  const sync = () => {
    setText($id('sParkV'), `${$id('sPark').value} dtk`);
    setText($id('sSensV'), SENS_LABEL[$id('sSens').value]);
    for (const id of ['sPark', 'sSens']) {                  // bagian trek di kiri gagang berwarna, seperti kontrol geser iOS
      const r = $id(id);
      r.style.setProperty('--p', `${((r.value - r.min) / (r.max - r.min)) * 100}%`);
    }
  };
  $id('sPark').oninput = () => { st.park = Number($id('sPark').value); sync(); };
  $id('sSens').oninput = () => { st.sens = Number($id('sSens').value); sync(); };
  for (const b of view.querySelectorAll('[data-size]')) b.onclick = () => {
    st.size = b.dataset.size;
    for (const o of view.querySelectorAll('[data-size]')) o.setAttribute('aria-pressed', String(o === b));
  };
  sync();
  const manual = cam.zone === 'manual';
  const zoneRow = $id('sZoneRow');
  if (manual && cam.auto_available) {
    setText($id('sZoneText'), 'Anda menggambar zona sendiri, jadi model tidak mengubahnya. Kembali ke zona otomatis bila ingin model mencari trotoar sendiri.');
    zoneRow.innerHTML = `<button class="btn line" id="sZoneAuto" type="button">${ICON.sync}<span>Kembali ke otomatis</span></button>`;
    $id('sZoneAuto').onclick = async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      try { await D.save({ roi: null }); toast('Zona otomatis aktif', 'Model mencari trotoar dari gambar kamera.'); location.hash = '#/'; }
      catch (err) { btn.disabled = false; showError($id('sZoneMsg'), err); }
    };
  } else if (manual) {
    setText($id('sZoneText'), 'Zona digambar manual. Zona otomatis tidak tersedia karena model trotoar tidak ditemukan.');
  } else if (cam.auto_available) {
    setText($id('sZoneText'), 'Zona dicari otomatis dari gambar kamera dan menyesuaikan diri bila kamera digeser. Untuk menggambar sendiri, buka layar pantau lalu pilih Zona manual.');
  } else {
    setText($id('sZoneText'), 'Zona belum ada. Tanpa model trotoar, gambar zona di layar pantau.');
  }
  const MODEL_STATE = { siap: 'Siap dipakai.', memuat: 'Sedang dimuat.', galat: 'Belum siap. Lihat layar pantau untuk penyebabnya.', belum: 'Belum dimuat.' };
  const ms = D.state && D.state.detector;
  setText($id('sModelState'), ms ? (MODEL_STATE[ms.state] || '') : '');

  $id('sNameSave').onclick = async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try { await D.save({ name: $id('sName').value }); toast('Nama disimpan'); $id('sNameMsg').textContent = ''; }
    catch (err) { showError($id('sNameMsg'), err); }
    btn.disabled = false;
  };

  $id('sSave').onclick = async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      await D.save({ settings: { park_seconds: st.park, conf: SENS[st.sens], min_box_frac: SIZE[st.size] } });
      toast('Setelan disimpan', 'Berlaku langsung pada pemantauan.');
      $id('sMsg').textContent = '';
    } catch (err) { showError($id('sMsg'), err); }
    btn.disabled = false;
  };

  const armed = (btn, run) => {                         // hapus butuh dua ketukan
    let timer = 0;
    const idle = btn.innerHTML;
    btn.onclick = async () => {
      if (!timer) {
        btn.firstElementChild?.remove();
        btn.querySelector('span').textContent = 'Ketuk lagi untuk menghapus';
        timer = setTimeout(() => { timer = 0; btn.innerHTML = idle; }, 4000);
        return;
      }
      clearTimeout(timer);
      timer = 0;
      btn.disabled = true;
      try { await run(); } catch (err) { toast('Gagal menghapus', err.message, 'error'); btn.disabled = false; btn.innerHTML = idle; }
    };
  };
  armed($id('sDel'), async () => {
    await del(`/api/cams/${cam.id}`);
    toast('Data dihapus', 'Perangkat ini mulai dari awal.');
    location.hash = '#/';                               // gambar berikutnya dijawab 404; perangkat mendaftar ulang sendiri
  });

  (async () => {
    try {
      const { cams } = await getJSON('/api/cams');
      if (dead) return;
      const others = cams.filter((c) => c.id !== cam.id);
      if (!others.length) return;
      $id('sOthers').hidden = false;
      const ul = $id('devs');
      for (const o of others) {
        const li = document.createElement('li');
        li.innerHTML = `<span class="stack"><b></b><span class="faint small"></span></span><button type="button" class="btn danger">${ICON.trash}<span>Hapus</span></button>`;
        li.querySelector('b').textContent = o.name;
        const meta = li.querySelector('.small');
        meta.textContent = `${ipLabel(o.ip)} · ${o.counts_today.parkir + o.counts_today.naik_trotoar} kejadian hari ini`;
        if (o.ip_since) meta.title = `IP ini tercatat sejak ${new Date(o.ip_since).toLocaleString('id-ID')}`;
        armed(li.querySelector('button'), async () => { await del(`/api/cams/${o.id}`); li.remove(); toast('Perangkat dihapus'); });
        ul.append(li);
      }
    } catch (e) { if (!(e instanceof ApiError)) throw e; }
  })();

  return { dispose() { dead = true; } };
}

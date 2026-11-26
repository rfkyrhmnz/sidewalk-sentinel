// Layar pantau: kamera perangkat ini sebagai CCTV. Video lokal, zona trotoar, kotak kendaraan langsung, hitungan
// hari ini, dan kejadian terbaru. Pemantauan sendiri berjalan di server; halaman ini hanya mengirim gambar dan
// menggambar hasilnya.
import { getJSON } from './api.js';
import { $id, ICON, clock, eventRow, setText, showError, toast } from './core.js';
import { Overlay, PRESET } from './overlay.js';

const EDIT_HINT = 'Seret titik kuning sampai garis menutupi trotoar. Seret titik kecil di tengah sisi untuk menambah sudut, ketuk dua kali sebuah titik untuk menghapusnya.';

const BLOCKED = {
  insecure: {
    title: 'Kamera butuh koneksi aman',
    html: `Browser hanya mengizinkan kamera lewat <code>https</code> atau <code>localhost</code>. Di laptop yang menjalankan aplikasi,
      buka <code>http://localhost:8000</code>. Dari HP, jalankan ulang dengan <code>python main.py --https</code>, lalu buka tautan HP
      yang tampil di terminal.`,
  },
  taken: {
    title: 'Aplikasi terbuka di tab lain',
    html: 'Satu perangkat hanya bisa dipantau dari satu tab. Tutup tab yang lain, atau pindahkan pemantauan ke tab ini.',
    action: ['takeover', 'Pakai kamera di tab ini'],
  },
  denied: { title: 'Izin kamera ditolak', action: ['retry', 'Coba lagi'] },
  error: { title: 'Kamera belum bisa dipakai', action: ['retry', 'Coba lagi'] },
};

export function viewPantau({ view, device: D, info, onEvent }) {
  view.innerHTML = `
  <div class="pantau" id="pantau">
    <section class="stage" aria-label="Kamera perangkat ini">
      <div class="vf" id="vf" data-st="idle" style="--ar: 16 / 9">
        <div class="vf-media" id="media"></div>
        <div class="vf-top">
          <span class="live" id="live" data-st="idle"><i></i><b id="liveT">Standby</b></span>
          <span class="vf-name" id="vfName"></span>
          <time class="vf-clock num" id="clock"></time>
        </div>
        <div class="vf-banner" id="banner" hidden>
          <b id="bTitle"></b><span id="bText"></span>
          <div class="bar" id="bBar" hidden><i></i></div>
        </div>
        <div class="vf-blocked" id="blocked" hidden></div>
      </div>
      <p class="hint" id="hint" hidden></p>
      <div class="tools" id="tools"></div>
    </section>
    <section class="side">
      <div class="meter" aria-label="Hitungan hari ini">
        <div data-k="parkir"><b class="num" id="cP">0</b><span>Parkir</span></div>
        <div data-k="naik_trotoar"><b class="num" id="cN">0</b><span>Naik trotoar</span></div>
        <div class="m-status"><b id="stT">Standby</b><span id="stS"></span></div>
      </div>
      <div class="recent stack">
        <div class="head-row"><h2 class="h3">Terbaru</h2><a class="btn quiet" href="#/kejadian">Lihat semua</a></div>
        <div class="events" id="recent"><p class="faint small" id="recentEmpty">Belum ada kejadian hari ini.</p></div>
      </div>
      <div id="err"></div>
    </section>
  </div>`;

  const vf = $id('vf');
  const media = $id('media');
  const overlay = new Overlay(vf);
  D.mount(media);

  let editing = false;
  let autoEdited = false;
  let toolsSig = '';
  let zoneSig = '';
  let lastSeq = null;
  let recentKey = '';
  let dead = false;

  const parkSeconds = () => Number((D.cam && D.cam.settings && D.cam.settings.park_seconds) || info.defaults.park_seconds);
  const hasZone = () => !!(D.cam && D.cam.roi && D.cam.roi.length >= 3);

  // ------------------------------------------------------------------ video dan bingkai
  const fit = () => {
    const v = D.video;
    if (v.videoWidth && v.videoHeight) vf.style.setProperty('--ar', `${v.videoWidth} / ${v.videoHeight}`);
  };
  D.video.addEventListener('loadedmetadata', fit);
  D.video.addEventListener('resize', fit);
  fit();

  const tick = setInterval(() => setText($id('clock'), clock()), 1000);
  setText($id('clock'), clock());

  // ----------------------------------------------------------------------- penyunting zona
  const enterEdit = () => {
    editing = true;
    overlay.setZone(hasZone() ? D.cam.roi : PRESET);
    overlay.setMode('edit');
    overlay.setScan(false);
    render();
  };

  const leaveEdit = () => {
    editing = false;
    overlay.setMode('view');
    zoneSig = '';
    render();
  };

  const saveZone = async (btn) => {
    const roi = overlay.getZone();
    if (!roi || roi.length < 3) return;
    btn.disabled = true;
    try {
      await D.save({ roi, learn: 'normal' });
      toast('Zona disimpan', 'Mempelajari latar. Biarkan trotoar kosong sebentar.');
      leaveEdit();
    } catch (e) {
      btn.disabled = false;
      showError($id('err'), e);
    }
  };

  // ------------------------------------------------------------------------ tombol
  const button = (id, icon, text, cls = 'btn line') => `<button type="button" class="${cls}" id="${id}">${icon}<span>${text}</span></button>`;

  const buildTools = () => {
    const enabled = !!(D.cam && D.cam.enabled);
    const sig = [D.live, editing, enabled, hasZone(), D.cameras > 1].join();
    if (sig === toolsSig) return;
    toolsSig = sig;
    const host = $id('tools');
    if (!D.live) { host.innerHTML = ''; return; }
    if (editing) {
      host.innerHTML = button('zSave', ICON.check, hasZone() ? 'Simpan zona' : 'Mulai pantau', 'btn primary')
        + button('zReset', ICON.undo, 'Atur ulang') + (hasZone() ? button('zCancel', ICON.close, 'Batal') : '');
      $id('zSave').onclick = (e) => saveZone(e.currentTarget);
      $id('zReset').onclick = () => overlay.setZone(PRESET);
      if (hasZone()) $id('zCancel').onclick = leaveEdit;
      return;
    }
    host.innerHTML = button('toggle', enabled ? ICON.pause : ICON.play, enabled ? 'Jeda' : 'Lanjut')
      + button('zoneBtn', ICON.zone, 'Atur zona')
      + button('recal', ICON.sync, 'Pelajari ulang latar')
      + (D.cameras > 1 ? button('flip', ICON.flip, 'Balik kamera') : '');
    $id('toggle').onclick = async (e) => {
      e.currentTarget.disabled = true;
      try { await D.save({ enabled: !enabled }); } catch (err) { showError($id('err'), err); }
    };
    $id('zoneBtn').onclick = enterEdit;
    $id('recal').onclick = async () => {
      try { await D.recalibrate(); toast('Mempelajari ulang latar', 'Biarkan trotoar kosong sebentar.'); } catch (err) { showError($id('err'), err); }
    };
    if (D.cameras > 1) $id('flip').onclick = () => D.flip();
  };

  // ------------------------------------------------------------------ keadaan
  const describe = (code, st) => {
    const lp = st && st.learn_progress;
    switch (code) {
      case 'asking': return ['Standby', 'Menunggu izin', 'Pilih Izinkan pada pesan browser', 'Menunggu izin kamera', 'Pilih Izinkan pada pesan browser agar pemantauan bisa mulai.'];
      case 'connecting': return ['Standby', 'Menyambung', 'Menghubungi server', 'Menyambung ke server', 'Kamera sudah menyala. Menunggu server menjawab.'];
      case 'offline': return ['Terputus', 'Terputus', D.error, 'Koneksi bermasalah', `${D.error} Pemantauan lanjut sendiri begitu server terjangkau.`];
      case 'edit':
      case 'perlu_area': return ['Atur zona', 'Tandai trotoar', 'Zona belum disimpan', 'Tandai trotoar', EDIT_HINT];
      case 'belajar': return ['Kalibrasi', 'Mempelajari latar', lp ? `${lp.have} dari ${lp.need}` : '', 'Mempelajari latar',
        'Jaga agar trotoar kosong dan perangkat tidak bergerak.'];
      case 'memantau': return ['Live', 'Memantau', 'Mengawasi zona trotoar', '', ''];
      case 'jeda': return ['Jeda', 'Dijeda', 'Tekan Lanjut untuk memantau', 'Pemantauan dijeda', 'Tidak ada kejadian yang dicatat selama dijeda.'];
      default: return ['Standby', 'Menunggu', 'Server menunggu gambar', 'Menunggu gambar', 'Server sedang menunggu gambar dari kamera ini.'];
    }
  };

  const renderBlocked = () => {
    const host = $id('blocked');
    const b = BLOCKED[D.phase];
    if (!b) { host.hidden = true; return false; }
    host.hidden = false;
    const key = `${D.phase}|${D.error}`;
    if (host.dataset.key !== key) {
      host.dataset.key = key;
      host.innerHTML = `<div class="blocked-card"><b></b><p></p>${b.action ? `<button type="button" class="btn primary" id="bAct"></button>` : ''}</div>`;
      host.querySelector('b').textContent = b.title;
      const p = host.querySelector('p');
      if (b.html) p.innerHTML = b.html; else p.textContent = D.error;
      if (b.action) {
        const act = $id('bAct');
        act.textContent = b.action[1];
        act.onclick = () => D.start({ takeOver: b.action[0] === 'takeover' });
      }
    }
    return true;
  };

  const render = () => {
    if (dead) return;
    const blocked = renderBlocked();
    const st = D.state;
    const cam = D.cam;

    if (D.live && !autoEdited && !hasZone()) { autoEdited = true; enterEdit(); return; }

    let code = 'idle';
    if (D.phase === 'asking') code = 'asking';
    else if (D.phase === 'connecting') code = 'connecting';
    else if (D.live) code = editing ? 'edit' : (D.error ? 'offline' : (st ? st.status : cam.status));
    if (blocked) code = 'idle';

    const [liveT, stT, stS, bTitle, bText] = describe(code, st);
    $id('live').dataset.st = code;
    vf.dataset.st = code;
    setText($id('liveT'), liveT);
    setText($id('stT'), stT);
    setText($id('stS'), stS);
    $id('stT').parentElement.dataset.st = code;
    setText($id('vfName'), cam ? cam.name : '');

    const banner = $id('banner');
    const zoning = code === 'edit' || code === 'perlu_area';   // petunjuk zona tampil di bawah gambar agar trotoar tetap terlihat
    banner.hidden = !bTitle || blocked || zoning;
    $id('hint').hidden = !zoning || blocked;
    setText($id('hint'), zoning ? bText : '');
    setText($id('bTitle'), bTitle);
    setText($id('bText'), bText);
    const bar = $id('bBar');
    const lp = st && st.learn_progress;
    bar.hidden = code !== 'belajar' || !lp || !lp.need;
    if (!bar.hidden) bar.firstElementChild.style.width = `${Math.round((100 * lp.have) / lp.need)}%`;

    const counts = (st && st.counts_today) || (cam && cam.counts_today) || { parkir: 0, naik_trotoar: 0 };
    setText($id('cP'), String(counts.parkir));
    setText($id('cN'), String(counts.naik_trotoar));

    if (!editing) {
      const sig = JSON.stringify(cam && cam.roi);
      if (sig !== zoneSig) { zoneSig = sig; overlay.setZone(cam && cam.roi); }
      overlay.setTracks(code === 'memantau' && st ? st.tracks : [], parkSeconds());
      overlay.setScan(code === 'belajar');
    }
    buildTools();
    if (st && cam) loadRecent(st.seq);
  };

  // --------------------------------------------------------------- kejadian terbaru
  const loadRecent = async (seq) => {
    const key = `${D.id}:${seq}`;
    if (key === recentKey) return;
    recentKey = key;
    try {
      const { events } = await getJSON(`/api/cams/${D.id}/events?limit=5`);
      if (dead) return;
      const host = $id('recent');
      const known = new Set([...host.querySelectorAll('.event')].map((n) => n.dataset.no));
      host.textContent = '';
      if (!events.length) {
        const p = document.createElement('p');
        p.className = 'faint small';
        p.id = 'recentEmpty';
        p.textContent = 'Belum ada kejadian hari ini.';
        host.append(p);
        return;
      }
      for (const e of events) host.append(eventRow({ ...e, camera: null }, { fresh: lastSeq !== null && !known.has(String(e.no)) }));
    } catch { /* baris kejadian hanya pelengkap; pesan galat koneksi sudah ada di status */ }
    lastSeq = seq;
  };

  const flash = (events) => {
    const last = events[events.length - 1];
    vf.dataset.alert = last.kind;
    setTimeout(() => { if (vf.dataset.alert === last.kind) delete vf.dataset.alert; }, 1500);
  };

  const off = D.on(render);
  const offEvent = onEvent(flash);
  render();

  return {
    dispose() {
      dead = true;
      off();
      offEvent();
      clearInterval(tick);
      D.video.removeEventListener('loadedmetadata', fit);
      D.video.removeEventListener('resize', fit);
      overlay.destroy();
      D.park();
    },
  };
}

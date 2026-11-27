// Layar pantau: kamera perangkat ini sebagai CCTV. Video lokal, zona trotoar (dicari otomatis oleh model; bisa digambar
// manual), kotak kendaraan langsung, hitungan hari ini, dan kejadian terbaru. Pemantauan sendiri berjalan di server;
// halaman ini hanya mengirim gambar dan menggambar hasilnya.
import { getJSON } from './api.js';
import { $id, ICON, clock, eventRow, setText, showError, toast } from './core.js';
import { Overlay, PRESET } from './overlay.js';

const EDIT_HINT = 'Seret titik putih sampai garis menutupi trotoar. Seret titik kecil di tengah sisi untuk menambah sudut, ketuk dua kali sebuah titik untuk menghapusnya.';

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
  closed: {
    title: 'Kamera ditutup',
    icon: 'cameraOff',
    html: 'Kamera mati sepenuhnya. Tidak ada gambar yang direkam, dikirim, atau disimpan selama ditutup. Kejadian lama tetap tersimpan.',
    action: ['open', 'Buka kamera'],
  },
  refused: { title: 'Server menolak perangkat ini', action: ['retry', 'Coba lagi'] },
  denied: { title: 'Izin kamera ditolak', action: ['retry', 'Coba lagi'] },
  error: { title: 'Kamera belum bisa dipakai', action: ['retry', 'Coba lagi'] },
};

export function viewPantau({ view, device: D, info, onEvent }) {
  view.innerHTML = `
  <div class="pantau" id="pantau">
    <section class="stage" aria-label="Kamera perangkat ini">
      <div class="vf" id="vf" data-st="idle">
        <div class="vf-media" id="media"></div>
        <div class="vf-meta"><span class="vf-name" id="vfName"></span><time class="vf-clock num" id="clock"></time></div>
        <span class="vf-zone" id="zoneChip" hidden></span>
        <div class="vf-banner" id="banner" hidden>
          <b id="bTitle"></b><span id="bText"></span>
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
  let proposing = false;                                   // usulan zona (di penyunting manual) sedang diminta
  let note = '';                                           // catatan tambahan di bawah gambar saat menata zona

  const parkSeconds = () => Number((D.cam && D.cam.settings && D.cam.settings.park_seconds) || info.defaults.park_seconds);
  // Zona yang berlaku: dari server (zona otomatis berubah sendiri), atau zona manual dari data perangkat.
  const zoneNow = () => (D.state && 'roi' in D.state ? D.state.roi : D.cam && D.cam.roi);
  const zoneMode = () => (D.state && D.state.zone) || (D.cam && D.cam.zone) || 'auto';
  const hasZone = () => { const z = zoneNow(); return !!(z && z.length >= 3); };
  const autoAvail = () => !!(D.cam ? D.cam.auto_available : info.detector && info.detector.sidewalk_model);

  // ------------------------------------------------------------------ video dan bingkai
  const fit = () => {
    const v = D.video;
    if (v.videoWidth && v.videoHeight) {
      vf.style.setProperty('--ar', `${v.videoWidth} / ${v.videoHeight}`);
      vf.style.setProperty('--arn', String(v.videoWidth / v.videoHeight));
    }
  };
  D.video.addEventListener('loadedmetadata', fit);
  D.video.addEventListener('resize', fit);
  fit();

  const tick = setInterval(() => setText($id('clock'), clock()), 1000);
  setText($id('clock'), clock());

  // ----------------------------------------------------------------------- penyunting zona manual
  const enterEdit = () => {
    editing = true;
    note = '';
    overlay.setZoneKind('manual');
    overlay.setZone(hasZone() ? zoneNow() : PRESET);
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
      await D.save({ roi });
      toast('Zona manual disimpan', 'Kendaraan yang berhenti atau naik di zona ini akan dicatat.');
      leaveEdit();
    } catch (e) {
      btn.disabled = false;
      showError($id('err'), e);
    }
  };

  const backToAuto = async (btn) => {
    btn.disabled = true;
    try {
      await D.save({ roi: null });
      toast('Zona otomatis aktif', 'Model mencari trotoar dari gambar kamera.');
      leaveEdit();
    } catch (e) {
      btn.disabled = false;
      showError($id('err'), e);
    }
  };

  // Minta usulan dari model trotoar sebagai titik awal penataan manual. Server perlu minimal satu gambar, jadi 409 dicoba lagi.
  const propose = async () => {
    if (proposing) return;
    proposing = true;
    note = 'Mencari trotoar di gambar.';
    render();
    try {
      for (let i = 0; ; i++) {
        try {
          const r = await D.proposeZone();
          if (dead || !editing) return;
          if (r.roi) {
            overlay.setZone(r.roi);
            note = 'Usulan dari model sudah dimuat. Periksa dulu sebelum menyimpan.';
          } else {
            note = r.message;
          }
          return;
        } catch (e) {
          if (e.status === 409 && i < 5) { await new Promise((ok) => setTimeout(ok, 1000)); continue; }
          throw e;
        }
      }
    } catch (e) {
      note = '';
      showError($id('err'), e);
    } finally {
      proposing = false;
      if (!dead) render();
    }
  };

  // ------------------------------------------------------------------------ tombol
  const button = (id, icon, text, cls = 'btn line') => `<button type="button" class="${cls}" id="${id}">${icon}<span>${text}</span></button>`;

  const buildTools = () => {
    const enabled = !!(D.cam && D.cam.enabled);
    const modelErr = !editing && !!(D.state && D.state.status === 'model_galat');
    const auto = autoAvail();
    const manual = zoneMode() === 'manual';
    const sig = [D.live, editing, enabled, hasZone(), D.cameras > 1, auto, manual, proposing, modelErr].join();
    if (sig === toolsSig) return;
    toolsSig = sig;
    const host = $id('tools');
    if (!D.live) { host.innerHTML = ''; return; }
    if (editing) {
      host.innerHTML = button('zSave', ICON.check, hasZone() ? 'Simpan zona' : 'Mulai pantau', 'btn primary')
        + (auto ? button('zAuto', ICON.auto, proposing ? 'Mencari trotoar' : 'Usulkan otomatis') : '')
        + button('zReset', ICON.undo, 'Atur ulang')
        + (auto && manual ? button('zBack', ICON.sync, 'Kembali ke otomatis') : '')
        + (hasZone() ? button('zCancel', ICON.close, 'Batal') : '');
      $id('zSave').onclick = (e) => saveZone(e.currentTarget);
      if (auto) {
        $id('zAuto').disabled = proposing;
        $id('zAuto').onclick = () => propose();
      }
      $id('zReset').onclick = () => overlay.setZone(PRESET);
      if ($id('zBack')) $id('zBack').onclick = (e) => backToAuto(e.currentTarget);
      if ($id('zCancel')) $id('zCancel').onclick = leaveEdit;
      return;
    }
    host.innerHTML = button('toggle', enabled ? ICON.pause : ICON.play, enabled ? 'Jeda' : 'Lanjut')
      + button('zoneBtn', ICON.zone, auto ? 'Zona manual' : 'Atur zona')
      + button('closeCam', ICON.cameraOff, 'Tutup kamera')
      + (modelErr ? button('retryModel', ICON.sync, 'Muat ulang model') : '')
      + (D.cameras > 1 ? button('flip', ICON.flip, 'Balik kamera') : '');
    $id('toggle').onclick = async (e) => {
      e.currentTarget.disabled = true;
      try { await D.save({ enabled: !enabled }); } catch (err) { showError($id('err'), err); }
    };
    $id('zoneBtn').onclick = enterEdit;
    $id('closeCam').onclick = () => { D.close(); toast('Kamera ditutup', 'Kamera mati dan tidak ada gambar yang dikirim.'); };
    if (modelErr) {
      $id('retryModel').onclick = async () => {
        try { await D.retryModel(); toast('Memuat ulang model'); } catch (err) { showError($id('err'), err); }
      };
    }
    if (D.cameras > 1) $id('flip').onclick = () => D.flip();
  };

  // ------------------------------------------------------------------ keadaan
  // [judul status, keterangan status, judul banner di gambar, isi banner]
  const describe = (code, st) => {
    const dmsg = (st && st.detector && st.detector.message) || '';
    switch (code) {
      case 'asking': return ['Menunggu izin', 'Pilih Izinkan pada pesan browser', 'Menunggu izin kamera', 'Pilih Izinkan pada pesan browser agar pemantauan bisa mulai.'];
      case 'connecting': return D.error
        ? ['Menyambung', 'Belum berhasil, mencoba lagi', 'Belum bisa menyambung ke server', `${D.error} Mencoba lagi tiap 3 detik.`]
        : ['Menyambung', 'Menghubungi server', 'Menyambung ke server', 'Kamera sudah menyala. Menunggu server menjawab.'];
      case 'offline': return ['Terputus', D.error, 'Koneksi bermasalah', `${D.error} Pemantauan lanjut sendiri begitu server terjangkau.`];
      case 'edit': return ['Tandai trotoar', 'Zona belum disimpan', 'Tandai trotoar', EDIT_HINT];
      case 'perlu_area': return ['Tandai trotoar', 'Zona otomatis tidak tersedia', 'Tandai trotoar',
        'Zona otomatis tidak tersedia. Tekan Zona manual, lalu tandai trotoar di gambar.'];
      case 'mencari_trotoar': return ['Mencari trotoar', 'Zona dibuat otomatis', 'Mencari trotoar',
        'Arahkan kamera ke trotoar. Zona dibuat otomatis begitu trotoar terlihat.'];
      case 'memuat': return ['Memuat model', 'Menyiapkan YOLO', 'Memuat model deteksi',
        'Saat pertama dijalankan, model kendaraan diunduh sekali dan butuh internet. Pemantauan mulai sendiri begitu model siap.'];
      case 'model_galat': return ['Model belum siap', 'Deteksi kendaraan belum bisa jalan', 'Model deteksi belum siap', dmsg || 'Model YOLO tidak bisa dimuat.'];
      case 'memantau': return ['Memantau', zoneMode() === 'manual' ? 'Mengawasi zona manual' : 'Mengawasi zona trotoar otomatis', '', ''];
      case 'jeda': return ['Dijeda', 'Tekan Lanjut untuk memantau', 'Pemantauan dijeda', 'Tidak ada kejadian yang dicatat selama dijeda.'];
      case 'ditutup': return ['Ditutup', 'Kamera mati, tidak ada gambar dikirim', '', ''];
      default: return ['Menunggu', 'Server menunggu gambar', 'Menunggu gambar', 'Server sedang menunggu gambar dari kamera ini.'];
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
      host.innerHTML = `<div class="blocked-card">${b.icon ? `<span class="bc-icon">${ICON[b.icon]}</span>` : ''}<b></b><p></p>${b.action ? `<button type="button" class="btn primary" id="bAct"></button>` : ''}</div>`;
      host.querySelector('b').textContent = b.title;
      const p = host.querySelector('p');
      if (b.html) p.innerHTML = b.html; else p.textContent = D.error;
      if (b.action) {
        const act = $id('bAct');
        act.textContent = b.action[1];
        act.onclick = () => (b.action[0] === 'open' ? D.open() : D.start({ takeOver: b.action[0] === 'takeover' }));
      }
    }
    return true;
  };

  const render = () => {
    if (dead) return;
    if (D.phase === 'closed' && editing) { leaveEdit(); return; }     // kamera ditutup (mungkin dari pusat) saat zona sedang ditata
    const blocked = renderBlocked();
    const st = D.state;
    const cam = D.cam;

    // Tanpa model trotoar tidak ada zona otomatis: langsung minta penggambaran zona, seperti biasa.
    if (D.live && !autoEdited && !hasZone() && !autoAvail()) { autoEdited = true; enterEdit(); return; }

    let code = 'idle';
    if (D.phase === 'asking') code = 'asking';
    else if (D.phase === 'connecting') code = 'connecting';
    else if (D.phase === 'closed') code = 'ditutup';
    else if (D.live) code = editing ? 'edit' : (D.error ? 'offline' : (st ? st.status : cam.status));
    if (blocked && code !== 'ditutup') code = 'idle';

    const [stT, stS, bTitle, bText] = describe(code, st);
    vf.dataset.st = code;
    setText($id('stT'), stT);
    setText($id('stS'), stS);
    $id('stT').parentElement.dataset.st = code;
    setText($id('vfName'), cam ? cam.name : '');

    const chip = $id('zoneChip');
    chip.hidden = !(D.live && hasZone() && !editing && !blocked);
    chip.dataset.zone = zoneMode();
    setText(chip, zoneMode() === 'manual' ? 'Zona manual' : 'Zona otomatis');

    const banner = $id('banner');
    const zoning = code === 'edit' || code === 'perlu_area';   // petunjuk zona tampil di bawah gambar agar trotoar tetap terlihat
    banner.hidden = !bTitle || blocked || zoning;
    $id('hint').hidden = !zoning || blocked;
    setText($id('hint'), zoning ? (note ? `${note} ${bText}` : bText) : '');
    setText($id('bTitle'), bTitle);
    setText($id('bText'), bText);

    const counts = (st && st.counts_today) || (cam && cam.counts_today) || { parkir: 0, naik_trotoar: 0 };
    setText($id('cP'), String(counts.parkir));
    setText($id('cN'), String(counts.naik_trotoar));

    if (!editing) {
      const zone = zoneNow();
      const sig = JSON.stringify(zone);
      overlay.setZoneKind(zoneMode());
      if (sig !== zoneSig) { zoneSig = sig; overlay.setZone(zone); }
      overlay.setTracks(code === 'memantau' && st ? st.tracks : [], parkSeconds());
      overlay.setScan(code === 'memuat' || code === 'mencari_trotoar');
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

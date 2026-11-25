// Kamera langsung: ambil frame dari <video>, kirim ke server, gambar hasilnya di atas video.
import { ApiError, postBlob } from './api.js';

export function cameraSupported() {
  return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia) && window.isSecureContext;
}

export async function openCamera(video, facing = 'environment') {
  const stream = await navigator.mediaDevices.getUserMedia({
    video: { facingMode: { ideal: facing }, width: { ideal: 1280 }, height: { ideal: 720 } },
    audio: false,
  });
  video.srcObject = stream;
  video.muted = true;
  video.playsInline = true;
  video.setAttribute('playsinline', '');
  await video.play();
  return stream;
}

// Elemen <video> otomatis di-pause saat dikeluarkan dari dokumen (aturan HTML). Setiap kali dipasang
// ulang ke tampilan baru, putar lagi; kalau tidak, pratinjau beku dan server menerima frame yang sama terus.
export function mountVideo(host, video) {
  host.appendChild(video);
  const p = video.play();
  if (p && p.catch) p.catch(() => {});
}

export function snapshot(video, maxW = 960) {
  const c = document.createElement('canvas');
  const w = Math.min(maxW, video.videoWidth);
  c.width = w;
  c.height = Math.round((video.videoHeight * w) / video.videoWidth);
  c.getContext('2d').drawImage(video, 0, 0, c.width, c.height);
  return c;
}

const COLORS = { parkir: '#ff5a5f', naik_trotoar: '#e056ff', diam: '#ffb020', bergerak: '#38e1ff', luar: '#8da2c0' };
const NAMES = { parkir: 'Parkir', naik_trotoar: 'Naik trotoar', diam: 'Diam', bergerak: 'Bergerak', luar: '' };

export function drawTracks(canvas, tracks, parkSeconds) {
  const ctx = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height;
  ctx.clearRect(0, 0, W, H);
  const k = W / 640;
  ctx.font = `700 ${Math.round(15 * k)}px Bahnschrift, "DIN Alternate", "Roboto Condensed", system-ui, sans-serif`;
  for (const t of tracks) {
    const [bx, by, bw, bh] = t.box;
    const x = bx * W, y = by * H, w = bw * W, h = bh * H;
    const color = COLORS[t.state] || COLORS.luar;
    const arm = Math.max(8 * k, Math.min(w, h) * 0.28);
    ctx.lineWidth = (t.state === 'luar' ? 1.5 : 3) * k;
    ctx.strokeStyle = color;
    ctx.beginPath();
    [[x, y, 1, 1], [x + w, y, -1, 1], [x, y + h, 1, -1], [x + w, y + h, -1, -1]].forEach(([px, py, dx, dy]) => {
      ctx.moveTo(px + dx * arm, py); ctx.lineTo(px, py); ctx.lineTo(px, py + dy * arm);
    });
    ctx.stroke();
    let label = NAMES[t.state];
    if (t.state === 'diam') {
      label = `Diam ${Math.round(t.dwell)}/${Math.round(parkSeconds)} dtk`;
      ctx.fillStyle = 'rgba(40,40,40,.9)';
      ctx.fillRect(x, y + h + 4 * k, w, 4 * k);
      ctx.fillStyle = color;
      ctx.fillRect(x, y + h + 4 * k, w * Math.min(1, t.dwell / parkSeconds), 4 * k);
    }
    if (label) {
      const tw = ctx.measureText(label).width;
      ctx.fillStyle = 'rgba(10,20,36,.85)';
      ctx.fillRect(x, y - 22 * k, tw + 12 * k, 20 * k);
      ctx.fillStyle = color;
      ctx.fillText(label, x + 6 * k, y - 7 * k);
    }
  }
}

function toBlob(canvas, quality) {
  return new Promise((res) => canvas.toBlob(res, 'image/jpeg', quality));
}

export class LiveLoop {
  constructor({ video, sid, onState, onError, fps = 5, width = 480 }) {
    Object.assign(this, { video, sid, onState, onError, interval: 1000 / fps, width });
    this.running = false;
    this.timer = null;
    this.canvas = document.createElement('canvas');
  }

  start() { this.running = true; this._tick(); }

  stop() { this.running = false; clearTimeout(this.timer); }

  async _tick() {
    if (!this.running) return;
    const t0 = performance.now();
    try {
      if (this.video.paused || this.video.ended) {
        // Dijeda sistem (layar mati, panggilan masuk, pindah tab): jangan kirim frame beku.
        const p = this.video.play();
        if (p && p.catch) p.catch(() => {});
      } else if (this.video.videoWidth) {
        const w = Math.min(this.width, this.video.videoWidth);
        this.canvas.width = w;
        this.canvas.height = Math.round((this.video.videoHeight * w) / this.video.videoWidth);
        this.canvas.getContext('2d').drawImage(this.video, 0, 0, this.canvas.width, this.canvas.height);
        const blob = await toBlob(this.canvas, 0.65);
        const data = await postBlob(`/api/live/${this.sid}/frame`, blob, 'image/jpeg');
        if (this.running) this.onState(data);
      }
    } catch (e) {
      this.onError(e);
      if (e instanceof ApiError && [400, 403, 404].includes(e.status)) { this.stop(); return; }
    }
    if (this.running) {
      this.timer = setTimeout(() => this._tick(), Math.max(30, this.interval - (performance.now() - t0)));
    }
  }
}

// ------------------------------------------------------------------------------ kamera perangkat
// Perangkat ini menjadi kamera CCTV: video lokal diambil framenya ~5 kali per detik dan didorong ke server,
// yang memantaunya seperti kamera lain. Pusher hidup selama aplikasi terbuka, bukan selama satu halaman,
// jadi pindah dari halaman kamera ke Kejadian tidak mematikan kamera.
export class Pusher {
  constructor({ fps = 5, width = 640 } = {}) {
    this.interval = 1000 / fps;
    this.width = width;
    this.camId = null;
    this.facing = 'environment';
    this.stream = null;
    this.state = null;          // respons terakhir server: status, jejak, hitungan
    this.error = '';
    this.listeners = new Set();
    this.timer = 0;
    this.lock = null;
    this.canvas = document.createElement('canvas');
    // <video> tetap berada di dokumen (tersembunyi) agar terus berjalan di iOS; halaman memindahkannya sementara.
    this.home = document.createElement('div');
    this.home.setAttribute('aria-hidden', 'true');
    this.home.style.cssText = 'position:fixed;left:0;bottom:0;width:1px;height:1px;overflow:hidden;opacity:0;pointer-events:none';
    this.video = document.createElement('video');
    this.home.appendChild(this.video);
    document.body.appendChild(this.home);
    document.addEventListener('visibilitychange', () => { if (!document.hidden && this.active) this._wake(); });
  }

  get active() { return !!this.stream && this.camId !== null; }

  on(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); }

  _emit() { for (const fn of [...this.listeners]) { try { fn(this); } catch (e) { console.error(e); } } }

  // Nyalakan kamera perangkat tanpa mengirim apa pun. Izin diminta di sini, sebelum kamera dibuat di server,
  // supaya penolakan izin tidak meninggalkan kamera kosong.
  async open(facing = this.facing) {
    this.stop(false);
    this.facing = facing;
    try {
      this.stream = await openCamera(this.video, facing);
    } catch (e) {
      this.stream = null;
      this.error = e.name === 'NotAllowedError'
        ? 'Izin kamera ditolak. Izinkan kamera untuk situs ini di pengaturan browser, lalu coba lagi.'
        : `Kamera tidak bisa dinyalakan: ${e.message}`;
      this._emit();
      throw e;
    }
    this.error = '';
  }

  // Mulai mengirim frame ke kamera server `camId` (kamera sudah dinyalakan oleh open()).
  begin(camId) {
    this.camId = camId;
    this.error = '';
    this.state = null;
    this._wake();
    this._emit();
    this._tick();
  }

  async start(camId, facing = this.facing) {
    await this.open(facing);
    this.begin(camId);
  }

  stop(emit = true) {
    clearTimeout(this.timer);
    if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
    this.stream = null;
    this.camId = null;
    this.state = null;
    this.video.srcObject = null;
    if (this.lock) { this.lock.release().catch(() => {}); this.lock = null; }
    if (emit) this._emit();
  }

  async flip() {
    if (!this.active) return;
    const id = this.camId;
    await this.start(id, this.facing === 'environment' ? 'user' : 'environment');
  }

  // Pasang <video> ke bingkai halaman; kembalikan ke tempat tersembunyi saat halaman ditutup.
  mount(host) { mountVideo(host, this.video); }

  park() {
    if (this.video.parentNode !== this.home) this.home.appendChild(this.video);
    const p = this.video.play();
    if (p && p.catch) p.catch(() => {});
  }

  async _wake() {                // layar yang mati menghentikan kamera di HP; tahan layar selama kamera aktif
    try {
      if (navigator.wakeLock && !this.lock) {
        this.lock = await navigator.wakeLock.request('screen');
        this.lock.addEventListener('release', () => { this.lock = null; });
      }
    } catch { /* tidak wajib */ }
  }

  async _tick() {
    if (!this.active) return;
    const id = this.camId;
    const t0 = performance.now();
    try {
      const v = this.video;
      if (v.paused || v.ended) {                  // dijeda sistem (panggilan masuk, pindah tab): jangan kirim frame beku
        const p = v.play();
        if (p && p.catch) p.catch(() => {});
      } else if (v.videoWidth) {
        const w = Math.min(this.width, v.videoWidth);
        this.canvas.width = w;
        this.canvas.height = Math.round((v.videoHeight * w) / v.videoWidth);
        this.canvas.getContext('2d').drawImage(v, 0, 0, this.canvas.width, this.canvas.height);
        const blob = await toBlob(this.canvas, 0.7);
        const data = await postBlob(`/api/cams/${id}/frame`, blob, 'image/jpeg');
        if (this.camId === id) { this.state = data; this.error = ''; this._emit(); }
      }
    } catch (e) {
      if (this.camId !== id) return;
      if (e instanceof ApiError && [400, 404].includes(e.status)) {   // kamera dihapus atau bukan kamera perangkat
        this.error = e.status === 404 ? 'Kamera ini sudah dihapus.' : e.message;
        this.stop();
        return;
      }
      this.error = 'Koneksi ke server bermasalah, mencoba lagi.';
      this._emit();
    }
    if (this.active && this.camId === id) {
      this.timer = setTimeout(() => this._tick(), Math.max(30, this.interval - (performance.now() - t0)));
    }
  }
}

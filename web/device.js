// Perangkat ini adalah kameranya. Laptop memakai kamera laptop, HP memakai kamera HP; tidak ada sumber lain.
// Halaman mengambil gambar dari kamera lokal sekitar lima kali per detik, mengirimnya ke server, dan menerima
// balik status serta posisi kendaraan untuk digambar. Objek ini hidup selama aplikasi terbuka (bukan selama satu
// halaman), jadi pindah ke Laporan atau Kejadian tidak mematikan kamera.
import { ApiError, getJSON, postBlob, postJSON } from './api.js';
import { keep } from './core.js';

export const cameraSupported = () => !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia) && window.isSecureContext;
const isMobile = () => /Android|iPhone|iPad|Mobile/i.test(navigator.userAgent || '');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const toBlob = (canvas, quality) => new Promise((res) => canvas.toBlob(res, 'image/jpeg', quality));

function randomKey() {
  const a = new Uint8Array(12);
  crypto.getRandomValues(a);
  return Array.from(a, (b) => b.toString(16).padStart(2, '0')).join('');
}

/** Kunci acak yang mengenali perangkat ini ke server; sama setiap kali aplikasi dibuka. */
export function deviceKey() {
  let k = keep.get('ss_device');
  if (!/^[0-9a-f]{16,64}$/.test(k || '')) { k = randomKey(); keep.set('ss_device', k); }
  return k;
}

export function guessName() {
  const ua = navigator.userAgent || '';
  if (/iPhone/.test(ua)) return 'iPhone';
  if (/iPad/.test(ua)) return 'iPad';
  if (/Android/.test(ua)) return /Mobile/.test(ua) ? 'Ponsel Android' : 'Tablet Android';
  if (/Windows/.test(ua)) return 'Laptop Windows';
  if (/Macintosh|Mac OS X/.test(ua)) return 'Mac';
  if (/Linux|X11/.test(ua)) return 'Laptop Linux';
  return 'Perangkat';
}

export function cameraMessage(e) {
  switch (e && e.name) {
    case 'NotAllowedError':
    case 'SecurityError':
      return 'Izin kamera ditolak. Izinkan kamera untuk situs ini di pengaturan browser, lalu coba lagi.';
    case 'NotFoundError':
    case 'OverconstrainedError':
      return 'Perangkat ini tidak punya kamera yang bisa dipakai.';
    case 'NotReadableError':
    case 'AbortError':
      return 'Kamera sedang dipakai aplikasi lain. Tutup aplikasi itu, lalu coba lagi.';
    default:
      return `Kamera tidak bisa dinyalakan: ${(e && e.message) || e}`;
  }
}

async function openCamera(video, facing) {
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

/**
 * Fase: idle, insecure (konteks tidak aman), taken (tab lain memakai kamera ini), asking (menunggu izin),
 * denied, error, connecting (kamera menyala, server belum menjawab), live.
 */
export class Device {
  constructor({ fps = 5, width = 640 } = {}) {
    this.interval = 1000 / fps;
    this.width = width;
    this.key = deviceKey();
    this.phase = 'idle';
    this.error = '';
    this.cam = null;              // info kamera dari server: id, nama, roi, pengaturan
    this.state = null;            // jawaban terakhir server: status, jejak kendaraan, hitungan
    this.facing = keep.get('ss_facing') || (isMobile() ? 'environment' : 'user');
    this.cameras = 1;             // jumlah kamera di perangkat ini (tombol balik kamera)
    this.stream = null;
    this.timer = 0;
    this.wake = null;
    this.release = null;
    this.listeners = new Set();
    this.canvas = document.createElement('canvas');
    // <video> tetap berada di dokumen (tersembunyi) agar terus berjalan di iOS; halaman memindahkannya sementara.
    this.home = document.createElement('div');
    this.home.setAttribute('aria-hidden', 'true');
    this.home.style.cssText = 'position:fixed;left:0;bottom:0;width:1px;height:1px;overflow:hidden;opacity:0;pointer-events:none';
    this.video = document.createElement('video');
    this.home.appendChild(this.video);
    document.body.appendChild(this.home);
    document.addEventListener('visibilitychange', () => { if (!document.hidden && this.running) this._holdScreen(); });
  }

  get running() { return !!this.stream; }
  get live() { return this.phase === 'live' && !!this.cam; }
  get id() { return this.cam ? this.cam.id : null; }

  on(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); }

  _emit() { for (const fn of [...this.listeners]) { try { fn(this); } catch (e) { console.error(e); } } }

  _set(phase, error = '') { this.phase = phase; this.error = error; this._emit(); }

  // Satu tab saja yang boleh mengirim untuk satu perangkat: dua tab menyisipkan gambar ke kamera yang sama dan
  // merusak pelacakan. Kunci Web Locks dilepas otomatis saat tab ditutup atau dimuat ulang.
  async _acquire(steal = false) {
    if (!navigator.locks) return true;
    if (this.release) return true;
    for (let i = 0; i < (steal ? 1 : 6); i++) {
      const got = await new Promise((resolve) => {
        navigator.locks.request('sentinel-camera', steal ? { steal: true } : { ifAvailable: true }, (lock) => {
          if (!lock) { resolve(false); return undefined; }
          resolve(true);
          return new Promise((rel) => { this.release = rel; });
        }).catch(() => {                                   // kunci direbut tab lain
          resolve(false);
          if (this.running) { this.stop(false); this._set('taken'); }
        });
      });
      if (got) return true;
      await sleep(500);                                    // tab lama yang baru dimuat ulang butuh sejenak melepas kunci
    }
    return false;
  }

  _free() { if (this.release) { this.release(); this.release = null; } }

  async start({ takeOver = false } = {}) {
    this.stop(false);
    if (!cameraSupported()) { this._set('insecure'); return; }
    if (!(await this._acquire(takeOver))) { this._set('taken'); return; }
    this._set('asking');
    try {
      this.stream = await openCamera(this.video, this.facing);
    } catch (e) {
      this._free();
      this._set(e && e.name === 'NotAllowedError' ? 'denied' : 'error', cameraMessage(e));
      return;
    }
    this._countCameras();
    this._holdScreen();
    this._set('connecting');
    this._connect();
  }

  async _connect() {
    const stream = this.stream;
    while (this.stream === stream && stream) {
      try {
        this.cam = await postJSON('/api/device', { key: this.key, name: guessName() });
        this.state = null;
        this._set('live');
        this._tick();
        return;
      } catch (e) {
        this.error = e.message;
        this._emit();
        await sleep(3000);
      }
    }
  }

  stop(emit = true) {
    clearTimeout(this.timer);
    if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
    this.stream = null;
    this.cam = null;
    this.state = null;
    this.video.srcObject = null;
    if (this.wake) { this.wake.release().catch(() => {}); this.wake = null; }
    this._free();
    if (emit) this._set('idle');
  }

  async _countCameras() {
    try {
      const list = await navigator.mediaDevices.enumerateDevices();
      this.cameras = Math.max(1, list.filter((d) => d.kind === 'videoinput').length);
      this._emit();
    } catch { /* tidak wajib */ }
  }

  async flip() {
    const next = this.facing === 'environment' ? 'user' : 'environment';
    try {
      const old = this.stream;
      const stream = await openCamera(this.video, next);
      if (old) old.getTracks().forEach((t) => t.stop());
      this.stream = stream;
      this.facing = next;
      keep.set('ss_facing', next);
      await this.recalibrate();                            // sudut pandang baru: latar harus dipelajari ulang
    } catch (e) {
      this.error = cameraMessage(e);
      this._emit();
    }
  }

  // ---------------------------------------------------------------- perintah ke server
  async save(patch) {
    if (!this.cam) throw new ApiError('Perangkat belum tersambung ke server.', 0);
    this.cam = await postJSON(`/api/cams/${this.cam.id}`, patch);
    this._emit();
    return this.cam;
  }

  async recalibrate() { if (this.cam) await postJSON(`/api/cams/${this.cam.id}/recalibrate`); }

  async refresh() {
    if (!this.cam) return null;
    this.cam = await getJSON(`/api/cams/${this.cam.id}`);
    this._emit();
    return this.cam;
  }

  // ------------------------------------------------------------------ tampilan
  mount(host) {
    host.appendChild(this.video);
    const p = this.video.play();
    if (p && p.catch) p.catch(() => {});
  }

  park() {
    if (this.video.parentNode !== this.home) this.home.appendChild(this.video);
    const p = this.video.play();
    if (p && p.catch) p.catch(() => {});
  }

  async _holdScreen() {                                    // layar yang mati menghentikan kamera di HP
    try {
      if (navigator.wakeLock && !this.wake) {
        this.wake = await navigator.wakeLock.request('screen');
        this.wake.addEventListener('release', () => { this.wake = null; });
      }
    } catch { /* tidak wajib */ }
  }

  // ------------------------------------------------------------------ pengiriman
  async _tick() {
    if (!this.live) return;
    const id = this.cam.id;
    const t0 = performance.now();
    let slow = false;
    try {
      const v = this.video;
      if (v.paused || v.ended) {                           // dijeda sistem (panggilan masuk, pindah tab): jangan kirim frame beku
        const p = v.play();
        if (p && p.catch) p.catch(() => {});
      } else if (v.videoWidth) {
        const w = Math.min(this.width, v.videoWidth);
        this.canvas.width = w;
        this.canvas.height = Math.round((v.videoHeight * w) / v.videoWidth);
        this.canvas.getContext('2d').drawImage(v, 0, 0, this.canvas.width, this.canvas.height);
        const blob = await toBlob(this.canvas, 0.7);
        const data = await postBlob(`/api/cams/${id}/frame`, blob, 'image/jpeg');
        if (this.cam && this.cam.id === id) { this.state = data; this.error = ''; slow = data.enabled === false; this._emit(); }
      }
    } catch (e) {
      if (!this.cam || this.cam.id !== id) return;
      if (e instanceof ApiError && e.status === 404) {     // perangkat dihapus dari server: daftar lagi, mulai bersih
        this.cam = null;
        this.state = null;
        this._set('connecting');
        this._connect();
        return;
      }
      this.error = e instanceof ApiError && e.status === 400 ? e.message : 'Koneksi ke server bermasalah, mencoba lagi.';
      this._emit();
    }
    if (this.live && this.cam.id === id) {
      const every = slow ? 1000 : this.interval;           // dijeda: cukup sesekali, tidak perlu 5 gambar per detik
      this.timer = setTimeout(() => this._tick(), Math.max(30, every - (performance.now() - t0)));
    }
  }
}

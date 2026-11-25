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

// Lapisan gambar di atas video kamera: zona trotoar, kotak kendaraan, dan penyunting zona.
// Semua koordinat dari server ternormalisasi 0..1, jadi gambar tetap pas di ukuran layar apa pun.

export const PRESET = [[0.04, 0.62], [0.96, 0.62], [0.96, 0.95], [0.04, 0.95]];

const COLORS = { parkir: '#ff6b7a', naik_trotoar: '#b39bff', diam: '#ffc857', bergerak: '#62d6ff', luar: '#c9d2f0' };
const ZONE = { auto: '98, 214, 255', manual: '255, 200, 87' };     // zona otomatis biru, zona manual kuning
const FONT = '600 12.5px "Segoe UI Variable Text", system-ui, -apple-system, Roboto, sans-serif';
const HIT_VERTEX = 28;      // radius sentuh titik sudut, piksel layar
const HIT_MID = 22;
const MIN_POINTS = 3;

const cap = (t) => (t ? t[0].toUpperCase() + t.slice(1) : '');
function pill(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

export class Overlay {
  constructor(host) {
    this.canvas = document.createElement('canvas');
    this.canvas.className = 'overlay';
    this.canvas.setAttribute('aria-hidden', 'true');
    host.appendChild(this.canvas);
    this.ctx = this.canvas.getContext('2d');
    this.zone = null;
    this.tracks = [];
    this.parkSeconds = 10;
    this.mode = 'view';
    this.kind = 'auto';
    this.scan = false;
    this.drag = -1;
    this.lastTap = { i: -1, t: 0 };
    this.onChange = () => {};
    this.raf = 0;
    this.w = this.h = 0;
    const c = this.canvas;
    c.addEventListener('pointerdown', (e) => this._down(e));
    c.addEventListener('pointermove', (e) => this._move(e));
    c.addEventListener('pointerup', (e) => this._up(e));
    c.addEventListener('pointercancel', (e) => this._up(e));
    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(c);
    this.resize();
  }

  destroy() { this.ro.disconnect(); cancelAnimationFrame(this.raf); this.canvas.remove(); }

  resize() {
    const r = this.canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    this.w = r.width;
    this.h = r.height;
    this.canvas.width = Math.max(1, Math.round(r.width * dpr));
    this.canvas.height = Math.max(1, Math.round(r.height * dpr));
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.draw();
  }

  setZone(points) { this.zone = points ? points.map((p) => [p[0], p[1]]) : null; this.draw(); }
  getZone() { return this.zone ? this.zone.map((p) => [p[0], p[1]]) : null; }
  setTracks(tracks, parkSeconds) { this.tracks = tracks || []; if (parkSeconds) this.parkSeconds = parkSeconds; this.draw(); }

  setZoneKind(kind) { if (kind !== this.kind) { this.kind = kind; this.draw(); } }

  setMode(mode) {
    this.mode = mode;
    this.canvas.classList.toggle('edit', mode === 'edit');
    this.canvas.setAttribute('aria-hidden', mode === 'edit' ? 'false' : 'true');
    if (mode === 'edit') {
      this.canvas.setAttribute('role', 'img');
      this.canvas.setAttribute('aria-label', 'Penyunting zona trotoar. Seret titik putih agar garis menutupi trotoar. Seret titik kecil di tengah sisi untuk menambah sudut. Ketuk dua kali sebuah titik untuk menghapusnya.');
    } else {
      this.canvas.removeAttribute('role');
      this.canvas.removeAttribute('aria-label');
    }
    this.draw();
  }

  setScan(on) {
    if (on === this.scan) return;
    this.scan = on;
    cancelAnimationFrame(this.raf);
    if (on) {
      const loop = () => { this.draw(); if (this.scan) this.raf = requestAnimationFrame(loop); };
      this.raf = requestAnimationFrame(loop);
    } else this.draw();
  }

  // ------------------------------------------------------------------ gambar
  px() { return this.zone.map(([x, y]) => [x * this.w, y * this.h]); }

  path(px) {
    const { ctx } = this;
    ctx.beginPath();
    px.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
    ctx.closePath();
  }

  draw() {
    const { ctx, w, h } = this;
    ctx.clearRect(0, 0, w, h);
    if (this.zone && this.zone.length >= MIN_POINTS) this._drawZone();
    if (this.scan) this._drawScan();
    if (this.mode === 'view') this._drawTracks();
    else this._drawHandles();
  }

  _drawZone() {
    const { ctx, h } = this;
    const px = this.px();
    const edit = this.mode === 'edit';
    const rgb = ZONE[edit ? 'manual' : this.kind] || ZONE.auto;
    const ys = px.map((p) => p[1]);
    const g = ctx.createLinearGradient(0, Math.min(...ys), 0, Math.max(...ys, h * 0.01));
    g.addColorStop(0, `rgba(${rgb}, ${edit ? .10 : .05})`);
    g.addColorStop(1, `rgba(${rgb}, ${edit ? .30 : .20})`);
    this.path(px);
    ctx.fillStyle = g;
    ctx.fill();
    ctx.lineJoin = 'round';
    ctx.lineWidth = edit ? 2.5 : 2;
    ctx.strokeStyle = `rgba(${rgb}, ${edit ? 1 : .9})`;
    ctx.shadowColor = `rgba(${rgb}, .8)`;
    ctx.shadowBlur = 12;
    this.path(px);
    ctx.stroke();
    ctx.shadowBlur = 0;
  }

  // Berkas cahaya menyapu gambar saat model memuat atau mencari trotoar: terlihat bahwa aplikasi sedang bekerja.
  _drawScan() {
    const { ctx, w, h } = this;
    const phase = (performance.now() / 2200) % 2;
    const f = phase < 1 ? phase : 2 - phase;
    const y = f * h;
    const band = Math.max(60, h * 0.22);
    const g = ctx.createLinearGradient(0, y - band, 0, y + band);
    g.addColorStop(0, 'rgba(98, 214, 255, 0)');
    g.addColorStop(0.5, 'rgba(98, 214, 255, .26)');
    g.addColorStop(1, 'rgba(98, 214, 255, 0)');
    ctx.fillStyle = g;
    ctx.fillRect(0, y - band, w, band * 2);
    ctx.strokeStyle = 'rgba(190, 240, 255, .85)';
    ctx.lineWidth = 1.5;
    ctx.shadowColor = 'rgba(98, 214, 255, .9)';
    ctx.shadowBlur = 10;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(w, y);
    ctx.stroke();
    ctx.shadowBlur = 0;
  }

  _drawHandles() {
    if (!this.zone) return;
    const { ctx } = this;
    const px = this.px();
    px.forEach(([x, y], i) => {                           // titik tengah sisi: seret untuk menambah sudut
      const [nx, ny] = px[(i + 1) % px.length];
      ctx.beginPath();
      ctx.arc((x + nx) / 2, (y + ny) / 2, 5, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(255, 255, 255, .28)';
      ctx.fill();
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = 'rgba(255, 255, 255, .7)';
      ctx.stroke();
    });
    px.forEach(([x, y], i) => {
      const r = i === this.drag ? 13 : 10;
      ctx.beginPath();
      ctx.arc(x, y, r + 5, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(255, 200, 87, .28)';
      ctx.fill();
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fillStyle = '#f1f4ff';
      ctx.shadowColor = 'rgba(0, 0, 0, .55)';
      ctx.shadowBlur = 8;
      ctx.fill();
      ctx.shadowBlur = 0;
    });
  }

  _drawTracks() {
    const { ctx, w, h } = this;
    ctx.font = FONT;
    ctx.textBaseline = 'middle';
    for (const t of this.tracks) {
      const [bx, by, bw, bh] = t.box;
      const x = bx * w, y = by * h, bwp = bw * w, bhp = bh * h;
      const color = COLORS[t.state] || COLORS.luar;
      const radius = Math.min(14, bwp / 4, bhp / 4);
      const alert = t.state === 'parkir' || t.state === 'naik_trotoar';
      pill(ctx, x, y, bwp, bhp, radius);                  // kotak membulat dengan cahaya tipis
      ctx.fillStyle = color + (alert ? '22' : '12');
      ctx.fill();
      ctx.lineWidth = alert ? 2.5 : 1.75;
      ctx.strokeStyle = color;
      ctx.shadowColor = color;
      ctx.shadowBlur = alert ? 14 : 6;
      ctx.stroke();
      ctx.shadowBlur = 0;

      let text = cap(t.label || 'kendaraan');
      if (t.state === 'parkir') text = `${text} parkir${t.dwell >= 1 ? ` ${Math.round(t.dwell)} dtk` : ''}`;
      else if (t.state === 'naik_trotoar') text = `${text} naik trotoar`;
      else if (t.state === 'diam') {
        text = `${text} diam ${Math.round(t.dwell)}/${Math.round(this.parkSeconds)} dtk`;
        const bar = Math.max(24, bwp - 2 * radius);        // kemajuan menuju batas parkir
        pill(ctx, x + radius, y + bhp + 6, bar, 5, 2.5);
        ctx.fillStyle = 'rgba(255, 255, 255, .22)';
        ctx.fill();
        pill(ctx, x + radius, y + bhp + 6, Math.max(5, bar * Math.min(1, t.dwell / this.parkSeconds)), 5, 2.5);
        ctx.fillStyle = color;
        ctx.fill();
      } else if (t.state === 'bergerak') text = `${text} bergerak`;

      const tw = ctx.measureText(text).width;              // label pil: titik berwarna lalu nama kendaraan
      const pw = tw + 30, ph = 24;
      const lx = Math.max(4, Math.min(x, w - pw - 4));
      const ly = y - ph - 6 < 4 ? Math.min(h - ph - 4, y + bhp + 14) : y - ph - 6;
      pill(ctx, lx, ly, pw, ph, ph / 2);
      ctx.fillStyle = 'rgba(8, 11, 32, .78)';
      ctx.fill();
      ctx.lineWidth = 1;
      ctx.strokeStyle = 'rgba(255, 255, 255, .18)';
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(lx + 13, ly + ph / 2, 4, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
      ctx.fillStyle = '#eef1ff';
      ctx.fillText(text, lx + 23, ly + ph / 2 + 0.5);
    }
  }

  // ---------------------------------------------------------------- penyunting
  _pos(e) {
    const r = this.canvas.getBoundingClientRect();
    return [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))];
  }

  _near(pos, list, radius) {
    let best = -1, bestD = radius;
    list.forEach((p, i) => {
      const d = Math.hypot((p[0] - pos[0]) * this.w, (p[1] - pos[1]) * this.h);
      if (d < bestD) { best = i; bestD = d; }
    });
    return best;
  }

  _down(e) {
    if (this.mode !== 'edit' || !this.zone) return;
    e.preventDefault();
    const pos = this._pos(e);
    let i = this._near(pos, this.zone, HIT_VERTEX);
    if (i >= 0) {
      const now = performance.now();
      if (this.lastTap.i === i && now - this.lastTap.t < 350 && this.zone.length > MIN_POINTS) {   // ketuk dua kali: hapus titik
        this.zone.splice(i, 1);
        this.lastTap = { i: -1, t: 0 };
        this.draw();
        this.onChange(this.getZone());
        return;
      }
      this.lastTap = { i, t: now };
    } else {
      const mids = this.zone.map((p, k) => { const n = this.zone[(k + 1) % this.zone.length]; return [(p[0] + n[0]) / 2, (p[1] + n[1]) / 2]; });
      const m = this._near(pos, mids, HIT_MID);
      if (m < 0 || this.zone.length >= 12) return;
      this.zone.splice(m + 1, 0, mids[m]);                 // titik baru muncul di tengah sisi, lalu langsung diseret
      i = m + 1;
    }
    this.canvas.setPointerCapture(e.pointerId);
    this.drag = i;
    this.draw();
  }

  _move(e) {
    if (this.drag < 0) return;
    this.zone[this.drag] = this._pos(e);
    this.draw();
  }

  _up() {
    if (this.drag < 0) return;
    this.drag = -1;
    this.draw();
    this.onChange(this.getZone());
  }
}

// Lapisan gambar di atas video kamera: zona trotoar, kotak kendaraan, dan penyunting zona.
// Semua koordinat dari server ternormalisasi 0..1, jadi gambar tetap pas di ukuran layar apa pun.

export const PRESET = [[0.04, 0.62], [0.96, 0.62], [0.96, 0.95], [0.04, 0.95]];

const COLORS = { parkir: '#ff5a5f', naik_trotoar: '#d94ff7', diam: '#ffb020', bergerak: '#35e0ff', luar: '#8da2c0' };
const NAMES = { parkir: 'Parkir', naik_trotoar: 'Naik trotoar', diam: 'Diam', bergerak: 'Bergerak', luar: '' };
const KERB = '#ffd31f';
const SCAN = '#35e0ff';
const FONT = '700 13px Bahnschrift, "DIN Alternate", "Roboto Condensed", system-ui, sans-serif';
const HIT_VERTEX = 26;      // radius sentuh titik sudut, piksel layar
const HIT_MID = 22;
const MIN_POINTS = 3;

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

  setMode(mode) {
    this.mode = mode;
    this.canvas.classList.toggle('edit', mode === 'edit');
    this.canvas.setAttribute('aria-hidden', mode === 'edit' ? 'false' : 'true');
    if (mode === 'edit') {
      this.canvas.setAttribute('role', 'img');
      this.canvas.setAttribute('aria-label', 'Penyunting zona trotoar. Seret titik kuning agar garis menutupi trotoar. Seret titik kecil di tengah sisi untuk menambah sudut. Ketuk dua kali sebuah titik untuk menghapusnya.');
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
    if (this.mode === 'view') this._drawTracks();
    else this._drawHandles();
  }

  _drawZone() {
    const { ctx } = this;
    const px = this.px();
    const edit = this.mode === 'edit';
    this.path(px);
    ctx.fillStyle = edit ? 'rgba(53, 224, 255, .16)' : 'rgba(53, 224, 255, .08)';
    ctx.fill();
    if (this.scan) this._drawScan(px);
    ctx.lineJoin = 'round';
    ctx.lineWidth = edit ? 2.5 : 2;
    ctx.setLineDash([9, 7]);                              // marka kerb: garis putus-putus kuning
    ctx.strokeStyle = KERB;
    ctx.globalAlpha = edit ? 1 : 0.85;
    this.path(px);
    ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.setLineDash([]);
  }

  // Garis pindai menyapu zona saat latar dipelajari: terlihat bagian mana yang sedang diamati.
  _drawScan(px) {
    const { ctx } = this;
    const ys = px.map((p) => p[1]);
    const top = Math.min(...ys), bottom = Math.max(...ys);
    const phase = (performance.now() / 1600) % 2;
    const f = phase < 1 ? phase : 2 - phase;
    const y = top + f * (bottom - top);
    ctx.save();
    this.path(px);
    ctx.clip();
    const g = ctx.createLinearGradient(0, y - 34, 0, y + 34);
    g.addColorStop(0, 'rgba(53, 224, 255, 0)');
    g.addColorStop(0.5, 'rgba(53, 224, 255, .38)');
    g.addColorStop(1, 'rgba(53, 224, 255, 0)');
    ctx.fillStyle = g;
    ctx.fillRect(0, y - 34, this.w, 68);
    ctx.strokeStyle = SCAN;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(this.w, y);
    ctx.stroke();
    ctx.restore();
  }

  _drawHandles() {
    if (!this.zone) return;
    const { ctx } = this;
    const px = this.px();
    px.forEach(([x, y], i) => {                           // titik tengah sisi: seret untuk menambah sudut
      const [nx, ny] = px[(i + 1) % px.length];
      const mx = (x + nx) / 2, my = (y + ny) / 2;
      ctx.beginPath();
      ctx.arc(mx, my, 5, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(6, 11, 20, .85)';
      ctx.fill();
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = KERB;
      ctx.stroke();
    });
    px.forEach(([x, y], i) => {
      ctx.beginPath();
      ctx.arc(x, y, i === this.drag ? 13 : 10, 0, Math.PI * 2);
      ctx.fillStyle = '#060b14';
      ctx.fill();
      ctx.lineWidth = 3;
      ctx.strokeStyle = KERB;
      ctx.stroke();
    });
  }

  _drawTracks() {
    const { ctx, w, h } = this;
    ctx.font = FONT;
    for (const t of this.tracks) {
      const [bx, by, bw, bh] = t.box;
      const x = bx * w, y = by * h, bwp = bw * w, bhp = bh * h;
      const color = COLORS[t.state] || COLORS.luar;
      const arm = Math.max(8, Math.min(bwp, bhp) * 0.28);
      ctx.lineWidth = t.state === 'luar' ? 1.5 : 2.5;
      ctx.strokeStyle = color;
      ctx.beginPath();
      [[x, y, 1, 1], [x + bwp, y, -1, 1], [x, y + bhp, 1, -1], [x + bwp, y + bhp, -1, -1]].forEach(([cx, cy, dx, dy]) => {
        ctx.moveTo(cx + dx * arm, cy); ctx.lineTo(cx, cy); ctx.lineTo(cx, cy + dy * arm);
      });
      ctx.stroke();
      if (t.state !== 'luar') {
        ctx.globalAlpha = 0.35;
        ctx.lineWidth = 1;
        ctx.strokeRect(x, y, bwp, bhp);
        ctx.globalAlpha = 1;
      }
      let label = NAMES[t.state];
      if (label && t.state === 'parkir' && t.dwell >= 1) label = `${label} ${Math.round(t.dwell)} dtk`;
      if (t.state === 'diam') {
        label = `Diam ${Math.round(t.dwell)}/${Math.round(this.parkSeconds)} dtk`;
        ctx.fillStyle = 'rgba(40, 40, 40, .9)';
        ctx.fillRect(x, y + bhp + 4, bwp, 4);
        ctx.fillStyle = color;
        ctx.fillRect(x, y + bhp + 4, bwp * Math.min(1, t.dwell / this.parkSeconds), 4);
      }
      if (!label) continue;
      if (t.id != null) label = `#${t.id} ${label}`;
      const tw = ctx.measureText(label).width;
      const lx = Math.max(0, Math.min(x, w - tw - 12));
      const ly = y - 24 < 0 ? y + bhp + 12 : y - 24;
      ctx.fillStyle = 'rgba(6, 11, 20, .88)';
      ctx.fillRect(lx, ly, tw + 12, 20);
      ctx.fillStyle = color;
      ctx.fillText(label, lx + 6, ly + 14.5);
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

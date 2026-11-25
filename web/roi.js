// Editor poligon area trotoar: ketuk untuk menambah titik, seret titik untuk memperbaiki.

export const PRESET = [[0, 0.62], [1, 0.62], [1, 0.98], [0, 0.98]];

export class RoiEditor {
  constructor(host, { onChange } = {}) {
    this.canvas = document.createElement('canvas');
    this.canvas.className = 'editor';
    this.canvas.setAttribute('role', 'img');
    this.canvas.setAttribute('aria-label', 'Gambar untuk menandai area trotoar. Ketuk untuk menambah titik.');
    host.appendChild(this.canvas);
    this.ctx = this.canvas.getContext('2d');
    this.points = [];
    this.img = null;
    this.drag = -1;
    this.onChange = onChange || (() => {});
    const c = this.canvas;
    c.addEventListener('pointerdown', (e) => this._down(e));
    c.addEventListener('pointermove', (e) => this._move(e));
    c.addEventListener('pointerup', (e) => this._up(e));
    c.addEventListener('pointercancel', (e) => this._up(e));
  }

  setImage(src) {
    this.img = src;
    this.canvas.width = src.naturalWidth || src.width;
    this.canvas.height = src.naturalHeight || src.height;
    this.draw();
  }

  setPoints(points) {
    this.points = points.map((p) => [p[0], p[1]]);
    this.draw();
    this.onChange(this.points);
  }

  undo() { this.points.pop(); this.draw(); this.onChange(this.points); }
  clear() { this.points = []; this.draw(); this.onChange(this.points); }

  _pos(e) {
    const r = this.canvas.getBoundingClientRect();
    const x = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    const y = Math.min(1, Math.max(0, (e.clientY - r.top) / r.height));
    return [x, y];
  }

  _hit(pos) {
    const r = this.canvas.getBoundingClientRect();
    let best = -1, bestD = 28;      // radius sentuh 28 px layar
    this.points.forEach((p, i) => {
      const d = Math.hypot((p[0] - pos[0]) * r.width, (p[1] - pos[1]) * r.height);
      if (d < bestD) { best = i; bestD = d; }
    });
    return best;
  }

  _down(e) {
    e.preventDefault();
    this.canvas.setPointerCapture(e.pointerId);
    const pos = this._pos(e);
    const hit = this._hit(pos);
    if (hit >= 0) this.drag = hit;
    else { this.points.push(pos); this.drag = this.points.length - 1; }
    this.draw();
    this.onChange(this.points);
  }

  _move(e) {
    if (this.drag < 0) return;
    this.points[this.drag] = this._pos(e);
    this.draw();
  }

  _up() {
    if (this.drag >= 0) { this.drag = -1; this.draw(); this.onChange(this.points); }
  }

  draw() {
    const { ctx, canvas, img, points } = this;
    const W = canvas.width, H = canvas.height;
    const rect = canvas.getBoundingClientRect();
    const k = rect.width ? W / rect.width : 1;      // 1 piksel layar = k piksel kanvas
    ctx.clearRect(0, 0, W, H);
    if (img) ctx.drawImage(img, 0, 0, W, H);
    if (!points.length) return;
    const px = points.map((p) => [p[0] * W, p[1] * H]);
    ctx.lineJoin = 'round';
    ctx.beginPath();
    px.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
    if (px.length >= 3) {
      ctx.closePath();
      ctx.fillStyle = 'rgba(56, 225, 255, 0.20)';
      ctx.fill();
    }
    ctx.setLineDash([7 * k, 5 * k]);
    ctx.lineWidth = 2.5 * k;
    ctx.strokeStyle = '#38e1ff';
    ctx.stroke();
    ctx.setLineDash([]);
    px.forEach(([x, y], i) => {
      ctx.beginPath();
      ctx.arc(x, y, (i === this.drag ? 13 : 10) * k, 0, Math.PI * 2);
      ctx.fillStyle = '#0a1424';
      ctx.fill();
      ctx.lineWidth = 3 * k;
      ctx.strokeStyle = '#ffd31f';
      ctx.stroke();
    });
  }
}

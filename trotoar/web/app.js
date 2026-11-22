"use strict";
/* Sidewalk Sentinel: antarmuka dasbor. Tanpa pustaka luar. Semua koordinat deteksi dari server
   berupa pecahan 0-1 terhadap gambar, jadi overlay ikut ukuran layar berapa pun. */

const $ = (id) => document.getElementById(id);
const el = {
  stage: $("stage"), video: $("video"), stream: $("stream"), canvas: $("overlay"), empty: $("empty"),
  liveChip: $("live-chip"), liveText: $("live-text"), swChip: $("sw-chip"), btnAuto: $("btn-auto"),
  btnFull: $("btn-full"), banner: $("banner"), bannerText: $("banner-text"),
  drawPanel: $("draw-panel"), drawUndo: $("draw-undo"), drawClose: $("draw-close"),
  drawCancel: $("draw-cancel"), drawApply: $("draw-apply"),
  tabs: document.querySelectorAll(".seg button"), seg: document.querySelector(".seg"),
  srcCamera: $("src-camera"), srcStream: $("src-stream"), srcVideo: $("src-video"),
  camSelect: $("cam-select"), camOn: $("cam-on"), streamUrl: $("stream-url"),
  videoFile: $("video-file"), videoPick: $("video-pick"), videoName: $("video-name"),
  btnDraw: $("btn-draw"), btnRun: $("btn-run"), notice: $("notice"),
  modelChip: $("model-chip"), modelText: $("model-text"), metrics: $("metrics"),
  nVeh: $("n-veh"), nSide: $("n-side"), nPark: $("n-park"), warn: document.querySelector(".summary .warn"),
  evList: $("ev-list"), evEmpty: $("ev-empty"), evCount: $("ev-count"), evDwell: $("ev-dwell"), csv: $("csv-link"),
  setDwell: $("set-dwell"), oDwell: $("o-dwell"), setTau: $("set-tau"), oTau: $("o-tau"),
  setComp: $("set-comp"), setSound: $("set-sound"), btnRefresh: $("btn-refresh"),
  lightbox: $("lightbox"), lightboxImg: $("lightbox-img"),
};
const ctx = el.canvas.getContext("2d");
const FONT = getComputedStyle(document.body).fontFamily;

const STATUS = {
  DI_JALAN:                  { text: "di jalan",                 color: "#2f9e5f" },
  DI_TROTOAR_ADA_PENGENDARA: { text: "di trotoar, ada pengendara", color: "#e0a800" },
  DI_TROTOAR_BERGERAK:       { text: "di trotoar, bergerak",     color: "#e0a800" },
  KANDIDAT_PARKIR:           { text: "kandidat parkir",          color: "#ee8a1c" },
  TERINDIKASI_PARKIR:        { text: "parkir di trotoar",        color: "#d3302a" },
  PARKIR_TERKONFIRMASI:      { text: "parkir terkonfirmasi",     color: "#d3302a" },
};

const EMPTY_COPY = {
  camera: ["Nyalakan kamera", "Pilih kamera lalu tekan <b>Nyalakan kamera</b>. Browser meminta izin satu kali. Setelah gambar muncul, tekan <b>Mulai pantau</b>."],
  stream: ["Sambungkan kamera IP", "Isi indeks webcam (<b>0</b>) atau alamat stream kamera IP/CCTV, lalu tekan <b>Mulai pantau</b>. Gambar muncul setelah pemantauan dimulai."],
  video:  ["Buka berkas video", "Pilih video dari komputer. Video diputar di sini dan dianalisis sambil diputar."],
};

const S = {
  kind: "camera",                    // camera | stream | video
  running: false, token: 0, t0: 0, startedAt: 0,
  model: "memuat", modelNotified: false,
  payload: null, lastSeq: 0,
  alertCount: 0, seenAlerts: new Set(),
  polys: [],                         // trotoar manual yang tersimpan di server
  drawing: false, draft: [], shapes: [],
  camReady: false, videoReady: false, videoUrl: null,
  sound: true, settingsLoaded: false,
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fmt1 = (n) => Number(n).toLocaleString("id-ID", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const cap = (s) => s ? s[0].toUpperCase() + s.slice(1) : s;

function notify(msg, isError = false) {
  el.notice.textContent = msg || "";
  el.notice.classList.toggle("error", !!isError);
}

async function api(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  let data = {};
  try { data = await res.json(); } catch (_) { /* balasan kosong */ }
  if (!res.ok) throw new Error(data.error || `Server membalas ${res.status}`);
  return data;
}

/* ------------------------------------------------------------------ keadaan tampilan */
function hasPicture() {
  if (S.running) return true;
  return (S.kind === "camera" && S.camReady) || (S.kind === "video" && S.videoReady);
}

function refreshUI() {
  const usesVideo = S.kind !== "stream";
  el.video.hidden = !usesVideo;
  el.stream.hidden = !(S.kind === "stream" && S.running);
  el.stage.dataset.empty = String(!hasPicture());
  const [title, body] = EMPTY_COPY[S.kind];
  el.empty.querySelector("h2").textContent = title;
  el.empty.querySelector("p").innerHTML = body;

  el.tabs.forEach((b) => b.setAttribute("aria-selected", String(b.dataset.kind === S.kind)));
  el.seg.classList.toggle("locked", S.running);
  el.srcCamera.hidden = S.kind !== "camera";
  el.srcStream.hidden = S.kind !== "stream";
  el.srcVideo.hidden = S.kind !== "video";
  [el.camSelect, el.camOn, el.streamUrl, el.videoPick].forEach((n) => { n.disabled = S.running; });

  const ready = S.model === "siap" && (S.running || (S.kind === "camera" && S.camReady) ||
    (S.kind === "stream" && el.streamUrl.value.trim() !== "") || (S.kind === "video" && S.videoReady));
  el.btnRun.disabled = !ready;
  el.btnRun.textContent = S.running ? "Berhenti" : "Mulai pantau";
  el.btnRun.classList.toggle("primary", !S.running);
  el.btnRun.classList.toggle("stop", S.running);

  el.btnDraw.classList.toggle("on", S.drawing);
  el.btnDraw.setAttribute("aria-pressed", String(S.drawing));
  el.btnDraw.textContent = S.drawing ? "Selesai menggambar" : "Gambar trotoar";
  el.stage.classList.toggle("drawing", S.drawing);
  el.drawPanel.hidden = !S.drawing;
  syncBanner();
  el.swChip.hidden = !S.polys.length;

  el.liveChip.dataset.state = S.running ? "live" : "idle";
  if (!S.running) el.liveText.textContent = "Belum dimulai";
}

function detachVideo() {
  const v = el.video;
  v.pause();
  if (v.srcObject) v.srcObject.getTracks().forEach((t) => t.stop());
  v.srcObject = null; v.removeAttribute("src"); v.load();
  S.camReady = false; S.videoReady = false;
}

function selectKind(kind) {
  if (S.running || kind === S.kind) return;
  detachVideo();
  S.kind = kind;
  if (kind === "video" && S.videoUrl) attachVideo();      // berkas yang dipilih sebelumnya dipasang lagi
  notify("");
  refreshUI();
}

/* ------------------------------------------------------------------ kamera browser */
function cameraError(e) {
  switch (e && e.name) {
    case "NotAllowedError": return "Izin kamera ditolak. Izinkan kamera untuk alamat ini di pengaturan browser, lalu tekan Nyalakan kamera lagi.";
    case "NotFoundError": return "Tidak ada kamera yang terdeteksi di perangkat ini.";
    case "NotReadableError": return "Kamera sedang dipakai aplikasi lain. Tutup aplikasi itu, lalu coba lagi.";
    case "OverconstrainedError": return "Kamera yang dipilih tidak tersedia. Pilih kamera lain.";
    default: return `Kamera gagal dinyalakan: ${(e && e.message) || e}`;
  }
}

async function listCameras(currentId) {
  try {
    const devs = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "videoinput");
    el.camSelect.innerHTML = "";
    devs.forEach((d, i) => {
      const o = document.createElement("option");
      o.value = d.deviceId; o.textContent = d.label || `Kamera ${i + 1}`;
      el.camSelect.appendChild(o);
    });
    if (currentId) el.camSelect.value = currentId;
  } catch (_) { /* daftar kamera tidak wajib */ }
}

function stopCamera() {
  const s = el.video.srcObject;
  if (s) s.getTracks().forEach((t) => t.stop());
  el.video.srcObject = null;
  S.camReady = false;
}

async function startCamera() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    notify("Browser tidak mengizinkan kamera di alamat ini. Buka lewat http://localhost:8765 di laptop, atau pakai HTTPS (lihat README).", true);
    return;
  }
  stopCamera();
  notify("");
  const id = el.camSelect.value;
  const video = { width: { ideal: 1280 }, height: { ideal: 720 } };
  if (id) video.deviceId = { exact: id };
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ video, audio: false });
    el.video.srcObject = stream;
    await el.video.play();
    S.camReady = true;
    await listCameras(stream.getVideoTracks()[0].getSettings().deviceId);
  } catch (e) {
    notify(cameraError(e), true);
  }
  refreshUI();
}

/* ------------------------------------------------------------------ berkas video */
function attachVideo() {
  el.video.srcObject = null;
  el.video.loop = false;
  el.video.src = S.videoUrl;
  S.videoReady = false;
  el.video.onloadeddata = () => { S.videoReady = true; refreshUI(); };
  el.video.onerror = () => { S.videoReady = false; notify("Video tidak bisa diputar di browser. Coba berkas .mp4 (H.264).", true); refreshUI(); };
}

function openVideoFile(file) {
  if (!file) return;
  if (S.videoUrl) URL.revokeObjectURL(S.videoUrl);
  S.videoUrl = URL.createObjectURL(file);
  el.videoName.textContent = file.name;
  el.videoName.title = file.name;
  attachVideo();
  notify("");
  refreshUI();
}

/* ------------------------------------------------------------------ mulai & berhenti */
let audio = null;
function unlockAudio() {
  try {
    audio = audio || new (window.AudioContext || window.webkitAudioContext)();
    if (audio.state === "suspended") audio.resume();
  } catch (_) { /* tanpa bunyi */ }
}
function beep() {
  if (!audio || !S.sound) return;
  const now = audio.currentTime;
  [0, 0.3].forEach((d) => {
    const o = audio.createOscillator(), g = audio.createGain();
    o.type = "square"; o.frequency.value = 880;
    g.gain.setValueAtTime(0.0001, now + d);
    g.gain.exponentialRampToValueAtTime(0.16, now + d + 0.01);
    g.gain.exponentialRampToValueAtTime(0.0001, now + d + 0.22);
    o.connect(g).connect(audio.destination);
    o.start(now + d); o.stop(now + d + 0.25);
  });
}

async function start() {
  notify("");
  unlockAudio();
  try {
    if (S.kind === "stream") {
      const src = el.streamUrl.value.trim();
      if (!src) { notify("Isi dulu indeks webcam (0) atau alamat stream.", true); return; }
      el.btnRun.disabled = true;
      await api("/api/start", { kind: "server", source: src });
      S.running = true; S.lastSeq = 0; S.startedAt = Date.now();
      el.stream.src = "/stream.mjpg?" + Date.now();
      pollResults(++S.token);
    } else {
      await api("/api/start", { kind: "browser" });
      S.running = true; S.startedAt = Date.now(); S.t0 = performance.now();
      if (S.kind === "video") {
        if (el.video.ended) el.video.currentTime = 0;
        await el.video.play();
      }
      analyseLoop(++S.token);
    }
  } catch (e) {
    S.running = false;
    notify(e.message, true);
  }
  refreshUI();
}

async function stop(msg) {
  const wasStream = S.kind === "stream";
  S.running = false; S.token++;
  if (S.kind === "video") el.video.pause();
  try { await api("/api/stop"); } catch (_) { /* server mungkin sudah berhenti */ }
  if (wasStream) el.stream.removeAttribute("src");
  S.payload = null;
  setSummary(null); showAlertState([]);
  el.metrics.hidden = true;
  notify(msg || "");
  refreshUI();
}

/* ------------------------------------------------------------------ analisis (kamera/video di browser) */
async function analyseLoop(token) {
  const off = document.createElement("canvas");
  const octx = off.getContext("2d");
  while (S.running && token === S.token) {
    const v = el.video;
    if (S.kind === "video" && v.ended) { await stop("Video selesai."); return; }
    if (v.readyState < 2 || v.seeking || (S.kind === "video" && v.paused)) { await sleep(100); continue; }
    const scale = Math.min(1, 800 / Math.max(v.videoWidth, v.videoHeight));
    off.width = Math.round(v.videoWidth * scale); off.height = Math.round(v.videoHeight * scale);
    octx.drawImage(v, 0, 0, off.width, off.height);
    const blob = await new Promise((r) => off.toBlob(r, "image/jpeg", 0.72));
    const t = S.kind === "video" ? v.currentTime : (performance.now() - S.t0) / 1000;
    const t1 = performance.now();
    try {
      const res = await fetch("/api/frame?t=" + t.toFixed(3), { method: "POST", headers: { "Content-Type": "image/jpeg" }, body: blob });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `Server membalas ${res.status}`);
      if (token !== S.token) return;
      onPayload(data);
    } catch (e) {
      if (token === S.token) { await stop(null); notify(e.message, true); }
      return;
    }
    const spent = performance.now() - t1;
    if (spent < 80) await sleep(80 - spent);
  }
}

/* ------------------------------------------------------------------ hasil dari server (kamera di sisi server) */
async function pollResults(token) {
  while (S.running && token === S.token) {
    try {
      const r = await fetch("/api/result?since=" + S.lastSeq, { cache: "no-store" });
      if (r.status === 200) { const p = await r.json(); S.lastSeq = p.seq; onPayload(p); }
    } catch (_) { /* coba lagi */ }
    await sleep(120);
  }
}

/* ------------------------------------------------------------------ hasil deteksi -> ringkasan, banner, daftar */
function setSummary(p) {
  const s = p ? p.summary : { kendaraan: 0, di_trotoar: 0, parkir: 0 };
  el.nVeh.textContent = s.kendaraan; el.nSide.textContent = s.di_trotoar; el.nPark.textContent = s.parkir;
  el.warn.classList.toggle("hot", s.parkir > 0);
}

function syncBanner() {
  el.banner.hidden = el.banner.dataset.on !== "1" || S.drawing;     // saat menggambar, panel gambar yang tampil
}

function showAlertState(confirmed) {
  el.stage.classList.toggle("alerting", confirmed.length > 0);
  el.banner.dataset.on = confirmed.length ? "1" : "0";
  syncBanner();
  if (!confirmed.length) return;
  const v = confirmed[0];
  el.bannerText.textContent = confirmed.length === 1
    ? `${cap(v.kelas)}${v.id != null ? " #" + v.id : ""} parkir di trotoar · diam ${Math.round(v.dwell)} dtk`
    : `${confirmed.length} kendaraan parkir di trotoar`;
}

function onPayload(p) {
  S.payload = p;
  setSummary(p);
  showAlertState(p.vehicles.filter((v) => v.status === "PARKIR_TERKONFIRMASI"));
  el.metrics.hidden = false;
  el.metrics.textContent = `${fmt1(p.fps)} fps · ${p.latency_ms} ms`;
  el.evDwell.textContent = Math.round(p.min_dwell);
  if (p.alerts !== S.alertCount) loadAlerts(true);
}

async function loadAlerts(withSound) {
  let data;
  try { data = await (await fetch("/api/alerts", { cache: "no-store" })).json(); } catch (_) { return; }
  const items = data.alerts || [];
  const fresh = items.filter((a) => !S.seenAlerts.has(a.id));
  el.evList.innerHTML = "";
  items.forEach((a) => {
    const li = document.createElement("li");
    const b = document.createElement("button");
    b.type = "button"; b.className = "ev" + (fresh.includes(a) && withSound ? " fresh" : "");
    const img = document.createElement("img");
    img.src = a.foto; img.alt = ""; img.loading = "lazy";
    const box = document.createElement("span");
    const t = document.createElement("span"); t.className = "t";
    t.textContent = `${cap(a.kelas)} #${a.track_id} parkir di trotoar`;
    const m = document.createElement("span"); m.className = "m";
    const when = new Date(a.waktu);
    m.textContent = `${when.toLocaleTimeString("en-GB")} · diam ${Math.round(a.dwell_s)} dtk`;
    box.append(t, m); b.append(img, box); li.append(b); el.evList.append(li);
    b.addEventListener("click", () => { el.lightboxImg.src = a.foto; el.lightbox.showModal(); });
  });
  items.forEach((a) => S.seenAlerts.add(a.id));
  S.alertCount = items.length;
  el.evCount.textContent = items.length;
  el.evEmpty.hidden = items.length > 0;
  el.csv.hidden = items.length === 0;
  if (fresh.length && withSound) beep();
}

/* ------------------------------------------------------------------ overlay di atas video */
function mediaRect() {
  const cw = el.stage.clientWidth, ch = el.stage.clientHeight;
  let vw = 0, vh = 0;
  if (S.kind === "stream") { vw = el.stream.naturalWidth; vh = el.stream.naturalHeight; }
  else { vw = el.video.videoWidth; vh = el.video.videoHeight; }
  if ((!vw || !vh) && S.payload) { vw = S.payload.w; vh = S.payload.h; }
  if (!vw || !vh) return null;
  const s = Math.min(cw / vw, ch / vh);
  return { x: (cw - vw * s) / 2, y: (ch - vh * s) / 2, w: vw * s, h: vh * s };
}

function sizeCanvas() {
  const dpr = window.devicePixelRatio || 1;
  const w = el.stage.clientWidth, h = el.stage.clientHeight;
  el.canvas.width = Math.round(w * dpr); el.canvas.height = Math.round(h * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
new ResizeObserver(sizeCanvas).observe(el.stage);

function tracePolys(polys, r, closePath = true) {
  ctx.beginPath();
  polys.forEach((poly) => {
    poly.forEach(([x, y], i) => (i ? ctx.lineTo(r.x + x * r.w, r.y + y * r.h) : ctx.moveTo(r.x + x * r.w, r.y + y * r.h)));
    if (closePath) ctx.closePath();
  });
}

function roundRect(x, y, w, h, rad) {
  ctx.beginPath();
  ctx.moveTo(x + rad, y);
  ctx.arcTo(x + w, y, x + w, y + h, rad); ctx.arcTo(x + w, y + h, x, y + h, rad);
  ctx.arcTo(x, y + h, x, y, rad); ctx.arcTo(x, y, x + w, y, rad); ctx.closePath();
}

function signBadge(cx, cy, rad) {
  ctx.save();
  ctx.fillStyle = "#d3302a"; ctx.beginPath(); ctx.arc(cx, cy, rad, 0, 7); ctx.fill();
  ctx.fillStyle = "#0a58b5"; ctx.beginPath(); ctx.arc(cx, cy, rad * 0.78, 0, 7); ctx.fill();
  ctx.fillStyle = "#fff"; ctx.font = `700 ${Math.round(rad * 1.2)}px ${FONT}`;
  ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText("P", cx, cy + rad * 0.06);
  ctx.strokeStyle = "#d3302a"; ctx.lineWidth = rad * 0.26; ctx.beginPath();
  ctx.moveTo(cx - rad * 0.72, cy - rad * 0.72); ctx.lineTo(cx + rad * 0.72, cy + rad * 0.72); ctx.stroke();
  ctx.restore();
}

function drawVehicle(v, r) {
  const st = STATUS[v.status] || STATUS.DI_JALAN;
  const confirmed = v.status === "PARKIR_TERKONFIRMASI";
  if (v.poly) {
    tracePolys([v.poly], r);
    ctx.fillStyle = st.color + (confirmed ? "66" : "40"); ctx.fill();
    ctx.lineWidth = confirmed ? 3 : 2; ctx.strokeStyle = st.color; ctx.lineJoin = "round"; ctx.stroke();
  } else {
    ctx.lineWidth = 2; ctx.strokeStyle = st.color;
    ctx.strokeRect(r.x + v.box[0] * r.w, r.y + v.box[1] * r.h, (v.box[2] - v.box[0]) * r.w, (v.box[3] - v.box[1]) * r.h);
  }
  let label = `${v.id != null ? "#" + v.id + " " : ""}${v.kelas}`;
  if (v.status === "KANDIDAT_PARKIR") label += ` · ${Math.round(v.dwell)}/${Math.round(S.payload.min_dwell)} dtk`;
  else if (confirmed) label += ` · parkir ${Math.round(v.dwell)} dtk`;
  else label += ` · ${st.text}`;
  ctx.font = `600 13px ${FONT}`;
  const pad = 7, badge = confirmed ? 20 : 0, th = 22;
  const tw = ctx.measureText(label).width + pad * 2 + badge;
  let x = r.x + v.box[0] * r.w, y = r.y + v.box[1] * r.h - th - 3;
  x = Math.max(r.x + 2, Math.min(x, r.x + r.w - tw - 2));
  if (y < r.y + 2) y = r.y + v.box[3] * r.h + 3;
  roundRect(x, y, tw, th, 4); ctx.fillStyle = st.color; ctx.fill();
  if (confirmed) signBadge(x + 13, y + th / 2, 9);
  ctx.fillStyle = "#fff"; ctx.textAlign = "left"; ctx.textBaseline = "middle";
  ctx.fillText(label, x + pad + badge, y + th / 2 + 0.5);
}

function drawShapes(shapes, r, solid) {
  shapes.forEach((poly) => {
    tracePolys([poly], r);
    ctx.fillStyle = "rgba(20,130,240,.22)"; ctx.fill();
    ctx.lineWidth = 2; ctx.strokeStyle = "#5aa9ff"; ctx.setLineDash(solid ? [] : [7, 5]); ctx.stroke(); ctx.setLineDash([]);
  });
}

function drawDraft(r) {
  const d = S.draft;
  drawShapes(S.shapes, r, true);
  if (!d.length) return;
  ctx.lineWidth = 2; ctx.strokeStyle = "#f5c400";
  tracePolys([d], r, false); ctx.stroke();
  if (d.length >= 3) {
    ctx.setLineDash([5, 5]); ctx.globalAlpha = 0.7;
    ctx.beginPath(); ctx.moveTo(r.x + d[d.length - 1][0] * r.w, r.y + d[d.length - 1][1] * r.h);
    ctx.lineTo(r.x + d[0][0] * r.w, r.y + d[0][1] * r.h); ctx.stroke();
    ctx.setLineDash([]); ctx.globalAlpha = 1;
  }
  d.forEach(([x, y], i) => {
    ctx.beginPath(); ctx.arc(r.x + x * r.w, r.y + y * r.h, i === 0 ? 7 : 4.5, 0, 7);
    ctx.fillStyle = i === 0 ? "#f5c400" : "#fff"; ctx.fill();
    ctx.lineWidth = 1.5; ctx.strokeStyle = "#1d242a"; ctx.stroke();
  });
}

function frame() {
  ctx.clearRect(0, 0, el.stage.clientWidth, el.stage.clientHeight);
  const r = mediaRect();
  if (r) {
    const p = S.payload;
    if (S.drawing) {
      if (S.polys.length) drawShapes(S.polys, r, false);
      drawDraft(r);
    } else if (p) {
      if (p.sidewalk.length) {
        tracePolys(p.sidewalk, r);
        ctx.fillStyle = "rgba(20,130,240,.30)"; ctx.fill("evenodd");
        ctx.lineWidth = 1.5; ctx.strokeStyle = "rgba(110,180,255,.9)"; ctx.stroke();
      }
      // kendaraan yang bukan "di jalan" digambar belakangan agar tidak tertutup
      const order = (v) => (v.status === "DI_JALAN" ? 0 : v.status === "PARKIR_TERKONFIRMASI" ? 2 : 1);
      [...p.vehicles].sort((a, b) => order(a) - order(b)).forEach((v) => drawVehicle(v, r));
    } else if (S.polys.length) {
      drawShapes(S.polys, r, false);
    }
  }
  if (S.running) {
    const s = Math.floor((Date.now() - S.startedAt) / 1000);
    el.liveText.textContent = `Memantau · ${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  }
  requestAnimationFrame(frame);
}

/* ------------------------------------------------------------------ menggambar area trotoar */
function toggleDraw() {
  if (S.drawing) { cancelDraw(); return; }
  if (!mediaRect() || !hasPicture()) {
    notify("Nyalakan kamera, buka video, atau mulai pemantauan dulu supaya ada gambar yang bisa digambari.", true);
    return;
  }
  notify(""); S.drawing = true; S.draft = []; S.shapes = []; refreshUI();
}
function cancelDraw() { S.drawing = false; S.draft = []; S.shapes = []; refreshUI(); }
function closeShape() { if (S.draft.length >= 3) { S.shapes.push(S.draft); S.draft = []; } }
function undoPoint() {
  if (S.draft.length) S.draft.pop();
  else if (S.shapes.length) S.draft = S.shapes.pop();
}
async function applyDraw() {
  closeShape();
  if (!S.shapes.length) { notify("Gambar minimal satu bentuk dengan 3 titik atau lebih.", true); return; }
  try {
    const st = await api("/api/sidewalk", { polygons: S.shapes });
    S.polys = st.polygon || [];
    notify("Area trotoar manual dipakai.");
    cancelDraw();
  } catch (e) { notify(e.message, true); }
}
async function backToAuto() {
  try { const st = await api("/api/sidewalk", { polygons: null }); S.polys = st.polygon || []; notify("Kembali ke deteksi trotoar otomatis."); refreshUI(); }
  catch (e) { notify(e.message, true); }
}

el.canvas.addEventListener("click", (e) => {
  if (!S.drawing) return;
  const r = mediaRect(), b = el.canvas.getBoundingClientRect();
  if (!r) return;
  const nx = (e.clientX - b.left - r.x) / r.w, ny = (e.clientY - b.top - r.y) / r.h;
  if (nx < 0 || nx > 1 || ny < 0 || ny > 1) return;
  if (S.draft.length >= 3) {
    const f = S.draft[0];
    if (Math.hypot((f[0] - nx) * r.w, (f[1] - ny) * r.h) < 12) { closeShape(); return; }
  }
  S.draft.push([nx, ny]);
});

document.addEventListener("keydown", (e) => {
  if (!S.drawing || /^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName)) return;
  if (e.key === "Enter") { closeShape(); e.preventDefault(); }
  else if (e.key === "Backspace") { undoPoint(); e.preventDefault(); }
  else if (e.key === "Escape") { cancelDraw(); }
});

/* ------------------------------------------------------------------ status server & pengaturan */
function applyStatus(st) {
  S.model = st.model;
  el.modelChip.dataset.state = st.model;
  el.modelText.textContent = st.model === "siap" ? "Model siap" : st.model === "memuat" ? "Memuat model…" : "Model gagal dimuat";
  el.modelChip.title = st.model === "galat" ? (st.model_error || "") :
    st.model === "memuat" ? "Pertama kali, bobot YOLO bisa diunduh dulu. Tunggu beberapa saat." : "";
  if (st.model === "galat" && !S.modelNotified) { S.modelNotified = true; notify(`Model deteksi gagal dimuat: ${st.model_error}`, true); }

  S.polys = st.polygon || [];
  if (!S.settingsLoaded) {
    S.settingsLoaded = true;
    el.setDwell.value = st.settings.min_dwell_s; el.oDwell.textContent = `${Math.round(st.settings.min_dwell_s)} dtk`;
    el.setTau.value = st.settings.tau; el.oTau.textContent = fmt2(st.settings.tau);
    el.setComp.checked = st.settings.camera_comp; el.evDwell.textContent = Math.round(st.settings.min_dwell_s);
  }
  if (S.running && S.kind === "stream" && st.active !== "server") stop(st.message || "Sumber berhenti mengirim gambar.");
  if (st.alerts !== S.alertCount && !S.running) loadAlerts(false);
  refreshUI();
}
const fmt2 = (n) => Number(n).toLocaleString("id-ID", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

async function pollStatus() {
  try { applyStatus(await (await fetch("/api/status", { cache: "no-store" })).json()); } catch (_) { /* server mati sementara */ }
  setTimeout(pollStatus, 1500);
}

let settingsTimer = null;
function sendSettings() {
  clearTimeout(settingsTimer);
  settingsTimer = setTimeout(async () => {
    try {
      await api("/api/settings", { min_dwell_s: +el.setDwell.value, tau: +el.setTau.value, camera_comp: el.setComp.checked });
      el.evDwell.textContent = el.setDwell.value;
    } catch (e) { notify(e.message, true); }
  }, 250);
}

/* ------------------------------------------------------------------ pasang pendengar */
el.tabs.forEach((b) => b.addEventListener("click", () => selectKind(b.dataset.kind)));
el.camOn.addEventListener("click", startCamera);
el.streamUrl.addEventListener("input", refreshUI);
el.streamUrl.addEventListener("keydown", (e) => { if (e.key === "Enter" && !el.btnRun.disabled) start(); });
el.videoPick.addEventListener("click", () => el.videoFile.click());
el.videoFile.addEventListener("change", () => openVideoFile(el.videoFile.files[0]));
el.btnRun.addEventListener("click", () => (S.running ? stop() : start()));
el.btnDraw.addEventListener("click", toggleDraw);
el.drawUndo.addEventListener("click", undoPoint);
el.drawClose.addEventListener("click", closeShape);
el.drawCancel.addEventListener("click", cancelDraw);
el.drawApply.addEventListener("click", applyDraw);
el.btnAuto.addEventListener("click", backToAuto);
el.btnFull.addEventListener("click", () => (document.fullscreenElement ? document.exitFullscreen() : el.stage.requestFullscreen()));
el.setDwell.addEventListener("input", () => { el.oDwell.textContent = `${el.setDwell.value} dtk`; sendSettings(); });
el.setTau.addEventListener("input", () => { el.oTau.textContent = fmt2(el.setTau.value); sendSettings(); });
el.setComp.addEventListener("change", sendSettings);
el.setSound.addEventListener("change", () => {
  S.sound = el.setSound.checked;
  try { localStorage.setItem("ss-sound", S.sound ? "1" : "0"); } catch (_) { /* penyimpanan tidak tersedia */ }
});
el.btnRefresh.addEventListener("click", async () => { try { await api("/api/refresh"); notify("Trotoar akan dihitung ulang pada gambar berikutnya."); } catch (e) { notify(e.message, true); } });
el.lightbox.addEventListener("click", (e) => { if (e.target === el.lightbox) el.lightbox.close(); });

(async function init() {
  try { S.sound = localStorage.getItem("ss-sound") !== "0"; } catch (_) { /* biarkan default */ }
  el.setSound.checked = S.sound;
  sizeCanvas();
  refreshUI();
  requestAnimationFrame(frame);
  // Halaman dimuat ulang saat server masih memantau: sambungkan lagi (sumber server) atau bersihkan (sumber browser).
  try {
    const st = await (await fetch("/api/status", { cache: "no-store" })).json();
    if (st.active === "server") {
      S.kind = "stream"; S.running = true; S.startedAt = Date.now();
      el.streamUrl.value = st.source || ""; el.stream.src = "/stream.mjpg?" + Date.now(); pollResults(++S.token);
    } else if (st.active === "browser") { await api("/api/stop"); }
  } catch (_) { /* tampilan awal tetap dipakai */ }
  loadAlerts(false);
  pollStatus();
})();

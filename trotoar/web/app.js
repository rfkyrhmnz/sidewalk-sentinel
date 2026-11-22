"use strict";
/* Sidewalk Sentinel: antarmuka dasbor. Tanpa pustaka luar. Semua koordinat deteksi dari server
   berupa pecahan 0-1 terhadap gambar, jadi overlay ikut ukuran layar berapa pun. */

const $ = (id) => document.getElementById(id);
const el = {
  stage: $("stage"), view: $("view"), video: $("video"), stream: $("stream"), canvas: $("overlay"), empty: $("empty"),
  liveChip: $("live-chip"), liveText: $("live-text"), swChip: $("sw-chip"), btnAuto: $("btn-auto"),
  btnFull: $("btn-full"), banner: $("banner"), bannerText: $("banner-text"),
  drawPanel: $("draw-panel"), drawUndo: $("draw-undo"), drawClose: $("draw-close"),
  drawCancel: $("draw-cancel"), drawApply: $("draw-apply"),
  dock: $("dock"), tabs: document.querySelectorAll(".seg button"), seg: document.querySelector(".seg"),
  srcCamera: $("src-camera"), srcStream: $("src-stream"), srcVideo: $("src-video"),
  camSelect: $("cam-select"), camOn: $("cam-on"), streamUrl: $("stream-url"),
  videoFile: $("video-file"), videoPick: $("video-pick"), videoName: $("video-name"),
  btnDraw: $("btn-draw"), btnHeat: $("btn-heat"), btnTrail: $("btn-trail"), btnShot: $("btn-shot"),
  btnRun: $("btn-run"), notice: $("notice"),
  modelChip: $("model-chip"), modelText: $("model-text"), metrics: $("metrics"),
  perfFps: $("perf-fps"), perfLat: $("perf-lat"), sparkFps: $("spark-fps"),
  btnHelp: $("btn-help"), btnSettings: $("btn-settings"), help: $("help"),
  uptime: $("uptime"), nVeh: $("n-veh"), nSide: $("n-side"), nPark: $("n-park"), warn: document.querySelector(".metric.warn"),
  sparkVeh: $("spark-veh"), sparkSide: $("spark-side"), sparkPark: $("spark-park"), mix: $("mix"),
  evList: $("ev-list"), evEmpty: $("ev-empty"), evCount: $("ev-count"), evDwell: $("ev-dwell"),
  csv: $("csv-link"), csvReview: $("csv-review"), precision: $("precision"),
  scrim: $("scrim"), drawer: $("drawer"), drawerClose: $("drawer-close"),
  setDwell: $("set-dwell"), oDwell: $("o-dwell"), setTau: $("set-tau"), oTau: $("o-tau"),
  setAlert: $("set-alert"), setNotify: $("set-notify"), setComp: $("set-comp"),
  btnRefresh: $("btn-refresh"), outDir: $("out-dir"),
  lightbox: $("lightbox"), lightboxImg: $("lightbox-img"),
};
const ctx = el.canvas.getContext("2d");
const FONT = getComputedStyle(document.body).fontFamily;
const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const C = { mint: "#3df2c0", amber: "#ffc24b", orange: "#ff9a3c", plasma: "#ff3d6e", ion: "#7b8cff" };
const STATUS = {
  DI_JALAN:                  { text: "di jalan",                   color: C.mint },
  DI_TROTOAR_ADA_PENGENDARA: { text: "di trotoar, ada pengendara", color: C.amber },
  DI_TROTOAR_BERGERAK:       { text: "di trotoar, bergerak",       color: C.amber },
  KANDIDAT_PARKIR:           { text: "kandidat parkir",            color: C.orange },
  TERINDIKASI_PARKIR:        { text: "parkir di trotoar",          color: C.plasma },
  PARKIR_TERKONFIRMASI:      { text: "parkir terkonfirmasi",       color: C.plasma },
};

const EMPTY_COPY = {
  camera: ["Nyalakan kamera", "Pilih kamera lalu tekan <b>Nyalakan kamera</b>. Browser meminta izin satu kali. Setelah gambar muncul, tekan <b>Mulai pantau</b>."],
  stream: ["Sambungkan kamera IP", "Isi indeks webcam (<b>0</b>) atau alamat stream kamera IP/CCTV, lalu tekan <b>Mulai pantau</b>. Gambar muncul setelah pemantauan dimulai."],
  video:  ["Buka berkas video", "Pilih video dari komputer. Video diputar di sini dan dianalisis sambil diputar."],
};

const S = {
  kind: "camera",                    // camera | stream | video
  running: false, alerting: false, token: 0, t0: 0, startedAt: 0,
  model: "memuat", modelNotified: false,
  payload: null, lastSeq: 0,
  items: [], alertCount: 0, seenAlerts: new Set(),
  polys: [],                         // trotoar manual yang tersimpan di server
  drawing: false, draft: [], shapes: [],
  camReady: false, videoReady: false, videoUrl: null, settingsLoaded: false,
  heat: false, trail: true,
  vis: new Map(), trails: new Map(), frameNo: 0, lastFrameT: 0,
  hist: { veh: [], side: [], park: [], fps: [] }, histT: 0, mixKey: "",
  wake: null, idleTimer: 0, lastState: "",
};
const pref = { alert: "bip", notify: false, trail: true };

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fmt1 = (n) => Number(n).toLocaleString("id-ID", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const fmt2 = (n) => Number(n).toLocaleString("id-ID", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const hexA = (h, a) => { const n = parseInt(h.slice(1), 16); return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`; };

function loadPrefs() { try { Object.assign(pref, JSON.parse(localStorage.getItem("ss-prefs") || "{}")); } catch (_) { /* default */ } }
function savePrefs() { try { localStorage.setItem("ss-prefs", JSON.stringify(pref)); } catch (_) { /* penyimpanan tidak tersedia */ } }

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

function updateState() {
  const st = !S.running ? "idle" : S.alerting ? "alert" : "live";
  if (st === S.lastState) return;
  S.lastState = st;
  document.documentElement.dataset.state = st;
  el.liveChip.dataset.state = st;
  el.liveText.textContent = st === "idle" ? "Belum dimulai" : st === "alert" ? "Peringatan parkir" : "Memantau langsung";
  document.title = st === "alert" ? "⚠ Parkir di trotoar · Sidewalk Sentinel" : "Sidewalk Sentinel";
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
  el.btnRun.classList.toggle("stop", S.running);

  el.btnDraw.setAttribute("aria-pressed", String(S.drawing));
  el.btnDraw.querySelector("span").textContent = S.drawing ? "Selesai" : "Trotoar";
  el.btnHeat.setAttribute("aria-pressed", String(S.heat));
  el.btnTrail.setAttribute("aria-pressed", String(S.trail));
  el.stage.classList.toggle("drawing", S.drawing);
  el.drawPanel.hidden = !S.drawing;
  syncBanner();
  el.swChip.hidden = !S.polys.length;
  updateState();
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
  fitView();
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

/* ------------------------------------------------------------------ bunyi, suara, notifikasi, layar tetap menyala */
let audio = null;
function unlockAudio() {
  try {
    audio = audio || new (window.AudioContext || window.webkitAudioContext)();
    if (audio.state === "suspended") audio.resume();
  } catch (_) { /* tanpa bunyi */ }
}
function beep() {
  if (!audio) return;
  const now = audio.currentTime;
  [0, 0.3].forEach((d) => {
    const o = audio.createOscillator(), g = audio.createGain();
    o.type = "sine"; o.frequency.value = 880;
    g.gain.setValueAtTime(0.0001, now + d);
    g.gain.exponentialRampToValueAtTime(0.2, now + d + 0.01);
    g.gain.exponentialRampToValueAtTime(0.0001, now + d + 0.22);
    o.connect(g).connect(audio.destination);
    o.start(now + d); o.stop(now + d + 0.25);
  });
}
function speak(text) {
  try {
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "id-ID"; u.rate = 1.05;
    window.speechSynthesis.cancel(); window.speechSynthesis.speak(u);
  } catch (_) { /* suara tidak tersedia */ }
}
function announce(a) {
  const msg = `${cap(a.kelas)} parkir di trotoar`;
  if (pref.alert === "bip") beep();
  else if (pref.alert === "suara") speak(`Peringatan. ${msg}.`);
  if (pref.notify && document.hidden && "Notification" in window && Notification.permission === "granted") {
    try { new Notification("Sidewalk Sentinel", { body: `${msg} · diam ${Math.round(a.dwell_s)} detik`, icon: "/static/favicon.svg", tag: "ss-alert" }); } catch (_) { /* abaikan */ }
  }
}
async function keepAwake(on) {
  try {
    if (on && "wakeLock" in navigator && !S.wake) {
      S.wake = await navigator.wakeLock.request("screen");
      S.wake.addEventListener("release", () => { S.wake = null; });
    } else if (!on && S.wake) { await S.wake.release(); S.wake = null; }
  } catch (_) { /* layar boleh padam */ }
}
document.addEventListener("visibilitychange", () => { if (!document.hidden && S.running) keepAwake(true); });

/* ------------------------------------------------------------------ mulai & berhenti */
function resetTelemetry() {
  S.vis.clear(); S.trails.clear();
  S.hist = { veh: [], side: [], park: [], fps: [] }; S.histT = 0; S.mixKey = "";
  [el.sparkVeh, el.sparkSide, el.sparkPark, el.sparkFps].forEach((s) => { s.innerHTML = ""; });
  el.mix.textContent = "";
  el.uptime.textContent = "00:00";
  setSummary(null);
}

async function start() {
  notify("");
  unlockAudio();
  resetTelemetry();
  hctx.clearRect(0, 0, heat.width, heat.height);
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
    keepAwake(true);
    poke();
  } catch (e) {
    S.running = false;
    notify(e.message, true);
  }
  refreshUI();
}

async function stop(msg) {
  const wasStream = S.kind === "stream";
  S.running = false; S.alerting = false; S.token++;
  if (S.kind === "video") el.video.pause();
  try { await api("/api/stop"); } catch (_) { /* server mungkin sudah berhenti */ }
  if (wasStream) el.stream.removeAttribute("src");
  S.payload = null; S.payloadSeen = false;
  resetTelemetry(); showAlertState([]);
  el.metrics.hidden = true;
  el.stage.dataset.idle = "0";
  keepAwake(false);
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

/* ------------------------------------------------------------------ hasil deteksi -> telemetri, banner, daftar */
function setSummary(p) {
  const s = p ? p.summary : { kendaraan: 0, di_trotoar: 0, parkir: 0 };
  el.nVeh.textContent = s.kendaraan; el.nSide.textContent = s.di_trotoar; el.nPark.textContent = s.parkir;
  el.warn.classList.toggle("hot", s.parkir > 0);
}

function drawSpark(svg, arr, floor) {
  if (arr.length < 2) { svg.innerHTML = ""; return; }
  const W = 120, H = 30, max = Math.max(floor, ...arr);
  const line = arr.map((v, i) => `${i ? "L" : "M"}${((i / (arr.length - 1)) * W).toFixed(1)},${(H - 2 - (v / max) * (H - 6)).toFixed(1)}`).join("");
  svg.innerHTML = `<path class="area" d="${line}L${W},${H}L0,${H}Z"/><path class="line" d="${line}"/>`;
}

function pushHistory(p) {
  const now = performance.now();
  if (now - S.histT < 700) return;
  S.histT = now;
  const s = p.summary, h = S.hist;
  [["veh", s.kendaraan], ["side", s.di_trotoar], ["park", s.parkir], ["fps", p.fps]].forEach(([k, v]) => {
    h[k].push(v); if (h[k].length > 80) h[k].shift();
  });
  drawSpark(el.sparkVeh, h.veh, 3); drawSpark(el.sparkSide, h.side, 3);
  drawSpark(el.sparkPark, h.park, 2); drawSpark(el.sparkFps, h.fps, 5);
}

function renderMix(p) {
  const counts = {};
  p.vehicles.forEach((v) => { counts[v.kelas] = (counts[v.kelas] || 0) + 1; });
  const key = JSON.stringify(counts);
  if (key === S.mixKey) return;
  S.mixKey = key;
  el.mix.textContent = "";
  const names = Object.keys(counts).sort();
  if (!names.length) { const c = document.createElement("span"); c.className = "chip"; c.textContent = "Belum ada kendaraan"; el.mix.append(c); return; }
  names.forEach((k) => {
    const c = document.createElement("span"); c.className = "chip";
    const b = document.createElement("b"); b.textContent = counts[k];
    c.append(b, " " + k); el.mix.append(c);
  });
}

function syncBanner() {
  el.banner.hidden = el.banner.dataset.on !== "1" || S.drawing;     // saat menggambar, panel gambar yang tampil
}

function showAlertState(confirmed) {
  S.alerting = S.running && confirmed.length > 0;
  el.banner.dataset.on = S.alerting ? "1" : "0";
  syncBanner();
  updateState();
  if (!confirmed.length) return;
  const v = confirmed[0];
  el.bannerText.textContent = confirmed.length === 1
    ? `${cap(v.kelas)}${v.id != null ? " #" + v.id : ""} parkir di trotoar · diam ${Math.round(v.dwell)} dtk`
    : `${confirmed.length} kendaraan parkir di trotoar`;
}

/* jejak gerak & peta panas: dihitung dari setiap hasil, digambar oleh frame() */
const heat = document.createElement("canvas");
heat.width = 384; heat.height = 216;
const hctx = heat.getContext("2d");

function recordMotion(p) {
  const now = performance.now();
  p.vehicles.forEach((v) => {
    const [x0, y0, x1, y1] = v.box;
    if (v.id != null) {
      let tr = S.trails.get(v.id);
      if (!tr) { tr = { pts: [], last: now }; S.trails.set(v.id, tr); }
      tr.last = now;
      const x = (x0 + x1) / 2, y = y1, q = tr.pts[tr.pts.length - 1];
      if (!q || Math.hypot(x - q.x, y - q.y) > 0.003) { tr.pts.push({ x, y, t: now }); if (tr.pts.length > 40) tr.pts.shift(); }
    }
    if (v.di_trotoar) {
      const x = ((x0 + x1) / 2) * heat.width, y = (y0 + (y1 - y0) * 0.8) * heat.height;
      const rad = Math.max(6, (x1 - x0) * heat.width * 0.4);
      const g = hctx.createRadialGradient(x, y, 0, x, y, rad);
      g.addColorStop(0, "rgba(255,110,70,.045)"); g.addColorStop(1, "rgba(255,110,70,0)");
      hctx.globalCompositeOperation = "lighter"; hctx.fillStyle = g;
      hctx.fillRect(x - rad, y - rad, rad * 2, rad * 2);
    }
  });
  for (const [id, tr] of S.trails) if (now - tr.last > 2500) S.trails.delete(id);
}

function onPayload(p) {
  S.payload = p;
  setSummary(p);
  showAlertState(p.vehicles.filter((v) => v.status === "PARKIR_TERKONFIRMASI"));
  el.metrics.hidden = false;
  el.perfFps.textContent = fmt1(p.fps); el.perfLat.textContent = p.latency_ms;
  el.evDwell.textContent = Math.round(p.min_dwell);
  renderMix(p); pushHistory(p); recordMotion(p);
  if (!S.payloadSeen) { S.payloadSeen = true; fitView(); }
  if (p.alerts !== S.alertCount) loadAlerts(true);
}

/* ------------------------------------------------------------------ kejadian & tinjauan benar/salah */
function renderPrecision() {
  const c = { benar: 0, salah: 0 };
  S.items.forEach((a) => { if (a.label in c) c[a.label]++; });
  const reviewed = c.benar + c.salah, pending = S.items.length - reviewed;
  el.precision.hidden = S.items.length === 0;
  el.csvReview.hidden = reviewed === 0;
  if (!S.items.length) return;
  if (!reviewed) {
    el.precision.textContent = "Tandai tiap kejadian Benar atau Salah untuk menghitung presisi alert.";
    el.precision.style.setProperty("--p", 0);
    return;
  }
  const pct = Math.round((c.benar / reviewed) * 100);
  el.precision.textContent = `Presisi alert ${pct}% · ${c.benar} benar, ${c.salah} salah` + (pending ? ` · ${pending} belum ditinjau` : "");
  el.precision.style.setProperty("--p", pct);
}

async function review(a, label) {
  const next = a.label === label ? null : label;
  try { const res = await api("/api/alerts/label", { id: a.id, label: next }); a.label = res.alert.label; }
  catch (e) { notify(e.message, true); return; }
  renderEvents(new Set());
}

function renderEvents(fresh) {
  el.evList.textContent = "";
  S.items.forEach((a) => {                                    // server mengirim yang terbaru lebih dulu
    const li = document.createElement("li");
    const card = document.createElement("div");
    card.className = "ev" + (fresh.has(a.id) ? " fresh" : "");
    card.dataset.label = a.label || "";

    const thumb = document.createElement("button");
    thumb.type = "button"; thumb.className = "thumb"; thumb.title = "Perbesar foto bukti";
    const img = document.createElement("img");
    img.src = a.foto; img.alt = `Foto bukti ${a.kelas} #${a.track_id}`; img.loading = "lazy";
    thumb.append(img);
    thumb.addEventListener("click", () => { el.lightboxImg.src = a.foto; el.lightbox.showModal(); });

    const body = document.createElement("div");
    const t = document.createElement("span"); t.className = "t";
    t.textContent = `${cap(a.kelas)} #${a.track_id} parkir di trotoar`;
    const m = document.createElement("span"); m.className = "m";
    m.textContent = `${new Date(a.waktu).toLocaleTimeString("en-GB")} · diam ${Math.round(a.dwell_s)} dtk`;
    const rv = document.createElement("div"); rv.className = "rv";
    [["ok", "benar", "Benar"], ["no", "salah", "Salah"]].forEach(([cls, val, text]) => {
      const b = document.createElement("button");
      b.type = "button"; b.className = cls; b.textContent = text;
      b.setAttribute("aria-pressed", String(a.label === val));
      b.title = val === "benar" ? "Kendaraan memang parkir di trotoar" : "Alarm palsu";
      b.addEventListener("click", () => review(a, val));
      rv.append(b);
    });
    body.append(t, m, rv);
    card.append(thumb, body); li.append(card); el.evList.append(li);
  });
  el.evCount.textContent = S.items.length;
  el.evEmpty.hidden = S.items.length > 0;
  el.csv.hidden = S.items.length === 0;
  renderPrecision();
}

async function loadAlerts(announceNew) {
  let data;
  try { data = await (await fetch("/api/alerts", { cache: "no-store" })).json(); } catch (_) { return; }
  const items = data.alerts || [];
  const fresh = items.filter((a) => !S.seenAlerts.has(a.id));
  S.items = items; S.alertCount = items.length;
  items.forEach((a) => S.seenAlerts.add(a.id));
  renderEvents(announceNew ? new Set(fresh.map((a) => a.id)) : new Set());
  if (announceNew && fresh.length) announce(fresh[0]);
}

/* ------------------------------------------------------------------ overlay di atas video */
function mediaRect() {
  const cw = el.view.clientWidth, ch = el.view.clientHeight;
  let vw = 0, vh = 0;
  if (S.kind === "stream") { vw = el.stream.naturalWidth; vh = el.stream.naturalHeight; }
  else { vw = el.video.videoWidth; vh = el.video.videoHeight; }
  if ((!vw || !vh) && S.payload) { vw = S.payload.w; vh = S.payload.h; }
  if (!vw || !vh) return null;
  const s = Math.min(cw / vw, ch / vh);
  return { x: (cw - vw * s) / 2, y: (ch - vh * s) / 2, w: vw * s, h: vh * s };
}

/* di layar sempit, tinggi panggung mengikuti rasio gambar (video HP biasanya tegak) */
const NARROW = window.matchMedia("(max-width: 860px)");
function fitView() {
  let vw = 0, vh = 0;
  if (S.kind === "stream") { vw = el.stream.naturalWidth; vh = el.stream.naturalHeight; }
  else { vw = el.video.videoWidth; vh = el.video.videoHeight; }
  if ((!vw || !vh) && S.payload) { vw = S.payload.w; vh = S.payload.h; }
  el.view.style.aspectRatio = NARROW.matches && vw && vh ? String(clamp(vw / vh, 0.6, 1.8)) : "";
}
NARROW.addEventListener("change", fitView);
el.video.addEventListener("loadedmetadata", fitView);
el.video.addEventListener("resize", fitView);

function sizeCanvas() {
  const dpr = window.devicePixelRatio || 1;
  const w = el.view.clientWidth, h = el.view.clientHeight;
  el.canvas.width = Math.round(w * dpr); el.canvas.height = Math.round(h * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
new ResizeObserver(sizeCanvas).observe(el.view);

function tracePolys(polys, r, closePath = true, dx = 0, dy = 0) {
  ctx.beginPath();
  polys.forEach((poly) => {
    poly.forEach(([x, y], i) => (i ? ctx.lineTo(r.x + x * r.w + dx, r.y + y * r.h + dy) : ctx.moveTo(r.x + x * r.w + dx, r.y + y * r.h + dy)));
    if (closePath) ctx.closePath();
  });
}

function roundRect(x, y, w, h, rad) {
  ctx.beginPath();
  ctx.moveTo(x + rad, y);
  ctx.arcTo(x + w, y, x + w, y + h, rad); ctx.arcTo(x + w, y + h, x, y + h, rad);
  ctx.arcTo(x, y + h, x, y, rad); ctx.arcTo(x, y, x + w, y, rad); ctx.closePath();
}

/* kotak kendaraan dihaluskan antar-hasil supaya bergerak mulus di 60 fps meski server mengirim ~10 hasil per detik */
function smoothBox(v, dt) {
  if (v.id == null) return v.box;
  let s = S.vis.get(v.id);
  if (!s) { s = { box: v.box.slice(), f: 0 }; S.vis.set(v.id, s); }
  const k = 1 - Math.exp(-dt * 16);
  for (let i = 0; i < 4; i++) s.box[i] += (v.box[i] - s.box[i]) * k;
  s.f = S.frameNo;
  return s.box;
}

function drawTrail(id, tr, color, now) {
  const pts = tr.pts.filter((q) => now - q.t < 4000);
  for (let i = 1; i < pts.length; i++) {
    const age = (now - pts[i].t) / 4000;
    ctx.strokeStyle = hexA(color, 0.75 * (1 - age));
    ctx.lineWidth = 2.4 * (1 - age * 0.6) + 0.6;
    ctx.beginPath(); ctx.moveTo(pts[i - 1].px, pts[i - 1].py); ctx.lineTo(pts[i].px, pts[i].py); ctx.stroke();
  }
}

function drawVehicle(v, r, now, dt) {
  const st = STATUS[v.status] || STATUS.DI_JALAN;
  const danger = v.status === "PARKIR_TERKONFIRMASI" || v.status === "TERINDIKASI_PARKIR";
  const b = smoothBox(v, dt);
  const sx = r.x + b[0] * r.w, sy = r.y + b[1] * r.h, ex = r.x + b[2] * r.w, ey = r.y + b[3] * r.h;
  const w = ex - sx, h = ey - sy;

  if (v.poly) {                                                // bentuk segmentasi, ikut bergeser bersama kotak yang dihaluskan
    tracePolys([v.poly], r, true, (b[0] - v.box[0]) * r.w, (b[1] - v.box[1]) * r.h);
    ctx.fillStyle = hexA(st.color, danger ? 0.28 : 0.15); ctx.fill();
    ctx.lineWidth = 1.5; ctx.lineJoin = "round"; ctx.strokeStyle = hexA(st.color, 0.9); ctx.stroke();
  }

  const L = clamp(Math.min(w, h) * 0.28, 10, 22);              // kurung sudut ala HUD
  ctx.save();
  ctx.lineWidth = 2.6; ctx.lineCap = "round"; ctx.lineJoin = "round";
  ctx.strokeStyle = st.color; ctx.shadowColor = st.color; ctx.shadowBlur = danger ? 14 : 8;
  ctx.beginPath();
  [[sx, sy, 1, 1], [ex, sy, -1, 1], [sx, ey, 1, -1], [ex, ey, -1, -1]].forEach(([x, y, a, c]) => {
    ctx.moveTo(x + a * L, y); ctx.lineTo(x, y); ctx.lineTo(x, y + c * L);
  });
  ctx.stroke(); ctx.restore();

  if (v.status === "PARKIR_TERKONFIRMASI" && !REDUCED) {       // denyut peringatan
    const ph = (now / 1100) % 1, g = 4 + ph * 18;
    roundRect(sx - g, sy - g, w + 2 * g, h + 2 * g, 10 + g / 2);
    ctx.lineWidth = 2; ctx.strokeStyle = hexA(st.color, (1 - ph) * 0.55); ctx.stroke();
  }

  const min = S.payload ? S.payload.min_dwell : 10;
  let label = `${v.id != null ? "#" + v.id + " " : ""}${cap(v.kelas)}`, frac = null;
  if (v.status === "KANDIDAT_PARKIR") { frac = clamp(v.dwell / min, 0, 1); label += ` · ${Math.round(v.dwell)}/${Math.round(min)} dtk`; }
  else if (v.status === "PARKIR_TERKONFIRMASI") { frac = 1; label += ` · parkir ${Math.round(v.dwell)} dtk`; }
  else label += ` · ${st.text}`;

  ctx.font = `600 12.5px ${FONT}`;
  const th = 26, padL = 10, ringW = 20, tw = ctx.measureText(label).width + padL + ringW + 10;
  const x = clamp(sx, r.x + 2, r.x + r.w - tw - 2);
  let y = sy - th - 6; if (y < r.y + 2) y = ey + 6;
  roundRect(x, y, tw, th, 8); ctx.fillStyle = "rgba(7,10,20,.84)"; ctx.fill();
  ctx.lineWidth = 1; ctx.strokeStyle = hexA(st.color, 0.7); ctx.stroke();
  const cx = x + padL + 6, cy = y + th / 2;
  if (frac === null) { ctx.beginPath(); ctx.arc(cx, cy, 3.6, 0, 7); ctx.fillStyle = st.color; ctx.fill(); }
  else {
    ctx.lineWidth = 2.6; ctx.lineCap = "round";
    ctx.beginPath(); ctx.arc(cx, cy, 6, 0, 7); ctx.strokeStyle = "rgba(255,255,255,.18)"; ctx.stroke();
    ctx.beginPath(); ctx.arc(cx, cy, 6, -Math.PI / 2, -Math.PI / 2 + frac * Math.PI * 2); ctx.strokeStyle = st.color; ctx.stroke();
  }
  ctx.fillStyle = "#e9ecff"; ctx.textAlign = "left"; ctx.textBaseline = "middle";
  ctx.fillText(label, x + padL + ringW, cy + 0.5);
}

function drawShapes(shapes, r, solid) {
  shapes.forEach((poly) => {
    tracePolys([poly], r);
    ctx.fillStyle = "rgba(123,140,255,.2)"; ctx.fill();
    ctx.lineWidth = 2; ctx.strokeStyle = C.ion; ctx.setLineDash(solid ? [] : [7, 5]); ctx.stroke(); ctx.setLineDash([]);
  });
}

function drawDraft(r) {
  const d = S.draft;
  drawShapes(S.shapes, r, true);
  if (!d.length) return;
  ctx.lineWidth = 2; ctx.strokeStyle = C.amber;
  tracePolys([d], r, false); ctx.stroke();
  if (d.length >= 3) {
    ctx.setLineDash([5, 5]); ctx.globalAlpha = 0.7;
    ctx.beginPath(); ctx.moveTo(r.x + d[d.length - 1][0] * r.w, r.y + d[d.length - 1][1] * r.h);
    ctx.lineTo(r.x + d[0][0] * r.w, r.y + d[0][1] * r.h); ctx.stroke();
    ctx.setLineDash([]); ctx.globalAlpha = 1;
  }
  d.forEach(([x, y], i) => {
    ctx.beginPath(); ctx.arc(r.x + x * r.w, r.y + y * r.h, i === 0 ? 7 : 4.5, 0, 7);
    ctx.fillStyle = i === 0 ? C.amber : "#fff"; ctx.fill();
    ctx.lineWidth = 1.5; ctx.strokeStyle = "#0a0d1c"; ctx.stroke();
  });
}

function frame(now) {
  const dt = Math.min(0.1, (now - (S.lastFrameT || now)) / 1000);
  S.lastFrameT = now; S.frameNo++;
  ctx.clearRect(0, 0, el.view.clientWidth, el.view.clientHeight);
  const r = mediaRect();
  if (r) {
    const p = S.payload;
    if (S.drawing) {
      if (S.polys.length) drawShapes(S.polys, r, false);
      drawDraft(r);
    } else if (p) {
      if (S.heat) { ctx.save(); ctx.globalAlpha = 0.9; ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = "high"; ctx.drawImage(heat, r.x, r.y, r.w, r.h); ctx.restore(); }
      if (p.sidewalk.length) {
        tracePolys(p.sidewalk, r);
        ctx.fillStyle = "rgba(123,140,255,.16)"; ctx.fill("evenodd");
        ctx.lineWidth = 1.5; ctx.strokeStyle = "rgba(160,175,255,.85)";
        if (!REDUCED) { ctx.setLineDash([8, 6]); ctx.lineDashOffset = -((now / 60) % 14); }
        ctx.stroke(); ctx.setLineDash([]);
      }
      if (S.trail) {
        for (const [id, tr] of S.trails) {
          const v = p.vehicles.find((q) => q.id === id);
          tr.pts.forEach((q) => { q.px = r.x + q.x * r.w; q.py = r.y + q.y * r.h; });
          drawTrail(id, tr, (STATUS[v && v.status] || STATUS.DI_JALAN).color, performance.now());
        }
      }
      // kendaraan yang bukan "di jalan" digambar belakangan agar tidak tertutup
      const order = (v) => (v.status === "DI_JALAN" ? 0 : v.status === "PARKIR_TERKONFIRMASI" ? 2 : 1);
      [...p.vehicles].sort((a, b) => order(a) - order(b)).forEach((v) => drawVehicle(v, r, now, dt));
      for (const [id, s] of S.vis) if (s.f !== S.frameNo) S.vis.delete(id);
    } else if (S.polys.length) {
      drawShapes(S.polys, r, false);
    }
  }
  if (S.running) {
    const s = Math.floor((Date.now() - S.startedAt) / 1000);
    const txt = `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
    if (el.uptime.textContent !== txt) el.uptime.textContent = txt;
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

/* ------------------------------------------------------------------ alat tambahan: peta panas, jejak, foto, layar penuh */
function toggleHeat() { S.heat = !S.heat; refreshUI(); }
function toggleTrail() { S.trail = !S.trail; pref.trail = S.trail; savePrefs(); refreshUI(); }

function snapshot() {
  const r = mediaRect();
  if (!r || !hasPicture()) { notify("Belum ada gambar untuk difoto.", true); return; }
  const src = S.kind === "stream" ? el.stream : el.video, dpr = window.devicePixelRatio || 1;
  const c = document.createElement("canvas");
  c.width = Math.round(r.w * dpr); c.height = Math.round(r.h * dpr);
  try {
    const g = c.getContext("2d");
    g.drawImage(src, 0, 0, c.width, c.height);
    g.drawImage(el.canvas, r.x * dpr, r.y * dpr, r.w * dpr, r.h * dpr, 0, 0, c.width, c.height);
  } catch (e) { notify(`Gambar tidak bisa disimpan: ${e.message}`, true); return; }
  c.toBlob((blob) => {
    if (!blob) { notify("Gambar tidak bisa disimpan.", true); return; }
    const d = new Date(), z = (n) => String(n).padStart(2, "0");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `sidewalk-sentinel-${d.getFullYear()}${z(d.getMonth() + 1)}${z(d.getDate())}-${z(d.getHours())}${z(d.getMinutes())}${z(d.getSeconds())}.png`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 3000);
    notify("Gambar dengan overlay disimpan ke folder unduhan.");
  }, "image/png");
}

function toggleFull() {
  const ask = el.stage.requestFullscreen || el.stage.webkitRequestFullscreen;
  if (!document.fullscreenElement && !ask) { notify("Browser ini tidak mendukung layar penuh. Putar HP ke mode lanskap.", true); return; }
  const p = document.fullscreenElement ? document.exitFullscreen() : ask.call(el.stage);
  if (p && p.catch) p.catch(() => notify("Browser menolak layar penuh. Tekan tombolnya sekali lagi.", true));
}

/* dok melebur saat memantau supaya gambar tampil penuh; muncul lagi begitu pointer bergerak atau layar disentuh */
function poke() {
  el.stage.dataset.idle = "0";
  clearTimeout(S.idleTimer);
  S.idleTimer = setTimeout(() => {
    if (S.running && !S.drawing && !el.dock.matches(":hover, :focus-within")) el.stage.dataset.idle = "1";
  }, 3500);
}
["pointermove", "pointerdown", "keydown"].forEach((ev) => el.stage.addEventListener(ev, poke, { passive: true }));

/* ------------------------------------------------------------------ laci pengaturan */
function openDrawer() {
  el.drawer.hidden = false; el.scrim.hidden = false;
  requestAnimationFrame(() => el.drawer.classList.add("in"));
  el.drawerClose.focus();
}
function closeDrawer() {
  if (el.drawer.hidden) return;
  el.drawer.classList.remove("in"); el.scrim.hidden = true;
  setTimeout(() => { if (!el.drawer.classList.contains("in")) el.drawer.hidden = true; }, 260);
  el.btnSettings.focus();
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

/* ------------------------------------------------------------------ status server */
function applyStatus(st) {
  S.model = st.model;
  el.modelChip.dataset.state = st.model;
  el.modelText.textContent = st.model === "siap" ? "Model siap" : st.model === "memuat" ? "Memuat model…" : "Model gagal dimuat";
  el.modelChip.title = st.model === "galat" ? (st.model_error || "") :
    st.model === "memuat" ? "Pertama kali, bobot YOLO bisa diunduh dulu. Tunggu beberapa saat." : "";
  if (st.model === "galat" && !S.modelNotified) { S.modelNotified = true; notify(`Model deteksi gagal dimuat: ${st.model_error}`, true); }

  S.polys = st.polygon || [];
  el.outDir.textContent = st.out_dir ? `Foto bukti dan CSV disimpan di: ${st.out_dir}` : "";
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

async function pollStatus() {
  try { applyStatus(await (await fetch("/api/status", { cache: "no-store" })).json()); } catch (_) { /* server mati sementara */ }
  setTimeout(pollStatus, 1500);
}

/* ------------------------------------------------------------------ pendengar & pintasan */
el.tabs.forEach((b) => b.addEventListener("click", () => selectKind(b.dataset.kind)));
el.seg.addEventListener("keydown", (e) => {
  if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
  const i = [...el.tabs].findIndex((b) => b.dataset.kind === S.kind);
  const next = el.tabs[(i + (e.key === "ArrowRight" ? 1 : el.tabs.length - 1)) % el.tabs.length];
  selectKind(next.dataset.kind); next.focus();
});
el.camOn.addEventListener("click", startCamera);
el.streamUrl.addEventListener("input", refreshUI);
el.streamUrl.addEventListener("keydown", (e) => { if (e.key === "Enter" && !el.btnRun.disabled) start(); });
el.videoPick.addEventListener("click", () => el.videoFile.click());
el.videoFile.addEventListener("change", () => openVideoFile(el.videoFile.files[0]));
el.btnRun.addEventListener("click", () => (S.running ? stop() : start()));
el.btnDraw.addEventListener("click", toggleDraw);
el.btnHeat.addEventListener("click", toggleHeat);
el.btnTrail.addEventListener("click", toggleTrail);
el.btnShot.addEventListener("click", snapshot);
el.drawUndo.addEventListener("click", undoPoint);
el.drawClose.addEventListener("click", closeShape);
el.drawCancel.addEventListener("click", cancelDraw);
el.drawApply.addEventListener("click", applyDraw);
el.btnAuto.addEventListener("click", backToAuto);
el.btnFull.addEventListener("click", toggleFull);
el.btnHelp.addEventListener("click", () => el.help.showModal());
el.btnSettings.addEventListener("click", openDrawer);
el.drawerClose.addEventListener("click", closeDrawer);
el.scrim.addEventListener("click", closeDrawer);
el.setDwell.addEventListener("input", () => { el.oDwell.textContent = `${el.setDwell.value} dtk`; sendSettings(); });
el.setTau.addEventListener("input", () => { el.oTau.textContent = fmt2(el.setTau.value); sendSettings(); });
el.setComp.addEventListener("change", sendSettings);
el.setAlert.addEventListener("change", () => {
  pref.alert = el.setAlert.value; savePrefs(); unlockAudio();
  if (pref.alert === "bip") beep(); else if (pref.alert === "suara") speak("Suara peringatan aktif.");
});
el.setNotify.addEventListener("change", async () => {
  if (!el.setNotify.checked) { pref.notify = false; savePrefs(); return; }
  if (!("Notification" in window)) { el.setNotify.checked = false; notify("Browser ini tidak mendukung notifikasi.", true); return; }
  const perm = Notification.permission === "default" ? await Notification.requestPermission() : Notification.permission;
  pref.notify = perm === "granted";
  el.setNotify.checked = pref.notify;
  if (!pref.notify) notify("Izin notifikasi ditolak. Izinkan di pengaturan situs browser bila ingin memakainya.", true);
  savePrefs();
});
el.btnRefresh.addEventListener("click", async () => { try { await api("/api/refresh"); notify("Trotoar akan dihitung ulang pada gambar berikutnya."); } catch (e) { notify(e.message, true); } });
[el.lightbox, el.help].forEach((d) => d.addEventListener("click", (e) => { if (e.target === d) d.close(); }));

document.addEventListener("keydown", (e) => {
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const tag = document.activeElement.tagName;
  if (/^(INPUT|SELECT|TEXTAREA)$/.test(tag) || document.querySelector("dialog[open]")) return;
  if (e.key === "Escape" && !el.drawer.hidden) { closeDrawer(); return; }
  if (S.drawing) {
    if (e.key === "Enter" && tag !== "BUTTON") { closeShape(); e.preventDefault(); return; }
    if (e.key === "Backspace") { undoPoint(); e.preventDefault(); return; }
    if (e.key === "Escape") { cancelDraw(); return; }
  }
  const k = e.key.toLowerCase();
  if (k === " " && tag !== "BUTTON") { if (!el.btnRun.disabled) (S.running ? stop() : start()); e.preventDefault(); }
  else if (k === "d") toggleDraw();
  else if (k === "h") toggleHeat();
  else if (k === "t") toggleTrail();
  else if (k === "s") snapshot();
  else if (k === "f") toggleFull();
  else if (k === "?") el.help.showModal();
});

(async function init() {
  loadPrefs();
  S.trail = pref.trail;
  el.setAlert.value = pref.alert;
  el.setNotify.checked = pref.notify && "Notification" in window && Notification.permission === "granted";
  if (!el.stage.requestFullscreen && !el.stage.webkitRequestFullscreen) el.btnFull.hidden = true;
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

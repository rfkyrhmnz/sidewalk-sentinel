"use strict";
/* Sidewalk Sentinel: antarmuka. Tanpa pustaka luar. Semua koordinat deteksi dari server berupa pecahan 0-1
   terhadap gambar, jadi overlay ikut ukuran layar berapa pun. Alur utama: buka -> kamera menyala -> pemantauan mulai sendiri. */

const $ = (id) => document.getElementById(id);
const el = {
  stage: $("stage"), view: $("view"), video: $("video"), stream: $("stream"), canvas: $("overlay"),
  empty: $("empty"), emptyRetry: $("empty-retry"),
  btnFlip: $("btn-flip"), btnPause: $("btn-pause"), icoPause: $("ico-pause"), icoPlay: $("ico-play"), btnSettings: $("btn-settings"),
  drawPanel: $("draw-panel"), drawUndo: $("draw-undo"), drawClose: $("draw-close"), drawCancel: $("draw-cancel"), drawApply: $("draw-apply"),
  status: $("status"), ring: $("ring-arc"), statusMain: $("status-main"), statusSub: $("status-sub"),
  btnEvents: $("btn-events"), evCount: $("ev-count"), notice: $("notice"), scrim: $("scrim"),
  sheetEvents: $("sheet-events"), closeEvents: $("close-events"), csv: $("csv-link"), csvReview: $("csv-review"),
  precision: $("precision"), evList: $("ev-list"), evEmpty: $("ev-empty"), evDwell: $("ev-dwell"),
  sheetSettings: $("sheet-settings"), closeSettings: $("close-settings"), tabs: document.querySelectorAll(".seg button"),
  srcCamera: $("src-camera"), srcVideo: $("src-video"), srcStream: $("src-stream"),
  camSelect: $("cam-select"), videoFile: $("video-file"), videoPick: $("video-pick"), videoName: $("video-name"),
  streamUrl: $("stream-url"), streamGo: $("stream-go"),
  setDwell: $("set-dwell"), oDwell: $("o-dwell"), setTau: $("set-tau"), oTau: $("o-tau"), setComp: $("set-comp"),
  btnDraw: $("btn-draw"), btnAuto: $("btn-auto"), btnRefresh: $("btn-refresh"),
  setAlert: $("set-alert"), setNotify: $("set-notify"), setAuto: $("set-auto"),
  btnInstall: $("btn-install"), btnShot: $("btn-shot"), btnFull: $("btn-full"), installHint: $("install-hint"),
  modelChip: $("model-chip"), modelText: $("model-text"), metrics: $("metrics"), perfFps: $("perf-fps"), perfLat: $("perf-lat"),
  outDir: $("out-dir"), lightbox: $("lightbox"), lightboxImg: $("lightbox-img"),
};
const ctx = el.canvas.getContext("2d");
const FONT = getComputedStyle(document.body).fontFamily;
const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const NARROW = window.matchMedia("(max-width: 860px)");      // layar sempit: gambar memenuhi layar (cover), selain itu utuh (contain)

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
  camera: ["Nyalakan kamera", "Pilih kamera di pengaturan, atau tekan tombol di bawah. Deteksi mulai sendiri setelah gambar muncul."],
  video:  ["Buka berkas video", "Buka pengaturan di kanan atas, pilih <b>Video</b>, lalu pilih berkasnya. Video langsung dianalisis sambil diputar."],
  stream: ["Sambungkan kamera IP", "Buka pengaturan di kanan atas, pilih <b>Stream</b>, isi indeks webcam (<b>0</b>) atau alamat kamera IP/CCTV, lalu tekan Mulai."],
};

const S = {
  kind: "camera",                    // camera | stream | video
  running: false, alerting: false, token: 0, startedAt: 0, t0: 0,
  model: "memuat", modelNotified: false,
  payload: null, lastSeq: 0, lastState: "", lastStatus: "",
  items: [], alertCount: 0, seenAlerts: new Set(), seenCount: 0, firstAlerts: true,
  polys: [], drawing: false, draft: [], shapes: [],
  camReady: false, camError: "", camBusy: false, camCount: 0, facing: "environment",
  videoReady: false, videoUrl: null, settingsLoaded: false,
  manual: false, go: false, starting: false,
  vis: new Map(), frameNo: 0, lastFrameT: 0,
  wake: null, idleTimer: 0, sheet: null,
};
const pref = { alert: "bip", notify: false, auto: true };

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fmt1 = (n) => Number(n).toLocaleString("id-ID", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const fmt2 = (n) => Number(n).toLocaleString("id-ID", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const hexA = (h, a) => { const n = parseInt(h.slice(1), 16); return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`; };

function loadPrefs() { try { Object.assign(pref, JSON.parse(localStorage.getItem("ss-prefs") || "{}")); } catch (_) { /* default */ } }
function savePrefs() { try { localStorage.setItem("ss-prefs", JSON.stringify(pref)); } catch (_) { /* penyimpanan tidak tersedia */ } }

let noticeTimer = 0;
function notify(msg, isError = false) {
  el.notice.textContent = msg || "";
  el.notice.classList.toggle("error", !!isError);
  clearTimeout(noticeTimer);
  if (msg && !isError) noticeTimer = setTimeout(() => { el.notice.textContent = ""; }, 4500);
}

async function api(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  let data = {};
  try { data = await res.json(); } catch (_) { /* balasan kosong */ }
  if (!res.ok) throw new Error(data.error || `Server membalas ${res.status}`);
  return data;
}

/* ------------------------------------------------------------------ kapsul status: satu kalimat tentang keadaan sekarang */
function hasPicture() {
  if (S.running) return true;
  return (S.kind === "camera" && S.camReady) || (S.kind === "video" && S.videoReady);
}
function sourceReady() {
  return (S.kind === "camera" && S.camReady) || (S.kind === "video" && S.videoReady) || (S.kind === "stream" && el.streamUrl.value.trim() !== "");
}

function computeStatus() {
  const p = S.payload;
  if (S.running) {
    if (!p) return { tone: "boot", main: "Memantau…", sub: "Menunggu hasil pertama", p: null };
    const conf = p.vehicles.filter((v) => v.status === "PARKIR_TERKONFIRMASI");
    if (conf.length) {
      const v = conf[0];
      return { tone: "alert", p: 100, sub: `diam ${Math.round(v.dwell)} dtk · foto bukti tersimpan`,
               main: conf.length === 1 ? `${cap(v.kelas)} parkir di trotoar` : `${conf.length} kendaraan parkir di trotoar` };
    }
    const cand = p.vehicles.filter((v) => v.status === "KANDIDAT_PARKIR" || v.status === "TERINDIKASI_PARKIR");
    if (cand.length) {
      const v = cand.reduce((a, b) => (b.dwell > a.dwell ? b : a));
      return { tone: "watch", main: `${cap(v.kelas)} berhenti di trotoar`, p: clamp((v.dwell / p.min_dwell) * 100, 4, 100),
               sub: `Memastikan… ${Math.round(v.dwell)}/${Math.round(p.min_dwell)} dtk` };
    }
    if (p.vehicles.some((v) => v.di_trotoar)) return { tone: "watch", main: "Ada kendaraan di trotoar", sub: "Masih bergerak atau ada pengendara", p: 100 };
    const n = p.summary.kendaraan;
    return { tone: "clear", main: "Trotoar aman", p: 100, sub: n ? `${n} kendaraan terpantau, tak ada di trotoar` : "Belum ada kendaraan terlihat" };
  }
  if (S.model === "galat") return { tone: "error", main: "Model gagal dimuat", sub: "Lihat pesan di atas", p: 100 };
  if (S.kind === "camera" && pref.auto && !S.manual) {
    if (S.camError) return { tone: "error", main: "Kamera belum aktif", sub: "Izinkan akses kamera untuk memulai", p: 100 };
    if (!S.camReady) return { tone: "boot", main: "Menyalakan kamera…", sub: "Izinkan akses kamera bila diminta", p: null };
    if (S.model === "memuat") return { tone: "boot", main: "Menyiapkan model…", sub: "Pertama kali bisa lebih lama (mengunduh bobot)", p: null };
    return { tone: "boot", main: "Memulai…", sub: "", p: null };
  }
  if (hasPicture()) return { tone: "idle", main: "Dijeda", sub: "Ketuk ▶ untuk melanjutkan", p: 100 };
  return { tone: "idle", main: "Pilih sumber gambar", sub: "Buka pengaturan di kanan atas", p: 100 };
}

function renderStatus() {
  const s = computeStatus();
  const key = `${s.tone}|${s.main}|${s.sub}|${s.p === null ? "" : Math.round(s.p)}`;
  if (key === S.lastStatus) return;
  S.lastStatus = key;
  el.status.dataset.tone = s.tone;
  el.statusMain.textContent = s.main;
  el.statusSub.textContent = s.sub;
  el.status.style.setProperty("--p", s.p === null ? 100 : Math.round(s.p));
}

function updateState() {
  const st = !S.running ? "idle" : S.alerting ? "alert" : "live";
  if (document.documentElement.dataset.state !== st) {
    document.documentElement.dataset.state = st;
    document.title = st === "alert" ? "⚠ Parkir di trotoar · Sidewalk Sentinel" : "Sidewalk Sentinel";
  }
}

function refreshUI() {
  el.video.hidden = S.kind === "stream";
  el.stream.hidden = !(S.kind === "stream" && S.running);
  el.stage.dataset.empty = String(!hasPicture());
  const showRetry = S.kind === "camera" && !!S.camError;
  const [title, body] = S.kind === "camera" && pref.auto && !S.manual && !S.camError
    ? ["Menyalakan kamera…", "Izinkan akses kamera saat browser bertanya. Deteksi mulai sendiri setelah gambar muncul."]
    : S.camError && S.kind === "camera" ? ["Kamera belum bisa dipakai", S.camError] : EMPTY_COPY[S.kind];
  el.empty.querySelector("h2").textContent = title;
  el.empty.querySelector("p")[S.camError && S.kind === "camera" ? "textContent" : "innerHTML"] = body;
  el.emptyRetry.hidden = !showRetry;

  el.tabs.forEach((b) => b.setAttribute("aria-selected", String(b.dataset.kind === S.kind)));
  el.srcCamera.hidden = S.kind !== "camera";
  el.srcVideo.hidden = S.kind !== "video";
  el.srcStream.hidden = S.kind !== "stream";

  el.btnPause.disabled = !(S.running || (S.model === "siap" && sourceReady()));
  el.icoPause.hidden = !S.running; el.icoPlay.hidden = S.running;
  el.btnPause.setAttribute("aria-label", S.running ? "Jeda" : "Lanjutkan");
  el.btnFlip.hidden = !(S.kind === "camera" && S.camReady && S.camCount > 1);
  el.btnAuto.hidden = !S.polys.length;
  el.btnDraw.textContent = S.drawing ? "Batalkan gambar" : "Gambar area trotoar";
  el.stage.classList.toggle("drawing", S.drawing);
  el.drawPanel.hidden = !S.drawing;
  renderStatus(); updateState();
}

function detachVideo() {
  const v = el.video;
  v.pause();
  if (v.srcObject) v.srcObject.getTracks().forEach((t) => t.stop());
  v.srcObject = null; v.removeAttribute("src"); v.load();
  S.camReady = false; S.videoReady = false;
}

async function selectKind(kind) {
  if (kind === S.kind) return;
  if (S.running) await stop();
  detachVideo();
  S.kind = kind; S.go = false;
  S.manual = kind !== "camera";                           // kembali ke tab Kamera = mode langsung aktif lagi
  if (kind === "video" && S.videoUrl) attachVideo();      // berkas yang dipilih sebelumnya dipasang lagi
  notify("");
  refreshUI();
  if (kind === "camera" && pref.auto) recover();
}

/* ------------------------------------------------------------------ kamera */
function cameraError(e) {
  switch (e && e.name) {
    case "NotAllowedError": return "Izin kamera ditolak. Izinkan kamera untuk alamat ini di pengaturan browser, lalu coba lagi.";
    case "NotFoundError": return "Tidak ada kamera yang terdeteksi di perangkat ini.";
    case "NotReadableError": return "Kamera sedang dipakai aplikasi lain. Tutup aplikasi itu, lalu coba lagi.";
    case "OverconstrainedError": return "Kamera yang dipilih tidak tersedia. Pilih kamera lain.";
    default: return `Kamera gagal dinyalakan: ${(e && e.message) || e}`;
  }
}

async function listCameras(currentId) {
  try {
    const devs = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "videoinput");
    S.camCount = devs.length;
    el.camSelect.innerHTML = "";
    const auto = document.createElement("option");
    auto.value = ""; auto.textContent = "Otomatis (kamera belakang bila ada)";
    el.camSelect.appendChild(auto);
    devs.forEach((d, i) => {
      const o = document.createElement("option");
      o.value = d.deviceId; o.textContent = d.label || `Kamera ${i + 1}`;
      el.camSelect.appendChild(o);
    });
    if (currentId && el.camSelect.dataset.picked === "1") el.camSelect.value = currentId;
  } catch (_) { /* daftar kamera tidak wajib */ }
}

function stopCamera() {
  const s = el.video.srcObject;
  if (s) s.getTracks().forEach((t) => t.stop());
  el.video.srcObject = null;
  S.camReady = false;
}

function watchTrack(stream) {
  const t = stream.getVideoTracks()[0];
  if (t) t.addEventListener("ended", onCameraLost);       // kamera dicabut, atau HP mematikannya saat aplikasi di latar belakang
}

async function startCamera() {
  if (S.camBusy) return;
  S.camBusy = true; S.camError = "";
  try {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      S.camError = "Browser tidak mengizinkan kamera di alamat ini. Buka lewat http://localhost:8765 di laptop, atau lewat HTTPS (lihat README).";
      return;
    }
    stopCamera();
    notify("");
    refreshUI();
    const id = el.camSelect.value;
    const video = { width: { ideal: 1280 }, height: { ideal: 720 } };
    if (id) video.deviceId = { exact: id };
    else video.facingMode = { ideal: S.facing };           // di HP, kamera belakang lebih dulu
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video, audio: false });
      el.video.srcObject = stream;
      await el.video.play();
      S.camReady = true;
      watchTrack(stream);
      await listCameras(stream.getVideoTracks()[0].getSettings().deviceId);
    } catch (e) {
      S.camError = cameraError(e);
    }
  } finally {
    S.camBusy = false;
    refreshUI();
  }
}

async function restartCamera() {                           // ganti kamera / balik kamera: hentikan dulu bila sedang memantau
  if (S.running) await stop();
  S.manual = false;
  await startCamera();
  maybeAutoStart();
}
function flipCamera() {
  S.facing = S.facing === "environment" ? "user" : "environment";
  el.camSelect.value = ""; el.camSelect.dataset.picked = "0";
  restartCamera();
}

/* mode langsung: setelah sumber siap dan model siap, pemantauan mulai sendiri */
function maybeAutoStart() {
  if (S.running || S.starting || S.manual || S.model !== "siap") return;
  const ok = S.kind === "camera" ? pref.auto && S.camReady
    : S.kind === "video" ? S.go && S.videoReady
    : S.go && el.streamUrl.value.trim() !== "";
  if (ok) start();
}
async function recover() {
  if (!pref.auto || S.manual || S.kind !== "camera" || S.running || S.camReady || S.camBusy) { maybeAutoStart(); return; }
  await startCamera();
  maybeAutoStart();
}
async function onCameraLost() {
  if (S.kind !== "camera") return;
  S.camReady = false;
  if (S.running) await stop("Kamera terputus. Mencoba menyalakan ulang…");
  refreshUI();
  if (pref.auto && !S.manual) setTimeout(recover, 1500);
}

/* ------------------------------------------------------------------ berkas video */
function attachVideo() {
  el.video.srcObject = null;
  el.video.loop = false;
  el.video.src = S.videoUrl;
  S.videoReady = false;
  el.video.onloadeddata = () => { S.videoReady = true; refreshUI(); maybeAutoStart(); };
  el.video.onerror = () => { S.videoReady = false; notify("Video tidak bisa diputar di browser. Coba berkas .mp4 (H.264).", true); refreshUI(); };
}

async function openVideoFile(file) {
  if (!file) return;
  if (S.running) await stop();
  if (S.videoUrl) URL.revokeObjectURL(S.videoUrl);
  S.videoUrl = URL.createObjectURL(file);
  el.videoName.textContent = file.name; el.videoName.title = file.name;
  S.go = true; S.manual = false;                            // memilih berkas = ingin langsung dianalisis
  attachVideo();
  notify("");
  hideSheet();
  refreshUI();
}

/* ------------------------------------------------------------------ bunyi, suara, getar, notifikasi, layar tetap menyala */
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
  const tapped = !navigator.userActivation || navigator.userActivation.hasBeenActive;      // getar hanya boleh setelah layar pernah disentuh
  if (pref.alert !== "mati" && tapped) { try { if (navigator.vibrate) navigator.vibrate([220, 100, 220]); } catch (_) { /* tanpa getar */ } }
  if (pref.alert === "bip") beep();
  else if (pref.alert === "suara") speak(`Peringatan. ${msg}.`);
  if (pref.notify && document.hidden && "Notification" in window && Notification.permission === "granted") {
    try { new Notification("Sidewalk Sentinel", { body: `${msg} · diam ${Math.round(a.dwell_s)} detik`, icon: "/static/icon-192.png", tag: "ss-alert" }); } catch (_) { /* abaikan */ }
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
document.addEventListener("visibilitychange", () => { if (document.hidden) return; if (S.running) keepAwake(true); else recover(); });

/* ------------------------------------------------------------------ mulai & berhenti */
function resetSession() {
  S.vis.clear(); S.payload = null; S.payloadSeen = false;
  el.metrics.hidden = true;
}

async function start() {
  if (S.starting) return;
  S.starting = true; S.manual = false; S.go = false;
  try { await startNow(); } finally { S.starting = false; }
}

async function startNow() {
  notify("");
  unlockAudio();
  resetSession();
  try {
    if (S.kind === "stream") {
      const src = el.streamUrl.value.trim();
      if (!src) { notify("Isi dulu indeks webcam (0) atau alamat stream.", true); return; }
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
    S.running = false; S.manual = true;                    // gagal mulai: jangan dicoba ulang terus-menerus
    notify(e.message, true);
  }
  refreshUI();
}

function userStop() { S.manual = true; return stop(); }
function toggleRun() { if (!el.btnPause.disabled) (S.running ? userStop() : start()); }

async function stop(msg) {
  const wasStream = S.kind === "stream";
  S.running = false; S.alerting = false; S.token++;
  if (S.kind === "video") el.video.pause();
  try { await api("/api/stop"); } catch (_) { /* server mungkin sudah berhenti */ }
  if (wasStream) el.stream.removeAttribute("src");
  resetSession();
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
      if (token === S.token) { S.manual = true; await stop(null); notify(e.message, true); }
      return;
    }
    const spent = performance.now() - t1;
    if (spent < 80) await sleep(80 - spent);
  }
}

/* hasil dari server (kamera di sisi server) */
async function pollResults(token) {
  while (S.running && token === S.token) {
    try {
      const r = await fetch("/api/result?since=" + S.lastSeq, { cache: "no-store" });
      if (r.status === 200) { const p = await r.json(); S.lastSeq = p.seq; onPayload(p); }
    } catch (_) { /* coba lagi */ }
    await sleep(120);
  }
}

/* ------------------------------------------------------------------ hasil deteksi -> status, kejadian */
function onPayload(p) {
  S.payload = p;
  const confirmed = p.vehicles.filter((v) => v.status === "PARKIR_TERKONFIRMASI");
  S.alerting = S.running && confirmed.length > 0;
  el.metrics.hidden = false;
  el.perfFps.textContent = fmt1(p.fps); el.perfLat.textContent = p.latency_ms;
  el.evDwell.textContent = Math.round(p.min_dwell);
  renderStatus(); updateState();
  if (p.alerts !== S.alertCount) loadAlerts(true);
}

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
  if (S.sheet === el.sheetEvents) S.seenCount = S.items.length;
  el.btnEvents.dataset.new = S.items.length > S.seenCount ? "1" : "0";
  renderPrecision();
}

async function loadAlerts(announceNew) {
  let data;
  try { data = await (await fetch("/api/alerts", { cache: "no-store" })).json(); } catch (_) { return; }
  const items = data.alerts || [];
  const fresh = items.filter((a) => !S.seenAlerts.has(a.id));
  S.items = items; S.alertCount = items.length;
  items.forEach((a) => S.seenAlerts.add(a.id));
  if (S.firstAlerts) { S.firstAlerts = false; S.seenCount = items.length; }   // kejadian lama dari sesi sebelumnya dianggap sudah dilihat
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
  const s = NARROW.matches ? Math.max(cw / vw, ch / vh) : Math.min(cw / vw, ch / vh);
  return { x: (cw - vw * s) / 2, y: (ch - vh * s) / 2, w: vw * s, h: vh * s };
}

function sizeCanvas() {
  const dpr = window.devicePixelRatio || 1;
  el.canvas.width = Math.round(el.view.clientWidth * dpr); el.canvas.height = Math.round(el.view.clientHeight * dpr);
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

function corners(sx, sy, ex, ey, L) {
  ctx.beginPath();
  [[sx, sy, 1, 1], [ex, sy, -1, 1], [sx, ey, 1, -1], [ex, ey, -1, -1]].forEach(([x, y, a, c]) => {
    ctx.moveTo(x + a * L, y); ctx.lineTo(x, y); ctx.lineTo(x, y + c * L);
  });
  ctx.stroke();
}

function drawVehicle(v, r, now, dt) {
  const st = STATUS[v.status] || STATUS.DI_JALAN;
  const danger = v.status === "PARKIR_TERKONFIRMASI" || v.status === "TERINDIKASI_PARKIR";
  const b = smoothBox(v, dt);
  const sx = r.x + b[0] * r.w, sy = r.y + b[1] * r.h, ex = r.x + b[2] * r.w, ey = r.y + b[3] * r.h;
  const w = ex - sx, h = ey - sy, L = clamp(Math.min(w, h) * 0.28, 10, 22);
  ctx.lineCap = "round"; ctx.lineJoin = "round";

  if (v.status === "DI_JALAN") {                                // kendaraan di jalan: cukup isyarat tipis, tanpa label
    ctx.lineWidth = 1.6; ctx.strokeStyle = hexA(st.color, 0.4); corners(sx, sy, ex, ey, L);
    return;
  }
  if (v.poly) {                                                // bentuk segmentasi, ikut bergeser bersama kotak yang dihaluskan
    tracePolys([v.poly], r, true, (b[0] - v.box[0]) * r.w, (b[1] - v.box[1]) * r.h);
    ctx.fillStyle = hexA(st.color, danger ? 0.28 : 0.15); ctx.fill();
    ctx.lineWidth = 1.5; ctx.strokeStyle = hexA(st.color, 0.9); ctx.stroke();
  }
  ctx.save();
  ctx.lineWidth = 2.6; ctx.strokeStyle = st.color; ctx.shadowColor = st.color; ctx.shadowBlur = danger ? 14 : 8;
  corners(sx, sy, ex, ey, L);
  ctx.restore();

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

  const vw = el.view.clientWidth, vh = el.view.clientHeight;
  ctx.font = `600 12.5px ${FONT}`;
  const th = 26, padL = 10, ringW = 20, tw = ctx.measureText(label).width + padL + ringW + 10;
  const x = clamp(sx, 2, Math.max(2, vw - tw - 2));
  let y = sy - th - 6; if (y < 2) y = Math.min(vh - th - 2, ey + 6);
  roundRect(x, y, tw, th, 8); ctx.fillStyle = "rgba(7,10,20,.84)"; ctx.fill();
  ctx.lineWidth = 1; ctx.strokeStyle = hexA(st.color, 0.7); ctx.stroke();
  const cx = x + padL + 6, cy = y + th / 2;
  if (frac === null) { ctx.beginPath(); ctx.arc(cx, cy, 3.6, 0, 7); ctx.fillStyle = st.color; ctx.fill(); }
  else {
    ctx.lineWidth = 2.6;
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
      if (p.sidewalk.length) {
        tracePolys(p.sidewalk, r);
        ctx.fillStyle = "rgba(123,140,255,.1)"; ctx.fill("evenodd");
        ctx.lineWidth = 1.4; ctx.strokeStyle = "rgba(160,175,255,.6)";
        if (!REDUCED) { ctx.setLineDash([8, 6]); ctx.lineDashOffset = -((now / 60) % 14); }
        ctx.stroke(); ctx.setLineDash([]);
      }
      const order = (v) => (v.status === "DI_JALAN" ? 0 : v.status === "PARKIR_TERKONFIRMASI" ? 2 : 1);
      [...p.vehicles].sort((a, b) => order(a) - order(b)).forEach((v) => drawVehicle(v, r, now, dt));
      for (const [id, s] of S.vis) if (s.f !== S.frameNo) S.vis.delete(id);
    } else if (S.polys.length) {
      drawShapes(S.polys, r, false);
    }
  }
  requestAnimationFrame(frame);
}

/* ------------------------------------------------------------------ menggambar area trotoar */
function toggleDraw() {
  if (S.drawing) { cancelDraw(); return; }
  if (!mediaRect() || !hasPicture()) {
    notify("Nyalakan kamera atau buka video dulu supaya ada gambar yang bisa digambari.", true);
    return;
  }
  hideSheet();
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
    if (Math.hypot((f[0] - nx) * r.w, (f[1] - ny) * r.h) < 14) { closeShape(); return; }
  }
  S.draft.push([nx, ny]);
});

/* ------------------------------------------------------------------ foto, layar penuh, pasang aplikasi */
function snapshot() {
  const r = mediaRect();
  if (!r || !hasPicture()) { notify("Belum ada gambar untuk difoto.", true); return; }
  const src = S.kind === "stream" ? el.stream : el.video, dpr = window.devicePixelRatio || 1;
  const c = document.createElement("canvas");
  c.width = Math.round(el.view.clientWidth * dpr); c.height = Math.round(el.view.clientHeight * dpr);
  try {
    const g = c.getContext("2d");
    g.fillStyle = "#000"; g.fillRect(0, 0, c.width, c.height);
    g.drawImage(src, r.x * dpr, r.y * dpr, r.w * dpr, r.h * dpr);
    g.drawImage(el.canvas, 0, 0, c.width, c.height);
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
  if (!document.fullscreenElement && !ask) { notify("Browser ini tidak mendukung layar penuh. Pasang sebagai aplikasi untuk tampilan penuh.", true); return; }
  const p = document.fullscreenElement ? document.exitFullscreen() : ask.call(el.stage);
  if (p && p.catch) p.catch(() => notify("Browser menolak layar penuh. Coba lagi.", true));
}

let installEvent = null;
const standalone = () => window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
function refreshInstall() {
  el.btnInstall.hidden = !installEvent || standalone();
  const ios = /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  el.installHint.hidden = !(ios && !standalone());
  if (!el.installHint.hidden) el.installHint.textContent = "Di iPhone: ketuk tombol Bagikan di Safari, lalu pilih Tambah ke Layar Utama.";
}
window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); installEvent = e; refreshInstall(); });
window.addEventListener("appinstalled", () => { installEvent = null; refreshInstall(); notify("Aplikasi terpasang."); });
el.btnInstall.addEventListener("click", async () => {
  if (!installEvent) return;
  installEvent.prompt();
  try { await installEvent.userChoice; } catch (_) { /* abaikan */ }
  installEvent = null; refreshInstall();
});

/* panel muncul hanya saat diminta; antarmuka lain melebur saat memantau supaya gambar tampil penuh */
function showSheet(which) {
  hideSheet(true);
  const sh = which === "events" ? el.sheetEvents : el.sheetSettings;
  sh.hidden = false; el.scrim.hidden = false; S.sheet = sh;
  requestAnimationFrame(() => sh.classList.add("in"));
  if (which === "events") { S.seenCount = S.items.length; el.btnEvents.dataset.new = "0"; }
  el.stage.dataset.idle = "0";
  (which === "events" ? el.closeEvents : el.closeSettings).focus({ preventScroll: true });
}
function hideSheet(instant) {
  const sh = S.sheet;
  if (!sh) return;
  S.sheet = null;
  sh.classList.remove("in"); el.scrim.hidden = true;
  const done = () => { if (!sh.classList.contains("in")) sh.hidden = true; };
  if (instant) done(); else setTimeout(done, 300);
}
function toggleSheet(which) { (S.sheet === (which === "events" ? el.sheetEvents : el.sheetSettings)) ? hideSheet() : showSheet(which); }

function poke() {
  el.stage.dataset.idle = "0";
  clearTimeout(S.idleTimer);
  S.idleTimer = setTimeout(() => {
    if (S.running && !S.drawing && !S.sheet) el.stage.dataset.idle = "1";
  }, 4000);
}
["pointermove", "pointerdown", "keydown"].forEach((ev) => el.stage.addEventListener(ev, poke, { passive: true }));

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
  maybeAutoStart();
}

async function pollStatus() {
  try { applyStatus(await (await fetch("/api/status", { cache: "no-store" })).json()); } catch (_) { /* server mati sementara */ }
  setTimeout(pollStatus, 1500);
}

/* ------------------------------------------------------------------ pendengar & pintasan */
el.tabs.forEach((b) => b.addEventListener("click", () => selectKind(b.dataset.kind)));
el.camSelect.addEventListener("change", () => { el.camSelect.dataset.picked = el.camSelect.value ? "1" : "0"; restartCamera(); });
el.emptyRetry.addEventListener("click", () => { S.manual = false; recover(); });
el.btnFlip.addEventListener("click", flipCamera);
el.btnPause.addEventListener("click", toggleRun);
el.btnSettings.addEventListener("click", () => toggleSheet("settings"));
el.btnEvents.addEventListener("click", () => toggleSheet("events"));
el.closeSettings.addEventListener("click", () => hideSheet());
el.closeEvents.addEventListener("click", () => hideSheet());
el.scrim.addEventListener("click", () => hideSheet());
el.videoPick.addEventListener("click", () => el.videoFile.click());
el.videoFile.addEventListener("change", () => openVideoFile(el.videoFile.files[0]));
el.streamUrl.addEventListener("keydown", (e) => { if (e.key === "Enter") el.streamGo.click(); });
el.streamGo.addEventListener("click", () => {
  if (!el.streamUrl.value.trim()) { notify("Isi dulu indeks webcam (0) atau alamat stream.", true); return; }
  S.go = true; S.manual = false; hideSheet(); maybeAutoStart();
});
el.btnDraw.addEventListener("click", toggleDraw);
el.btnAuto.addEventListener("click", backToAuto);
el.btnShot.addEventListener("click", snapshot);
el.btnFull.addEventListener("click", toggleFull);
el.drawUndo.addEventListener("click", undoPoint);
el.drawClose.addEventListener("click", closeShape);
el.drawCancel.addEventListener("click", cancelDraw);
el.drawApply.addEventListener("click", applyDraw);
el.setDwell.addEventListener("input", () => { el.oDwell.textContent = `${el.setDwell.value} dtk`; sendSettings(); });
el.setTau.addEventListener("input", () => { el.oTau.textContent = fmt2(el.setTau.value); sendSettings(); });
el.setComp.addEventListener("change", sendSettings);
el.setAuto.addEventListener("change", () => {
  pref.auto = el.setAuto.checked; savePrefs();
  if (pref.auto && S.kind === "camera") { S.manual = false; recover(); }
  refreshUI();
});
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
el.lightbox.addEventListener("click", (e) => { if (e.target === el.lightbox) el.lightbox.close(); });
NARROW.addEventListener("change", sizeCanvas);

document.addEventListener("keydown", (e) => {
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const tag = document.activeElement.tagName;
  if (/^(INPUT|SELECT|TEXTAREA)$/.test(tag) || document.querySelector("dialog[open]")) return;
  if (e.key === "Escape" && S.sheet) { hideSheet(); return; }
  if (S.drawing) {
    if (e.key === "Enter" && tag !== "BUTTON") { closeShape(); e.preventDefault(); return; }
    if (e.key === "Backspace") { undoPoint(); e.preventDefault(); return; }
    if (e.key === "Escape") { cancelDraw(); return; }
  }
  const k = e.key.toLowerCase();
  if (k === " " && tag !== "BUTTON") { toggleRun(); e.preventDefault(); }
  else if (k === "e") toggleSheet("events");
  else if (k === "d") toggleDraw();
  else if (k === "f") toggleFull();
});

(async function init() {
  loadPrefs();
  el.setAuto.checked = pref.auto;
  el.setAlert.value = pref.alert;
  el.setNotify.checked = pref.notify && "Notification" in window && Notification.permission === "granted";
  if (!("Notification" in window)) el.setNotify.closest("label").hidden = true;
  if (!el.stage.requestFullscreen && !el.stage.webkitRequestFullscreen) el.btnFull.hidden = true;
  sizeCanvas();
  refreshUI();
  refreshInstall();
  requestAnimationFrame(frame);
  if ("serviceWorker" in navigator && window.isSecureContext) navigator.serviceWorker.register("/sw.js").catch(() => { /* aplikasi tetap jalan tanpa SW */ });
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
  // Bunyi baru boleh diputar setelah pengguna menyentuh halaman sekali; pasang pembuka itu diam-diam.
  ["pointerdown", "keydown"].forEach((ev) => window.addEventListener(ev, unlockAudio, { once: true, passive: true }));
  if (!S.running && pref.auto) { await startCamera(); maybeAutoStart(); }
})();

/* Sidewalk Sentinel: service worker minimal.
   Aplikasi butuh server deteksi yang sedang berjalan, jadi tidak ada cache berkas: halaman selalu diambil langsung
   dari server (tidak pernah memakai versi lama). Service worker ini hanya membuat aplikasi bisa dipasang dan
   memberi pesan yang jelas saat server belum dinyalakan. */
"use strict";

const OFFLINE = `<!doctype html><html lang="id"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#05070d"><title>Sidewalk Sentinel</title>
<style>
  html,body{height:100%;margin:0;background:#05070d;color:#eceeff;font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
  main{min-height:100%;display:grid;place-content:center;justify-items:center;gap:10px;padding:28px;text-align:center}
  h1{margin:0;font-size:21px;font-weight:600}p{margin:0;max-width:40ch;color:#8e97c0}
  code{color:#eceeff;background:rgba(160,175,255,.14);padding:2px 7px;border-radius:6px}
  button{margin-top:10px;height:40px;padding:0 20px;border:0;border-radius:12px;color:#fff;font:600 14px inherit;
         background:linear-gradient(135deg,#7b8cff,#a06bff)}
</style>
<main>
  <h1>Server belum menyala</h1>
  <p>Jalankan <code>python main.py</code> di komputer yang menjalankan Sidewalk Sentinel, lalu coba lagi.</p>
  <button onclick="location.reload()">Coba lagi</button>
</main></html>`;

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.mode !== "navigate") return;                       // aset, API, dan stream langsung ke jaringan
  e.respondWith(fetch(req).catch(() => new Response(OFFLINE, {
    status: 503, headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" },
  })));
});

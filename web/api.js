// Pembungkus fetch tipis. Semua pesan galat berbahasa Indonesia dan menyebut cara memperbaiki.

export class ApiError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.status = status;
  }
}

async function parse(res) {
  let data = null;
  try { data = await res.json(); } catch (e) { if (e && e.name === 'AbortError') throw e; /* bukan JSON */ }
  if (!res.ok) {
    const msg = (data && data.error) || `Permintaan gagal (kode ${res.status}).`;
    throw new ApiError(msg, res.status);
  }
  return data;
}

// Batas waktu per permintaan. Tanpa batas, satu permintaan yang tak pernah dijawab (jaringan HP goyah, tunnel tersendat)
// membekukan pendaftaran perangkat atau pengiriman gambar selamanya tanpa pesan apa pun.
export const timeouts = { call: 20000, frame: 10000, slow: 120000 };

function network(err) {
  if (err instanceof ApiError) return err;
  if (err && err.name === 'AbortError') {
    return new ApiError('Server tidak menjawab. Periksa koneksi internet HP dan pastikan tunnel atau aplikasi di laptop masih berjalan.', 0);
  }
  return new ApiError('Server tidak terjangkau. Pastikan python main.py masih berjalan dan perangkat satu jaringan dengannya.', 0);
}

async function send(url, init, ms) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), ms);
  try { return await parse(await fetch(url, { credentials: 'same-origin', ...init, signal: ctl.signal })); }
  catch (e) { throw network(e); }
  finally { clearTimeout(timer); }
}

export const getJSON = (url, ms = timeouts.call) => send(url, { cache: 'no-store' }, ms);

export const postJSON = (url, body = {}, ms = timeouts.call) => send(url, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
}, ms);

export const del = (url, ms = timeouts.call) => send(url, { method: 'DELETE' }, ms);

export const postBlob = (url, blob, type, ms = timeouts.frame) => send(url, {
  method: 'POST', headers: { 'Content-Type': type }, body: blob,
}, ms);

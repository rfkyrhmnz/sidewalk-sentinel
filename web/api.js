// Pembungkus fetch tipis. Semua pesan galat berbahasa Indonesia dan menyebut cara memperbaiki.

export class ApiError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.status = status;
  }
}

async function parse(res) {
  let data = null;
  try { data = await res.json(); } catch { /* bukan JSON */ }
  if (!res.ok) {
    const msg = (data && data.error) || `Permintaan gagal (kode ${res.status}).`;
    throw new ApiError(msg, res.status);
  }
  return data;
}

function network(err) {
  if (err instanceof ApiError) return err;
  return new ApiError('Server tidak terjangkau. Pastikan python main.py masih berjalan dan perangkat satu jaringan dengannya.', 0);
}

export async function getJSON(url) {
  try { return await parse(await fetch(url, { credentials: 'same-origin', cache: 'no-store' })); }
  catch (e) { throw network(e); }
}

export async function postJSON(url, body = {}) {
  try {
    return await parse(await fetch(url, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }));
  } catch (e) { throw network(e); }
}

export async function del(url) {
  try { return await parse(await fetch(url, { method: 'DELETE', credentials: 'same-origin' })); }
  catch (e) { throw network(e); }
}

export async function postBlob(url, blob, type) {
  try {
    return await parse(await fetch(url, {
      method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': type }, body: blob,
    }));
  } catch (e) { throw network(e); }
}

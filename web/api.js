// Pembungkus fetch/XHR tipis. Semua pesan galat berbahasa Indonesia dan menyebut cara memperbaiki.

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
  return new ApiError('Server tidak terjangkau. Pastikan python main.py serve masih berjalan dan perangkat satu jaringan.', 0);
}

export async function getJSON(url) {
  try { return await parse(await fetch(url, { credentials: 'same-origin', cache: 'no-store' })); }
  catch (e) { throw network(e); }
}

export async function postJSON(url, body = {}, method = 'POST') {
  try {
    return await parse(await fetch(url, {
      method, credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }));
  } catch (e) { throw network(e); }
}

export async function postBlob(url, blob, type) {
  try {
    return await parse(await fetch(url, {
      method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': type }, body: blob,
    }));
  } catch (e) { throw network(e); }
}

export function upload(file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/upload?name=' + encodeURIComponent(file.name || 'video.mp4'));
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
    xhr.onload = () => {
      let data = null;
      try { data = JSON.parse(xhr.responseText); } catch { /* abaikan */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new ApiError((data && data.error) || `Unggahan gagal (kode ${xhr.status}).`, xhr.status));
    };
    xhr.onerror = () => reject(new ApiError('Koneksi terputus saat mengunggah. Pastikan HP dan laptop di Wi-Fi yang sama, lalu coba lagi.', 0));
    xhr.send(file);
  });
}

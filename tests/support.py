"""Pembantu uji: klien HTTP, dan 'ponsel tiruan' yang mengirim frame seperti halaman web sungguhan.

Ponsel tiruan hanya ada di pengujian. Aplikasinya sendiri tidak punya sumber lain selain kamera perangkat."""
from __future__ import annotations

import http.client
import json
import secrets
import ssl
import time

import cv2
import numpy as np

from scenes import Renderer, _draw_vehicle, default_scene


class Client:
    def __init__(self, port: int, cookie: str | None = None, https: bool = False):
        self.port, self.cookie, self.https = port, cookie, https

    def _conn(self, timeout=60):
        if self.https:
            ctx = ssl.create_default_context()
            ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
            return http.client.HTTPSConnection("127.0.0.1", self.port, timeout=timeout, context=ctx)
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)

    def req(self, method, path, body=None, headers=None, raw=False):
        conn = self._conn()
        h = dict(headers or {})
        if self.cookie:
            h["Cookie"] = self.cookie
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        conn.request(method, path, body=body, headers=h)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        if raw:
            return resp, data
        try:
            return resp.status, json.loads(data) if data else None
        except ValueError:
            return resp.status, data

    def wait(self, pred, timeout=30.0, msg="kondisi tidak tercapai"):
        t0 = time.time()
        while time.time() - t0 < timeout:
            v = pred()
            if v:
                return v
            time.sleep(0.15)
        raise AssertionError(msg)


def new_key() -> str:
    return secrets.token_hex(12)


def jpeg(img: np.ndarray, quality: int = 80) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    assert ok
    return buf.tobytes()


class Street:
    """Pemandangan tetap: jalan, kerb, trotoar. `frame(vehicle=True)` menaruh kendaraan diam di trotoar."""

    def __init__(self, seed: int = 3):
        self.r = Renderer(default_scene(seed=seed))

    def frame(self, vehicle: bool = False) -> np.ndarray:
        img = self.r.frame(0.0, actors=False)
        if vehicle:
            _draw_vehicle(img, 300, 300, 70, 36, (200, 70, 40))
        return img


class Phone:
    """Mendaftarkan diri lalu mengirim frame JPEG, seperti halaman web di perangkat."""

    def __init__(self, client: Client, key: str | None = None, name: str = "Ponsel uji"):
        self.c, self.key, self.name = client, key or new_key(), name
        self.cam: dict | None = None
        self.state: dict | None = None

    def register(self) -> dict:
        st, d = self.c.req("POST", "/api/device", {"key": self.key, "name": self.name})
        assert st in (200, 201), d
        self.cam = d
        return d

    @property
    def id(self) -> str:
        return self.cam["id"]

    def push(self, img: np.ndarray) -> dict:
        st, d = self.c.req("POST", f"/api/cams/{self.id}/frame", jpeg(img), {"Content-Type": "image/jpeg"})
        assert st == 200, d
        self.state = d
        return d

    def stream(self, img_fn, seconds: float, fps: float = 8.0, until=None) -> None:
        """Kirim frame dari `img_fn()` selama `seconds` (atau sampai `until(state)` benar)."""
        t_end = time.time() + seconds
        while time.time() < t_end:
            t0 = time.time()
            s = self.push(img_fn())
            if until is not None and until(s):
                return
            time.sleep(max(0.0, 1.0 / fps - (time.time() - t0)))
        if until is not None:
            raise AssertionError(f"kondisi tidak tercapai dalam {seconds:.0f} dtk; keadaan terakhir: {self.state}")

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
from sidewalk_sentinel.detector import Detection


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


class ColorDetector:
    """Pengganti YOLO untuk tes tanpa torch: kotak di sekeliling gumpalan yang sangat berwarna (jenuh), bernama 'car'.

    Pemandangan uji (jalan dan trotoar abu-abu) tidak jenuh, sedangkan kendaraan ujinya berwarna, jadi kendaraan
    ditemukan dan latar tidak. Antarmukanya sama dengan VehicleDetector, termasuk status model, sehingga jalur
    kamera -> mesin -> server teruji utuh. Mutu pengenalan YOLO sungguhan tidak diuji di sini."""

    weights = "tiruan.pt"

    def __init__(self, state: str = "siap", message: str = ""):
        self.state, self.message = state, message
        self.calls = 0
        self.retried = 0

    def status(self) -> dict:
        return {"state": self.state, "message": self.message, "weights": self.weights}

    @property
    def ready(self) -> bool:
        return self.state == "siap"

    def start_loading(self) -> None:
        pass

    def retry(self) -> None:
        self.retried += 1
        if self.state == "galat":
            self.state, self.message = "siap", ""

    def detect(self, frame: np.ndarray, conf: float = 0.3, imgsz: int = 640) -> list[Detection]:
        self.calls += 1
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        m = ((hsv[..., 1] > 150) & (hsv[..., 2] > 60)).astype(np.uint8) * 255
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        out = []
        for c in cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2]:
            x, y, w, h = cv2.boundingRect(c)
            if w * h >= 200:
                out.append(Detection((float(x), float(y), float(x + w), float(y + h)), 0.9, "car", "mobil"))
        return out


class FakeSegmenter:
    """Pengganti model trotoar: selalu mengusulkan `roi` (atau tidak menemukan trotoar bila None)."""

    available = True

    def __init__(self, roi=((0.04, 0.62), (0.96, 0.62), (0.96, 0.95), (0.04, 0.95))):
        self.roi = None if roi is None else [list(p) for p in roi]
        self.calls = 0

    def mask(self, frame: np.ndarray, conf: float = 0.25, imgsz: int = 640) -> np.ndarray:
        """Mask trotoar seukuran frame (kosong bila trotoar 'tidak terlihat')."""
        self.calls += 1
        h, w = frame.shape[:2]
        m = np.zeros((h, w), np.uint8)
        if self.roi is not None:
            cv2.fillPoly(m, [np.array([[x * w, y * h] for x, y in self.roi], np.int32)], 1)
        return m.astype(bool)

    def propose(self, frame: np.ndarray, max_points: int = 10):
        self.calls += 1
        return None if self.roi is None else {"roi": self.roi, "coverage": 0.3}

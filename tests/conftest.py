"""Pengaman bersama: tes tidak boleh memuat atau mengunduh model YOLO sungguhan di komputer siapa pun.

Paket `ultralytics` dibuat tidak bisa diimpor selama tes; tes yang butuh model memakai detektor tiruan (support.py)
atau mengganti `sidewalk_sentinel.detector._load_yolo` sendiri."""
import sys

import pytest


@pytest.fixture(autouse=True)
def _tanpa_model_sungguhan(monkeypatch):
    monkeypatch.setitem(sys.modules, "ultralytics", None)

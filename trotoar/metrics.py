"""Metrik evaluasi: segmentasi (IoU, Dice), klasifikasi keputusan (confusion matrix, P/R/F1), dan kesepakatan anotator (kappa)."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


# ----------------------------------------------------------------------------- segmentasi
def iou_dice(pred: np.ndarray, gt: np.ndarray) -> tuple[float, float]:
    pred, gt = pred.astype(bool), gt.astype(bool)
    inter = int((pred & gt).sum())
    union = int((pred | gt).sum())
    tot = int(pred.sum() + gt.sum())
    iou = inter / union if union else 1.0
    dice = 2 * inter / tot if tot else 1.0
    return iou, dice


def pixel_accuracy(pred: np.ndarray, gt: np.ndarray) -> float:
    return float((pred.astype(bool) == gt.astype(bool)).mean())


def eval_seg_dirs(pred_dir, gt_dir, pred_suffix: str = "_sidewalk"):
    """Cocokkan ``<stem>{pred_suffix}.png`` (prediksi) dengan ``<stem>.png`` (ground truth biner)."""
    pred_dir, gt_dir = Path(pred_dir), Path(gt_dir)
    rows = []
    for gp in sorted(gt_dir.glob("*.png")):
        pp = pred_dir / f"{gp.stem}{pred_suffix}.png"
        if not pp.exists():
            continue
        gt = cv2.imread(str(gp), cv2.IMREAD_GRAYSCALE) > 0
        pr = cv2.imread(str(pp), cv2.IMREAD_GRAYSCALE) > 0
        if pr.shape != gt.shape:
            pr = cv2.resize(pr.astype(np.uint8), (gt.shape[1], gt.shape[0]), interpolation=cv2.INTER_NEAREST) > 0
        iou, dice = iou_dice(pr, gt)
        rows.append({"gambar": gp.stem, "iou": iou, "dice": dice, "pixel_acc": pixel_accuracy(pr, gt)})
    return rows


# ----------------------------------------------------------------------------- klasifikasi
def confusion(y_true, y_pred) -> tuple[int, int, int, int]:
    yt, yp = np.asarray(y_true, bool), np.asarray(y_pred, bool)
    return (int((yt & yp).sum()), int((~yt & yp).sum()), int((yt & ~yp).sum()), int((~yt & ~yp).sum()))


def prf(tp: int, fp: int, fn: int, tn: int) -> dict:
    def div(a, b):
        return a / b if b else float("nan")
    p, r = div(tp, tp + fp), div(tp, tp + fn)
    f1 = div(2 * p * r, p + r) if not (np.isnan(p) or np.isnan(r)) else float("nan")
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": p, "recall": r, "f1": f1,
            "accuracy": div(tp + tn, tp + fp + fn + tn), "specificity": div(tn, tn + fp)}


def cohen_kappa(a, b) -> float:
    """Kesepakatan dua anotator (label biner/kategorikal) terkoreksi peluang."""
    a, b = np.asarray(a), np.asarray(b)
    cats = np.unique(np.concatenate([a, b]))
    po = float((a == b).mean())
    pe = sum(float((a == c).mean()) * float((b == c).mean()) for c in cats)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


_TRUE = {"1", "true", "ya", "y", "parkir", "pelanggaran", "positif", "yes", "t"}
_FALSE = {"0", "false", "tidak", "n", "bukan", "negatif", "no", "f"}


def parse_label(v) -> bool:
    s = str(v).strip().lower()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    raise ValueError(f"Label tidak dikenali: {v!r} (pakai 1/0, ya/tidak, parkir/bukan)")

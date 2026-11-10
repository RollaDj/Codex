"""Step 2a — AI matting with Robust Video Matting (RVM, Apache-2.0), ONNX on CPU.

Produces, once per clip (cached, reused for every décor):
* matte.mkv : lossless alpha (FFV1 gray)
* fgr.mkv   : RVM foreground colour estimate (used only on soft edges / hair,
              never on the face interior, which keeps the original pixels)
* matte_stats.json : per-frame coverage, face-protection events (for QC)
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

from .faces import FaceTrack, face_box, face_core_mask
from .ffmpeg_utils import FrameReader, FrameWriter, ffv1_gray_args, h264_args, media_info

MODELS_DIR = Path(os.environ.get("DECORPIPE_MODELS", Path(__file__).resolve().parent.parent / "models"))
MATTING_VERSION = 4   # bump when the matte algorithm changes (invalidates cached mattes)
MODEL_FILES = {
    "rvm-resnet50": "rvm_resnet50_fp32.onnx",
    "rvm-mobilenetv3": "rvm_mobilenetv3_fp32.onnx",
}


@dataclass
class MattingConfig:
    model: str = "rvm-resnet50"
    downsample_ratio: float | None = None   # None → auto (≈512 px long side)
    warmup_frames: int = 30                 # pre-roll so recurrent state is settled at frame 0
    alpha_low: float = 0.015                # alpha levels: kills faint background haze …
    alpha_high: float = 0.985               # … and makes the body fully opaque
    protect_face: bool = True               # never let the matte eat into eyes / mouth
    keep_main_subject: bool = True          # drop disconnected false positives
    temporal_smooth: float = 0.6            # edge-only, motion-adaptive alpha smoothing (0 = off)
    keep_seat: bool = False                 # also keep the seat/chair the speaker sits in
    seat_vmax: float = 0.30                 # seat = dark pixels (HSV value below this) …
    seat_smin: float = 0.25                 # … clearly coloured (drops grey/black furniture) …
    seat_vdeep: float = 0.12                # near-black seat shadow, kept whatever its colour
    seat_below: float = 0.68                # … below this fraction of the frame height …
    seat_radius: float = 0.2                # … within this distance of the body (fraction of width)
    threads: int | None = None


def seat_mask(frame: np.ndarray, alpha: np.ndarray, cfg: MattingConfig, state: dict) -> np.ndarray:
    """Soft mask of the seat behind the speaker: dark, coloured regions under the shoulders
    that touch the body. RVM is trained on people, so it drops the chair; this adds it back.
    The seat's hue is learned on the fly (state["hue"]) so grey/black furniture or a dark
    object of another colour next to the arm is not kept."""
    h, w = alpha.shape
    q = 2
    small = cv2.resize(frame, (w // q, h // q), interpolation=cv2.INTER_AREA)
    a = cv2.resize(alpha, (w // q, h // q), interpolation=cv2.INTER_AREA)
    v = small.max(axis=2).astype(np.float32) / 255
    sat = (v - small.min(axis=2).astype(np.float32) / 255) / np.maximum(v, 1e-3)
    hue = cv2.cvtColor(small, cv2.COLOR_RGB2HSV_FULL)[..., 0].astype(np.float32) * (2 * np.pi / 256)
    body = (a > 0.5).astype(np.uint8)
    r = max(3, int(cfg.seat_radius * w / q))
    near = cv2.dilate(body, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
    cand = (v < cfg.seat_vmax) & (sat >= cfg.seat_smin) & (near > 0) & (a < 0.5)
    cand[: int(h / q * cfg.seat_below)] = False
    if cand.sum() > 50:                     # running estimate of the seat colour
        wgt = sat[cand]
        z = np.array([np.sum(wgt * np.cos(hue[cand])), np.sum(wgt * np.sin(hue[cand]))])
        z /= max(np.linalg.norm(z), 1e-6)
        prev_z = state.get("hue")
        state["hue"] = z if prev_z is None else (0.9 * prev_z + 0.1 * z) / max(np.linalg.norm(0.9 * prev_z + 0.1 * z), 1e-6)
    if state.get("hue") is not None:
        zc, zs = state["hue"]
        cand &= (np.cos(hue) * zc + np.sin(hue) * zs) > np.cos(np.radians(45))
    # The seat in deep shadow (between arm and torso) is near-black: no measurable colour.
    shadow = (v < cfg.seat_vdeep) & (near > 0) & (a < 0.5)
    shadow[: int(h / q * cfg.seat_below)] = False
    # Only broad shadow areas (the hollow of the seat), not thin dark lines of furniture.
    shadow = cv2.morphologyEx(shadow.astype(np.uint8), cv2.MORPH_OPEN,
                              cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13))).astype(bool)
    cand |= shadow
    cand = cand.astype(np.uint8)
    cand = cv2.morphologyEx(cand, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    cand = cv2.morphologyEx(cand, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    touch = cv2.dilate(body, np.ones((9, 9), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(cand, 8)
    keep = np.zeros_like(cand, dtype=np.float32)
    for lab in range(1, n):
        comp = labels == lab
        if stats[lab, cv2.CC_STAT_AREA] > 300 and (touch[comp] > 0).any():
            keep[comp] = 1.0
    keep = cv2.GaussianBlur(keep, (0, 0), 1.5)
    keep = cv2.resize(keep, (w, h), interpolation=cv2.INTER_LINEAR)
    prev = state.get("prev")
    if prev is not None:                    # steadier outline from frame to frame
        keep = 0.55 * keep + 0.45 * prev
    state["prev"] = keep
    return keep


def merge_seat(alpha: np.ndarray, seat: np.ndarray) -> np.ndarray:
    """Union of body and seat, with the thin seam between the two soft edges closed
    (otherwise a 2–3 px line of the new décor shows between arm and chair)."""
    merged = np.maximum(alpha, seat)
    near = cv2.dilate((seat > 0.1).astype(np.uint8), np.ones((15, 15), np.uint8)).astype(bool)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11))
    closed = cv2.morphologyEx(merged, cv2.MORPH_CLOSE, k)
    merged[near] = np.maximum(merged[near], closed[near])
    return merged


class RVM:
    def __init__(self, cfg: MattingConfig, width: int, height: int):
        path = MODELS_DIR / MODEL_FILES[cfg.model]
        if not path.exists():
            raise FileNotFoundError(
                f"Modèle introuvable : {path}. Lancez d'abord : bash tools/fetch_models.sh")
        so = ort.SessionOptions()
        so.intra_op_num_threads = cfg.threads or os.cpu_count() or 4
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.sess = ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])
        dr = cfg.downsample_ratio or min(512 / max(width, height), 1.0)
        self.dr = np.array([dr], np.float32)
        self.reset()

    def reset(self) -> None:
        self.rec = [np.zeros((1, 1, 1, 1), np.float32) for _ in range(4)]

    def __call__(self, rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        src = (rgb.astype(np.float32) / 255.0).transpose(2, 0, 1)[None]
        feeds = {"src": src, "r1i": self.rec[0], "r2i": self.rec[1], "r3i": self.rec[2],
                 "r4i": self.rec[3], "downsample_ratio": self.dr}
        fgr, pha, *self.rec = self.sess.run(None, feeds)
        return fgr[0].transpose(1, 2, 0), pha[0, 0]


def _keep_main(alpha: np.ndarray, box) -> np.ndarray:
    h, w = alpha.shape
    small = cv2.resize(alpha, (w // 4, h // 4), interpolation=cv2.INTER_AREA)
    n, labels, stats, _ = cv2.connectedComponentsWithStats((small > 0.5).astype(np.uint8), 8)
    if n <= 2:
        return alpha
    areas = stats[1:, cv2.CC_STAT_AREA]
    keep = set((np.where(areas >= 0.15 * areas.max())[0] + 1).tolist())
    if box is not None:
        fx, fy, fw, fh = box
        lab = labels[min(h // 4 - 1, (fy + fh // 2) // 4), min(w // 4 - 1, (fx + fw // 2) // 4)]
        if lab > 0:
            keep.add(int(lab))
    mask = np.isin(labels, list(keep)).astype(np.uint8)
    mask = cv2.dilate(mask, np.ones((5, 5), np.uint8))
    mask = cv2.resize(mask.astype(np.float32), (w, h), interpolation=cv2.INTER_LINEAR)
    return alpha * np.clip(mask, 0, 1)


def run_matting(master: str | Path, workdir: str | Path, cfg: MattingConfig | None = None,
                force: bool = False, log=print) -> dict:
    cfg = cfg or MattingConfig()
    workdir = Path(workdir)
    matte_path, fgr_path = workdir / "matte.mkv", workdir / "fgr.mkv"
    stats_path = workdir / "matte_stats.json"
    if not force and matte_path.exists() and fgr_path.exists() and stats_path.exists():
        stats = json.loads(stats_path.read_text())
        if stats.get("config") == cfg.__dict__ and stats.get("version") == MATTING_VERSION:
            log("  matte en cache réutilisé")
            return stats

    info = media_info(master)
    W, H, fps = info.width, info.height, Fraction(info.fps)
    model = RVM(cfg, W, H)

    # Pre-roll: run the first frames once so the recurrent state is converged at frame 0.
    if cfg.warmup_frames:
        for i, frame in enumerate(FrameReader(master, W, H)):
            model(frame)
            if i + 1 >= cfg.warmup_frames:
                break

    matte_w = FrameWriter(matte_path, W, H, fps, ffv1_gray_args(), gray=True)
    fgr_w = FrameWriter(fgr_path, W, H, fps, h264_args(crf=12, preset="medium", yuv444=True))
    track = FaceTrack(every=1, smooth=0.0)   # exact per-frame box: a lagging box would paint background
    coverage, repaired, face_min = [], 0, []
    prev_alpha = prev_luma = None
    seat_state: dict = {}
    t0 = time.time()
    n = 0
    for frame in FrameReader(master, W, H):
        fgr, alpha = model(frame)
        alpha = np.clip((alpha - cfg.alpha_low) / (cfg.alpha_high - cfg.alpha_low), 0, 1)
        luma = cv2.GaussianBlur(cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY).astype(np.float32), (5, 5), 0)
        if cfg.temporal_smooth > 0 and prev_alpha is not None:
            # Where the picture is static, hair/edge alpha should be static too: blend with the
            # previous matte. Any motion (|ΔY| ≥ 4) disables the blend, so nothing lags.
            still = np.clip(1 - np.abs(luma - prev_luma) / 4.0, 0, 1)
            edge = ((alpha > 0.01) & (alpha < 0.99)) | ((prev_alpha > 0.01) & (prev_alpha < 0.99))
            k = cfg.temporal_smooth * still * edge
            alpha = alpha * (1 - k) + prev_alpha * k
        prev_alpha, prev_luma = alpha, luma
        face = track.update(frame)
        box = face_box(face) if face is not None else None
        if cfg.keep_main_subject:
            alpha = _keep_main(alpha, box)
        if face is not None:
            core = face_core_mask((H, W), face, shrink=0.75)
            inside = core > 0.5
            fmin = float(alpha[inside].min()) if inside.any() else 1.0
            face_min.append(fmin)
            if cfg.protect_face:
                if fmin < 0.9:
                    repaired += 1
                alpha = np.maximum(alpha, core)
        else:
            face_min.append(None)
        if cfg.keep_seat:
            seat = seat_mask(frame, alpha, cfg, seat_state)
            alpha = merge_seat(alpha, seat)
            fgr = fgr * (1 - seat[..., None]) + (frame.astype(np.float32) / 255) * seat[..., None]
        coverage.append(float(alpha.mean()))
        matte_w.write((alpha * 255 + 0.5).astype(np.uint8))
        fgr_w.write((np.clip(fgr, 0, 1) * 255 + 0.5).astype(np.uint8))
        n += 1
        if n % 50 == 0:
            log(f"  matting : {n} images ({n / (time.time() - t0):.1f} img/s)")
    matte_w.close()
    fgr_w.close()
    elapsed = time.time() - t0
    stats = {
        "config": cfg.__dict__, "version": MATTING_VERSION, "frames": n, "seconds": round(elapsed, 1),
        "fps_processing": round(n / max(elapsed, 1e-6), 2),
        "coverage": coverage, "face_min_alpha": face_min,
        "face_repaired_frames": repaired, "face_missing_frames": sum(v is None for v in face_min),
    }
    stats_path.write_text(json.dumps(stats))
    log(f"  matting terminé : {n} images en {elapsed:.0f}s")
    return stats

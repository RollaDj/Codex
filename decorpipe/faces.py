"""Face detection with OpenCV YuNet (ONNX, 230 KB, CPU) — box + 5 landmarks.

A detection is a float vector:
[x, y, w, h, eye_r_x, eye_r_y, eye_l_x, eye_l_y, nose_x, nose_y,
 mouth_r_x, mouth_r_y, mouth_l_x, mouth_l_y, score] in full-resolution pixels.
"""
from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np

MODELS_DIR = Path(os.environ.get("DECORPIPE_MODELS", Path(__file__).resolve().parent.parent / "models"))
YUNET = MODELS_DIR / "face_detection_yunet_2023mar.onnx"
_DET: dict[tuple[int, int], cv2.FaceDetectorYN] = {}


def _detector(w: int, h: int) -> cv2.FaceDetectorYN:
    if not YUNET.exists():
        raise FileNotFoundError(f"{YUNET} manquant : lancez bash tools/fetch_models.sh")
    if (w, h) not in _DET:
        _DET[(w, h)] = cv2.FaceDetectorYN.create(str(YUNET), "", (w, h), 0.7, 0.3, 50)
    return _DET[(w, h)]


def detect_face(rgb: np.ndarray, work_width: int = 540) -> np.ndarray | None:
    """Largest face as a 15-float vector (see module doc), or None."""
    h, w = rgb.shape[:2]
    s = work_width / w
    sw, sh = work_width, int(round(h * s))
    small = cv2.resize(rgb, (sw, sh), interpolation=cv2.INTER_AREA)
    _, faces = _detector(sw, sh).detect(cv2.cvtColor(small, cv2.COLOR_RGB2BGR))
    if faces is None or len(faces) == 0:
        return None
    f = max(faces, key=lambda r: r[2] * r[3]).astype(np.float32).copy()
    f[:14] /= s
    return f


def face_box(f) -> tuple[int, int, int, int]:
    return tuple(int(v) for v in f[:4])


def mouth_box(f) -> tuple[int, int, int, int]:
    """Lips region from the two mouth-corner landmarks."""
    rx, ry, lx, ly = f[10], f[11], f[12], f[13]
    cx, cy = (rx + lx) / 2, (ry + ly) / 2
    mw = max(8.0, abs(lx - rx) * 1.7)
    mh = mw * 0.75
    return (int(cx - mw / 2), int(cy - mh * 0.45), int(mw), int(mh))


class FaceTrack:
    """Detect every `every` frames; hold and smooth the detection in between."""

    def __init__(self, every: int = 3, smooth: float = 0.5):
        self.every, self.smooth = every, smooth
        self.face: np.ndarray | None = None
        self.i = 0
        self.misses = 0

    def update(self, rgb: np.ndarray) -> np.ndarray | None:
        if self.i % self.every == 0 or self.face is None:
            found = detect_face(rgb)
            if found is not None:
                self.face = found if self.face is None else \
                    self.smooth * self.face + (1 - self.smooth) * found
            else:
                self.misses += 1
        self.i += 1
        return self.face


def face_core_mask(shape: tuple[int, int], f, shrink: float = 0.85) -> np.ndarray:
    """Soft ellipse over eyes, nose, mouth and chin — never the hair outline."""
    h, w = shape
    x, y, fw, fh = face_box(f)
    mask = np.zeros((h, w), np.float32)
    center = (int(x + fw / 2), int(y + fh * 0.56))
    axes = (max(1, int(fw * shrink / 2)), max(1, int(fh * shrink / 2)))
    cv2.ellipse(mask, center, axes, 0, 0, 360, 1.0, -1)
    k = max(3, int(fw * 0.08) | 1)
    return cv2.GaussianBlur(mask, (k, k), 0)

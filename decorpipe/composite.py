"""Step 2b — Composite the untouched subject over a décor plate.

Design rule: the subject's interior pixels (face, mouth, hands, clothes) come
straight from the source frame. Only the soft edge (hair, contour) uses the
matting foreground estimate, plus an optional, clip-constant colour harmonisation.
Frames are processed 1:1, so timing (and lip-sync) is identical to the source.
"""
from __future__ import annotations

import time
from fractions import Fraction
from pathlib import Path

import cv2
import numpy as np

from .decors import Decor
from .ffmpeg_utils import FrameReader, FrameWriter, h264_args, media_info

MARGIN = 1.12   # plate is rendered 12 % larger than the frame so camera tracking can pan it


# ---------------------------------------------------------------- plate preparation
def _srgb_to_lin(x: np.ndarray) -> np.ndarray:
    return np.power(np.clip(x, 0, 1), 2.2)


def _lin_to_srgb(x: np.ndarray) -> np.ndarray:
    return np.power(np.clip(x, 0, 1), 1 / 2.2)


def _disk_kernel(radius: float) -> np.ndarray:
    r = max(1, int(round(radius)))
    y, x = np.mgrid[-r:r + 1, -r:r + 1]
    k = ((x * x + y * y) <= r * r + 0.5).astype(np.float32)
    k = cv2.GaussianBlur(k, (3, 3), 0)
    return k / k.sum()


def cover_fit(img: np.ndarray, w: int, h: int, focus=(0.5, 0.5), zoom: float = 1.0) -> np.ndarray:
    ih, iw = img.shape[:2]
    s = max(w / iw, h / ih) * zoom
    nw, nh = int(np.ceil(iw * s)), int(np.ceil(ih * s))
    img = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LANCZOS4)
    x = int(np.clip(focus[0] * nw - w / 2, 0, nw - w))
    y = int(np.clip(focus[1] * nh - h / 2, 0, nh - h))
    return img[y:y + h, x:x + w]


def apply_look(rgb: np.ndarray, decor: Decor) -> np.ndarray:
    """rgb float32 sRGB 0..1 → graded plate (plate only, never the subject)."""
    lk = decor.look
    out = rgb * decor.exposure
    if lk.warmth:
        out = out * np.array([1 + 0.06 * lk.warmth, 1 + 0.01 * lk.warmth, 1 - 0.06 * lk.warmth],
                             np.float32)
    if lk.contrast != 1.0:
        out = (out - 0.5) * lk.contrast + 0.5
    if lk.saturation != 1.0:
        luma = out @ np.array([0.2126, 0.7152, 0.0722], np.float32)
        out = luma[..., None] + (out - luma[..., None]) * lk.saturation
    if lk.vignette:
        h, w = out.shape[:2]
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        r2 = ((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2
        out = out * (1 - lk.vignette * np.clip(r2 / 2, 0, 1))[..., None]
    return np.clip(out, 0, 1).astype(np.float32)


def prepare_plate_frame(rgb_u8: np.ndarray, decor: Decor, w: int, h: int) -> np.ndarray:
    """Cover-fit, depth-of-field bokeh (in linear light), grade. Returns float32 sRGB."""
    bw, bh = int(round(w * MARGIN / 2)) * 2, int(round(h * MARGIN / 2)) * 2
    img = cover_fit(rgb_u8, bw, bh, decor.focus, decor.plate_zoom).astype(np.float32) / 255
    if decor.blur > 0.5:
        lin = _srgb_to_lin(img)
        half = cv2.resize(lin, (bw // 2, bh // 2), interpolation=cv2.INTER_AREA)
        half = cv2.filter2D(half, -1, _disk_kernel(decor.blur * (w / 1080) / 2),
                            borderType=cv2.BORDER_REFLECT)
        lin = cv2.resize(half, (bw, bh), interpolation=cv2.INTER_CUBIC)
        img = _lin_to_srgb(lin)
    return apply_look(img, decor)


class PlateSource:
    """Still image or looping video plate, already prepared (blur + look)."""

    def __init__(self, decor: Decor, w: int, h: int):
        self.decor, self.w, self.h = decor, w, h
        if decor.is_video:
            pinfo = media_info(decor.plate)
            self.pw, self.ph = pinfo.width, pinfo.height
            self._open()
            self.still = None
        else:
            bgr = cv2.imread(str(decor.plate), cv2.IMREAD_COLOR)
            if bgr is None:
                raise FileNotFoundError(decor.plate)
            self.still = prepare_plate_frame(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), decor, w, h)

    def _open(self) -> None:
        self.it = iter(FrameReader(self.decor.plate, self.pw, self.ph))

    def next(self) -> np.ndarray:
        if self.still is not None:
            return self.still
        try:
            frame = next(self.it)
        except StopIteration:
            self._open()
            frame = next(self.it)
        return prepare_plate_frame(frame, self.decor, self.w, self.h)


# ---------------------------------------------------------------- camera tracking
def estimate_camera_motion(master: Path, matte: Path, w: int, h: int, log=print) -> list[np.ndarray]:
    """Track the ORIGINAL background (where alpha≈0) to recover handheld camera motion.

    Returns, per frame, a 2x3 similarity transform mapping frame-0 background
    coordinates to frame-t coordinates. Identity when the clip is on a tripod or
    when the original background has no texture to track.
    """
    s = 0.5
    sw, sh = int(w * s), int(h * s)
    prev = None
    acc = np.eye(3, dtype=np.float64)
    out: list[np.ndarray] = []
    ok_frames = 0
    for rgb, a in zip(FrameReader(master, sw, sh), FrameReader(matte, sw, sh, gray=True)):
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        mask = cv2.erode((a < 10).astype(np.uint8) * 255, np.ones((15, 15), np.uint8))
        if prev is not None:
            pg, pm = prev
            pts = cv2.goodFeaturesToTrack(pg, 400, 0.01, 8, mask=pm)
            m = None
            if pts is not None and len(pts) >= 20:
                nxt, st, _ = cv2.calcOpticalFlowPyrLK(pg, gray, pts, None, winSize=(21, 21))
                good = st.ravel() == 1
                if good.sum() >= 15:
                    m, inl = cv2.estimateAffinePartial2D(pts[good], nxt[good],
                                                         method=cv2.RANSAC, ransacReprojThreshold=1.5)
                    if m is not None and inl is not None and inl.sum() < 12:
                        m = None
            if m is not None:
                ok_frames += 1
                m3 = np.vstack([m, [0, 0, 1]])
                m3[0, 2] /= s
                m3[1, 2] /= s
                acc = m3 @ acc
        out.append(acc[:2].copy())
        prev = (gray, mask)
    # Smooth (moving average) to remove tracking jitter.
    arr = np.array(out)
    k = 5
    if len(arr) > k:
        pad = np.pad(arr, ((k // 2, k // 2), (0, 0), (0, 0)), mode="edge")
        arr = np.stack([pad[i:i + k].mean(0) for i in range(len(out))])
    shift = np.abs(arr[:, :, 2]).max() if len(arr) else 0
    log(f"  suivi caméra : {ok_frames}/{max(len(out) - 1, 1)} images suivies, "
        f"déplacement max {shift:.1f}px")
    return list(arr)


# ---------------------------------------------------------------- per-clip analysis
def estimate_grain(src: np.ndarray, alpha: np.ndarray) -> float:
    """Sensor-noise sigma (0..255) of the footage, measured on flat, non-clipped areas
    (background and subject: a blown-out window has no grain to measure)."""
    g = cv2.cvtColor(src, cv2.COLOR_RGB2GRAY).astype(np.float32)
    resid = g - cv2.GaussianBlur(g, (0, 0), 1.2)
    grad = cv2.magnitude(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1))
    usable = (g > 20) & (g < 235)
    if usable.sum() < 5000:
        return 1.5
    flat = usable & (grad < np.percentile(grad[usable], 25))
    if flat.sum() < 1000:
        return 1.5
    return float(np.clip(resid[flat].std() * 1.15, 0.6, 4.0))


def subject_tone(decor: Decor) -> tuple[np.ndarray, float] | None:
    """Clip-constant RGB gain + contrast for the subject, or None if neutral."""
    if not (decor.subject_exposure or decor.subject_warmth or decor.subject_contrast != 1.0):
        return None
    gain = 2.0 ** decor.subject_exposure
    w = decor.subject_warmth
    rgb = np.array([1 + 0.10 * w, 1 + 0.015 * w, 1 - 0.10 * w], np.float32) * gain
    return rgb, decor.subject_contrast


def falloff_map(w: int, h: int, face_center: tuple[float, float] | None, amount: float) -> np.ndarray:
    """Soft key-light falloff centred on the face: 1 at the face, darker below / to the sides."""
    if amount <= 0:
        return np.ones((h, w), np.float32)
    cx, cy = face_center or (w / 2, h * 0.3)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.sqrt(((xx - cx) / (w * 0.75)) ** 2 + (np.maximum(yy - cy, 0) / (h * 0.75)) ** 2)
    return (1 - amount * np.clip(d, 0, 1) ** 1.5).astype(np.float32)


def harmonize_gains(samples: list[tuple[np.ndarray, np.ndarray]], plate: np.ndarray,
                    strength: float) -> np.ndarray:
    """Clip-constant RGB gains that move the subject from the ORIGINAL room's light
    toward the décor's light (grey-world estimate of each illuminant).

    Gains are capped at ±6 %, i.e. a white-balance / exposure trim — never a change
    of the subject's features. strength=0 (strict mode) returns exact identity.
    """
    if strength <= 0:
        return np.ones(3, np.float32)
    src_ill = np.mean([s.reshape(-1, 3).mean(0) for s, _ in samples], axis=0) / 255
    pl_ill = plate.reshape(-1, 3).mean(0)
    chroma = (pl_ill / pl_ill.mean()) / (src_ill / max(src_ill.mean(), 1e-3))
    expo = np.clip(pl_ill.mean() / max(src_ill.mean(), 1e-3), 0.7, 1.3) ** 0.5
    gains = np.power(chroma * expo, strength)
    return np.clip(gains, 0.94, 1.06).astype(np.float32)


# ---------------------------------------------------------------- main compositor
def _smoothstep(e0: float, e1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def composite_clip(master: Path, workdir: Path, decor: Decor, out_video: Path,
                   strict: bool = False, log=print) -> dict:
    info = media_info(master)
    W, H, fps = info.width, info.height, Fraction(info.fps)
    matte, fgr_path = workdir / "matte.mkv", workdir / "fgr.mkv"
    plate_src = PlateSource(decor, W, H)
    first_plate = plate_src.still if plate_src.still is not None else plate_src.next()

    # Per-clip constants (computed once → no flicker).
    samples = []
    for i, (src, a) in enumerate(zip(FrameReader(master, W, H), FrameReader(matte, W, H, gray=True))):
        if i % 25 == 0:
            samples.append((src.copy(), a.copy()))
        if len(samples) >= 8:
            break
    harmonize = 0.0 if strict else decor.harmonize
    bw, bh = first_plate.shape[1], first_plate.shape[0]
    crop0 = first_plate[(bh - H) // 2:(bh - H) // 2 + H, (bw - W) // 2:(bw - W) // 2 + W]
    gains = harmonize_gains(samples, crop0, harmonize)
    sigma = estimate_grain(*samples[0]) if decor.grain == "auto" else float(decor.grain)
    motion = (estimate_camera_motion(master, matte, W, H, log)
              if decor.track_camera else None)
    log(f"  harmonisation RVB {np.round(gains, 3).tolist()} · grain σ={sigma:.2f}")
    tone = None if strict else subject_tone(decor)
    face_c = None
    if not strict and decor.subject_falloff > 0:
        from .faces import detect_face, face_box
        cs = [face_box(f) for f in (detect_face(s_) for s_, _ in samples) if f is not None]
        if cs:
            face_c = (float(np.median([x + w_ / 2 for x, _, w_, _ in cs])),
                      float(np.median([y + h_ / 2 for _, y, _, h_ in cs])))
    fall = (falloff_map(W, H, face_c, decor.subject_falloff)[..., None]
            if not strict and decor.subject_falloff > 0 else None)
    rim = 0.0 if strict else decor.rim_suppress

    ox, oy = (bw - W) / 2, (bh - H) / 2
    choke = (cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * decor.edge_choke + 1,) * 2)
             if decor.edge_choke > 0 else None)
    rng = np.random.default_rng(1234)
    lw, wr = decor.light_wrap, decor.wrap_radius * (W / 1080)
    q = 4   # light-wrap / shadow computed at quarter resolution
    qs = (W // q, H // q)
    writer = FrameWriter(out_video, W, H, fps, h264_args(crf=16, preset="slow"))
    t0, n = time.time(), 0
    readers = zip(FrameReader(master, W, H), FrameReader(fgr_path, W, H),
                  FrameReader(matte, W, H, gray=True))
    for i, (src_u8, fgr_u8, a_u8) in enumerate(readers):
        a = a_u8.astype(np.float32) / 255
        if choke is not None:                          # trims the 1-px halo of the old room
            a = np.minimum(a, cv2.erode(a, choke))
        plate = plate_src.next() if plate_src.still is None else plate_src.still
        if motion is not None:
            A = np.vstack([motion[i], [0, 0, 1]])
            M = np.array([[1, 0, ox], [0, 1, oy], [0, 0, 1]]) @ np.linalg.inv(A)
            bg = cv2.warpAffine(plate, M[:2], (W, H), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                                borderMode=cv2.BORDER_REFLECT)
        else:
            bg = plate[int(oy):int(oy) + H, int(ox):int(ox) + W]

        a_small = cv2.resize(a, qs, interpolation=cv2.INTER_AREA)
        if decor.ambient_shadow > 0:
            dx, dy = decor.shadow_offset
            Ms = np.float32([[1, 0, dx * qs[0]], [0, 1, dy * qs[1]]])
            sh = cv2.warpAffine(a_small, Ms, qs)
            sh = cv2.GaussianBlur(sh, (0, 0), 30 / q)
            bg = bg * (1 - decor.ambient_shadow * cv2.resize(sh, (W, H)))[..., None]
        if sigma > 0:
            noise = rng.standard_normal((H, W), dtype=np.float32) * (sigma / 255)
            bg = bg + noise[..., None]

        src = src_u8.astype(np.float32) / 255
        fgr = fgr_u8.astype(np.float32) / 255
        w_src = _smoothstep(0.80, 0.97, a)[..., None]
        fg = src * w_src + fgr * (1 - w_src)          # original pixels wherever opaque
        if harmonize > 0:
            fg = fg * gains
        if tone is not None:                          # global tone only: same pixels, new light
            fg = fg * tone[0]
            if tone[1] != 1.0:
                fg = (fg - 0.45) * tone[1] + 0.45
        if fall is not None:
            fg = fg * fall
        if rim > 0:                                   # the old room's backlight rim
            inner = a_small * (1 - cv2.GaussianBlur(a_small, (0, 0), 10 / q))
            inner = cv2.resize(np.clip(inner * 2.5, 0, 1), (W, H))[..., None]
            fg = fg * (1 - rim * 0.55 * inner)
        fg = np.clip(fg, 0, 1)
        a3 = a[..., None]
        out = fg * a3 + bg * (1 - a3)

        if lw > 0:
            inv = 1 - a_small
            bg_small = cv2.resize(bg, qs, interpolation=cv2.INTER_AREA)
            num = cv2.GaussianBlur(bg_small * inv[..., None], (0, 0), wr / q)
            den = cv2.GaussianBlur(inv, (0, 0), wr / q)[..., None] + 1e-4
            wrap = cv2.resize(num / den, (W, H))
            edge = a_small * (1 - cv2.GaussianBlur(a_small, (0, 0), wr / q))
            edge = cv2.resize(np.clip(edge * 2.0, 0, 1), (W, H))
            if strict:                              # keep the opaque interior bit-exact
                edge = edge * (a < 0.97)
            k = (lw * edge)[..., None]
            out = out * (1 - k) + wrap * k

        writer.write(np.clip(out * 255 + 0.5, 0, 255).astype(np.uint8))
        n += 1
        if n % 50 == 0:
            log(f"  composition « {decor.id} » : {n} images ({n / (time.time() - t0):.1f} img/s)")
    writer.close()
    return {"frames": n, "seconds": round(time.time() - t0, 1),
            "harmonize_gains": np.round(gains, 4).tolist(), "grain_sigma": round(sigma, 3),
            "camera_tracking": motion is not None,
            "camera_max_shift_px": float(np.abs(np.array(motion)[:, :, 2]).max()) if motion else 0.0,
            "strict": strict, "subject_tone": None if tone is None else
            {"gain_rgb": np.round(tone[0], 3).tolist(), "contrast": tone[1]},
            "subject_falloff": 0.0 if fall is None else decor.subject_falloff, "rim_suppress": rim}

"""Step 1 — Import: normalise an iPhone clip into a clean 1080x1920 working master.

* applies the iPhone rotation, tone-maps HDR (HLG / Dolby Vision) to SDR BT.709,
* crops to 9:16 around the face when the clip is landscape,
* converts variable frame rate to constant frame rate (timestamps kept, so sync is kept),
* keeps the original audio: stream copy (bit-exact) when the clip is not trimmed,
  sample-accurate PCM otherwise.
"""
from __future__ import annotations

import subprocess
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path

import numpy as np

from .faces import detect_face, face_box
from .ffmpeg_utils import BT709_TAGS, FFMPEG, SWS_FLAGS, MediaInfo, media_info, run

OUT_W, OUT_H = 1080, 1920


@dataclass
class IngestResult:
    master: Path
    info_src: dict
    fps: str
    crop: tuple[int, int, int, int]
    audio_mode: str          # "copy" (bit-exact) | "pcm" (sample-accurate) | "none"
    hdr_tonemapped: bool
    start: float
    duration: float | None


def _grab(path: Path, t: float, vf: str, w: int, h: int) -> np.ndarray | None:
    proc = subprocess.run(
        [FFMPEG, "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
         "-vf", f"{vf}scale={w}:{h},format=rgb24", "-f", "rawvideo", "-"],
        capture_output=True)
    if proc.returncode != 0 or len(proc.stdout) < w * h * 3:
        return None
    return np.frombuffer(proc.stdout, np.uint8)[: w * h * 3].reshape(h, w, 3)


def target_fps(src: Fraction, keep_high_fps: bool) -> Fraction:
    f = float(src)
    for std in (Fraction(24000, 1001), Fraction(24), Fraction(25),
                Fraction(30000, 1001), Fraction(30)):
        if abs(f - float(std)) < 0.6:
            return std
    if f > 31 and not keep_high_fps:
        return Fraction(30)
    return Fraction(round(f)) if f > 0 else Fraction(30)


def hdr_chain(info: MediaInfo) -> str:
    if not info.is_hdr:
        return ""
    return ("zscale=t=linear:npl=203,format=gbrpf32le,zscale=p=bt709,"
            "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p,")


def compute_crop(info: MediaInfo, pre_vf: str, start: float) -> tuple[int, int, int, int]:
    """9:16 crop window (w, h, x, y) in display pixels, centred on the face if any."""
    W, H = info.width, info.height
    target = 9 / 16
    if abs(W / H - target) < 0.01:
        return (W, H, 0, 0)
    if W / H > target:                      # too wide → crop width around the face
        cw, ch = int(round(H * target / 2)) * 2, H
        cx = W / 2
        xs = []
        dur = max(info.duration - start, 0.5)
        for frac in (0.15, 0.5, 0.85):
            sw = 640
            sh = int(round(H * sw / W / 2)) * 2
            img = _grab(info.path, start + dur * frac, pre_vf, sw, sh)
            f = detect_face(img) if img is not None else None
            if f is not None:
                box = face_box(f)
                xs.append((box[0] + box[2] / 2) * W / sw)
        if xs:
            cx = float(np.median(xs))
        x = int(np.clip(cx - cw / 2, 0, W - cw)) // 2 * 2
        return (cw, ch, x, 0)
    cw, ch = W, int(round(W / target / 2)) * 2   # too tall → crop height (keep head room)
    y = int((H - ch) * 0.35) // 2 * 2
    return (cw, ch, 0, y)


def ingest(src: str | Path, workdir: str | Path, start: float = 0.0,
           duration: float | None = None, keep_high_fps: bool = False) -> IngestResult:
    src = Path(src)
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    info = media_info(src)
    pre = hdr_chain(info)
    crop = compute_crop(info, pre, start)
    fps = target_fps(info.fps, keep_high_fps)
    trimmed = start > 0 or duration is not None
    master = workdir / "master.mkv"

    vf = (f"{pre}crop={crop[0]}:{crop[1]}:{crop[2]}:{crop[3]},"
          f"scale={OUT_W}:{OUT_H}:out_color_matrix=bt709:out_range=tv:"
          f"{SWS_FLAGS.replace('bicubic', 'lanczos')},format=yuv420p,setsar=1")
    cmd: list[str] = [FFMPEG, "-v", "error", "-y"]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(src)]
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-map", "0:v:0", "-vf", vf, "-fps_mode", "cfr",
            "-r", f"{fps.numerator}/{fps.denominator}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "10", "-pix_fmt", "yuv420p",
            *BT709_TAGS]
    if info.has_audio:
        cmd += ["-map", "0:a:0"]
        if not trimmed and info.acodec in ("aac", "alac", "pcm_s16le", "pcm_s24le"):
            cmd += ["-c:a", "copy"]
            audio_mode = "copy"
        else:
            cmd += ["-c:a", "pcm_s24le"]
            audio_mode = "pcm"
    else:
        audio_mode = "none"
    cmd += [str(master)]
    run(cmd)

    res = IngestResult(master=master, info_src={k: str(v) for k, v in asdict(info).items()},
                       fps=f"{fps.numerator}/{fps.denominator}", crop=crop,
                       audio_mode=audio_mode, hdr_tonemapped=bool(pre),
                       start=start, duration=duration)
    return res

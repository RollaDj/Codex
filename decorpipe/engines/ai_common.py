"""Shared helpers for cloud video-to-video engines (Runway, Kling).

The cloud model regenerates the WHOLE frame, face included. To honour the brief
(face, lips, gestures and outfit unchanged), its output is only used as a
background: the original subject is re-composited on top with the local matte
(`protect_subject=True`, default). The original audio is always re-attached.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import time
import urllib.error
import urllib.request
import uuid
from fractions import Fraction
from pathlib import Path

import cv2
import numpy as np

from ..ffmpeg_utils import FFMPEG, FrameReader, FrameWriter, h264_args, media_info, run


def http_json(method: str, url: str, headers: dict, body: dict | None = None,
              timeout: int = 120) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={**headers, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} sur {url} : {e.read().decode(errors='replace')[:800]}")


def http_multipart(url: str, fields: dict, file_path: Path, file_field: str = "file") -> None:
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    ctype = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; "
                 f"filename=\"{file_path.name}\"\r\nContent-Type: {ctype}\r\n\r\n".encode())
    parts.append(file_path.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    req = urllib.request.Request(url, data=b"".join(parts), method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=600) as r:
        r.read()


def download(url: str, dest: Path) -> Path:
    with urllib.request.urlopen(url, timeout=600) as r, open(dest, "wb") as f:
        f.write(r.read())
    return dest


def data_uri(path: Path) -> str:
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{ctype};base64,{base64.b64encode(path.read_bytes()).decode()}"


def poll(fn, interval: float = 6.0, timeout: float = 1800, log=print):
    t0 = time.time()
    while True:
        done, value, status = fn()
        if done:
            return value
        if time.time() - t0 > timeout:
            raise TimeoutError("Tâche distante trop longue")
        log(f"  … {status} ({time.time() - t0:.0f}s)")
        time.sleep(interval + np.random.uniform(0, 1.5))


def make_ai_input(master: Path, workdir: Path, width: int = 720, height: int = 1280) -> Path:
    """Silent H.264 proxy sent to the cloud (audio never leaves; it is re-attached locally)."""
    out = workdir / f"ai_input_{width}x{height}.mp4"
    if not out.exists():
        run([FFMPEG, "-v", "error", "-y", "-i", str(master), "-an",
             "-vf", f"scale={width}:{height}:flags=lanczos,format=yuv420p",
             "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-movflags", "+faststart", str(out)])
    return out


def plate_reference(decor_plate: Path, workdir: Path) -> Path:
    """JPEG of the décor plate (≤ 1080x1920) used as visual reference by the cloud model."""
    out = workdir / f"ref_{decor_plate.parent.name}.jpg"
    img = cv2.imread(str(decor_plate))
    h, w = img.shape[:2]
    s = min(1.0, 1920 / max(h, w))
    img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(out), img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return out


def conform_ai_output(ai_video: Path, master: Path, workdir: Path, out_video: Path,
                      protect_subject: bool = True, log=print) -> dict:
    """Resample the AI clip onto the master's exact frame grid, then (by default)
    put the ORIGINAL subject back on top using the local matte."""
    m = media_info(master)
    W, H, fps = m.width, m.height, Fraction(m.fps)
    ai = media_info(ai_video)
    ratio = (m.duration / ai.duration) if ai.duration else 1.0
    if abs(ratio - 1) > 0.03:
        log(f"  ⚠ durée IA {ai.duration:.2f}s ≠ source {m.duration:.2f}s : recalage temporel ×{ratio:.3f}")
    vf = (f"setpts=PTS*{ratio:.6f},fps={fps.numerator}/{fps.denominator},"
          f"tpad=stop_mode=clone:stop_duration=2")
    writer = FrameWriter(out_video, W, H, fps, h264_args(crf=16, preset="slow"))
    readers = zip(FrameReader(master, W, H), FrameReader(ai_video, W, H, extra_vf=vf),
                  FrameReader(workdir / "matte.mkv", W, H, gray=True),
                  FrameReader(workdir / "fgr.mkv", W, H))
    n = 0
    for src, aif, al, fgr in readers:
        if protect_subject:
            a = (al.astype(np.float32) / 255)[..., None]
            t = np.clip((a - 0.80) / 0.17, 0, 1)
            w_src = t * t * (3 - 2 * t)
            fg = src * w_src + fgr * (1 - w_src)
            out = fg * a + aif.astype(np.float32) * (1 - a)
            writer.write(np.clip(out + 0.5, 0, 255).astype(np.uint8))
        else:
            writer.write(aif)
        n += 1
    writer.close()
    return {"frames": n, "time_ratio": round(ratio, 4), "protect_subject": protect_subject,
            "ai_resolution": f"{ai.width}x{ai.height}", "ai_fps": str(ai.fps)}

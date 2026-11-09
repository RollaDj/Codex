"""Thin wrappers around ffmpeg / ffprobe: probing, raw frame pipes."""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Iterator

import numpy as np

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"

# Every intermediate and final file is tagged and converted explicitly as
# BT.709 limited range, so RGB <-> YUV round trips never shift skin tones.
BT709_TAGS = ["-colorspace", "bt709", "-color_primaries", "bt709",
              "-color_trc", "bt709", "-color_range", "tv"]
SWS_FLAGS = "flags=bicubic+accurate_rnd+full_chroma_int"


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    proc = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Commande échouée (code {proc.returncode}) : {' '.join(map(str, cmd))}\n"
            f"{proc.stderr[-3000:]}")
    return proc


def probe(path: str | Path) -> dict:
    out = run([FFPROBE, "-v", "error", "-print_format", "json",
               "-show_format", "-show_streams", str(path)]).stdout
    return json.loads(out)


@dataclass
class MediaInfo:
    path: Path
    width: int            # display width (after rotation)
    height: int           # display height (after rotation)
    fps: Fraction
    duration: float
    nb_frames: int | None
    vcodec: str
    pix_fmt: str
    color_transfer: str
    color_primaries: str
    rotation: int
    v_start: float
    has_audio: bool
    acodec: str | None
    a_rate: int | None
    a_channels: int | None
    a_start: float | None

    @property
    def is_hdr(self) -> bool:
        return self.color_transfer in ("arib-std-b67", "smpte2084") or \
            self.color_primaries == "bt2020"


def _rotation(stream: dict) -> int:
    for sd in stream.get("side_data_list", []) or []:
        if "rotation" in sd:
            return int(round(float(sd["rotation"])))
    rot = stream.get("tags", {}).get("rotate")
    return int(rot) if rot else 0


def media_info(path: str | Path) -> MediaInfo:
    data = probe(path)
    v = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    if v is None:
        raise ValueError(f"Aucun flux vidéo dans {path}")
    a = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    rot = _rotation(v)
    w, h = int(v["width"]), int(v["height"])
    if abs(rot) % 180 == 90:
        w, h = h, w
    rate = v.get("avg_frame_rate") or v.get("r_frame_rate") or "30/1"
    fps = Fraction(rate) if rate != "0/0" else Fraction(v.get("r_frame_rate", "30/1"))
    duration = float(v.get("duration") or data["format"].get("duration") or 0)
    nb = v.get("nb_frames")
    return MediaInfo(
        path=Path(path), width=w, height=h, fps=fps, duration=duration,
        nb_frames=int(nb) if nb and nb.isdigit() else None,
        vcodec=v.get("codec_name", "?"), pix_fmt=v.get("pix_fmt", "?"),
        color_transfer=v.get("color_transfer", "unknown"),
        color_primaries=v.get("color_primaries", "unknown"),
        rotation=rot, v_start=float(v.get("start_time") or 0.0),
        has_audio=a is not None,
        acodec=a.get("codec_name") if a else None,
        a_rate=int(a["sample_rate"]) if a and a.get("sample_rate") else None,
        a_channels=int(a["channels"]) if a and a.get("channels") else None,
        a_start=float(a.get("start_time") or 0.0) if a else None,
    )


def count_frames(path: str | Path) -> int:
    out = run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-count_packets",
               "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(path)]).stdout
    return int(out.strip().split(",")[0])


class FrameReader:
    """Decode a video into numpy frames (RGB uint8 HxWx3, or gray HxW)."""

    def __init__(self, path: str | Path, width: int, height: int, gray: bool = False,
                 extra_vf: str | None = None):
        self.w, self.h, self.gray = width, height, gray
        pix = "gray" if gray else "rgb24"
        vf = f"scale={width}:{height}:in_color_matrix=bt709:in_range=tv:{SWS_FLAGS},format={pix}"
        if gray:
            vf = f"scale={width}:{height}:{SWS_FLAGS},format=gray"
        if extra_vf:
            vf = f"{extra_vf},{vf}"
        self.proc = subprocess.Popen(
            [FFMPEG, "-v", "error", "-i", str(path), "-vf", vf, "-fps_mode", "passthrough",
             "-f", "rawvideo", "-pix_fmt", pix, "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=10 ** 8)
        self.frame_bytes = width * height * (1 if gray else 3)

    def __iter__(self) -> Iterator[np.ndarray]:
        assert self.proc.stdout is not None
        while True:
            buf = self.proc.stdout.read(self.frame_bytes)
            if len(buf) < self.frame_bytes:
                break
            arr = np.frombuffer(buf, np.uint8)
            yield arr.reshape(self.h, self.w) if self.gray else arr.reshape(self.h, self.w, 3)
        self.close()

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait()


class FrameWriter:
    """Encode numpy frames to a video file through an ffmpeg pipe."""

    def __init__(self, path: str | Path, width: int, height: int, fps: Fraction,
                 encoder_args: list[str], gray: bool = False):
        pix = "gray" if gray else "rgb24"
        cmd = [FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", pix,
               "-s", f"{width}x{height}", "-r", f"{fps.numerator}/{fps.denominator}",
               "-i", "-", *encoder_args, str(path)]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        self.path = Path(path)

    def write(self, frame: np.ndarray) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(np.ascontiguousarray(frame, dtype=np.uint8).tobytes())

    def close(self) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.close()
        err = self.proc.stderr.read().decode(errors="replace") if self.proc.stderr else ""
        if self.proc.wait() != 0:
            raise RuntimeError(f"Encodage échoué pour {self.path}:\n{err[-2000:]}")


def h264_args(crf: int = 17, preset: str = "slow", yuv444: bool = False) -> list[str]:
    pix = "yuv444p" if yuv444 else "yuv420p"
    prof = ["-profile:v", "high444"] if yuv444 else ["-profile:v", "high", "-level:v", "4.1"]
    return ["-vf", f"scale=out_color_matrix=bt709:out_range=tv:{SWS_FLAGS},format={pix}",
            "-c:v", "libx264", "-preset", preset, "-crf", str(crf), *prof,
            "-pix_fmt", pix, *BT709_TAGS]


def ffv1_gray_args() -> list[str]:
    return ["-c:v", "ffv1", "-level", "3", "-pix_fmt", "gray"]


def read_audio_mono(path: str | Path, rate: int = 48000, start: float = 0.0) -> np.ndarray:
    """Decode the first audio stream to mono float32 (used by QC)."""
    proc = subprocess.run(
        [FFMPEG, "-v", "error", "-ss", f"{start}", "-i", str(path), "-map", "0:a:0",
         "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"],
        capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode(errors="replace")[-1500:])
    return np.frombuffer(proc.stdout, np.float32)


def audio_pcm_md5(path: str | Path) -> str:
    out = run([FFMPEG, "-v", "error", "-i", str(path), "-map", "0:a:0",
               "-f", "md5", "-"]).stdout
    return out.strip().split("=")[-1]

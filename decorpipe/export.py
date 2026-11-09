"""Step 4 — Export: mux the composited picture with the ORIGINAL audio into an
Instagram-ready vertical MP4 (1080x1920, H.264 High, AAC, faststart)."""
from __future__ import annotations

from pathlib import Path

from .ffmpeg_utils import FFMPEG, media_info, run


def export_instagram(video: Path, master: Path, out: Path, audio_mode: str) -> Path:
    """`video` is already encoded at final quality; it is stream-copied (no 2nd encode).

    audio_mode "copy": the iPhone AAC track is copied bit-for-bit.
    audio_mode "pcm" : the trimmed, sample-accurate PCM is encoded once to AAC 320 kb/s.
    The original audio/video start offset of the master is re-applied, so lip-sync
    is exactly the source's.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    m = media_info(master)
    cmd = [FFMPEG, "-v", "error", "-y", "-i", str(video)]
    if m.has_audio:
        offset = (m.a_start or 0.0) - m.v_start
        if abs(offset) > 1e-4:
            cmd += ["-itsoffset", f"{offset:.6f}"]
        cmd += ["-i", str(master), "-map", "0:v:0", "-map", "1:a:0"]
    else:
        cmd += ["-map", "0:v:0"]
    cmd += ["-c:v", "copy", "-tag:v", "avc1"]
    if m.has_audio:
        if audio_mode == "copy" and m.acodec == "aac":
            cmd += ["-c:a", "copy"]
        else:
            cmd += ["-c:a", "aac", "-b:a", "320k", "-ar", str(m.a_rate or 48000)]
    cmd += ["-movflags", "+faststart", "-metadata", "comment=decorpipe: décor remplacé, "
            "sujet et audio d'origine conservés", str(out)]
    run(cmd)
    return out


def side_by_side(videos: list[Path], out: Path, height: int = 960) -> Path:
    """Mosaic (one row, labelled) for reviewing several décors against the source.
    Audio comes from the first video."""
    if not 2 <= len(videos) <= 6:
        raise ValueError("2 à 6 vidéos")
    out.parent.mkdir(parents=True, exist_ok=True)
    w = height * 9 // 16 // 2 * 2
    cmd = [FFMPEG, "-v", "error", "-y"]
    for v in videos:
        cmd += ["-i", str(v)]
    parts = []
    for i, v in enumerate(videos):
        bits = v.stem.split("_")
        if bits[-1] in ("local", "runway", "kling") and len(bits) > 1:
            label = bits[-2] + ("" if bits[-1] == "local" else f" {bits[-1]}")
        elif bits[-1] == "strict" and len(bits) > 2:
            label = f"{bits[-3]} strict"
        else:
            label = "source"
        label = label.replace(":", " ").replace("'", " ")
        parts.append(f"[{i}:v]scale={w}:{height},setsar=1,drawtext=text='{label}':x=12:y=12:"
                     f"fontsize=26:fontcolor=white:box=1:boxcolor=black@0.45:boxborderw=8[v{i}]")
    stack = "".join(f"[v{i}]" for i in range(len(videos)))
    fc = ";".join(parts) + f";{stack}hstack=inputs={len(videos)}:shortest=1[out]"
    cmd += ["-filter_complex", fc, "-map", "[out]", "-map", "0:a:0?", "-c:v", "libx264",
            "-crf", "20", "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", str(out)]
    run(cmd)
    return out

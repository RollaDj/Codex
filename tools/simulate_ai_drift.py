"""Check the safety net used for cloud engines, without spending anything.

Builds a fake "AI output" from a processed clip: 720x1280 like Runway/Kling, the
picture delayed by 2 frames and the mouth region re-drawn (blurred). That is the
kind of drift a regenerating model can introduce. Then it runs the real
post-processing twice:
  * protect_subject=False → QC must FAIL (lip-sync / face)
  * protect_subject=True  → QC must PASS (original subject put back on top)

    python tools/simulate_ai_drift.py work/<clip_dir>
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from decorpipe.engines.ai_common import conform_ai_output  # noqa: E402
from decorpipe.export import export_instagram  # noqa: E402
from decorpipe.faces import FaceTrack, mouth_box  # noqa: E402
from decorpipe.ffmpeg_utils import FrameReader, FrameWriter, h264_args, media_info  # noqa: E402
from decorpipe.qc import run_qc  # noqa: E402


def fake_ai(master: Path, out: Path, delay: int = 2) -> None:
    m = media_info(master)
    w = FrameWriter(out, 720, 1280, m.fps, h264_args(crf=18, preset="fast"))
    frames, track = [], FaceTrack(every=1, smooth=0.0)
    for i, f in enumerate(FrameReader(master, m.width, m.height)):
        face = track.update(f)
        g = f.copy()
        if face is not None:                      # "regenerated" mouth: smoothed, slightly shifted
            x, y, mw, mh = mouth_box(face)
            roi = g[y:y + mh, x:x + mw]
            g[y + 3:y + 3 + mh, x:x + mw] = cv2.GaussianBlur(roi, (0, 0), 4)[: max(0, min(mh, g.shape[0] - y - 3))]
        g = cv2.convertScaleAbs(g, alpha=0.95, beta=6)   # global tone drift
        frames.append(cv2.resize(g, (720, 1280), interpolation=cv2.INTER_AREA))
    frames = [frames[0]] * delay + frames[:-delay]
    for f in frames:
        w.write(f)
    w.close()


def main() -> int:
    work = Path(sys.argv[1])
    master = work / "master.mkv"
    ai = work / "fake_ai_output.mp4"
    fake_ai(master, ai)
    out_dir = Path(__file__).resolve().parent.parent / "output" / "simulation_ia"
    for protect in (False, True):
        comp = work / f"comp_fakeai_{'protect' if protect else 'raw'}.mp4"
        conform_ai_output(ai, master, work, comp, protect_subject=protect)
        label = f"simulation_ia_{'protection' if protect else 'brut'}"
        final = out_dir / f"{label}.mp4"
        export_instagram(comp, master, final, "pcm")
        res = run_qc(master, work, final, out_dir / "qc", strict=False, label=label)
        print(f"\n== protect_subject={protect} → verdict {res['verdict']}")
        for c in res["checks"]:
            if any(k in c["name"] for k in ("labiale", "Visage", "Sujet", "Audio")):
                print(f"  {c['status']:4s} {c['name']}: {c['value']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

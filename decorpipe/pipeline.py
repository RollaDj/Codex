"""End-to-end workflow: import → matting → décor replacement → export → QC."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .composite import composite_clip
from .costs import estimate_usd, guard_spend, log_spend
from .decors import load_decor
from .export import export_instagram
from .ffmpeg_utils import media_info
from .ingest import ingest
from .matting import MattingConfig, run_matting
from .qc import run_qc

ROOT = Path(__file__).resolve().parent.parent


def _slug(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", s).strip("-").lower()


def process(input_path: str | Path, decor_ids: list[str], engine: str = "local",
            start: float = 0.0, duration: float | None = None, strict: bool = False,
            out_dir: Path | None = None, work_root: Path | None = None,
            matting: MattingConfig | None = None, protect_subject: bool = True,
            yes_spend: bool = False, max_usd: float | None = None, log=print) -> list[dict]:
    input_path = Path(input_path)
    out_dir = Path(out_dir or ROOT / "output")
    work_root = Path(work_root or ROOT / "work")
    tag = f"{_slug(input_path.stem)}_{start:g}s" + (f"_{duration:g}s" if duration else "")
    workdir = work_root / tag
    decors = [load_decor(d) for d in decor_ids]

    seconds = duration or media_info(input_path).duration
    guard_spend(engine, seconds, len(decors), max_usd, yes_spend)   # raises before any spend

    log(f"[1/4] Import : {input_path.name}")
    t0 = time.time()
    ing = ingest(input_path, workdir, start=start, duration=duration)
    log(f"  master 1080x1920 @ {ing.fps} fps · audio : {ing.audio_mode} · "
        f"recadrage {ing.crop} · HDR→SDR : {ing.hdr_tonemapped}")

    log("[2/4] Détourage IA (local, gratuit)")
    mstats = run_matting(ing.master, workdir, matting, log=log)

    results = []
    for decor in decors:
        label = f"{tag}_{decor.id}_{engine}" + ("_strict" if strict else "")
        log(f"[3/4] Décor « {decor.label} » — moteur {engine}")
        comp = workdir / f"comp_{decor.id}_{engine}.mp4"
        t1 = time.time()
        extra: dict = {}
        if engine == "local":
            extra = composite_clip(ing.master, workdir, decor, comp, strict=strict, log=log)
        elif engine in ("runway", "kling"):
            from .engines.ai_common import conform_ai_output
            if engine == "runway":
                from .engines.runway import runway_edit as edit
            else:
                from .engines.kling import kling_edit as edit
            ai_out, task_id = edit(ing.master, workdir, decor, log=log)
            log_spend(engine, seconds, estimate_usd(engine, seconds), tag, decor.id, task_id)
            extra = conform_ai_output(ai_out, ing.master, workdir, comp,
                                      protect_subject=protect_subject, log=log)
        else:
            raise ValueError(f"Moteur inconnu : {engine}")
        final = out_dir / f"{label}.mp4"
        log("[4/4] Export Instagram + contrôle qualité")
        export_instagram(comp, ing.master, final, ing.audio_mode)
        qc = run_qc(ing.master, workdir, final, out_dir / "qc", strict, label,
                    source=input_path, trimmed=start > 0 or duration is not None)
        res = {"decor": decor.id, "engine": engine, "output": str(final),
               "verdict": qc["verdict"], "render_seconds": round(time.time() - t1, 1),
               "cost_usd": estimate_usd(engine, seconds), "details": extra}
        log(f"  → {final.name} : QC {qc['verdict']}")
        results.append(res)

    summary = {"input": str(input_path), "start": start, "duration": duration,
               "ingest": {"fps": ing.fps, "audio_mode": ing.audio_mode, "crop": ing.crop},
               "matting": {k: mstats[k] for k in ("frames", "seconds", "fps_processing",
                                                    "face_repaired_frames", "face_missing_frames")
                           if k in mstats},
               "total_seconds": round(time.time() - t0, 1), "results": results}
    (out_dir / f"{tag}_{engine}_resume.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return results

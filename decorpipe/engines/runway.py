"""Runway Aleph (video-to-video) client — raw HTTPS, no SDK required.

Requires RUNWAYML_API_SECRET (key from dev.runwayml.com; prepaid API credits,
no subscription). Field names follow Runway's public API (videoUri, promptText,
ratio, references); check docs.dev.runwayml.com if a 400 error mentions a field.
"""
from __future__ import annotations

import os
from pathlib import Path

from ..costs import engines_config
from .ai_common import data_uri, download, http_json, http_multipart, make_ai_input, plate_reference, poll


def _headers(cfg: dict) -> dict:
    key = os.environ.get("RUNWAYML_API_SECRET")
    if not key:
        raise RuntimeError("RUNWAYML_API_SECRET absent : créez une clé sur dev.runwayml.com")
    return {"Authorization": f"Bearer {key}", "X-Runway-Version": cfg["version_header"]}


def _upload(path: Path, cfg: dict, headers: dict) -> str:
    """Ephemeral upload → runway:// URI; falls back to a data URI for small files."""
    try:
        r = http_json("POST", f"{cfg['base_url']}/uploads", headers,
                      {"filename": path.name, "type": "ephemeral"})
        http_multipart(r["uploadUrl"], r.get("fields", {}), path)
        return r["runwayUri"]
    except Exception as e:  # noqa: BLE001 — fallback is intentional
        if path.stat().st_size > 16 * 1024 * 1024:
            raise RuntimeError(f"Upload Runway impossible ({e}) et fichier trop gros pour une data URI")
        return data_uri(path)


def runway_edit(master: Path, workdir: Path, decor, log=print) -> tuple[Path, str]:
    cfg = engines_config()["runway"]
    headers = _headers(cfg)
    w, h = (int(v) for v in cfg["ratio"].split(":"))
    clip = make_ai_input(master, workdir, w, h)
    body = {"model": cfg["model"], "videoUri": _upload(clip, cfg, headers),
            "promptText": decor.prompt, "ratio": cfg["ratio"], "seed": 7}
    if cfg.get("send_plate_as_reference"):
        body["references"] = [{"type": "image",
                               "uri": _upload(plate_reference(decor.plate, workdir), cfg, headers)}]
    task = http_json("POST", f"{cfg['base_url']}/video_to_video", headers, body)
    task_id = task["id"]
    log(f"  tâche Runway {task_id} créée")

    def check():
        t = http_json("GET", f"{cfg['base_url']}/tasks/{task_id}", headers)
        st = t.get("status")
        if st == "SUCCEEDED":
            return True, t["output"][0], st
        if st in ("FAILED", "CANCELLED"):
            raise RuntimeError(f"Runway {st}: {t.get('failure') or t.get('failureCode')}")
        return False, None, st

    url = poll(check, log=log)
    out = workdir / f"runway_{decor.id}.mp4"
    download(url, out)
    return out, task_id

"""Kling O1 "Edit Video" through fal.ai's queue REST API (pay-as-you-go).

Requires FAL_KEY. Input clip: 3–10 s, 720–2160 px, ≤ 200 MB (fal docs).
"""
from __future__ import annotations

import os
from pathlib import Path

from ..costs import engines_config
from .ai_common import data_uri, download, http_json, make_ai_input, plate_reference, poll


def kling_edit(master: Path, workdir: Path, decor, log=print) -> tuple[Path, str]:
    cfg = engines_config()["kling"]
    key = os.environ.get("FAL_KEY")
    if not key:
        raise RuntimeError("FAL_KEY absent : créez une clé sur fal.ai (paiement à l'usage)")
    headers = {"Authorization": f"Key {key}"}
    clip = make_ai_input(master, workdir, 720, 1280)
    body = {"prompt": decor.prompt, "video_url": data_uri(clip), "keep_audio": False}
    if cfg.get("send_plate_as_reference"):
        body["image_urls"] = [data_uri(plate_reference(decor.plate, workdir))]
        body["prompt"] = decor.prompt + " Use @Image1 as the reference for the new background."
    sub = http_json("POST", f"{cfg['queue_url']}/{cfg['endpoint']}", headers, body)
    req_id = sub["request_id"]
    status_url, response_url = sub["status_url"], sub["response_url"]
    log(f"  requête fal/Kling {req_id} créée")

    def check():
        st = http_json("GET", status_url, headers).get("status")
        if st == "COMPLETED":
            return True, http_json("GET", response_url, headers), st
        if st in ("FAILED", "ERROR"):
            raise RuntimeError(f"Kling/fal {st}")
        return False, None, st

    res = poll(check, log=log)
    out = workdir / f"kling_{decor.id}.mp4"
    download(res["video"]["url"], out)
    return out, req_id

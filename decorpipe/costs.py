"""Cost estimation and spend guard for paid engines."""
from __future__ import annotations

import csv
import datetime as dt
import json
import math
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINES_CFG = ROOT / "config" / "engines.json"


def engines_config() -> dict:
    """config/engines.json, or the file named by DECORPIPE_ENGINES (tests / sandbox)."""
    path = Path(os.environ.get("DECORPIPE_ENGINES", ENGINES_CFG))
    return json.loads(path.read_text(encoding="utf-8"))


def estimate_usd(engine: str, seconds: float) -> float:
    cfg = engines_config()[engine]
    if engine == "runway":
        credits = max(cfg["min_credits"], math.ceil(seconds) * cfg["credits_per_second"])
        return round(credits * cfg["usd_per_credit"], 2)
    if engine == "kling":
        secs = max(cfg["min_seconds"], math.ceil(seconds))
        return round(secs * cfg["usd_per_second"], 2)
    return 0.0


def estimate_table(seconds: float) -> list[tuple[str, str, float]]:
    cfg = engines_config()
    return [(k, cfg[k]["label"], estimate_usd(k, seconds)) for k in ("local", "runway", "kling")]


class BudgetError(RuntimeError):
    pass


def guard_spend(engine: str, seconds: float, n_decors: int, max_usd: float | None,
                confirmed: bool) -> float:
    """Refuse any paid call unless explicitly confirmed and within budget."""
    total = estimate_usd(engine, seconds) * n_decors
    if total == 0:
        return 0.0
    max_s = engines_config()[engine].get("max_seconds")
    if max_s and seconds > max_s:
        raise BudgetError(f"« {engine} » accepte au plus {max_s}s par appel ({seconds:.1f}s demandées) : "
                          f"utilisez --duration, ou le moteur local (sans limite).")
    limit = max_usd if max_usd is not None else engines_config()["budget"]["max_usd_per_run"]
    if total > limit:
        raise BudgetError(f"Coût estimé {total:.2f} $ > budget {limit:.2f} $ (--max-usd).")
    if not confirmed:
        raise BudgetError(f"Moteur payant « {engine} » : coût estimé {total:.2f} $ pour "
                          f"{n_decors} décor(s) × {seconds:.1f}s. Relancez avec --yes-spend "
                          f"pour confirmer.")
    return total


def log_spend(engine: str, seconds: float, usd: float, clip: str, decor: str, task_id: str) -> None:
    path = ROOT / engines_config()["budget"]["ledger"]
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date", "moteur", "clip", "decor", "secondes", "usd_estime", "tache"])
        w.writerow([dt.datetime.now().isoformat(timespec="seconds"), engine, clip, decor,
                    f"{seconds:.2f}", f"{usd:.2f}", task_id])

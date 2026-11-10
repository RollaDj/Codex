"""Saved décor presets: decors/<id>/decor.json + a plate (still image or looping video)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

DECORS_DIR = Path(__file__).resolve().parent.parent / "decors"


@dataclass
class Look:
    contrast: float = 1.0
    saturation: float = 1.0
    warmth: float = 0.0        # -1 (cool) … +1 (warm)
    vignette: float = 0.15     # 0 … 0.5, plate only


@dataclass
class Decor:
    id: str
    label: str
    plate: Path
    focus: tuple[float, float] = (0.5, 0.5)   # anchor of the cover-fit crop (x, y in 0..1)
    plate_zoom: float = 1.0
    blur: float = 6.0            # depth-of-field bokeh radius, px at 1080 width
    exposure: float = 1.0
    look: Look = field(default_factory=Look)
    harmonize: float = 0.3       # 0 = subject pixels untouched (strict), 1 = full match
    edge_choke: int = 1          # px of matte erosion at soft edges (0 = off)
    light_wrap: float = 0.22
    wrap_radius: float = 24.0
    ambient_shadow: float = 0.0
    shadow_offset: tuple[float, float] = (0.0, 0.02)
    grain: float | str = "auto"
    track_camera: bool = True
    # Subject integration ("relight light"): global tone only — features untouched.
    # Ignored in --strict mode.
    subject_exposure: float = 0.0   # EV, e.g. -0.5 to sit in a dark room
    subject_warmth: float = 0.0     # -1 cool … +1 warm (tungsten picture lights)
    subject_contrast: float = 1.0
    subject_falloff: float = 0.0    # 0..1 darker toward the bottom/sides (top-front key light)
    rim_suppress: float = 0.0       # 0..1 removes the bright backlight rim of the old room
    prompt: str = ""             # used by AI engines (Runway Aleph / Kling edit)
    notes: str = ""

    @property
    def is_video(self) -> bool:
        return self.plate.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm")


def load_decor(decor_id: str, root: Path = DECORS_DIR) -> Decor:
    d = root / decor_id
    cfg_path = d / "decor.json"
    if not cfg_path.exists():
        raise FileNotFoundError(f"Décor inconnu « {decor_id} » (attendu : {cfg_path}). "
                                f"Disponibles : {', '.join(list_decors(root))}")
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    look = Look(**raw.pop("look", {}))
    plate = d / raw.pop("plate")
    if not plate.exists():
        raise FileNotFoundError(f"Plaque de décor manquante : {plate}")
    for key in ("focus", "shadow_offset"):
        if key in raw:
            raw[key] = tuple(raw[key])
    return Decor(id=decor_id, plate=plate, look=look, **raw)


def list_decors(root: Path = DECORS_DIR) -> list[str]:
    return sorted(p.parent.name for p in root.glob("*/decor.json") if not p.parent.name.startswith("_"))

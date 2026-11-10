"""Turn any photo (square, landscape, AI image…) into a 9:16 décor plate.

The photo is scaled to the plate width and positioned with an anchor (a source
row mapped to a plate row: e.g. "frames bottom just above the head"). Missing
height above/below is filled by repeating a strip of texture from the photo's
top/bottom edge (alternately flipped, lightly blurred, with a soft falloff), so
no new content is invented. That area normally sits behind the speaker's body.

    python tools/photo_to_plate.py photo.webp decors/<id>/plate.jpg \
        --anchor 672:540 --bottom-strip 1040 [--top-strip 120]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

W, H = 1215, 2160   # plate size used by the other décors (cover-fitted later)


def tile_fill(strip: np.ndarray, height: int, downward: bool) -> np.ndarray:
    """Stack the strip (flipped every other time) until `height` rows are filled."""
    rows, k = [], 0
    while sum(r.shape[0] for r in rows) < height:
        rows.append(strip[::-1] if k % 2 == 0 else strip)
        k += 1
    out = np.vstack(rows)[:height]
    return out if downward else out[::-1]


def photo_to_plate(src: Path, dst: Path, anchor: tuple[int, int], bottom_strip: int | None,
                   top_strip: int | None, falloff: float = 0.4, blur: float = 6.0) -> Path:
    img = cv2.imread(str(src), cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit(f"Image illisible : {src}")
    s = W / img.shape[1]
    img = cv2.resize(img, (W, int(round(img.shape[0] * s))), interpolation=cv2.INTER_AREA
                     if s < 1 else cv2.INTER_LANCZOS4)
    top = int(round(anchor[1] - anchor[0] * s))          # plate row of the photo's first row
    plate = np.zeros((H, W, 3), np.uint8)
    y0, y1 = max(top, 0), min(top + img.shape[0], H)
    plate[y0:y1] = img[y0 - top:y1 - top]

    if y1 < H:                                            # extend downward
        start = int(round((bottom_strip if bottom_strip is not None else img.shape[0] / s * 0.85) * s))
        strip = img[start:]
        sharp = tile_fill(strip, H - y1, downward=True).astype(np.float32)
        fill = cv2.GaussianBlur(sharp, (0, 0), blur)
        n = min(160, fill.shape[0])                       # gradual sharp → soft transition
        c = np.linspace(1, 0, n, dtype=np.float32)[:, None, None]
        fill[:n] = sharp[:n] * c + fill[:n] * (1 - c)
        ramp = np.linspace(0, 1, fill.shape[0], dtype=np.float32)[:, None, None]
        # Fade the repeated texture toward its average colour (no visible mirror pattern),
        # then darken: reads as the lower, shadowed part of the same surface.
        mean = strip.reshape(-1, 3).mean(0).astype(np.float32)
        t = np.clip(ramp * 3, 0, 1) * 0.75
        fill = fill * (1 - t) + mean * t
        plate[y1:] = np.clip(fill * (1 - falloff * ramp), 0, 255).astype(np.uint8)
    if y0 > 0:                                            # extend upward
        end = int(round((top_strip if top_strip is not None else img.shape[0] / s * 0.15) * s))
        strip = img[:end]
        fill = tile_fill(strip, y0, downward=False).astype(np.float32)
        fill = cv2.GaussianBlur(fill, (0, 0), blur)
        ramp = np.linspace(1, 0, fill.shape[0], dtype=np.float32)[:, None, None]
        plate[:y0] = np.clip(fill * (1 - 0.5 * falloff * ramp), 0, 255).astype(np.uint8)
    # Soften the seams between the photo and the extension.
    for yy in (y0, y1):
        if 8 < yy < H - 8:
            band = plate[yy - 8:yy + 8].astype(np.float32)
            plate[yy - 8:yy + 8] = cv2.GaussianBlur(band, (0, 0), 3).astype(np.uint8)
    dst.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(dst), plate, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return dst


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--anchor", default="0:0",
                    help="SRC_Y:PLATE_Y — ligne de la photo d'origine placée à cette ligne de la plaque")
    ap.add_argument("--bottom-strip", type=int, default=None,
                    help="ligne (photo d'origine) à partir de laquelle la texture du bas est répétée")
    ap.add_argument("--top-strip", type=int, default=None,
                    help="ligne (photo d'origine) jusqu'à laquelle la texture du haut est répétée")
    ap.add_argument("--falloff", type=float, default=0.55, help="assombrissement de l'extension (0..1)")
    a = ap.parse_args()
    sy, py = (int(v) for v in a.anchor.split(":"))
    print(photo_to_plate(Path(a.src), Path(a.dst), (sy, py), a.bottom_strip, a.top_strip, a.falloff))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Command line: python -m decorpipe <commande> …"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .costs import BudgetError, estimate_table
from .decors import list_decors, load_decor
from .matting import MattingConfig


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="decorpipe", description=(
        "Remplace le décor d'une vidéo face caméra (iPhone) sans toucher au visage, "
        "à la voix, aux gestes ni à la tenue. Export MP4 vertical 9:16 pour Instagram."))
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="traiter une vidéo")
    r.add_argument("input", help="fichier vidéo (MOV/MP4 iPhone)")
    r.add_argument("--decor", default="mur-clair-logos",
                   help="identifiant de décor, liste séparée par des virgules, ou 'all'")
    r.add_argument("--engine", choices=["local", "runway", "kling"], default="local")
    r.add_argument("--start", type=float, default=0.0, help="début en secondes")
    r.add_argument("--duration", type=float, default=None, help="durée en secondes (ex. 10)")
    r.add_argument("--strict", action="store_true",
                   help="aucune retouche colorimétrique du sujet (pixels d'origine)")
    r.add_argument("--model", choices=["rvm-resnet50", "rvm-mobilenetv3"], default="rvm-resnet50")
    r.add_argument("--no-protect-subject", action="store_true",
                   help="moteurs IA : garder le sujet régénéré par l'IA (déconseillé)")
    r.add_argument("--yes-spend", action="store_true", help="autoriser un moteur payant")
    r.add_argument("--max-usd", type=float, default=None, help="plafond de dépense pour ce lancement")
    r.add_argument("--out", default=None, help="dossier de sortie (défaut : output/)")

    b = sub.add_parser("batch", help="traiter tous les rushes d'un dossier (ex. dossier iCloud/AirDrop)")
    b.add_argument("folder", help="dossier contenant les .MOV/.MP4")
    b.add_argument("--decor", default="mur-clair-logos", help="décor(s) : id, liste, ou 'all'")
    b.add_argument("--strict", action="store_true")
    b.add_argument("--out", default=None)

    e = sub.add_parser("estimate", help="comparer le coût des moteurs pour une durée")
    e.add_argument("seconds", type=float)
    e.add_argument("--decors", type=int, default=1)

    sub.add_parser("decors", help="lister les décors enregistrés")

    c = sub.add_parser("compare", help="mosaïque côte à côte pour choisir un décor")
    c.add_argument("videos", nargs="+", help="2 à 6 vidéos (ex. source + exports)")
    c.add_argument("-o", "--output", default="output/comparatif.mp4")

    a = p.parse_args(argv)
    if a.cmd == "decors":
        for d in list_decors():
            dec = load_decor(d)
            print(f"{d:20s} {dec.label}  ({dec.plate.name})")
        return 0
    if a.cmd == "compare":
        from .export import side_by_side
        print(side_by_side([Path(v) for v in a.videos], Path(a.output)))
        return 0
    if a.cmd == "estimate":
        print(f"Coût estimé pour {a.seconds:g}s × {a.decors} décor(s) :")
        for key, label, usd in estimate_table(a.seconds):
            print(f"  {key:7s} {usd * a.decors:7.2f} $   {label}")
        return 0

    from .pipeline import _slug, process
    decors = list_decors() if a.decor == "all" else [d.strip() for d in a.decor.split(",")]
    if a.cmd == "batch":
        # Local engine only: batch mode never spends money.
        out = Path(a.out) if a.out else Path(__file__).resolve().parent.parent / "output"
        files = sorted(f for f in Path(a.folder).iterdir()
                       if f.suffix.lower() in (".mov", ".mp4", ".m4v"))
        failed = 0
        for f in files:
            if all(any(out.glob(f"{_slug(f.stem)}_0s_{d}_local*.mp4")) for d in decors):
                print(f"déjà traité : {f.name}")
                continue
            res = process(f, decors, strict=a.strict, out_dir=out)
            failed += sum(x["verdict"] == "FAIL" for x in res)
        return 1 if failed else 0
    try:
        res = process(a.input, decors, engine=a.engine, start=a.start, duration=a.duration,
                      strict=a.strict, out_dir=a.out, matting=MattingConfig(model=a.model),
                      protect_subject=not a.no_protect_subject, yes_spend=a.yes_spend,
                      max_usd=a.max_usd)
    except BudgetError as err:
        print(f"Arrêt (budget) : {err}", file=sys.stderr)
        return 2
    print("\nRésultats :")
    for x in res:
        print(f"  {x['verdict']:4s}  {x['output']}  ({x['render_seconds']}s, {x['cost_usd']:.2f} $)")
    return 0 if all(x["verdict"] != "FAIL" for x in res) else 1


if __name__ == "__main__":
    sys.exit(main())

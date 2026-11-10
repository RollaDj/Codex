# Logos officiels

Déposez ici les fichiers fournis par la marque (fond transparent de préférence) :

- `maison-belier.svg` (ou `.png`, `.webp`, `.jpg`) : cadre de gauche
- `rolla.svg` (ou `.png`, `.webp`, `.jpg`) : cadre de droite

Puis régénérez la plaque (~1 min 20 sur CPU) et relancez le traitement :

```bash
python tools/render_decors.py --only mur-noir-logos
python -m decorpipe run input/ma_video.MOV --decor mur-noir-logos
```

Tant qu'un fichier manque, le cadre affiche un logotype typographique **provisoire**
(Caladea / Inter), qui n'est pas le logo officiel.

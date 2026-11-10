# Logos officiels

Fichiers utilisés par les décors `mur-clair-logos` et `mur-noir-logos` :

- `maison-belier.png` : cadre de gauche (ivoire sur noir, 4000×4000)
- `rolla.png` : cadre de droite (ivoire sur noir, 1500×1049)

Formats acceptés : `.svg`, `.png`, `.webp`, `.jpg`.
- **Logo opaque** (fond plein, comme ici) : le tirage reprend la couleur du fond, et le logo
  est recadré sur son tracé. Les couleurs officielles sont conservées.
- **Logo à fond transparent** : le logo est posé sur un papier crème.

Après un changement de logo, régénérez les plaques (~1 min 20 chacune), puis relancez :

```bash
python tools/render_decors.py --only mur-clair-logos
python tools/render_decors.py --only mur-noir-logos
python -m decorpipe run input/ma_video.MOV --decor mur-clair-logos
```

Si un fichier manque, le cadre affiche un logotype typographique provisoire.

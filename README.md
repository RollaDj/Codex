# decorpipe : nouveau décor, même vous

Workflow automatisé de montage pour les contenus Instagram **ROLLA** et **Maison Bélier**.
Vous vous filmez face caméra à l'iPhone, n'importe où. Le pipeline remplace **tout le décor**
par un environnement haut de gamme enregistré, **sans toucher à votre visage, votre voix,
vos gestes ni votre tenue**. Il livre un **MP4 vertical 9:16** prêt pour un Reel.

```
iPhone (MOV/MP4) ─▶ 1. Import ─▶ 2. Détourage IA ─▶ 3. Décor ─▶ 4. Export 9:16 ─▶ Contrôle qualité
                   rotation, HDR→SDR,  RVM local, gratuit,  composition locale  H.264 + audio    audio, synchro labiale,
                   recadrage 9:16,     protection du        (ou Runway / Kling  d'origine        visage, bords, format
                   CFR, audio intact   visage               en option payante)                   Instagram
```

## Pourquoi ce choix technique
- **Visage et lèvres intacts par construction** : le sujet n'est jamais régénéré. Ses pixels
  viennent directement de votre prise ; seuls les bords (cheveux) sont fondus dans le décor.
  Les images sont traitées une à une (image N → image N), ce qui garantit la synchro labiale.
- **Audio d'origine** : copie bit à bit quand la vidéo n'est pas coupée, conversion
  sample-accurate sinon. Le contrôle qualité mesure un décalage de 0 ms.
- **0 € par vidéo, aucun abonnement** : détourage (Robust Video Matting) et composition
  (OpenCV + FFmpeg) tournent en local. Runway Aleph et Kling Edit restent disponibles en option,
  avec plafond de dépense et confirmation explicite.
  Comparatif complet, prix et sources : [docs/comparatif-moteurs.md](docs/comparatif-moteurs.md).

## Installation (une fois)
```bash
# Prérequis : Python ≥ 3.10 et FFmpeg ≥ 6 (macOS : brew install ffmpeg python)
pip install -r requirements.txt
bash tools/fetch_models.sh          # ~125 Mo de modèles libres, vérifiés par SHA-256
```

## Utilisation
```bash
# Preuve de concept : 10 premières secondes, décor haussmannien
python -m decorpipe run input/ma_video.MOV --decor haussmannien --duration 10

# Les 4 décors d'un coup (le détourage est calculé une fois puis réutilisé)
python -m decorpipe run input/ma_video.MOV --decor all

# Garder le siège / fauteuil dans lequel vous êtes assis
python -m decorpipe run input/ma_video.MOV --decor mur-beige-logos --keep-seat

# Mode strict : aucune correction colorimétrique du sujet (pixels d'origine uniquement)
python -m decorpipe run input/ma_video.MOV --decor studio-noir --strict

# Automatisation : traiter tous les nouveaux rushes d'un dossier (iCloud Drive, AirDrop…)
python -m decorpipe batch ~/iCloud/Rushes --decor haussmannien,studio-noir

# Mosaïque côte à côte (source + décors) pour choisir
python -m decorpipe compare work/<clip>/master.mov output/*_local.mp4 -o output/comparatif.mp4

# Comparer les coûts avant de choisir un moteur
python -m decorpipe estimate 30 --decors 4

# Moteur IA en option (payant, confirmation et plafond obligatoires)
export RUNWAYML_API_SECRET=…   # ou FAL_KEY=… pour Kling
python -m decorpipe run input/ma_video.MOV --decor hotel-luxe --duration 10 \
       --engine runway --yes-spend --max-usd 3
```

Sorties dans `output/` :
- `<clip>_<décor>_<moteur>.mp4` : la vidéo 1080×1920 H.264 + AAC, avec faststart.
- `qc/<…>_qc.md` / `.json` : le rapport de contrôle qualité (PASS / WARN / FAIL).
- `qc/<…>_planche.jpg` : la planche contact (source / masque / export) pour la relecture humaine.
- `depenses.csv` : le journal des appels payants.

## Décors enregistrés
| Identifiant | Décor | Plaque |
|---|---|---|
| `haussmannien` | Appartement haussmannien : moulures, parquet, cheminée en marbre, lumière du jour | `decors/haussmannien/plate.jpg` |
| `studio-noir` | Studio noir : fond continu anthracite, halo de lumière chaude | `decors/studio-noir/plate.jpg` |
| `interieur-design` | Intérieur design : enduit à la chaux, enfilade en noyer, luminaires | `decors/interieur-design/plate.jpg` |
| `hotel-luxe` | Hôtel de luxe : couloir en noyer cannelé, laiton, marbre noir, lustre | `decors/hotel-luxe/plate.jpg` |
| `mur-clair-logos` | Mur blanc cassé en lumière du jour, deux cadres noirs : Maison Bélier / ROLLA. **À privilégier pour un tournage en lumière du jour** | `decors/mur-clair-logos/plate.jpg` |
| `mur-beige-logos` | Mur enduit beige, rayons de soleil, branche d'olivier, cadres bronze Maison Bélier / DJ ROLLA à hauteur naturelle (image fournie, convertie en 9:16) | `decors/mur-beige-logos/plate.jpg` |
| `console-logos` | Mur enduit beige en lumière dorée, console en travertin, lampe laiton, hortensias, cadres bronze Maison Bélier / DJ ROLLA (image fournie, convertie en 9:16) | `decors/console-logos/plate.jpg` |
| `mur-noir-logos` | Mur noir mat, deux cadres en laiton éclairés : Maison Bélier / ROLLA. Le sujet est assombri et réchauffé pour coller aux appliques ; idéal si le tournage est fait dans une pièce sombre | `decors/mur-noir-logos/plate.jpg` |

Les logos officiels (`maison-belier.png`, `rolla.png`, ivoire sur noir) se trouvent dans
`decors/mur-noir-logos/logos/` et servent aux deux variantes. **`mur-clair-logos` est le décor
par défaut** pour les vidéos tournées en journée.

**Règle de réalisme** : la lumière du décor doit ressembler à celle du tournage. Un rush filmé en
plein jour s'incruste de façon crédible dans un décor clair ; un décor sombre demande soit un
tournage dans une pièce sombre avec une lumière chaude, soit les réglages d'intégration
`subject_exposure`, `subject_warmth`, `subject_falloff` et `rim_suppress` dans `decor.json`
(lumière globale seulement : les traits et les lèvres ne sont pas modifiés, ce que le contrôle
qualité vérifie).

Chaque décor est un dossier `decors/<id>/` contenant `decor.json` (flou de profondeur,
étalonnage, habillage lumineux, prompt pour les moteurs IA) et une **plaque** :
- **image fixe** (`.jpg`/`.png`) : les plaques fournies sont rendues en 3D avec Blender
  (`tools/render_decors.py`, gratuit et reproductible) ;
- **vidéo en boucle** (`.mp4`) pour un décor « vivant » : rideaux, reflets, flammes.

Pour un rendu photoréaliste maximal, remplacez une plaque par une photo, ou par une image IA
générée **une seule fois** (quelques centimes). Ajouter un décor revient à copier un dossier
et changer la plaque. Une photo carrée ou horizontale se convertit en plaque verticale avec :

```bash
# --anchor : ligne de la photo (ici le bas des cadres, y=672) placée juste au-dessus de la tête
python tools/photo_to_plate.py photo.webp decors/mon-decor/plate.jpg --anchor 672:540 --bottom-strip 1040
```

## Contrôle qualité automatique
| Contrôle | Méthode |
|---|---|
| Format Instagram | ffprobe : H.264 High, yuv420p, 1080×1920, SAR 1:1, CFR, faststart, ≤ 3 min |
| Images | même nombre d'images que la source (aucune perdue ni dupliquée) |
| Audio | MD5 du PCM (bit à bit) ou corrélation croisée : décalage ≤ 1 ms, corrélation ≥ 0,99 |
| Synchro labiale | énergie de mouvement de la bouche (repères YuNet) source vs export : décalage 0, corrélation ≥ 0,97 |
| Visage / sujet inchangés | SSIM et PSNR dans la zone opaque et dans l'ovale du visage |
| Détourage | scintillement des contours, sauts de silhouette, trous comblés dans le visage |
| Cadrage | visage hors des bandeaux de l'interface Reels |

## Bonnes pratiques de tournage
Lumière, réglages iPhone (HDR vidéo désactivé, AE/AF verrouillés), distance au mur :
voir [docs/tournage-iphone.md](docs/tournage-iphone.md).

## Transparence
Le décor est synthétique, la personne et la voix sont réelles. Selon le contexte (publicité,
partenariat), Instagram et l'AI Act européen peuvent demander une mention « contenu
retouché/IA ». Le fichier exporté porte un commentaire de métadonnées en ce sens.

## Preuve de concept
- **Clip de test** (10 s, 4 décors) : 5/5 PASS, audio décalé de 0,00 ms, synchro labiale avec
  0 image de décalage, visage SSIM ≥ 0,98, et 0 €.
- **Rush iPhone réel** (12,6 s en 4K, à main levée, en contre-jour) : son identique au bit près
  au fichier iPhone, 24 i/s et 302 images conservées, synchro labiale avec 0 image de décalage.
  Un seul avertissement, sur le scintillement des contours lors des mouvements de mains rapides.

Les détails, la comparaison qualité/coût et le test du garde-fou IA sont dans
[docs/poc-rapport.md](docs/poc-rapport.md).

## Structure
```
decorpipe/           pipeline Python (ingest, matting, composite, export, qc, engines/)
decors/              décors enregistrés (decor.json + plaque)
config/engines.json  moteurs, tarifs, budget
tools/               fetch_models.sh, render_decors.py, simulate_ai_drift.py
tests/               tests rapides (python -m pytest -q), dont une fausse API Runway/Kling
docs/                comparatif des moteurs, guide de tournage, rapport de la preuve de concept
```

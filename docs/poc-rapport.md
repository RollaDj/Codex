# Preuve de concept : 10 secondes, 4 décors (9 novembre 2026)

## Protocole
- **Rush de test** : clip public face caméra, vertical 1080×1920 HEVC à 25 i/s avec piste AAC,
  homme en pull noir dans une cuisine carrelée claire (échantillon du dépôt open source
  LatentSync, utilisé uniquement pour ce test et non versionné). Ce sont les conditions d'un
  rush iPhone ; à refaire avec vos propres rushes ROLLA / Maison Bélier.
- **Extrait** : les 10 premières secondes, soit 250 images.
- **Machine** : conteneur cloud, 4 cœurs CPU, sans GPU.
- **Commande** : `python -m decorpipe run sample.mp4 --decor all --duration 10`.

## Résultats du moteur local (0 €)

| Décor | Verdict QC | Audio (décalage / corrélation) | Synchro labiale (décalage / corrélation) | Visage SSIM min | Sujet SSIM min / PSNR | Rendu |
|---|---|---|---|---|---|---|
| Haussmannien | **PASS** | 0,00 ms / 1,0000 | 0 image / 0,9998 | 0,981 | 0,976 / 34,6 dB | 77 s |
| Studio noir | **PASS** | 0,00 ms / 1,0000 | 0 image / 0,9998 | 0,981 | 0,991 / 39,8 dB | 78 s |
| Intérieur design | **PASS** | 0,00 ms / 1,0000 | 0 image / 0,9998 | 0,981 | 0,983 / 36,0 dB | 77 s |
| Hôtel de luxe | **PASS** | 0,00 ms / 1,0000 | 0 image / 0,9998 | 0,982 | 0,982 / 38,5 dB | 79 s |
| Hôtel de luxe `--strict` | **PASS** | 0,00 ms / 1,0000 | 0 image / 0,9998 | 0,983 | 0,992 / **50,3 dB** | 72 s |

Contrôles communs, tous PASS : format Instagram (H.264 High, yuv420p, 1080×1920, 25 i/s CFR,
faststart), 250 images en entrée comme en sortie, décalage A/V du conteneur de 0 ms, aucun saut
de silhouette, aucun trou dans le visage, visage dans la zone sûre des Reels. Scintillement
des contours : médiane 0,023 et p95 0,062.

**Lecture des chiffres**
- En **mode strict**, le sujet ne subit que l'encodage H.264 (PSNR 50 dB, visuellement sans
  perte).
- En **mode normal**, l'harmonisation colorimétrique du décor ajuste légèrement la balance des
  blancs (±6 % maximum). Le PSNR baisse donc (34–40 dB) alors que la structure reste identique
  (SSIM ≥ 0,98) : traits, lèvres et regard ne bougent pas, seule la teinte globale suit la
  lumière du décor.
- **Temps de calcul** : détourage 102 s pour 10 s de vidéo (2,45 images/s, calculé une seule
  fois puis réutilisé pour tous les décors), puis ~75 s par décor (composition, export et
  contrôle qualité). Un Reel de 30 s demande donc environ 5 min pour le premier décor et 4 min
  pour chaque décor suivant, sur 4 cœurs CPU.

## Rush réel : `IMG_2102.MOV` (10 novembre 2026)
Prise iPhone réelle : 4K vertical (2160×3840) H.264 SDR, 24 i/s, 12,6 s, AAC mono 48 kHz, plus
4 pistes de métadonnées Apple. Plan assis, à main levée et en contre-plongée, grande baie vitrée
lumineuse en contre-jour, t-shirt blanc, micro-cravate, chaise de bureau verte, mains qui
avancent vers l'objectif. Le clip est traité **en entier**, sans coupe, donc l'audio est copié
bit à bit.

| Décor | Verdict | Synchro labiale | Visage SSIM min | Sujet SSIM min | Rendu |
|---|---|---|---|---|---|
| Haussmannien | WARN | 0 image / 0,996 | 0,980 | 0,977 | 93 s |
| Studio noir | WARN | 0 image / 0,997 | 0,980 | 0,976 | 94 s |
| Intérieur design | WARN | 0 image / 0,997 | 0,979 | 0,976 | 92 s |
| Hôtel de luxe | WARN | 0 image / 0,996 | 0,979 | 0,975 | 93 s |

Contrôles communs, tous PASS :
- **Audio** : décodé, il est **identique au bit près au fichier iPhone** (même MD5), avec un
  décalage de 0,00 ms.
- **Timing** : 24,000 i/s comme l'original, 302 images sur 302, durée à 1,7 ms de l'original
  (moins d'une image).
- **Format** : Instagram 1080×1920, avec faststart.
- **Visage** : dans la zone sûre des Reels, aucun trou dans le visage, aucun saut de silhouette.
- **Suivi caméra** : 301 images sur 301 suivies (jusqu'à 24 px de mouvement à main levée) ; le
  décor bouge avec la caméra.

Le seul **WARN** est le scintillement des contours (p95 0,089 pour un seuil de 0,08). Il
s'explique par le tournage à main levée et les mains qui passent très près de l'objectif. En
vérifiant les images une à une, les mains floues restent opaques et la chaise verte visible
entre le bras et le torse est bien remplacée par le décor.

**Défauts trouvés grâce à ce rush, et corrigés**
1. **Cadence** : 24 i/s était arrondi à 23,976 i/s, soit 12 ms de dérive en fin de clip. On
   choisit désormais la cadence standard la plus proche.
2. **Fichier de travail en MKV** : il perdait le « priming » AAC de l'iPhone (2 112
   échantillons, 44 ms). L'export final restait juste, mais le contrôle qualité se comparait à
   une référence fausse. Le fichier de travail est maintenant un MOV, et le contrôle qualité
   compare aussi directement au fichier iPhone d'origine (MD5 audio, cadence, durée).

## Test du garde-fou pour les moteurs IA (sans dépense)
`tools/simulate_ai_drift.py` fabrique une fausse sortie « IA » en 720×1280, avec un retard de
2 images, une bouche redessinée (floutée) et une dérive de tonalité. Elle passe ensuite dans le
vrai post-traitement des moteurs Runway/Kling :

| | Synchro labiale | Visage SSIM | Verdict |
|---|---|---|---|
| Sortie IA brute (`--no-protect-subject`) | **FAIL** : corrélation 0,54, décalage détecté | **FAIL** : 0,52 | FAIL |
| Avec protection du sujet (défaut) | PASS : 0 image / 0,9998 | PASS : 0,983 | PASS |

Le contrôle qualité **détecte** donc une dérive de lèvres ou de visage introduite par un moteur
génératif, et la protection du sujet (le sujet d'origine reposé sur le rendu IA) la **corrige**.
Les clients Runway et Kling sont par ailleurs testés contre une fausse API locale
(`tests/test_engines_mock.py` : upload, création de tâche, interrogation, téléchargement).

## Comparaison qualité / coût des moteurs

| Moteur | Qualité attendue sur ce brief | Coût pour 10 s | Statut dans la preuve de concept |
|---|---|---|---|
| **Local (RVM + FFmpeg)** | Visage, voix et gestes intacts et mesurés. Décor crédible ; lumière du sujet non recalculée | **0 €** | **Testé : 5/5 PASS** |
| Runway Aleph 2.0 | Meilleure intégration lumineuse ; visage régénéré, donc protection obligatoire | 2,80 $ | Code prêt et testé sur fausse API. **Non exécuté** : pas de clé, hôte bloqué depuis ce conteneur |
| Kling O1 Edit (fal) | Proche de Runway, 10 s max par appel | 1,68 $ | Idem |
| HeyGen | Hors brief (avatar resynthétisé) | n/a | Exclu |

**Décision : le moteur local est retenu par défaut.** C'est le seul qui respecte le brief
*par construction*, sans abonnement et sans coût par vidéo. Les moteurs IA restent une option
« hero » ponctuelle. Pour les départager sur vos propres images, un test de 10 s × 1 décor
coûte ≈ 4,50 $ au total, sans abonnement (crédits API Runway prépayés et paiement à l'usage
fal.ai) :
```bash
export RUNWAYML_API_SECRET=…  FAL_KEY=…
python -m decorpipe run input/rush.MOV --decor haussmannien --duration 10 --engine runway --yes-spend --max-usd 3
python -m decorpipe run input/rush.MOV --decor haussmannien --duration 10 --engine kling  --yes-spend --max-usd 2
```
Le même contrôle qualité s'applique : synchro labiale, SSIM du visage et audio comparés
objectivement au moteur local.

## Limites constatées et pistes
1. **Plaques procédurales (Blender)** : propres et cohérentes, mais on devine encore la 3D.
   Pour un rendu « photo », remplacez `decors/<id>/plate.jpg` par une vraie photo, ou par une
   image IA générée une seule fois (quelques centimes, aucun abonnement).
2. **Lumière du sujet** : elle reste celle du tournage. Suivez
   [tournage-iphone.md](tournage-iphone.md) (lumière douce orientée comme dans le décor).
   C'est le facteur n°1 de réalisme.
3. **Tenue sombre sur studio noir** : le halo de lumière derrière la tête sépare le sujet du
   fond. Une lumière de contour au tournage améliorerait encore le rendu.
4. **Décors vivants** : une boucle vidéo de 5 à 10 s par décor (image→vidéo Kling/Runway,
   ~0,50–1 $ une seule fois) se branche telle quelle comme plaque `.mp4`.
5. **Rush de test unique** : refaire le protocole sur 3 ou 4 rushes réels (intérieur, extérieur,
   à main levée, contre-jour) avant de passer en production.

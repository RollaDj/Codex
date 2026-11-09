# Comparatif des moteurs : Runway, Kling, HeyGen, FFmpeg (+ local)

*Relevé du 9 novembre 2026. Les tarifs et identifiants d'API bougent tous les mois :
`config/engines.json` centralise les valeurs utilisées par le pipeline. Vérifiez-les sur
la console du fournisseur avant tout appel payant.*

## Le critère qui tranche

Votre cahier des charges impose de **ne modifier ni le visage, ni la voix, ni les gestes,
ni la tenue**, et de **conserver la synchro labiale et l'audio d'origine**. Il faut donc
distinguer deux familles d'outils :

| Famille | Principe | Visage / lèvres |
|---|---|---|
| **Détourage + compositing** (local RVM + FFmpeg) | on découpe le sujet image par image et on le pose sur un nouveau décor | **pixels d'origine** : identiques par construction |
| **Vidéo→vidéo génératif** (Runway Aleph, Kling Edit) | le modèle **redessine toute l'image**, y compris le visage, en suivant le mouvement source | régénérés : risque de dérive du visage, des dents, des lèvres ou des mains |
| **Avatar** (HeyGen) | votre visage est **resynthétisé** à partir d'un script ou d'un audio | contraire au brief |

## Synthèse

| | Local (RVM + FFmpeg) | Runway Aleph 2.0 | Kling O1 Edit (fal.ai) | HeyGen |
|---|---|---|---|---|
| Modifie une vidéo réelle | Oui | Oui | Oui | Non (avatar). Pas d'API de remplacement de fond pour une vidéo importée |
| Visage, lèvres, tenue | **Inchangés** (pixels source) | Régénérés : il faut **re-poser le sujet d'origine** par-dessus (fait par le pipeline) | Régénérés : même parade | Resynthétisés |
| Audio | Original (copie bit à bit si non coupé) | Perdu : **ré-attaché** par le pipeline | Option `keep_audio`, ré-attaché quoi qu'il arrive | Voix clonée possible : hors brief |
| Lumière cohérente avec le décor | Approximée (raccord colorimétrique, habillage lumineux des bords, grain) | **Excellente** (relighting génératif) | Très bonne | n/a |
| Durée max par appel | Illimitée | 30 s (1080p) | 3 à 10 s | n/a |
| Coût pour 10 s | **0 €** | **≈ 2,80 $** (28 crédits/s, minimum 56 crédits) | **≈ 1,68 $** (0,168 $/s) | n/a |
| Coût mensuel (12 Reels × 30 s) | 0 € | ≈ 100 $ | ≈ 60 $ (en 3 morceaux de 10 s par Reel) | n/a |
| Abonnement obligatoire | Non | Non : crédits API prépayés (dev.runwayml.com) | Non : paiement à l'usage | Non (paiement à l'usage dès 5 $) |
| Confidentialité | Rien ne sort de la machine | Upload. Runway entraîne ses modèles sur vos contenus par défaut en self-serve | Upload chez fal / Kuaishou | Upload |
| Intégration Claude | Ce dépôt | Plugin officiel « Runway Dev » (MCP + skills), non activé ici | Plugin officiel « Kling AI » (MCP), non activé ici | Connecteur « HyperFrames by HeyGen » (motion design uniquement) |

## Détail par moteur

### FFmpeg + détourage IA local (retenu par défaut)
- **FFmpeg** sait composer, étalonner, encoder et multiplexer, mais il ne sait pas isoler une
  personne sans fond vert. Ses filtres `chromakey` et `backgroundkey` supposent un fond uni ou
  un plan fixe. On lui adjoint donc un modèle de détourage vidéo.
- **Robust Video Matting** (RVM, licence Apache-2.0) est récurrent, donc stable d'une image à
  l'autre. Il produit un alpha doux pour les cheveux. Mesuré sur la preuve de concept, CPU
  4 cœurs : **2,7 images/s** en ResNet-50 et ~9 images/s en MobileNetV3.
- **YuNet** (OpenCV, MIT) détecte le visage et les commissures des lèvres. Il sert à protéger le
  visage dans le masque et à contrôler la synchro labiale.
- Limites : la lumière du sujet reste celle du tournage. Le pipeline l'atténue (balance
  colorimétrique plafonnée à ±6 %, habillage lumineux des bords, grain assorti, suivi caméra),
  mais un éclairage de tournage très différent du décor se voit. Voir
  [tournage-iphone.md](tournage-iphone.md).

### Runway (Aleph 2.0, vidéo→vidéo)
- Identifiant API `aleph2`. `gen4_aleph` est déprécié (arrêt annoncé au 30/07/2026). Endpoint
  `POST /v1/video_to_video`, en-tête `X-Runway-Version: 2024-11-06`, tâches asynchrones
  (`GET /v1/tasks/{id}`).
- **28 crédits/s, minimum 56 crédits** par génération, à 0,01 $ le crédit, soit **0,28 $/s**.
  Entrée jusqu'à 30 s en 1080p, avec jusqu'à 5 images-clés d'ancrage.
- L'appli « Remove from Video » est moins chère (15 crédits/s), mais limitée à 5 s et réservée
  aux abonnés Standard et plus.
- Pour : meilleure cohérence lumière/ombres et retouches guidées par images-clés.
  Contre : le visage est régénéré, il n'y a pas d'audio, c'est le plus cher, et vos rushes
  servent à l'entraînement par défaut.

### Kling (O1 / O3 / 4.0 « Edit Video » via fal.ai)
- Endpoint fal `fal-ai/kling-video/o1/video-to-video/edit` (Pro). Il existe aussi
  `…/o1/standard/…` et un `…/o3/standard/video-to-video/edit`. Entrée MP4/MOV de 3 à 10 s,
  720 à 2160 px, 200 Mo maximum. Paramètre `keep_audio` (défaut incertain, à fixer
  explicitement).
- Prix : **0,168 $/s** pour O1 Edit sur fal. Kling 4.0 Edit coûte 0,157 $/s en 720p et
  0,235 $/s en 1080p d'après le guide fal d'octobre 2026. Les revendeurs annoncent des prix
  divergents, d'où l'intérêt de vérifier.
- Pour : moins cher que Runway, et le remplacement d'arrière-plan fait partie des éditions
  prévues. Contre : 10 s maximum par appel (au-delà, il faut découper et les raccords se
  voient), visage régénéré, générations ratées facturées, service Kuaishou.

### HeyGen (exclu du flux principal)
- L'API sert à générer des avatars (Avatar IV ≈ 4 $/min, Avatar V ≈ 3 $/min, paiement à
  l'usage dès 5 $). Le remplacement de fond d'une vidéo importée n'existe que dans l'éditeur
  web, sans endpoint public, et des développeurs signalent des fonds transparents non
  respectés via l'API.
- Le brief exige de garder votre vraie prise : HeyGen ne convient pas. Il ne serait utile que
  plus tard, pour **traduire/doubler** une vidéo déjà montée, avec mention IA obligatoire.

### Alternatives repérées (non retenues pour l'instant)
- **Beeble SwitchX** : compositing génératif « ancré sur les pixels » du sujet, qui revendique
  conserver identité et synchro audio, en 720p/1080p. Il propose aussi le détourage MatAnyone 2
  (1 crédit / 30 images). Abonnement requis, donc à réserver au cas où le relighting deviendrait
  indispensable.
- **VideoBGRemover** (API tierce de détourage, payante) et **CapCut / DaVinci Resolve** (détourage
  manuel dans l'éditeur, non automatisable).

## Recommandation

1. **Moteur par défaut : local (0 €).** C'est la seule option qui garantit *par construction*
   visage, lèvres, gestes, tenue et audio intacts, sans limite de durée.
2. **Option « hero » au cas par cas :** Runway Aleph ou Kling Edit, toujours avec
   `protect_subject` (le sujet d'origine est reposé sur le rendu IA). Budget plafonné
   (`--max-usd`), confirmation explicite (`--yes-spend`), journal `output/depenses.csv`.
3. **Pour des décors vivants à coût quasi nul :** générer **une seule fois** par décor une
   boucle de 5 à 10 s (image→vidéo Kling ou Runway, ~0,50–1 $). Le pipeline accepte un `.mp4`
   comme plaque de décor et le compositing reste local et gratuit pour toutes les vidéos.
4. **Aucun abonnement nécessaire.** Un premier test des deux moteurs IA sur 10 s × 1 décor
   coûte ≈ 4,50 $ (2,80 + 1,68).

## Sources
- Runway, guide de démarrage API (en-tête de version) : https://docs.dev.runwayml.com/guides/using-the-api/
- Runway, endpoint vidéo→vidéo Aleph 2.0 : https://dev.runwayml.com/endpoints/video_to_video
- Tarifs Runway 2026 (Aleph 2.0 : 28 crédits/s, minimum 56) : https://creatify.ai/blog/runway-pricing-(2026)-plans-credits-and-what-you-ll-actually-pay
- Aleph, fournisseurs et limites : https://www.atlascloud.ai/blog/tips/runway-aleph, https://www.developersdigest.tech/models/video-generation/runway
- Paramètres gen4_aleph (videoUri, promptText, ratios) : https://vidofy.ai/en/models/runway/gen-4-aleph
- Dépréciation de gen4_aleph : https://runware.ai/docs/models/runway-aleph
- Kling O1 Edit sur fal (schéma, limites) : https://fal.ai/models/fal-ai/kling-video/o1/video-to-video/edit/api
- Kling O3 Edit sur fal : https://fal.ai/models/fal-ai/kling-video/o3/standard/video-to-video/edit/api
- Guide API Kling sur fal (Kling 4.0 Edit, prix/s) : https://fal.ai/learn/tools/kling-api
- Comparatif de prix Kling O1 Edit : https://muapi.ai/comparison/kling-o1-video-edit, https://aimlapi.com/models/kling-video-o1-video-to-video-edit
- HeyGen, outil de suppression de fond (éditeur web) : https://www.heygen.com/video/background-remover-from-video
- Forum développeurs HeyGen (fond transparent via API) : https://docs.heygen.com/discuss/6810a06a8eb74b006889e990
- Beeble SwitchX : https://docs.beeble.ai/beeble/switchx.md, https://docs.beeble.ai/help/faq
- Robust Video Matting : https://github.com/PeterL1n/RobustVideoMatting
- YuNet (OpenCV Zoo) : https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet

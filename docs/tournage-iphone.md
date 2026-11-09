# Guide de tournage iPhone (pour un remplacement de décor crédible)

Le pipeline ne retouche jamais votre visage. La qualité finale dépend donc surtout de la
prise de vue. Ces réglages prennent 2 minutes et font la différence entre un décor
« incrusté » et un décor crédible.

## Réglages iPhone
- **Format** : Réglages › Appareil photo › Enregistrement vidéo › **4K à 30 i/s** (ou 1080p à 30 i/s).
  Évitez 60 i/s : c'est inutile pour Instagram et ça double le temps de calcul.
- **HDR vidéo : désactivé** (Réglages › Appareil photo › Enregistrement vidéo › HDR vidéo).
  Le pipeline sait convertir le HDR (HLG / Dolby Vision) en SDR, mais une prise SDR donne
  des couleurs de peau plus fidèles.
- **Mode Cinématique : désactivé** : son flou artificiel abîme le détourage des cheveux.
- **Verrouillage AE/AF** : appui long sur votre visage avant de lancer. Cela évite les
  variations d'exposition et de mise au point, qui trahissent l'incrustation.
- Caméra principale (1×), téléphone **vertical**, objectif à hauteur des yeux ou de la poitrine.

## Cadre et placement
- Plan taille ou plan poitrine, **tête dans le tiers supérieur**, avec ~25 cm d'air au-dessus
  des cheveux. Le contrôle qualité vérifie que le visage reste dans la zone sûre des Reels
  (hors bandeaux de l'interface en haut, en bas et à droite).
- Tenez-vous **à 1,5 m ou plus d'un mur** : moins d'ombre portée et de lumière parasite sur
  les bords.
- **Fond uni et contrasté avec votre tenue** si possible : un mur clair derrière une tenue
  sombre (ou l'inverse) donne le meilleur détourage. Évitez les fonds chargés de la même
  couleur que vos cheveux.
- Trépied conseillé. À main levée, c'est accepté : le pipeline suit le mouvement de la caméra
  et fait bouger le décor en conséquence.

## Lumière (le facteur n°1 de réalisme)
- Lumière **douce, de face ou à 30-45°** : fenêtre, softbox ou panneau LED avec diffuseur.
- Faites correspondre la direction de la lumière avec le décor choisi :
  - *Haussmannien* : jour venant de la gauche.
  - *Studio noir* : lumière principale de face, idéalement un **contre-jour léger**
    (lumière de contour) pour détacher une tenue sombre du fond noir.
  - *Intérieur design* / *Hôtel* : ambiance chaude, balance des blancs vers 3 500 à 4 000 K.
- Évitez les **ombres dures** sur le mur derrière vous et les contre-jours violents (fenêtre
  dans le dos).

## Son
- Micro-cravate (ou micro sans fil type DJI/Rode) branché sur l'iPhone.
- Le pipeline **réutilise votre piste audio d'origine** : copie bit à bit quand la vidéo
  n'est pas coupée, conversion sans perte puis AAC 320 kb/s quand elle l'est. Le contrôle
  qualité vérifie un décalage de 0 ms.

## Ce qui fait échouer un détourage
- Objets tenus loin du corps et fins (stylo, verre transparent), cheveux très fins sur un fond
  de même teinte, mouvements très rapides (flou de bougé), personne qui entre et sort du cadre.
- Une deuxième personne ou un animal dans le champ : le pipeline ne garde que le sujet
  principal.

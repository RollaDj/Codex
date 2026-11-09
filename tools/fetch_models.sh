#!/usr/bin/env bash
# Télécharge les modèles locaux (gratuits, licences libres) dans ./models
#  - Robust Video Matting (Apache-2.0)  : détourage vidéo temporellement stable
#  - YuNet (MIT, OpenCV Zoo)             : détection du visage + bouche (QC, protection)
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p models
fetch() {  # url  fichier  sha256
  if [ -f "models/$2" ] && echo "$3  models/$2" | sha256sum -c --status; then
    echo "ok      $2"; return
  fi
  echo "fetch   $2"
  curl -fL --retry 4 -o "models/$2.part" "$1"
  echo "$3  models/$2.part" | sha256sum -c --status || { echo "SHA256 invalide pour $2" >&2; exit 1; }
  mv "models/$2.part" "models/$2"
}
RVM=https://github.com/PeterL1n/RobustVideoMatting/releases/download/v1.0.0
fetch "$RVM/rvm_resnet50_fp32.onnx"     rvm_resnet50_fp32.onnx     25db300fcb6ee27f941a1b52c97856e8d1f13c7f35817f81a612f89af0e8a85c
fetch "$RVM/rvm_mobilenetv3_fp32.onnx"  rvm_mobilenetv3_fp32.onnx  88d4531297118f595bf2fd60f6f566aec2e559393802d1f436c380f0cbbd2828
fetch "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx" \
      face_detection_yunet_2023mar.onnx  8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4
echo "Modèles prêts."

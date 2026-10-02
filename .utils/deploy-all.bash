#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Charger la configuration
if [ -f "${SCRIPT_DIR}/config.env" ]; then
    set -a
    source "${SCRIPT_DIR}/config.env"
    set +a
fi

PROJECT_PREFIX="${PROJECT_PREFIX:-thematic-podcast}"

echo "=============================================================================="
echo "DÉPLOIEMENT COMPLET DU PROJET : ${PROJECT_PREFIX}"
echo "=============================================================================="
echo "Ce script va préparer et déployer l'intégralité du pipeline sur AWS :"
echo "1. Infrastructure de base (S3 Bucket, Rôles IAM dédiés)"
echo "2. Lambda Main (${PROJECT_PREFIX}-Main)"
echo "3. Lambda AudioMixer (${PROJECT_PREFIX}-AudioMixer)"
echo "4. Lambda ShortsGenerator (${PROJECT_PREFIX}-ShortsGenerator)"
echo "5. Lambda SpotifyUploader (${PROJECT_PREFIX}-SpotifyUploader)"
echo "6. Module VideoGenerator (Lambdas + Step Function)"
echo "7. Master Pipeline Step Function (${PROJECT_PREFIX}-PipelineStateMachine)"
echo "=============================================================================="

# Étape 1 : Infrastructure
echo -e "\n>>> [1/7] Préparation de l'infrastructure S3 et IAM..."
bash "${SCRIPT_DIR}/setup-aws-infra.bash"

# Étape 2 : Lambda Main
echo -e "\n>>> [2/7] Déploiement de la Lambda Main..."
bash "${SCRIPT_DIR}/build-and-upload.bash" Main "${PROJECT_PREFIX}-Main"

# Étape 3 : Lambda AudioMixer
echo -e "\n>>> [3/7] Déploiement de la Lambda AudioMixer..."
bash "${SCRIPT_DIR}/deploy-audiomixer.bash"

# Étape 4 : Lambda ShortsGenerator
echo -e "\n>>> [4/7] Déploiement de la Lambda ShortsGenerator..."
bash "${SCRIPT_DIR}/build-and-upload.bash" ShortsGenerator "${PROJECT_PREFIX}-ShortsGenerator"

# Étape 5 : Lambda SpotifyUploader
echo -e "\n>>> [5/7] Déploiement de la Lambda SpotifyUploader..."
bash "${SCRIPT_DIR}/build-and-upload.bash" SpotifyUploader "${PROJECT_PREFIX}-SpotifyUploader"

# Étape 6 : Module VideoGenerator
echo -e "\n>>> [6/7] Déploiement du module VideoGenerator..."
bash "${SCRIPT_DIR}/deploy-videogenerator.bash"

# Étape 7 : Master Pipeline Step Function
echo -e "\n>>> [7/7] Déploiement du Master Workflow Step Function..."
bash "${SCRIPT_DIR}/deploy-pipeline.bash"

echo -e "\n=============================================================================="
echo "FÉLICITATIONS ! Déploiement complet de '${PROJECT_PREFIX}' terminé avec succès !"
echo "=============================================================================="

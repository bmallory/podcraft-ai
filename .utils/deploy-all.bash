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
echo "3. Lambda ShortsGenerator (${PROJECT_PREFIX}-ShortsGenerator)"
echo "4. Lambda SpotifyUploader (${PROJECT_PREFIX}-SpotifyUploader)"
echo "5. Module VideoGenerator (Lambdas + Step Function)"
echo "6. Master Pipeline Step Function (${PROJECT_PREFIX}-PipelineStateMachine)"
echo "=============================================================================="

# Étape 1 : Infrastructure
echo -e "\n>>> [1/6] Préparation de l'infrastructure S3 et IAM..."
bash "${SCRIPT_DIR}/setup-aws-infra.bash"

# Étape 2 : Lambda Main
echo -e "\n>>> [2/6] Déploiement de la Lambda Main..."
bash "${SCRIPT_DIR}/build-and-upload.bash" Main "${PROJECT_PREFIX}-Main"

# Étape 3 : Lambda ShortsGenerator
echo -e "\n>>> [3/6] Déploiement de la Lambda ShortsGenerator..."
bash "${SCRIPT_DIR}/build-and-upload.bash" ShortsGenerator "${PROJECT_PREFIX}-ShortsGenerator"

# Étape 4 : Lambda SpotifyUploader
echo -e "\n>>> [4/6] Déploiement de la Lambda SpotifyUploader..."
bash "${SCRIPT_DIR}/build-and-upload.bash" SpotifyUploader "${PROJECT_PREFIX}-SpotifyUploader"

# Étape 5 : Module VideoGenerator
echo -e "\n>>> [5/6] Déploiement du module VideoGenerator..."
bash "${SCRIPT_DIR}/deploy-videogenerator.bash"

# Étape 6 : Master Pipeline Step Function
echo -e "\n>>> [6/6] Déploiement du Master Workflow Step Function..."
bash "${SCRIPT_DIR}/deploy-pipeline.bash"

echo -e "\n=============================================================================="
echo "FÉLICITATIONS ! Déploiement complet de '${PROJECT_PREFIX}' terminé avec succès !"
echo "=============================================================================="

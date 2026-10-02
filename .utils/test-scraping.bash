#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Charger la configuration si présente
if [ -f "${SCRIPT_DIR}/config.env" ]; then
    set -a
    source "${SCRIPT_DIR}/config.env"
    set +a
fi

PROJECT_PREFIX="${PROJECT_PREFIX:-thematic-podcast}"
IMAGE_NAME="${PROJECT_PREFIX}-main:latest"

# Vérifier si l'image existe localement, sinon la construire
if ! docker image inspect "$IMAGE_NAME" > /dev/null 2>&1; then
    echo "Image $IMAGE_NAME non trouvée localement. Construction..."
    docker build -t "$IMAGE_NAME" "${PROJECT_ROOT}/Main"
fi

echo "Exécution du diagnostic de scraping avec le conteneur: $IMAGE_NAME..."
docker run --rm --entrypoint python3 \
  -v "${PROJECT_ROOT}/Main":/var/task \
  "$IMAGE_NAME" test_scraper.py "$@"

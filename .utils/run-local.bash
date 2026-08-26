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

if [ -z "$1" ]; then
    echo "Usage: $0 <FunctionDirectory>"
    echo "Exemple: $0 Main"
    exit 1
fi

FUNCTION_DIR=$1
PROJECT_PREFIX="${PROJECT_PREFIX:-thematic-podcast}"
IMAGE_NAME="${PROJECT_PREFIX}-${FUNCTION_DIR,,}"

# Clé API Gemini
if [ -z "$GEMINI_API_KEY" ] && [ -f "${PROJECT_ROOT}/.secret/geminikey" ]; then
    export GEMINI_API_KEY=$(cat "${PROJECT_ROOT}/.secret/geminikey" | tr -d '\n')
fi

if [ -z "$GEMINI_API_KEY" ]; then
    echo "Erreur : GEMINI_API_KEY n'est pas définie dans l'environnement ni dans .secret/geminikey."
    exit 1
fi

S3_BUCKET_NAME="${S3_BUCKET_NAME:-local-test-bucket}"

echo "=============================================================================="
echo "DÉMARRAGE LOCAL DE : ${FUNCTION_DIR} (${IMAGE_NAME})"
echo "=============================================================================="
echo "Pour invoquer le conteneur en local :"
echo "curl -XPOST \"http://localhost:9000/2015-03-31/functions/function/invocations\" -d '{}'"
echo "------------------------------------------------------------------------------"

docker run \
  -e GEMINI_API_KEY="$GEMINI_API_KEY" \
  -e S3_BUCKET_NAME="$S3_BUCKET_NAME" \
  -e PODCAST_TITLE="${PODCAST_TITLE:-Tech & IA Horizon}" \
  -e PODCAST_HOST="${PODCAST_HOST:-Alex, votre guide tech}" \
  -e PODCAST_THEME="${PODCAST_THEME}" \
  -e PODCAST_DURATION="${PODCAST_DURATION:-8 à 12 minutes}" \
  -e GEMINI_TTS_VOICE="${GEMINI_TTS_VOICE:-Laomedeia}" \
  -e GEMINI_SCRIPT_MODEL="${GEMINI_SCRIPT_MODEL:-gemini-3.7-flash}" \
  -e GEMINI_TTS_MODEL="${GEMINI_TTS_MODEL:-gemini-3.1-flash-tts-preview}" \
  -e GEMINI_SUMMARY_MODEL="${GEMINI_SUMMARY_MODEL:-gemini-2.5-flash}" \
  -e GEMINI_IMAGE_MODEL="${GEMINI_IMAGE_MODEL:-gemini-3.1-flash-image}" \
  -e SPOTIFY_SECRET_NAME="${SPOTIFY_SECRET_NAME:-${PROJECT_PREFIX}-SpotifyCredentials}" \
  -e SPOTIFY_SHOW_ID="${SPOTIFY_SHOW_ID}" \
  -e AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID" \
  -e AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" \
  -e AWS_SESSION_TOKEN="$AWS_SESSION_TOKEN" \
  -p 9000:8080 "${IMAGE_NAME}:latest"

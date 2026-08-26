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
    echo "Usage: $0 <FunctionDirectory> [LambdaFunctionName]"
    echo "Exemples:"
    echo "  $0 Main"
    echo "  $0 ShortsGenerator"
    echo "  $0 SpotifyUploader"
    exit 1
fi

FUNCTION_DIR=$1
PROJECT_PREFIX="${PROJECT_PREFIX:-thematic-podcast}"
LAMBDA_NAME="${2:-${PROJECT_PREFIX}-${FUNCTION_DIR}}"
AWS_REGION="${AWS_REGION:-eu-west-1}"

# Détecter le compte AWS si non défini
if [ -z "$AWS_ACCOUNT_ID" ]; then
    AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo "")
    if [ -z "$AWS_ACCOUNT_ID" ]; then
        echo "Erreur : Impossible de récupérer l'AWS Account ID."
        exit 1
    fi
fi

REPO_URL="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
# Nom de repository ECR unique et variabilisé par projet
IMAGE_NAME="${PROJECT_PREFIX}-${FUNCTION_DIR,,}"
ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${IAM_ROLE_NAME:-${PROJECT_PREFIX}-lambda-execution-role}"
S3_BUCKET_NAME="${S3_BUCKET_NAME:-${PROJECT_PREFIX}-audio-${AWS_ACCOUNT_ID}}"

# Clé API Gemini
if [ -z "$GEMINI_API_KEY" ] && [ -f "${PROJECT_ROOT}/.secret/geminikey" ]; then
    export GEMINI_API_KEY=$(cat "${PROJECT_ROOT}/.secret/geminikey" | tr -d '\n')
fi

echo "=============================================================================="
echo "DÉPLOIEMENT LAMBDA : ${LAMBDA_NAME}"
echo "=============================================================================="
echo "Dossier source  : ${PROJECT_ROOT}/${FUNCTION_DIR}"
echo "ECR Repository  : ${IMAGE_NAME}"
echo "Lambda Function : ${LAMBDA_NAME}"
echo "Région AWS      : ${AWS_REGION}"
echo "Bucket S3       : ${S3_BUCKET_NAME}"
echo "=============================================================================="

echo "1. Authentification ECR..."
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$REPO_URL"

echo "2. Construction de l'image Docker..."
docker build -t "$IMAGE_NAME" "${PROJECT_ROOT}/${FUNCTION_DIR}"

echo "3. Tag de l'image..."
docker tag "${IMAGE_NAME}:latest" "${REPO_URL}/${IMAGE_NAME}:latest"

echo "4. Vérification du repository ECR..."
if ! aws ecr describe-repositories --repository-names "$IMAGE_NAME" --region "$AWS_REGION" > /dev/null 2>&1; then
    echo "Création du repository ECR ${IMAGE_NAME}..."
    aws ecr create-repository --repository-name "$IMAGE_NAME" --region "$AWS_REGION"
fi

echo "5. Push de l'image vers ECR..."
docker push "${REPO_URL}/${IMAGE_NAME}:latest"
echo "Image poussée avec succès !"

# Timeout et mémoire selon la fonction
TIMEOUT=300
MEMORY=1024
if [ "$FUNCTION_DIR" = "Main" ]; then
    TIMEOUT=600
    MEMORY=2048
elif [ "$FUNCTION_DIR" = "ShortsGenerator" ]; then
    TIMEOUT=300
    MEMORY=1024
elif [ "$FUNCTION_DIR" = "SpotifyUploader" ]; then
    TIMEOUT=180
    MEMORY=512
fi

# Préparer les variables d'environnement pour la Lambda
ENV_VARS="Variables={"
ENV_VARS+="PROJECT_PREFIX=\"${PROJECT_PREFIX}\","
ENV_VARS+="S3_BUCKET_NAME=\"${S3_BUCKET_NAME}\","
ENV_VARS+="GEMINI_API_KEY=\"${GEMINI_API_KEY}\","
ENV_VARS+="PODCAST_TITLE=\"${PODCAST_TITLE:-Tech & IA Horizon}\","
ENV_VARS+="PODCAST_HOST=\"${PODCAST_HOST:-Alex, votre guide tech}\","
ENV_VARS+="PODCAST_THEME=\"${PODCAST_THEME}\","
ENV_VARS+="PODCAST_DURATION=\"${PODCAST_DURATION:-8 à 12 minutes}\","
ENV_VARS+="GEMINI_TTS_VOICE=\"${GEMINI_TTS_VOICE:-Laomedeia}\","
ENV_VARS+="GEMINI_SCRIPT_MODEL=\"${GEMINI_SCRIPT_MODEL:-gemini-3.7-flash}\","
ENV_VARS+="GEMINI_TTS_MODEL=\"${GEMINI_TTS_MODEL:-gemini-3.1-flash-tts-preview}\","
ENV_VARS+="GEMINI_SUMMARY_MODEL=\"${GEMINI_SUMMARY_MODEL:-gemini-2.5-flash}\","
ENV_VARS+="GEMINI_IMAGE_MODEL=\"${GEMINI_IMAGE_MODEL:-gemini-3.1-flash-image}\","
ENV_VARS+="HOST_IMAGE_STYLE=\"${HOST_IMAGE_STYLE}\","
ENV_VARS+="SPOTIFY_SECRET_NAME=\"${SPOTIFY_SECRET_NAME:-${PROJECT_PREFIX}-SpotifyCredentials}\","
ENV_VARS+="SPOTIFY_SHOW_ID=\"${SPOTIFY_SHOW_ID}\","
ENV_VARS+="SPOTIFY_EPISODE_DESCRIPTION=\"${SPOTIFY_EPISODE_DESCRIPTION}\","
ENV_VARS+="CLOUDFRONT_DOMAIN=\"${CLOUDFRONT_DOMAIN}\","
ENV_VARS+="PROXY_ENDPOINT=\"${PROXY_ENDPOINT}\","
ENV_VARS+="PROXY_API_KEY=\"${PROXY_API_KEY}\","
ENV_VARS+="HEYGEN_API_KEY=\"${HEYGEN_API_KEY}\","
ENV_VARS+="HEYGEN_AVATAR_ID=\"${HEYGEN_AVATAR_ID}\","
ENV_VARS+="BUFFER_ACCESS_TOKEN=\"${BUFFER_ACCESS_TOKEN}\","
ENV_VARS+="BUFFER_PROFILE_ID=\"${BUFFER_PROFILE_ID}\","
ENV_VARS+="STEP_FUNCTION_ARN=\"arn:aws:states:${AWS_REGION}:${AWS_ACCOUNT_ID}:stateMachine:${PROJECT_PREFIX}-VideoGeneratorStateMachine\""
ENV_VARS+="}"

echo "6. Mise à jour / Création de la fonction Lambda ${LAMBDA_NAME}..."
if aws lambda get-function --function-name "$LAMBDA_NAME" --region "$AWS_REGION" > /dev/null 2>&1; then
    echo "La fonction existe. Mise à jour du code..."
    aws lambda update-function-code \
        --function-name "$LAMBDA_NAME" \
        --image-uri "${REPO_URL}/${IMAGE_NAME}:latest" \
        --region "$AWS_REGION"
        
    echo "Attente de mise à jour..."
    aws lambda wait function-updated --function-name "$LAMBDA_NAME" --region "$AWS_REGION" 2>/dev/null || sleep 5

    echo "Mise à jour de la configuration et des variables d'environnement..."
    aws lambda update-function-configuration \
        --function-name "$LAMBDA_NAME" \
        --timeout "$TIMEOUT" \
        --memory-size "$MEMORY" \
        --environment "$ENV_VARS" \
        --region "$AWS_REGION"
else
    echo "Création de la nouvelle fonction Lambda..."
    aws lambda create-function \
        --function-name "$LAMBDA_NAME" \
        --package-type Image \
        --code ImageUri="${REPO_URL}/${IMAGE_NAME}:latest" \
        --role "$ROLE_ARN" \
        --timeout "$TIMEOUT" \
        --memory-size "$MEMORY" \
        --environment "$ENV_VARS" \
        --region "$AWS_REGION"
fi

echo "=============================================================================="
echo "SUCCÈS : Fonction ${LAMBDA_NAME} déployée avec succès !"
echo "=============================================================================="
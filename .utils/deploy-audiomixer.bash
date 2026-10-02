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
LAMBDA_NAME="${PROJECT_PREFIX}-AudioMixer"
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
IMAGE_NAME="${PROJECT_PREFIX}-audiomixer"
ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${IAM_ROLE_NAME:-${PROJECT_PREFIX}-lambda-execution-role}"
S3_BUCKET_NAME="${S3_BUCKET_NAME:-${PROJECT_PREFIX}-audio-${AWS_ACCOUNT_ID}}"

echo "=============================================================================="
echo "DÉPLOIEMENT DE LA LAMBDA AUDIOMIXER : ${LAMBDA_NAME}"
echo "=============================================================================="
echo "Dossier source  : ${PROJECT_ROOT}/AudioMixer"
echo "ECR Repository  : ${IMAGE_NAME}"
echo "Lambda Function : ${LAMBDA_NAME}"
echo "Région AWS      : ${AWS_REGION}"
echo "Bucket S3       : ${S3_BUCKET_NAME}"
echo "=============================================================================="

echo "1. Authentification avec ECR..."
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$REPO_URL"

echo "2. Construction de l'image Docker AudioMixer..."
docker build -t "$IMAGE_NAME" "${PROJECT_ROOT}/AudioMixer"

echo "3. Tagging de l'image..."
docker tag "${IMAGE_NAME}:latest" "${REPO_URL}/${IMAGE_NAME}:latest"

echo "4. Vérification de l'existence du repository ECR..."
if ! aws ecr describe-repositories --repository-names "$IMAGE_NAME" --region "$AWS_REGION" > /dev/null 2>&1; then
    echo "Création du repository ECR ${IMAGE_NAME}..."
    aws ecr create-repository --repository-name "$IMAGE_NAME" --region "$AWS_REGION"
fi

echo "5. Push de l'image vers ECR..."
docker push "${REPO_URL}/${IMAGE_NAME}:latest"

IMAGE_URI="${REPO_URL}/${IMAGE_NAME}:latest"

echo "6. Déploiement de la fonction Lambda ${LAMBDA_NAME}..."
if aws lambda get-function --function-name "$LAMBDA_NAME" --region "$AWS_REGION" > /dev/null 2>&1; then
    echo "La fonction existe. Mise à jour du code..."
    aws lambda update-function-code \
        --function-name "$LAMBDA_NAME" \
        --image-uri "$IMAGE_URI" \
        --region "$AWS_REGION"

    echo "Attente de la mise à jour..."
    aws lambda wait function-updated --function-name "$LAMBDA_NAME" --region "$AWS_REGION" 2>/dev/null || sleep 5

    echo "Mise à jour de la configuration..."
    aws lambda update-function-configuration \
        --function-name "$LAMBDA_NAME" \
        --timeout 300 \
        --memory-size 1024 \
        --environment "Variables={S3_BUCKET_NAME=${S3_BUCKET_NAME}}" \
        --region "$AWS_REGION"
else
    echo "Création de la fonction Lambda..."
    aws lambda create-function \
        --function-name "$LAMBDA_NAME" \
        --package-type Image \
        --code ImageUri="$IMAGE_URI" \
        --role "$ROLE_ARN" \
        --timeout 300 \
        --memory-size 1024 \
        --environment "Variables={S3_BUCKET_NAME=${S3_BUCKET_NAME}}" \
        --region "$AWS_REGION"
fi

echo "=============================================================================="
echo "SUCCÈS : Déploiement de ${LAMBDA_NAME} terminé avec succès !"
echo "=============================================================================="

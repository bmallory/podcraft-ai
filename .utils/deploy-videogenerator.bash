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

AWS_REGION="${AWS_REGION:-eu-west-1}"
PROJECT_PREFIX="${PROJECT_PREFIX:-thematic-podcast}"

if [ -z "$AWS_ACCOUNT_ID" ]; then
    AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo "")
    if [ -z "$AWS_ACCOUNT_ID" ]; then
        echo "Erreur : Impossible de récupérer l'AWS Account ID."
        exit 1
    fi
fi

REPO_URL="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
IMAGE_NAME="${PROJECT_PREFIX}-videogenerator"
ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${IAM_ROLE_NAME:-${PROJECT_PREFIX}-lambda-execution-role}"
SFN_ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${SFN_ROLE_NAME:-${PROJECT_PREFIX}-stepfunctions-role}"
S3_BUCKET_NAME="${S3_BUCKET_NAME:-${PROJECT_PREFIX}-audio-${AWS_ACCOUNT_ID}}"

if [ -z "$GEMINI_API_KEY" ] && [ -f "${PROJECT_ROOT}/.secret/geminikey" ]; then
    export GEMINI_API_KEY=$(cat "${PROJECT_ROOT}/.secret/geminikey" | tr -d '\n')
fi

echo "=============================================================================="
echo "DÉPLOIEMENT DU GÉNÉRATEUR VIDÉO POUR : ${PROJECT_PREFIX}"
echo "=============================================================================="

echo "1. Authentification ECR..."
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$REPO_URL"

echo "2. Construction de l'image Docker VideoGenerator..."
docker build -t "$IMAGE_NAME" "${PROJECT_ROOT}/VideoGenerator"

echo "3. Tag de l'image..."
docker tag "${IMAGE_NAME}:latest" "${REPO_URL}/${IMAGE_NAME}:latest"

echo "4. Vérification du repository ECR..."
if ! aws ecr describe-repositories --repository-names "$IMAGE_NAME" --region "$AWS_REGION" > /dev/null 2>&1; then
    echo "Création du repository ECR ${IMAGE_NAME}..."
    aws ecr create-repository --repository-name "$IMAGE_NAME" --region "$AWS_REGION"
fi

echo "5. Push de l'image vers ECR..."
docker push "${REPO_URL}/${IMAGE_NAME}:latest"

IMAGE_URI="${REPO_URL}/${IMAGE_NAME}:latest"

ENV_VARS="Variables={"
ENV_VARS+="PROJECT_PREFIX=\"${PROJECT_PREFIX}\","
ENV_VARS+="S3_BUCKET_NAME=\"${S3_BUCKET_NAME}\","
ENV_VARS+="GEMINI_API_KEY=\"${GEMINI_API_KEY}\","
ENV_VARS+="GEMINI_VIDEO_MODEL=\"${GEMINI_VIDEO_MODEL:-veo-3.1-generate-preview}\""
ENV_VARS+="}"

function deploy_lambda() {
    local func_name=$1
    local handler=$2
    local timeout=$3
    
    echo "Déploiement de la fonction Lambda ${func_name}..."
    if aws lambda get-function --function-name "${func_name}" --region "$AWS_REGION" > /dev/null 2>&1; then
        echo "La fonction ${func_name} existe. Mise à jour du code..."
        aws lambda update-function-code \
            --function-name "${func_name}" \
            --image-uri "${IMAGE_URI}" \
            --region "$AWS_REGION"
            
        aws lambda wait function-updated --function-name "${func_name}" --region "$AWS_REGION" 2>/dev/null || sleep 5

        echo "Mise à jour de la configuration..."
        aws lambda update-function-configuration \
            --function-name "${func_name}" \
            --timeout "${timeout}" \
            --memory-size 1024 \
            --environment "$ENV_VARS" \
            --image-config "{\"Command\":[\"${handler}\"]}" \
            --region "$AWS_REGION"
    else
        echo "Création de la fonction ${func_name}..."
        aws lambda create-function \
            --function-name "${func_name}" \
            --package-type Image \
            --code ImageUri="${IMAGE_URI}" \
            --role "${ROLE_ARN}" \
            --timeout "${timeout}" \
            --memory-size 1024 \
            --environment "$ENV_VARS" \
            --image-config "{\"Command\":[\"${handler}\"]}" \
            --region "$AWS_REGION"
    fi
}

deploy_lambda "${PROJECT_PREFIX}-VideoSplitter" "splitter.handler" 60
deploy_lambda "${PROJECT_PREFIX}-VideoChunkGenerator" "chunk_generator.handler" 900
deploy_lambda "${PROJECT_PREFIX}-VideoAssembler" "assembler.handler" 300

echo "6. Génération de la définition Step Function depuis le template..."
SFN_NAME="${PROJECT_PREFIX}-VideoGeneratorStateMachine"
SFN_TEMPLATE="${PROJECT_ROOT}/VideoGenerator/step_function.template.json"
SFN_JSON="${PROJECT_ROOT}/VideoGenerator/step_function.json"

sed -e "s/\${AWS_REGION}/${AWS_REGION}/g" \
    -e "s/\${AWS_ACCOUNT_ID}/${AWS_ACCOUNT_ID}/g" \
    -e "s/\${PROJECT_PREFIX}/${PROJECT_PREFIX}/g" \
    "$SFN_TEMPLATE" > "$SFN_JSON"

SFN_DEF=$(cat "$SFN_JSON")
SFN_ARN="arn:aws:states:${AWS_REGION}:${AWS_ACCOUNT_ID}:stateMachine:${SFN_NAME}"

echo "7. Déploiement de la State Machine ${SFN_NAME}..."
if aws stepfunctions describe-state-machine --state-machine-arn "$SFN_ARN" --region "$AWS_REGION" > /dev/null 2>&1; then
    echo "State Machine existante. Mise à jour..."
    aws stepfunctions update-state-machine \
        --state-machine-arn "$SFN_ARN" \
        --definition "$SFN_DEF" \
        --region "$AWS_REGION"
else
    echo "Création de la State Machine..."
    aws stepfunctions create-state-machine \
        --name "$SFN_NAME" \
        --definition "$SFN_DEF" \
        --role-arn "$SFN_ROLE_ARN" \
        --region "$AWS_REGION"
fi

echo "=============================================================================="
echo "SUCCÈS : Module VideoGenerator déployé pour '${PROJECT_PREFIX}' !"
echo "State Machine ARN : $SFN_ARN"
echo "=============================================================================="

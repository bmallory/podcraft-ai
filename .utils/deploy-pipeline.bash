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

SFN_ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${SFN_ROLE_NAME:-${PROJECT_PREFIX}-stepfunctions-role}"
SFN_NAME="${PROJECT_PREFIX}-PipelineStateMachine"
SFN_TEMPLATE="${PROJECT_ROOT}/aws/podcast_pipeline.template.json"
SFN_JSON="${PROJECT_ROOT}/aws/podcast_pipeline.json"

echo "=============================================================================="
echo "DÉPLOIEMENT DU MASTER WORKFLOW (STEP FUNCTION) : ${SFN_NAME}"
echo "=============================================================================="

echo "1. Génération du fichier JSON ASL à partir du template..."
sed -e "s/\${AWS_REGION}/${AWS_REGION}/g" \
    -e "s/\${AWS_ACCOUNT_ID}/${AWS_ACCOUNT_ID}/g" \
    -e "s/\${PROJECT_PREFIX}/${PROJECT_PREFIX}/g" \
    "$SFN_TEMPLATE" > "$SFN_JSON"

SFN_DEF=$(cat "$SFN_JSON")
SFN_ARN="arn:aws:states:${AWS_REGION}:${AWS_ACCOUNT_ID}:stateMachine:${SFN_NAME}"

echo "2. Déploiement de la State Machine ${SFN_NAME}..."
if aws stepfunctions describe-state-machine --state-machine-arn "$SFN_ARN" --region "$AWS_REGION" > /dev/null 2>&1; then
    echo "Mise à jour de la State Machine existante..."
    aws stepfunctions update-state-machine \
        --state-machine-arn "$SFN_ARN" \
        --definition "$SFN_DEF" \
        --region "$AWS_REGION"
else
    echo "Création de la nouvelle State Machine..."
    aws stepfunctions create-state-machine \
        --name "$SFN_NAME" \
        --definition "$SFN_DEF" \
        --role-arn "$SFN_ROLE_ARN" \
        --region "$AWS_REGION"
fi

echo "=============================================================================="
echo "SUCCÈS : Master Workflow ${SFN_NAME} déployé avec succès !"
echo "State Machine ARN : $SFN_ARN"
echo "=============================================================================="

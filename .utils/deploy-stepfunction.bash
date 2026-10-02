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
AWS_REGION="${AWS_REGION:-eu-west-1}"

if [ -z "$AWS_ACCOUNT_ID" ]; then
    AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo "")
    if [ -z "$AWS_ACCOUNT_ID" ]; then
        echo "Erreur : Impossible de récupérer l'AWS Account ID."
        exit 1
    fi
fi

SFN_NAME="${PROJECT_PREFIX}-PipelineStateMachine"
SFN_ROLE_NAME="${SFN_ROLE_NAME:-${PROJECT_PREFIX}-stepfunctions-role}"
QUEUE_NAME="${SQS_QUEUE_NAME:-${PROJECT_PREFIX}-ia-jobs-queue}"

echo "=============================================================================="
echo "CONFIGURATION SQS & STEP FUNCTIONS : ${PROJECT_PREFIX}"
echo "=============================================================================="
echo "Région AWS   : ${AWS_REGION}"
echo "File SQS     : ${QUEUE_NAME}"
echo "State Machine: ${SFN_NAME}"
echo "Rôle SFN     : ${SFN_ROLE_NAME}"
echo "=============================================================================="

echo "1. Vérification / Création de la file SQS (${QUEUE_NAME})..."
if ! aws sqs get-queue-url --queue-name "${QUEUE_NAME}" --region "${AWS_REGION}" > /dev/null 2>&1; then
    echo "Création de la file SQS ${QUEUE_NAME} dans ${AWS_REGION}..."
    aws sqs create-queue \
        --queue-name "${QUEUE_NAME}" \
        --region "${AWS_REGION}" \
        --attributes VisibilityTimeout=900,MessageRetentionPeriod=86400
    echo "File SQS créée avec succès."
else
    echo "La file SQS ${QUEUE_NAME} existe déjà."
fi

QUEUE_URL=$(aws sqs get-queue-url --queue-name "${QUEUE_NAME}" --region "${AWS_REGION}" --query 'QueueUrl' --output text)
QUEUE_ARN=$(aws sqs get-queue-attributes --queue-url "${QUEUE_URL}" --attribute-names QueueArn --region "${AWS_REGION}" --query 'Attributes.QueueArn' --output text)

echo "Queue URL : ${QUEUE_URL}"
echo "Queue ARN : ${QUEUE_ARN}"

echo "2. Configuration des permissions IAM pour Step Functions..."
POLICY_FILE=$(mktemp)
cat << EOF > "$POLICY_FILE"
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "sqs:SendMessage"
      ],
      "Resource": "${QUEUE_ARN}"
    }
  ]
}
EOF

aws iam put-role-policy \
    --role-name "${SFN_ROLE_NAME}" \
    --policy-name "${PROJECT_PREFIX}-StepFunctionsSQSSendMessagePolicy" \
    --policy-document "file://${POLICY_FILE}"

rm -f "$POLICY_FILE"
echo "Permission sqs:SendMessage ajoutée au rôle ${SFN_ROLE_NAME}."

echo "=============================================================================="
echo "SUCCÈS ! La file SQS ${QUEUE_NAME} est prête pour le worker local."
echo "Pour démarrer le worker local, exportez :"
echo "export SQS_QUEUE_URL=\"${QUEUE_URL}\""
echo "python3 worker_sqs_local.py"
echo "=============================================================================="

#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Charger la configuration
if [ -f "${SCRIPT_DIR}/config.env" ]; then
    echo "Chargement de la configuration depuis .utils/config.env..."
    # Export all variables from config.env
    set -a
    source "${SCRIPT_DIR}/config.env"
    set +a
fi

AWS_REGION="${AWS_REGION:-eu-west-1}"
PROJECT_PREFIX="${PROJECT_PREFIX:-thematic-podcast}"

# Détecter le compte AWS si non défini
if [ -z "$AWS_ACCOUNT_ID" ]; then
    echo "Détection automatique de l'AWS Account ID..."
    AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo "")
    if [ -z "$AWS_ACCOUNT_ID" ]; then
        echo "Erreur : Impossible de récupérer l'AWS Account ID. Vérifiez vos identifiants AWS CLI."
        exit 1
    fi
fi

S3_BUCKET_NAME="${S3_BUCKET_NAME:-${PROJECT_PREFIX}-audio-${AWS_ACCOUNT_ID}}"
IAM_ROLE_NAME="${IAM_ROLE_NAME:-${PROJECT_PREFIX}-lambda-execution-role}"
SFN_ROLE_NAME="${SFN_ROLE_NAME:-${PROJECT_PREFIX}-stepfunctions-role}"
SPOTIFY_SECRET_NAME="${SPOTIFY_SECRET_NAME:-${PROJECT_PREFIX}-SpotifyCredentials}"

echo "=============================================================================="
echo "PRÉPARATION DE L'INFRASTRUCTURE AWS : ${PROJECT_PREFIX}"
echo "=============================================================================="
echo "Compte AWS      : $AWS_ACCOUNT_ID"
echo "Région AWS      : $AWS_REGION"
echo "Bucket S3       : $S3_BUCKET_NAME"
echo "Rôle IAM Lambda : $IAM_ROLE_NAME"
echo "Rôle IAM SFN    : $SFN_ROLE_NAME"
echo "=============================================================================="

# 1. Création du Bucket S3 (s'il n'existe pas déjà)
echo "1. Vérification / Création du bucket S3 ($S3_BUCKET_NAME)..."
if ! aws s3api head-bucket --bucket "$S3_BUCKET_NAME" --region "$AWS_REGION" 2>/dev/null; then
    echo "Création du bucket S3 $S3_BUCKET_NAME..."
    if [ "$AWS_REGION" = "us-east-1" ]; then
        aws s3api create-bucket --bucket "$S3_BUCKET_NAME" --region "$AWS_REGION"
    else
        aws s3api create-bucket \
            --bucket "$S3_BUCKET_NAME" \
            --region "$AWS_REGION" \
            --create-bucket-configuration LocationConstraint="$AWS_REGION"
    fi
    echo "Bucket S3 créé avec succès."
else
    echo "Le bucket S3 existe déjà."
fi

# 2. Création du rôle IAM d'exécution pour les Lambdas
echo "2. Configuration du rôle IAM Lambda ($IAM_ROLE_NAME)..."
cat << 'EOF' > /tmp/lambda-trust-policy.json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "lambda.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

if ! aws iam get-role --role-name "$IAM_ROLE_NAME" 2>/dev/null; then
    echo "Création du rôle IAM Lambda..."
    aws iam create-role \
        --role-name "$IAM_ROLE_NAME" \
        --assume-role-policy-document file:///tmp/lambda-trust-policy.json
else
    echo "Le rôle IAM Lambda existe déjà."
fi

# Permissions de logs CloudWatch
aws iam attach-role-policy \
    --role-name "$IAM_ROLE_NAME" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole 2>/dev/null || true

# Politique personnalisée pour S3, SecretsManager et Step Functions
cat << EOF > /tmp/lambda-permissions-policy.json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:GetObject",
        "s3:DeleteObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::$S3_BUCKET_NAME",
        "arn:aws:s3:::$S3_BUCKET_NAME/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue"
      ],
      "Resource": "arn:aws:secretsmanager:${AWS_REGION}:${AWS_ACCOUNT_ID}:secret:${SPOTIFY_SECRET_NAME}*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "states:StartExecution"
      ],
      "Resource": "arn:aws:states:${AWS_REGION}:${AWS_ACCOUNT_ID}:stateMachine:${PROJECT_PREFIX}*"
    }
  ]
}
EOF

echo "Attachement de la politique de permissions au rôle Lambda..."
aws iam put-role-policy \
    --role-name "$IAM_ROLE_NAME" \
    --policy-name "${PROJECT_PREFIX}-lambda-policy" \
    --policy-document file:///tmp/lambda-permissions-policy.json

# 3. Création du rôle IAM pour Step Functions
echo "3. Configuration du rôle IAM Step Functions ($SFN_ROLE_NAME)..."
cat << 'EOF' > /tmp/sfn-trust-policy.json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "states.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

if ! aws iam get-role --role-name "$SFN_ROLE_NAME" 2>/dev/null; then
    echo "Création du rôle IAM Step Functions..."
    aws iam create-role \
        --role-name "$SFN_ROLE_NAME" \
        --assume-role-policy-document file:///tmp/sfn-trust-policy.json
else
    echo "Le rôle IAM Step Functions existe déjà."
fi

cat << EOF > /tmp/sfn-permissions-policy.json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "lambda:InvokeFunction"
      ],
      "Resource": "arn:aws:lambda:${AWS_REGION}:${AWS_ACCOUNT_ID}:function:${PROJECT_PREFIX}*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "states:StartExecution",
        "states:DescribeExecution",
        "states:StopExecution"
      ],
      "Resource": "arn:aws:states:${AWS_REGION}:${AWS_ACCOUNT_ID}:stateMachine:${PROJECT_PREFIX}*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "events:PutTargets",
        "events:PutRule",
        "events:DescribeRule"
      ],
      "Resource": "*"
    }
  ]
}
EOF

aws iam put-role-policy \
    --role-name "$SFN_ROLE_NAME" \
    --policy-name "${PROJECT_PREFIX}-stepfunctions-policy" \
    --policy-document file:///tmp/sfn-permissions-policy.json

# Nettoyage des fichiers temporaires
rm -f /tmp/lambda-trust-policy.json /tmp/lambda-permissions-policy.json /tmp/sfn-trust-policy.json /tmp/sfn-permissions-policy.json

echo "=============================================================================="
echo "SUCCÈS ! Infrastructure AWS pour '${PROJECT_PREFIX}' initialisée."
echo "Bucket S3      : $S3_BUCKET_NAME"
echo "Rôle Lambda    : arn:aws:iam::${AWS_ACCOUNT_ID}:role/${IAM_ROLE_NAME}"
echo "Rôle SFN       : arn:aws:iam::${AWS_ACCOUNT_ID}:role/${SFN_ROLE_NAME}"
echo "=============================================================================="

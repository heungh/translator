#!/usr/bin/env bash
set -euo pipefail

# ===========================================================================
# deploy.sh — Interactive provisioning for the Translator app
#
# Creates/verifies: S3 bucket, DynamoDB table, IAM policy, SSM parameters
# Idempotent: safe to re-run (skips existing resources)
# ===========================================================================

# ── Constants ──────────────────────────────────────────────────────────────
SSM_PREFIX="/translator"
POLICY_NAME="TranslatorAppPolicy"
DEFAULT_REGION="ap-northeast-2"
DEFAULT_BUCKET="my-translation-prompts"
DEFAULT_TABLE="translator"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── Color helpers ──────────────────────────────────────────────────────────
info()    { printf '\033[1;34m[INFO]\033[0m  %s\n' "$*"; }
success() { printf '\033[1;32m[OK]\033[0m    %s\n' "$*"; }
warn()    { printf '\033[1;33m[WARN]\033[0m  %s\n' "$*"; }
die()     { printf '\033[1;31m[ERROR]\033[0m %s\n' "$*" >&2; exit 1; }

# ── Pre-flight checks ─────────────────────────────────────────────────────
info "Checking prerequisites..."

command -v aws   >/dev/null 2>&1 || die "AWS CLI not found. Install: https://aws.amazon.com/cli/"
command -v python3 >/dev/null 2>&1 || die "python3 not found."

aws sts get-caller-identity >/dev/null 2>&1 \
    || die "AWS credentials not configured. Run 'aws configure' first."

CALLER_ARN=$(aws sts get-caller-identity --query Arn --output text)
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
success "AWS identity: $CALLER_ARN"

# ── Interactive input ──────────────────────────────────────────────────────
echo ""
info "=== Translator App Provisioning ==="
echo ""

read -rp "AWS Region       [$DEFAULT_REGION]: " REGION
REGION="${REGION:-$DEFAULT_REGION}"

read -rp "S3 Bucket name   [$DEFAULT_BUCKET]: " BUCKET
BUCKET="${BUCKET:-$DEFAULT_BUCKET}"

read -rp "DynamoDB Table   [$DEFAULT_TABLE]: " TABLE
TABLE="${TABLE:-$DEFAULT_TABLE}"

echo ""
info "Configuration summary:"
echo "  Region : $REGION"
echo "  Bucket : $BUCKET"
echo "  Table  : $TABLE"
echo ""
read -rp "Proceed? [Y/n]: " CONFIRM
CONFIRM="${CONFIRM:-Y}"
[[ "$CONFIRM" =~ ^[Yy]$ ]] || { info "Aborted."; exit 0; }

# ── Python virtual environment ─────────────────────────────────────────────
echo ""
info "Setting up Python environment..."

if [ ! -d "$SCRIPT_DIR/.venv" ]; then
    python3 -m venv "$SCRIPT_DIR/.venv"
    success "Created .venv"
else
    success ".venv already exists"
fi

source "$SCRIPT_DIR/.venv/bin/activate"

pip install --quiet --upgrade pip
pip install --quiet boto3 python-dotenv streamlit python-docx
success "Python dependencies installed"

# ── S3 Bucket ──────────────────────────────────────────────────────────────
echo ""
info "Provisioning S3 bucket: $BUCKET"

if aws s3api head-bucket --bucket "$BUCKET" --region "$REGION" 2>/dev/null; then
    success "S3 bucket already exists"
else
    if [ "$REGION" = "us-east-1" ]; then
        aws s3api create-bucket \
            --bucket "$BUCKET" \
            --region "$REGION"
    else
        aws s3api create-bucket \
            --bucket "$BUCKET" \
            --region "$REGION" \
            --create-bucket-configuration LocationConstraint="$REGION"
    fi
    success "S3 bucket created"
fi

# ── DynamoDB Table ─────────────────────────────────────────────────────────
info "Provisioning DynamoDB table: $TABLE"

if aws dynamodb describe-table --table-name "$TABLE" --region "$REGION" >/dev/null 2>&1; then
    success "DynamoDB table already exists"
else
    aws dynamodb create-table \
        --table-name "$TABLE" \
        --region "$REGION" \
        --billing-mode PAY_PER_REQUEST \
        --key-schema \
            AttributeName=PK,KeyType=HASH \
            AttributeName=SK,KeyType=RANGE \
        --attribute-definitions \
            AttributeName=PK,AttributeType=S \
            AttributeName=SK,AttributeType=S \
            AttributeName=GSI1_PK,AttributeType=S \
            AttributeName=GSI1_SK,AttributeType=S \
            AttributeName=GSI2_PK,AttributeType=S \
            AttributeName=GSI2_SK,AttributeType=S \
        --global-secondary-indexes \
            'IndexName=GSI1,KeySchema=[{AttributeName=GSI1_PK,KeyType=HASH},{AttributeName=GSI1_SK,KeyType=RANGE}],Projection={ProjectionType=ALL}' \
            'IndexName=GSI2,KeySchema=[{AttributeName=GSI2_PK,KeyType=HASH},{AttributeName=GSI2_SK,KeyType=RANGE}],Projection={ProjectionType=ALL}' \
        >/dev/null

    info "Waiting for table to become active..."
    aws dynamodb wait table-exists --table-name "$TABLE" --region "$REGION"
    success "DynamoDB table created"
fi

# ── IAM Policy ─────────────────────────────────────────────────────────────
info "Provisioning IAM policy: $POLICY_NAME"

POLICY_DOC=$(cat <<EOFPOLICY
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "DynamoDBAccess",
            "Effect": "Allow",
            "Action": [
                "dynamodb:PutItem",
                "dynamodb:GetItem",
                "dynamodb:UpdateItem",
                "dynamodb:DeleteItem",
                "dynamodb:Query",
                "dynamodb:DescribeTable",
                "dynamodb:CreateTable",
                "dynamodb:ListTables"
            ],
            "Resource": [
                "arn:aws:dynamodb:${REGION}:${ACCOUNT_ID}:table/${TABLE}",
                "arn:aws:dynamodb:${REGION}:${ACCOUNT_ID}:table/${TABLE}/index/*"
            ]
        },
        {
            "Sid": "S3Access",
            "Effect": "Allow",
            "Action": [
                "s3:PutObject",
                "s3:GetObject",
                "s3:DeleteObject",
                "s3:ListBucket",
                "s3:HeadBucket",
                "s3:CreateBucket"
            ],
            "Resource": [
                "arn:aws:s3:::${BUCKET}",
                "arn:aws:s3:::${BUCKET}/*"
            ]
        },
        {
            "Sid": "SSMGetParameters",
            "Effect": "Allow",
            "Action": [
                "ssm:GetParameter",
                "ssm:GetParameters",
                "ssm:GetParametersByPath",
                "ssm:PutParameter"
            ],
            "Resource": "arn:aws:ssm:${REGION}:${ACCOUNT_ID}:parameter/translator/*"
        }
    ]
}
EOFPOLICY
)

POLICY_ARN="arn:aws:iam::${ACCOUNT_ID}:policy/${POLICY_NAME}"

if aws iam get-policy --policy-arn "$POLICY_ARN" >/dev/null 2>&1; then
    # Update existing policy: create new version, delete oldest if at limit
    VERSIONS=$(aws iam list-policy-versions --policy-arn "$POLICY_ARN" \
        --query 'Versions[?IsDefaultVersion==`false`].VersionId' --output text)
    VERSION_COUNT=$(echo "$VERSIONS" | wc -w | tr -d ' ')

    if [ "$VERSION_COUNT" -ge 4 ]; then
        OLDEST=$(echo "$VERSIONS" | awk '{print $NF}')
        aws iam delete-policy-version --policy-arn "$POLICY_ARN" --version-id "$OLDEST"
        info "Deleted oldest policy version: $OLDEST"
    fi

    aws iam create-policy-version \
        --policy-arn "$POLICY_ARN" \
        --policy-document "$POLICY_DOC" \
        --set-as-default >/dev/null
    success "IAM policy updated"
else
    aws iam create-policy \
        --policy-name "$POLICY_NAME" \
        --policy-document "$POLICY_DOC" >/dev/null
    success "IAM policy created"
fi

# Attach policy to caller (user or role)
if echo "$CALLER_ARN" | grep -q ":user/"; then
    USERNAME=$(echo "$CALLER_ARN" | sed 's|.*/||')
    aws iam attach-user-policy --user-name "$USERNAME" --policy-arn "$POLICY_ARN" 2>/dev/null \
        && success "Policy attached to user: $USERNAME" \
        || warn "Could not attach policy to user (may already be attached or insufficient permissions)"
elif echo "$CALLER_ARN" | grep -qE ":(role|assumed-role)/"; then
    ROLE_NAME=$(echo "$CALLER_ARN" | sed 's|.*:\(role\|assumed-role\)/||' | cut -d'/' -f1)
    aws iam attach-role-policy --role-name "$ROLE_NAME" --policy-arn "$POLICY_ARN" 2>/dev/null \
        && success "Policy attached to role: $ROLE_NAME" \
        || warn "Could not attach policy to role (may already be attached or insufficient permissions)"
else
    warn "Unknown principal type in ARN: $CALLER_ARN — attach policy manually"
fi

# ── SSM Parameter Store ───────────────────────────────────────────────────
echo ""
info "Storing configuration in SSM Parameter Store..."

aws ssm put-parameter \
    --name "${SSM_PREFIX}/PROMPT_S3_BUCKET" \
    --value "$BUCKET" \
    --type String \
    --overwrite \
    --region "$REGION" >/dev/null
success "SSM: ${SSM_PREFIX}/PROMPT_S3_BUCKET = $BUCKET"

aws ssm put-parameter \
    --name "${SSM_PREFIX}/PROMPT_DYNAMO_TABLE" \
    --value "$TABLE" \
    --type String \
    --overwrite \
    --region "$REGION" >/dev/null
success "SSM: ${SSM_PREFIX}/PROMPT_DYNAMO_TABLE = $TABLE"

aws ssm put-parameter \
    --name "${SSM_PREFIX}/PROMPT_AWS_REGION" \
    --value "$REGION" \
    --type String \
    --overwrite \
    --region "$REGION" >/dev/null
success "SSM: ${SSM_PREFIX}/PROMPT_AWS_REGION = $REGION"

# ── Cleanup & file generation ─────────────────────────────────────────────
echo ""
info "Generating project files..."

# .env.sample
cat > "$SCRIPT_DIR/.env.sample" <<'EOFSAMPLE'
# AWS Configuration for Translator
# Values are stored in SSM Parameter Store: /translator/
# For local development, copy this to .env and fill in values
PROMPT_S3_BUCKET=
PROMPT_DYNAMO_TABLE=
PROMPT_AWS_REGION=
EOFSAMPLE
success "Created .env.sample"

# .gitignore
cat > "$SCRIPT_DIR/.gitignore" <<'EOFGIT'
.env
.venv/
__pycache__/
*.pyc
.DS_Store
~$*
.idea/
.vscode/
EOFGIT
success "Created .gitignore"

# Remove legacy .env.example
if [ -f "$SCRIPT_DIR/.env.example" ]; then
    rm "$SCRIPT_DIR/.env.example"
    success "Removed .env.example (replaced by .env.sample)"
fi

# ── Summary ────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
success "Deployment complete!"
echo ""
echo "  Resources:"
echo "    S3 Bucket      : $BUCKET"
echo "    DynamoDB Table : $TABLE"
echo "    IAM Policy     : $POLICY_NAME"
echo "    SSM Prefix     : $SSM_PREFIX/"
echo ""
echo "  Config priority: SSM → .env → defaults"
echo ""
echo "  Run the app:"
echo "    source .venv/bin/activate"
echo "    streamlit run app_translator.py"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

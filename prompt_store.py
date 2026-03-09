"""
Prompt & Job Store — DynamoDB single-table design for 'translator'.

Two entities in one table:
  1. PromptVersion — glossary version metadata (S3 for actual JSON files)
  2. TranslationJob — translation job tracking

Config priority: SSM Parameter Store → os.environ (.env) → hardcoded defaults.
"""

import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

from glossary_manager import load_glossary_layer, save_glossary_layer


# ---------------------------------------------------------------------------
# SSM Parameter Store cache
# ---------------------------------------------------------------------------

_SSM_PREFIX = "/translator"
_ssm_cache: dict[str, str] = {}
_ssm_loaded = False


def _load_ssm_config():
    """Load all /translator/* parameters from SSM once. Silently skip on failure."""
    global _ssm_cache, _ssm_loaded
    if _ssm_loaded:
        return
    _ssm_loaded = True
    try:
        region = os.environ.get("PROMPT_AWS_REGION", "ap-northeast-2")
        ssm = boto3.client("ssm", region_name=region)
        resp = ssm.get_parameters_by_path(Path=_SSM_PREFIX, Recursive=True)
        for p in resp.get("Parameters", []):
            _ssm_cache[p["Name"].rsplit("/", 1)[-1]] = p["Value"]
    except Exception:
        pass  # SSM unavailable — fall back to env / defaults


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _env(key: str, default: str = "") -> str:
    """SSM cache → os.environ → default."""
    _load_ssm_config()
    if key in _ssm_cache:
        return _ssm_cache[key]
    return os.environ.get(key, default)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_ts() -> str:
    """Compact timestamp for sort keys: 2026-03-05T143022"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S")


def _table_name() -> str:
    return _env("PROMPT_DYNAMO_TABLE", "translator")


def _region() -> str:
    return _env("PROMPT_AWS_REGION", "ap-northeast-2")


def _bucket() -> str:
    return _env("PROMPT_S3_BUCKET", "my-translation-prompts")


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------

_resources_checked = False


def _ensure_resources():
    """Auto-create S3 bucket and DynamoDB table if they don't exist. Runs once."""
    global _resources_checked
    if _resources_checked:
        return
    _resources_checked = True

    region = _region()
    bucket = _bucket()
    table_name = _table_name()

    # --- S3 bucket ---
    s3 = boto3.client("s3", region_name=region)
    try:
        s3.head_bucket(Bucket=bucket)
    except s3.exceptions.ClientError:
        try:
            create_args = {"Bucket": bucket}
            if region != "us-east-1":
                create_args["CreateBucketConfiguration"] = {
                    "LocationConstraint": region,
                }
            s3.create_bucket(**create_args)
        except Exception as e:
            print(f"[prompt_store] Could not create S3 bucket '{bucket}': {e}")

    # --- DynamoDB table ---
    ensure_table_exists()


def get_dynamodb_table():
    """Return a boto3 DynamoDB Table resource (auto-creates if needed)."""
    _ensure_resources()
    dynamodb = boto3.resource("dynamodb", region_name=_region())
    return dynamodb.Table(_table_name())


def get_s3_client():
    """Return a boto3 S3 client (auto-creates bucket if needed)."""
    _ensure_resources()
    return boto3.client("s3", region_name=_region())


def ensure_table_exists():
    """Create the 'translator' table with GSI1 + GSI2 if it does not exist."""
    table_name = _table_name()
    dynamodb = boto3.client("dynamodb", region_name=_region())

    existing = dynamodb.list_tables()["TableNames"]
    if table_name in existing:
        return False

    dynamodb.create_table(
        TableName=table_name,
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "GSI1_PK", "AttributeType": "S"},
            {"AttributeName": "GSI1_SK", "AttributeType": "S"},
            {"AttributeName": "GSI2_PK", "AttributeType": "S"},
            {"AttributeName": "GSI2_SK", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "GSI1",
                "KeySchema": [
                    {"AttributeName": "GSI1_PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI1_SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
            {
                "IndexName": "GSI2",
                "KeySchema": [
                    {"AttributeName": "GSI2_PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI2_SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    waiter = dynamodb.get_waiter("table_exists")
    waiter.wait(TableName=table_name)
    return True


# ---------------------------------------------------------------------------
# PromptVersion CRUD
# ---------------------------------------------------------------------------

def _count_items(glossary: dict) -> dict:
    """Count items per category in a glossary dict."""
    return {
        "characters": len(glossary.get("characters", [])),
        "places": len(glossary.get("places", [])),
        "terms": len(glossary.get("terms", [])),
    }


def save_version(project_id: str, layer: str, glossary: dict,
                 title: str, purpose: str = "") -> str:
    """Save a glossary version: upload JSON to S3, write metadata to DynamoDB.

    Returns the version_id (= layer#timestamp).
    """
    ts = _now_ts()
    version_id = f"{layer}#{ts}"

    # S3 upload
    s3_key = f"prompts/{project_id}/{layer}/{ts}.json"
    s3 = get_s3_client()
    s3.put_object(
        Bucket=_bucket(),
        Key=s3_key,
        Body=json.dumps(glossary, ensure_ascii=False, indent=2).encode("utf-8"),
        ContentType="application/json",
    )

    # DynamoDB put — single-table keys
    pk = f"PROJECT#{project_id}"
    sk = f"PROMPT#{layer}#{ts}"
    now = _now_iso()

    table = get_dynamodb_table()
    table.put_item(Item={
        "PK": pk,
        "SK": sk,
        "entity_type": "PROMPT_VERSION",
        "project_id": project_id,
        "title": title,
        "purpose": purpose,
        "layer": layer,
        "s3_key": s3_key,
        "created_at": now,
        "item_counts": _count_items(glossary),
        # GSI keys
        "GSI1_PK": "PROMPT_VERSION",
        "GSI1_SK": ts,
        "GSI2_PK": f"PROJECT#{project_id}#PROMPT",
        "GSI2_SK": f"{layer}#{ts}",
    })

    return version_id


def list_versions(project_id: str, layer: str | None = None) -> list[dict]:
    """List prompt versions for a project, optionally filtered by layer. Newest first."""
    table = get_dynamodb_table()
    pk = f"PROJECT#{project_id}"

    if layer:
        sk_prefix = f"PROMPT#{layer}#"
    else:
        sk_prefix = "PROMPT#"

    resp = table.query(
        KeyConditionExpression=(
            Key("PK").eq(pk) & Key("SK").begins_with(sk_prefix)
        ),
        ScanIndexForward=False,
    )
    return resp.get("Items", [])


def load_version(project_id: str, version_id: str) -> dict:
    """Load a specific version: get S3 key from DynamoDB, download JSON from S3.

    version_id format: layer#timestamp (e.g. work#2026-03-05T143022)
    """
    table = get_dynamodb_table()
    pk = f"PROJECT#{project_id}"
    # version_id = "layer#ts" → SK = "PROMPT#layer#ts"
    sk = f"PROMPT#{version_id}"

    resp = table.get_item(Key={"PK": pk, "SK": sk})
    item = resp.get("Item")
    if not item:
        raise ValueError(f"Version not found: {project_id}/{version_id}")

    s3 = get_s3_client()
    obj = s3.get_object(Bucket=_bucket(), Key=item["s3_key"])
    return json.loads(obj["Body"].read().decode("utf-8"))


def delete_version(project_id: str, version_id: str):
    """Delete a version from both DynamoDB and S3."""
    table = get_dynamodb_table()
    pk = f"PROJECT#{project_id}"
    sk = f"PROMPT#{version_id}"

    resp = table.get_item(Key={"PK": pk, "SK": sk})
    item = resp.get("Item")
    if item:
        s3 = get_s3_client()
        s3.delete_object(Bucket=_bucket(), Key=item["s3_key"])
        table.delete_item(Key={"PK": pk, "SK": sk})


def restore_version(project_id: str, version_id: str, target_path: str):
    """Load a version from S3 and save it to a local glossary file."""
    glossary = load_version(project_id, version_id)
    save_glossary_layer(glossary, target_path)
    return glossary


# ---------------------------------------------------------------------------
# TranslationJob CRUD
# ---------------------------------------------------------------------------

def prompt_hash(system_prompt: str) -> str:
    """Return first 16 hex chars of SHA-256 hash."""
    return hashlib.sha256(system_prompt.encode()).hexdigest()[:16]


def create_job(
    project_id: str,
    project_name: str,
    genre: str,
    model_name: str,
    model_id: str,
    engine: str,
    region: str,
    chunk_size: int,
    glossary_matched: dict,
    glossary_total: dict,
    system_prompt_hash: str,
    input_mode: str,
    filename: str,
    paragraph_count: int,
    total_chars: int,
    est_chunks: int,
) -> dict:
    """Create a new translation job (status=in_progress). Returns key info."""
    ts = _now_ts()
    pk = f"PROJECT#{project_id}"
    sk = f"JOB#{ts}"
    now = _now_iso()

    item = {
        "PK": pk,
        "SK": sk,
        "entity_type": "TRANSLATION_JOB",
        "project_id": project_id,
        "project_name": project_name,
        "genre": genre,
        # model
        "model_name": model_name,
        "model_id": model_id,
        "engine": engine,
        "region": region,
        # prompt
        "glossary_matched": glossary_matched,
        "glossary_total": glossary_total,
        "system_prompt_hash": system_prompt_hash,
        # input
        "input_mode": input_mode,
        "filename": filename,
        "paragraph_count": paragraph_count,
        "total_chars": total_chars,
        "chunk_size": chunk_size,
        "est_chunks": est_chunks,
        # status
        "status": "in_progress",
        "started_at": now,
        "created_at": now,
        # GSI keys
        "GSI1_PK": "JOB_STATUS#in_progress",
        "GSI1_SK": ts,
        "GSI2_PK": "TRANSLATION_JOB",
        "GSI2_SK": ts,
    }

    table = get_dynamodb_table()
    table.put_item(Item=item)

    return {"pk": pk, "sk": sk, "job_id": ts}


def update_job_completed(pk: str, sk: str, output_chars: int, chunks_processed: int):
    """Mark job as completed with result metrics."""
    now = _now_iso()
    ts = _now_ts()

    table = get_dynamodb_table()
    # Get started_at to compute duration
    resp = table.get_item(Key={"PK": pk, "SK": sk}, ProjectionExpression="started_at")
    item = resp.get("Item", {})
    started_at = item.get("started_at", now)

    start_dt = datetime.fromisoformat(started_at)
    end_dt = datetime.fromisoformat(now)
    duration = Decimal(str(round((end_dt - start_dt).total_seconds(), 1)))

    table.update_item(
        Key={"PK": pk, "SK": sk},
        UpdateExpression=(
            "SET #st = :status, completed_at = :completed_at, "
            "duration_seconds = :dur, output_chars = :oc, "
            "chunks_processed = :cp, GSI1_PK = :g1pk, GSI1_SK = :g1sk"
        ),
        ExpressionAttributeNames={"#st": "status"},
        ExpressionAttributeValues={
            ":status": "completed",
            ":completed_at": now,
            ":dur": duration,
            ":oc": output_chars,
            ":cp": chunks_processed,
            ":g1pk": "JOB_STATUS#completed",
            ":g1sk": ts,
        },
    )


def update_job_failed(pk: str, sk: str, error_message: str, chunks_processed: int = 0):
    """Mark job as failed with error info."""
    now = _now_iso()
    ts = _now_ts()

    table = get_dynamodb_table()
    resp = table.get_item(Key={"PK": pk, "SK": sk}, ProjectionExpression="started_at")
    item = resp.get("Item", {})
    started_at = item.get("started_at", now)

    start_dt = datetime.fromisoformat(started_at)
    end_dt = datetime.fromisoformat(now)
    duration = Decimal(str(round((end_dt - start_dt).total_seconds(), 1)))

    table.update_item(
        Key={"PK": pk, "SK": sk},
        UpdateExpression=(
            "SET #st = :status, completed_at = :completed_at, "
            "duration_seconds = :dur, error_message = :err, "
            "chunks_processed = :cp, GSI1_PK = :g1pk, GSI1_SK = :g1sk"
        ),
        ExpressionAttributeNames={"#st": "status"},
        ExpressionAttributeValues={
            ":status": "failed",
            ":completed_at": now,
            ":dur": duration,
            ":err": error_message,
            ":cp": chunks_processed,
            ":g1pk": "JOB_STATUS#failed",
            ":g1sk": ts,
        },
    )


def list_jobs(project_id: str, limit: int = 20) -> list[dict]:
    """List translation jobs for a project, newest first."""
    table = get_dynamodb_table()
    pk = f"PROJECT#{project_id}"

    resp = table.query(
        KeyConditionExpression=(
            Key("PK").eq(pk) & Key("SK").begins_with("JOB#")
        ),
        ScanIndexForward=False,
        Limit=limit,
    )
    return resp.get("Items", [])


def get_job(project_id: str, job_id: str) -> dict:
    """Get a single translation job."""
    table = get_dynamodb_table()
    pk = f"PROJECT#{project_id}"
    sk = f"JOB#{job_id}"

    resp = table.get_item(Key={"PK": pk, "SK": sk})
    item = resp.get("Item")
    if not item:
        raise ValueError(f"Job not found: {project_id}/{job_id}")
    return item

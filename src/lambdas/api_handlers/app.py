"""API handlers for resume tailoring platform."""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.parse
import uuid
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

UPLOAD_BUCKET = os.getenv("UPLOAD_BUCKET_NAME", "")
ARTIFACT_BUCKET = os.getenv("ARTIFACT_BUCKET_NAME", "")
JOB_TABLE = os.getenv("JOB_TABLE_NAME", "")
STATE_MACHINE_ARN = os.getenv("STATE_MACHINE_ARN", "")
DEFAULT_TENANT_KEY = os.getenv("DEFAULT_TENANT_KEY", "tenantId")
ARTIFACT_TTL_DAYS = int(os.getenv("ARTIFACT_TTL_DAYS", "7"))

s3_client = boto3.client("s3")
ddb_client = boto3.client("dynamodb")
sfn_client = boto3.client("stepfunctions")


def lambda_handler(event: Dict, _context) -> Dict:
    # EventBridge housekeeping hook
    if event.get("source") == "aws.events" or event.get("action") == "housekeeping":
        return respond(200, {"message": "Housekeeping executed", "deleted": perform_housekeeping()})

    method = (event.get("httpMethod") or "GET").upper()
    raw_path = event.get("path", "/")
    path = raw_path.split("?")[0]

    try:
        if path.endswith("/uploadJD") and method == "POST":
            return upload_document(event, kind="jd")
        if path.endswith("/uploadResume") and method == "POST":
            return upload_document(event, kind="resume")
        if path.endswith("/tailor") and method == "POST":
            return start_tailoring(event)
        if "/status/" in path and method == "GET":
            job_id = path.rsplit("/", 1)[-1]
            return get_status(event, job_id)
        if "/download/" in path and method == "GET":
            job_id = path.rsplit("/", 1)[-1]
            return get_download_links(event, job_id)
        if path.endswith("/artifacts") and method == "GET":
            return list_artifacts(event)
    except Exception as exc:  # pylint: disable=broad-except
        return respond(500, {"message": str(exc)})

    return respond(404, {"message": f"Route not found for {method} {path}"})


def upload_document(event: Dict, kind: str) -> Dict:
    body = parse_body(event)
    tenant_id = body.get("tenantId")
    if not tenant_id:
        return respond(400, {"message": "tenantId is required"})
    filename = body.get("fileName") or f"{kind}-{uuid.uuid4()}"
    extension = body.get("extension", "txt")
    content = body.get("content")
    if not content:
        return respond(400, {"message": "content is required"})
    decoded = base64.b64decode(content)
    content_type = body.get("contentType", "application/octet-stream")
    key = f"{tenant_id}/{kind}/{uuid.uuid4()}.{extension}"
    try:
        s3_client.put_object(
            Bucket=UPLOAD_BUCKET,
            Key=key,
            Body=decoded,
            ContentType=content_type,
            ServerSideEncryption="aws:kms",
        )
    except (ClientError, BotoCoreError) as exc:
        return respond(500, {"message": f"Failed to store document: {exc}"})
    metadata = body.get("metadata", {})
    record_upload_metadata(tenant_id, key, kind, metadata)
    return respond(200, {"key": key, "bucket": UPLOAD_BUCKET})


def record_upload_metadata(tenant_id: str, key: str, kind: str, metadata: Dict) -> None:
    if not JOB_TABLE:
        return
    item = {
        "tenantJobId": {"S": f"{tenant_id}#{key}"},
        "entityType": {"S": "UPLOAD"},
        "metadata": {"S": json.dumps({"kind": kind, **metadata})},
        "createdAt": {"N": str(int(time.time()))},
    }
    try:
        ddb_client.put_item(TableName=JOB_TABLE, Item=item)
    except (ClientError, BotoCoreError):
        # Non-fatal - proceed without failing upload
        pass


def start_tailoring(event: Dict) -> Dict:
    body = parse_body(event)
    tenant_id = body.get("tenantId")
    job_id = body.get("jobId") or str(uuid.uuid4())
    if not tenant_id:
        return respond(400, {"message": "tenantId is required"})

    execution_input = body.get("executionInput")
    if not execution_input:
        execution_input = {
            "tenantId": tenant_id,
            "jobId": job_id,
            "jobDescription": {"s3Key": body.get("jobDescriptionKey")},
            "baseResume": {"s3Key": body.get("baseResumeKey")},
            "validatedResumes": body.get("validatedResumes", []),
            "options": body.get("options", {}),
        }

    if not execution_input.get("jobDescription") or not execution_input.get("baseResume"):
        return respond(400, {"message": "jobDescription and baseResume inputs are required"})

    try:
        execution = sfn_client.start_execution(
            stateMachineArn=STATE_MACHINE_ARN,
            name=f"{tenant_id}-{job_id}-{int(time.time())}",
            input=json.dumps(execution_input),
        )
    except (ClientError, BotoCoreError) as exc:
        return respond(500, {"message": f"Failed to start tailoring job: {exc}"})

    if JOB_TABLE:
        ddb_client.put_item(
            TableName=JOB_TABLE,
            Item={
                "tenantJobId": {"S": f"{tenant_id}#{job_id}"},
                "entityType": {"S": "STATUS"},
                "status": {"S": "RUNNING"},
                "executionArn": {"S": execution["executionArn"]},
                "updatedAt": {"N": str(int(time.time()))},
            },
        )

    return respond(200, {"executionArn": execution.get("executionArn"), "jobId": job_id})


def get_status(event: Dict, job_id: str) -> Dict:
    tenant_id = extract_tenant(event)
    if not tenant_id:
        return respond(400, {"message": "tenantId missing"})
    if not JOB_TABLE:
        return respond(200, {"jobId": job_id, "status": "UNKNOWN"})
    try:
        response = ddb_client.get_item(
            TableName=JOB_TABLE,
            Key={
                "tenantJobId": {"S": f"{tenant_id}#{job_id}"},
                "entityType": {"S": "STATUS"},
            },
        )
    except (ClientError, BotoCoreError) as exc:
        return respond(500, {"message": f"Failed to read job status: {exc}"})
    item = response.get("Item")
    status = item.get("status", {}).get("S", "UNKNOWN") if item else "UNKNOWN"
    return respond(200, {"jobId": job_id, "status": status})


def get_download_links(event: Dict, job_id: str) -> Dict:
    tenant_id = extract_tenant(event)
    if not tenant_id:
        return respond(400, {"message": "tenantId missing"})
    prefix = f"{tenant_id}/{job_id}"
    artifacts = list_objects_with_prefix(prefix)
    signed = {}
    for key in artifacts:
        try:
            signed[key] = s3_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": ARTIFACT_BUCKET, "Key": key},
                ExpiresIn=900,
            )
        except (ClientError, BotoCoreError) as exc:
            return respond(500, {"message": f"Failed to create signed URL: {exc}"})
    return respond(200, {"jobId": job_id, "artifacts": signed})


def list_artifacts(event: Dict) -> Dict:
    tenant_id = extract_tenant(event)
    if not tenant_id:
        return respond(400, {"message": "tenantId missing"})
    prefix = f"{tenant_id}/"
    artifacts = list_objects_with_prefix(prefix)
    return respond(200, {"artifacts": artifacts})


def list_objects_with_prefix(prefix: str) -> List[str]:
    keys: List[str] = []
    continuation_token = None
    while True:
        kwargs: Dict[str, Any] = {
            "Bucket": ARTIFACT_BUCKET,
            "Prefix": prefix,
        }
        if continuation_token:
            kwargs["ContinuationToken"] = continuation_token
        response = s3_client.list_objects_v2(**kwargs)
        for obj in response.get("Contents", []):
            keys.append(obj["Key"])
        if not response.get("IsTruncated"):
            break
        continuation_token = response.get("NextContinuationToken")
    return keys


def perform_housekeeping() -> int:
    if not ARTIFACT_BUCKET:
        return 0
    cutoff = int(time.time()) - ARTIFACT_TTL_DAYS * 86400
    deleted = 0
    response = s3_client.list_objects_v2(Bucket=ARTIFACT_BUCKET)
    for obj in response.get("Contents", []):
        if obj.get("LastModified") and obj["LastModified"].timestamp() < cutoff:
            s3_client.delete_object(Bucket=ARTIFACT_BUCKET, Key=obj["Key"])
            deleted += 1
    return deleted


def extract_tenant(event: Dict) -> Optional[str]:
    params = event.get("queryStringParameters") or {}
    if DEFAULT_TENANT_KEY in params:
        return params[DEFAULT_TENANT_KEY]
    headers = event.get("headers") or {}
    if DEFAULT_TENANT_KEY in headers:
        return headers[DEFAULT_TENANT_KEY]
    return None


def parse_body(event: Dict) -> Dict:
    body = event.get("body")
    if not body:
        return {}
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    if isinstance(body, (bytes, bytearray)):
        body = body.decode("utf-8")
    return json.loads(body)


def respond(status: int, payload: Dict) -> Dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload),
    }

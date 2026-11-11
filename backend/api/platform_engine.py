"""Governed ingestion and queued agent execution for Sudogemini.

The model can propose structured work only. Authorization, approvals, and external
side effects remain in the API/worker code, never in model-generated instructions.
"""
from __future__ import annotations
import csv, hashlib, io, json, os, re, uuid
from datetime import datetime, timezone
from typing import Any
import boto3

def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value: raise RuntimeError(f"{name} is not configured")
    return value

def _now() -> str: return datetime.now(timezone.utc).isoformat()

def enqueue_job(kind: str, payload: dict[str, Any]) -> str:
    queue_url = _required("PLATFORM_JOBS_QUEUE_URL")
    job_id = payload.get("job_id") or str(uuid.uuid4())
    message = {"kind": kind, "created_at": _now(), **payload, "job_id": job_id}
    boto3.client("sqs", region_name=os.getenv("AWS_REGION", "us-east-2")).send_message(
        QueueUrl=queue_url, MessageBody=json.dumps(message, separators=(",", ":")))
    return job_id

def create_upload_url(*, tenant_id: str, dataset_id: str, filename: str, content_type: str) -> dict:
    if content_type not in {"text/csv", "application/json", "application/x-ndjson"}:
        raise ValueError("Only CSV, JSON, or NDJSON uploads are accepted")
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", filename)[:120] or "dataset"
    key = f"incoming/{tenant_id}/{dataset_id}/{uuid.uuid4()}/{safe_name}"
    bucket = _required("INGESTION_BUCKET")
    client = boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-2"))
    url = client.generate_presigned_url("put_object", Params={
        "Bucket": bucket, "Key": key, "ContentType": content_type,
        "ServerSideEncryption": "AES256"}, ExpiresIn=600)
    return {"upload_url": url, "object_key": key, "expires_in": 600,
            "required_headers": {"Content-Type": content_type,
                                "x-amz-server-side-encryption": "AES256"}}

_SENSITIVE_HEADERS = {"ssn","social_security_number","email","email_address","phone",
"phone_number","mobile","date_of_birth","dob","passport","drivers_license",
"driver_license","credit_card","card_number","bank_account","account_number",
"medical_record_number"}

def inspect_csv_or_json(data: bytes, content_type: str) -> dict:
    if len(data) > 100 * 1024 * 1024: raise ValueError("Upload exceeds the 100 MiB processing limit")
    text = data.decode("utf-8-sig")
    if content_type == "text/csv":
        reader = csv.DictReader(io.StringIO(text)); columns = reader.fieldnames or []
        row_count = sum(1 for _ in reader)
    else:
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines: raise ValueError("The uploaded file is empty")
        first = json.loads(lines[0])
        if not isinstance(first, dict): raise ValueError("JSON/NDJSON records must be objects")
        columns = list(first.keys()); row_count = 0
        for line in lines:
            item = json.loads(line)
            if not isinstance(item, dict): raise ValueError("Every JSON/NDJSON record must be an object")
            row_count += 1
    normalized = {re.sub(r"[^a-z0-9_]", "_", str(c).lower()) for c in columns}
    sensitive = sorted(normalized.intersection(_SENSITIVE_HEADERS))
    if sensitive:
        raise ValueError("Potentially sensitive personal-data columns require separate legal/security review: " + ", ".join(sensitive))
    return {"record_count": row_count, "columns": columns[:200],
            "schema_sha256": hashlib.sha256(json.dumps(columns, ensure_ascii=False,
                separators=(",", ":")).encode()).hexdigest()}

def process_ingestion(message: dict) -> dict:
    tenant_id, dataset_id, object_key = message["tenant_id"], message["dataset_id"], message["object_key"]
    bucket, table_name = _required("INGESTION_BUCKET"), _required("DATASETS_TABLE")
    s3 = boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-2"))
    obj = s3.get_object(Bucket=bucket, Key=object_key)
    content_type = obj.get("ContentType", "application/octet-stream")
    body = obj["Body"].read(100 * 1024 * 1024 + 1)
    if len(body) > 100 * 1024 * 1024: raise ValueError("Upload exceeds the 100 MiB processing limit")
    digest = hashlib.sha256(body).hexdigest()
    result = inspect_csv_or_json(body, content_type)
    # Promote validated bytes out of the expiring staging prefix. The buyer download
    # path only ever references this immutable content-addressed key.
    filename = object_key.rsplit("/", 1)[-1]
    curated_key = f"datasets/{tenant_id}/{dataset_id}/{digest}/{filename}"
    s3.copy_object(Bucket=bucket, Key=curated_key,
        CopySource={"Bucket":bucket,"Key":object_key},
        MetadataDirective="REPLACE", ContentType=content_type,
        Metadata={"sha256":digest,"tenant_id":tenant_id,"dataset_id":dataset_id})
    table = boto3.resource("dynamodb", region_name=os.getenv("AWS_REGION", "us-east-2")).Table(table_name)
    try:
        table.update_item(Key={"tenant_id": tenant_id, "record_id": dataset_id},
            UpdateExpression=("SET ingestion_status=:done, object_key=:key, object_sha256=:digest, "
            "ingested_record_count=:count, schema_sha256=:schema, ingested_at=:at, source_content_type=:ctype"),
            ConditionExpression="license_status = :approved AND #status = :approved",
            ExpressionAttributeNames={"#status":"status"},
            ExpressionAttributeValues={":done":"validated",":key":curated_key,":digest":digest,
            ":count":result["record_count"],":schema":result["schema_sha256"],":at":_now(),
            ":ctype":content_type,":approved":"approved"})
    except Exception:
        s3.delete_object(Bucket=bucket,Key=curated_key)
        raise
    s3.delete_object(Bucket=bucket,Key=object_key)
    return {"dataset_id":dataset_id,"sha256":digest,**result,"status":"validated"}

def run_agent(message: dict) -> dict:
    model_id = _required("BEDROCK_MODEL_ID")
    task = message.get("task","")
    if not isinstance(task,str) or not task.strip() or len(task)>6000:
        raise ValueError("Agent task must be 1-6000 characters")
    agent_id = message.get("agent_id","")
    if agent_id not in {"ingestion","quality","scoping","marketplace","sales","governance"}:
        raise ValueError("Unknown agent")
    system = ("You are a governed B2B data-platform planning agent. Return ONLY a JSON object with keys "
    "summary, findings (array of strings), proposed_steps (array of strings), risk_level "
    "(low|medium|high), requires_human_approval (boolean). Never claim an action was performed. "
    "Never request or expose raw personal data, credentials, secrets, or another tenant's data. "
    "Do not approve licenses, publish listings, authorize payments, export data, or execute tools. "
    "All external actions require a separate human-approved control plane.")
    client = boto3.client("bedrock-runtime",region_name=os.getenv("AWS_REGION","us-east-2"))
    response = client.converse(modelId=model_id,system=[{"text":system}],
        messages=[{"role":"user","content":[{"text":f"Agent: {agent_id}\nTask: {task}"}]}],
        inferenceConfig={"maxTokens":1200,"temperature":0.1})
    raw = response["output"]["message"]["content"][0]["text"]
    start,end=raw.find("{"),raw.rfind("}")
    if start<0 or end<=start: raise ValueError("Model did not return required JSON")
    result=json.loads(raw[start:end+1])
    required={"summary","findings","proposed_steps","risk_level","requires_human_approval"}
    if not required.issubset(result) or result["risk_level"] not in {"low","medium","high"}:
        raise ValueError("Model output failed the agent response schema")
    result["requires_human_approval"]=True; result["agent_id"]=agent_id
    return result

def save_job_result(message: dict, result: dict, status: str="completed") -> None:
    table_name=_required("AGENT_JOBS_TABLE")
    table=boto3.resource("dynamodb",region_name=os.getenv("AWS_REGION","us-east-2")).Table(table_name)
    table.update_item(Key={"tenant_id":message["tenant_id"],"job_id":message["job_id"]},
        UpdateExpression="SET #s=:status, result_json=:result, completed_at=:at",
        ExpressionAttributeNames={"#s":"status"},
        ExpressionAttributeValues={":status":status,":result":json.dumps(result),":at":_now()})

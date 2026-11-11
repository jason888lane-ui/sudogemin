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

def _validate_schema(columns: list[str], row_count: int) -> dict:
    if row_count < 1:
        raise ValueError("The uploaded dataset contains no records")
    normalized = {
        re.sub(r"[^a-z0-9_]", "_", str(column).lower()) for column in columns
    }
    sensitive = sorted(normalized.intersection(_SENSITIVE_HEADERS))
    if sensitive:
        raise ValueError(
            "Potentially sensitive personal-data columns require separate legal/security review: "
            + ", ".join(sensitive)
        )
    return {
        "record_count": row_count,
        "columns": columns[:200],
        "schema_sha256": hashlib.sha256(
            json.dumps(columns, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest(),
    }


def inspect_stream(fileobj, content_type: str) -> dict:
    """Validate CSV/NDJSON incrementally; cap JSON arrays before parsing."""
    fileobj.seek(0)
    if content_type == "application/json":
        raw = fileobj.read(10 * 1024 * 1024 + 1)
        if len(raw) > 10 * 1024 * 1024:
            raise ValueError("JSON object/array uploads are limited to 10 MiB; use NDJSON for larger files")
        return inspect_csv_or_json(raw, content_type)

    text = io.TextIOWrapper(fileobj, encoding="utf-8-sig", newline="")
    try:
        if content_type == "text/csv":
            reader = csv.reader(text, strict=True)
            columns = next(reader, [])
            if not columns or any(not str(column).strip() for column in columns):
                raise ValueError("CSV header row must contain non-empty column names")
            normalized_headers = [str(column).strip().lower() for column in columns]
            if len(set(normalized_headers)) != len(normalized_headers):
                raise ValueError("CSV column names must be unique")
            row_count = 0
            for row in reader:
                if not row:
                    continue
                if len(row) != len(columns):
                    raise ValueError("CSV records must have the same number of fields as the header")
                row_count += 1
            return _validate_schema(columns, row_count)

        if content_type == "application/x-ndjson":
            columns = None
            expected = None
            row_count = 0
            for line in text:
                if not line.strip():
                    continue
                if len(line.encode("utf-8")) > 10 * 1024 * 1024:
                    raise ValueError("An NDJSON record exceeds the 10 MiB per-record limit")
                item = json.loads(line)
                if not isinstance(item, dict):
                    raise ValueError("Every NDJSON record must be an object")
                if columns is None:
                    columns = list(item.keys())
                    expected = set(columns)
                elif set(item.keys()) != expected:
                    raise ValueError("NDJSON records must have a consistent schema")
                row_count += 1
            if columns is None:
                raise ValueError("The uploaded file is empty")
            return _validate_schema(columns, row_count)

        raise ValueError("Unsupported ingestion content type")
    finally:
        # Detach so the caller retains ownership of the temporary file.
        try:
            text.detach()
        except Exception:
            pass


_SENSITIVE_HEADERS = {"ssn","social_security_number","email","email_address","phone",
"phone_number","mobile","date_of_birth","dob","passport","drivers_license",
"driver_license","credit_card","card_number","bank_account","account_number",
"medical_record_number"}

def inspect_csv_or_json(data: bytes, content_type: str) -> dict:
    if len(data) > 100 * 1024 * 1024: raise ValueError("Upload exceeds the 100 MiB processing limit")
    text = data.decode("utf-8-sig")
    if content_type == "text/csv":
        reader = csv.reader(io.StringIO(text), strict=True)
        columns = next(reader, [])
        if not columns or any(not str(column).strip() for column in columns):
            raise ValueError("CSV header row must contain non-empty column names")
        normalized_headers = [str(column).strip().lower() for column in columns]
        if len(set(normalized_headers)) != len(normalized_headers):
            raise ValueError("CSV column names must be unique")
        row_count = 0
        for row in reader:
            if not row:
                continue
            if len(row) != len(columns):
                raise ValueError("CSV records must have the same number of fields as the header")
            row_count += 1
        if row_count < 1:
            raise ValueError("The uploaded dataset contains no records")
    elif content_type == "application/json":
        parsed = json.loads(text)
        records = [parsed] if isinstance(parsed, dict) else parsed if isinstance(parsed, list) else None
        if records is None or not records or not all(isinstance(x, dict) for x in records):
            raise ValueError("JSON must contain an object or a non-empty array of objects")
        columns = list(records[0].keys())
        if any(set(x.keys()) != set(columns) for x in records):
            raise ValueError("JSON records must have a consistent schema")
        row_count = len(records)
    else:
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines: raise ValueError("The uploaded file is empty")
        first = json.loads(lines[0])
        if not isinstance(first, dict): raise ValueError("NDJSON records must be objects")
        columns = list(first.keys()); row_count = 0
        expected = set(columns)
        for line in lines:
            item = json.loads(line)
            if not isinstance(item, dict): raise ValueError("Every NDJSON record must be an object")
            if set(item.keys()) != expected: raise ValueError("NDJSON records must have a consistent schema")
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
    # Spool to disk while hashing so CSV/NDJSON validation never retains the full
    # object in application memory. The hard upload ceiling still protects disk use.
    import tempfile
    digest_state = hashlib.sha256()
    total_bytes = 0
    with tempfile.TemporaryFile(mode="w+b") as staged:
        body_stream = obj["Body"]
        try:
            while True:
                chunk = body_stream.read(1024 * 1024)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > 100 * 1024 * 1024:
                    raise ValueError("Upload exceeds the 100 MiB processing limit")
                digest_state.update(chunk)
                staged.write(chunk)
        finally:
            body_stream.close()
        if total_bytes == 0:
            raise ValueError("The uploaded dataset is empty")
        digest = digest_state.hexdigest()
        staged.seek(0)
        result = inspect_stream(staged, content_type)
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
        # Leave an unreferenced content-addressed object for lifecycle/orphan cleanup;
        # the worker role intentionally cannot delete validated customer data.
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

def save_job_result(
    message: dict, result: dict, status: str = "completed",
    lease_owner: str | None = None,
) -> None:
    """Persist an agent result, optionally fenced by the worker's current lease."""
    table_name = _required("AGENT_JOBS_TABLE")
    table = boto3.resource(
        "dynamodb", region_name=os.getenv("AWS_REGION", "us-east-2")
    ).Table(table_name)
    args = {
        "Key": {"tenant_id": message["tenant_id"], "job_id": message["job_id"]},
        "UpdateExpression": (
            "SET #s=:status, result_json=:result, completed_at=:at "
            "REMOVE lease_owner, lease_expires_at"
        ),
        "ExpressionAttributeNames": {"#s": "status"},
        "ExpressionAttributeValues": {
            ":status": status, ":result": json.dumps(result), ":at": _now()
        },
    }
    if lease_owner is not None:
        args["ConditionExpression"] = "lease_owner = :owner AND #s = :running"
        args["ExpressionAttributeValues"][":owner"] = lease_owner
        args["ExpressionAttributeValues"][":running"] = "running"
    table.update_item(**args)

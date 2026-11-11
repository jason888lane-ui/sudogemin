"""Long-running worker for Sudogemini ingestion and governed agent jobs."""
from __future__ import annotations

import json
import logging
import os
import time
import uuid

import boto3
from botocore.exceptions import ClientError
from platform_engine import process_ingestion, run_agent, save_job_result

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("sudogemini-worker")
LEASE_SECONDS = 55 * 60


def _jobs_table():
    name = os.getenv("AGENT_JOBS_TABLE", "")
    if not name:
        raise RuntimeError("AGENT_JOBS_TABLE is not configured")
    return boto3.resource(
        "dynamodb", region_name=os.getenv("AWS_REGION", "us-east-2")
    ).Table(name)


def claim_job(message: dict, now: int | None = None) -> str | None:
    """Atomically claim a job; completed or actively leased duplicates are no-ops."""
    now = int(time.time()) if now is None else int(now)
    owner = str(uuid.uuid4())
    try:
        _jobs_table().update_item(
            Key={"tenant_id": message["tenant_id"], "job_id": message["job_id"]},
            UpdateExpression=(
                "SET #status=:running, lease_owner=:owner, lease_expires_at=:expiry, "
                "updated_at=:now ADD attempt_count :one"
            ),
            ConditionExpression=(
                "attribute_not_exists(#status) OR #status IN (:queued,:failed) "
                "OR (#status=:running AND lease_expires_at < :now)"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":running": "running", ":queued": "queued", ":failed": "failed",
                ":owner": owner, ":expiry": now + LEASE_SECONDS, ":now": now, ":one": 1,
            },
        )
        return owner
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return None
        raise


def complete_job(message: dict, owner: str) -> None:
    """Complete only if this worker still owns the lease."""
    _jobs_table().update_item(
        Key={"tenant_id": message["tenant_id"], "job_id": message["job_id"]},
        UpdateExpression=(
            "SET #status=:completed, completed_at=:now "
            "REMOVE lease_owner, lease_expires_at"
        ),
        ConditionExpression="lease_owner = :owner AND #status = :running",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":completed": "completed", ":now": int(time.time()), ":owner": owner,
            ":running": "running",
        },
    )


def release_job(message: dict, owner: str, error: Exception) -> None:
    """Release a failed attempt so SQS redelivery can claim it again."""
    _jobs_table().update_item(
        Key={"tenant_id": message["tenant_id"], "job_id": message["job_id"]},
        UpdateExpression=(
            "SET #status=:queued, last_error_type=:error, updated_at=:now "
            "REMOVE lease_owner, lease_expires_at"
        ),
        ConditionExpression="lease_owner = :owner AND #status = :running",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":queued": "queued", ":error": type(error).__name__,
            ":now": int(time.time()), ":owner": owner, ":running": "running",
        },
    )


def handle(message: dict, lease_owner: str) -> None:
    kind = message.get("kind")
    if kind == "ingestion":
        result = process_ingestion(message)
        log.info(
            "Ingestion validated dataset_id=%s sha256=%s",
            message.get("dataset_id"), result.get("sha256"),
        )
        complete_job(message, lease_owner)
        return
    if kind == "agent":
        table = _jobs_table()
        control = table.get_item(
            Key={"tenant_id": message["tenant_id"], "job_id": "__autonomy_control__"},
            ConsistentRead=True,
        ).get("Item") or {}
        modes = control.get("modes", {})
        mode = modes.get(message.get("agent_id"), "human_in_loop") if isinstance(modes, dict) else "human_in_loop"
        globally_paused = os.getenv("AI_GLOBAL_PAUSE", "false").lower() == "true"
        ai_enabled = bool(control.get("ai_enabled", True))
        human_approval_required = bool(control.get("human_approval_required", True))
        # A human-approved job carries approved_by on its tenant-scoped record.
        # Approval is a one-job authorization and must override the workspace's
        # human-approval default, but never the emergency pause or AI master switch.
        job_record = table.get_item(
            Key={"tenant_id": message["tenant_id"], "job_id": message["job_id"]},
            ConsistentRead=True,
        ).get("Item") or {}
        explicitly_approved = bool(job_record.get("approved_by"))
        needs_approval = (human_approval_required or mode != "autonomous") and not explicitly_approved
        if globally_paused or control.get("paused") or not ai_enabled or needs_approval:
            next_status = "paused" if (globally_paused or control.get("paused") or not ai_enabled) else "awaiting_approval"
            table.update_item(
                Key={"tenant_id": message["tenant_id"], "job_id": message["job_id"]},
                UpdateExpression="SET #s=:next, updated_at=:now REMOVE lease_owner, lease_expires_at",
                ConditionExpression="lease_owner = :owner AND #s = :running",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":next": next_status, ":now": int(time.time()),
                    ":owner": lease_owner, ":running": "running",
                },
            )
            log.info("Agent job deferred by autonomy controls job_id=%s status=%s",
                     message.get("job_id"), next_status)
            return
        result = run_agent(message)
        save_job_result(message, result, status="completed", lease_owner=lease_owner)
        return
    raise ValueError("Unsupported job kind")


def mark_terminal_failure(
    message: dict, error: Exception, lease_owner: str | None
) -> None:
    """Fence terminal failure state to the worker that owns the final attempt."""
    if not lease_owner:
        return
    _jobs_table().update_item(
        Key={"tenant_id": message["tenant_id"], "job_id": message["job_id"]},
        UpdateExpression=(
            "SET #status=:failed, last_error_type=:error, failed_at=:at "
            "REMOVE lease_owner, lease_expires_at"
        ),
        ConditionExpression="lease_owner = :owner AND #status = :running",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":failed": "failed", ":error": type(error).__name__,
            ":at": int(time.time()), ":owner": lease_owner, ":running": "running",
        },
    )
    if message.get("kind") == "ingestion":
        table_name = os.getenv("DATASETS_TABLE", "")
        if table_name:
            table = boto3.resource(
                "dynamodb", region_name=os.getenv("AWS_REGION", "us-east-2")
            ).Table(table_name)
            table.update_item(
                Key={"tenant_id": message["tenant_id"], "record_id": message["dataset_id"]},
                UpdateExpression=(
                    "SET ingestion_status=:failed, ingestion_error_type=:error, "
                    "ingestion_failed_at=:at"
                ),
                ConditionExpression="ingestion_job_id = :job AND ingestion_status <> :validated",
                ExpressionAttributeValues={
                    ":failed": "failed", ":error": type(error).__name__,
                    ":at": int(time.time()), ":job": message["job_id"],
                    ":validated": "validated",
                },
            )


def terminal_provider_error_code(error: Exception) -> str | None:
    """Return provider errors that should fail fast instead of burning SQS retries."""
    if not isinstance(error, ClientError):
        return None
    code = error.response.get("Error", {}).get("Code", "")
    terminal = {
        "ThrottlingException", "ServiceQuotaExceededException",
        "AccessDeniedException", "ResourceNotFoundException",
        "ValidationException",
    }
    return code if code in terminal else None


def decode_message(body: str) -> dict:
    """Decode one queue message and reject non-object JSON payloads."""
    message = json.loads(body)
    if not isinstance(message, dict):
        raise ValueError("SQS job body must be a JSON object")
    return message


def main() -> None:
    queue_url = os.environ["PLATFORM_JOBS_QUEUE_URL"]
    sqs = boto3.client("sqs", region_name=os.getenv("AWS_REGION", "us-east-2"))
    while True:
        response = sqs.receive_message(
            QueueUrl=queue_url, MaxNumberOfMessages=1, WaitTimeSeconds=20,
            VisibilityTimeout=3600, AttributeNames=["ApproximateReceiveCount"],
        )
        for item in response.get("Messages", []):
            message = {}
            owner = None
            try:
                # A malformed body is isolated to this delivery and is retried/DLQ'd.
                message = decode_message(item["Body"])
                owner = claim_job(message)
                if owner is None:
                    # Another delivery completed this job or currently owns its lease.
                    # This receipt is a duplicate; the original delivery remains eligible
                    # for retry if its worker crashes and the lease expires.
                    sqs.delete_message(
                        QueueUrl=queue_url, ReceiptHandle=item["ReceiptHandle"]
                    )
                    continue
                handle(message, owner)
                sqs.delete_message(
                    QueueUrl=queue_url, ReceiptHandle=item["ReceiptHandle"]
                )
            except Exception as exc:
                error_code = terminal_provider_error_code(exc)
                if error_code and message:
                    # Model quota/access/configuration failures will not heal through
                    # rapid SQS redelivery. Persist a visible terminal failure and
                    # remove this delivery instead of burning retries or DLQ capacity.
                    log.error("Terminal provider error for job_id=%s code=%s",
                              message.get("job_id"), error_code)
                    try:
                        mark_terminal_failure(message, exc, owner)
                    except Exception:
                        log.exception("Could not persist terminal provider failure")
                    try:
                        sqs.delete_message(
                            QueueUrl=queue_url, ReceiptHandle=item["ReceiptHandle"]
                        )
                    except Exception:
                        log.exception("Could not delete terminal provider-error message")
                    continue
                log.exception("Job failed; message retained for retry/DLQ")
                receive_count = int(
                    item.get("Attributes", {}).get("ApproximateReceiveCount", "1")
                )
                if receive_count >= 4 and message:
                    try:
                        mark_terminal_failure(message, exc, owner)
                    except Exception:
                        log.exception("Could not persist terminal job failure")
                elif owner and message:
                    try:
                        release_job(message, owner, exc)
                    except Exception:
                        log.exception("Could not release failed job lease")
        time.sleep(1)


if __name__ == "__main__":
    main()

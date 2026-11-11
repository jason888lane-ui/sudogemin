"""Long-running worker for Sudogemini ingestion and governed agent jobs."""
from __future__ import annotations
import json, logging, os, time
import boto3
from platform_engine import process_ingestion, run_agent, save_job_result
logging.basicConfig(level=os.getenv("LOG_LEVEL","INFO"))
log=logging.getLogger("sudogemini-worker")

def handle(message: dict) -> None:
    kind=message.get("kind")
    if kind=="ingestion":
        result=process_ingestion(message)
        log.info("Ingestion validated dataset_id=%s sha256=%s",message.get("dataset_id"),result.get("sha256"))
        return
    if kind=="agent":
        try:
            result=run_agent(message); save_job_result(message,result)
        except Exception as exc:
            save_job_result(message,{"error":type(exc).__name__},status="failed"); raise
        return
    raise ValueError("Unsupported job kind")

def mark_terminal_failure(message: dict, error: Exception) -> None:
    """Persist a terminal job failure when SQS is about to move the message to its DLQ."""
    kind=message.get("kind")
    table_name=os.getenv("DATASETS_TABLE","")
    if kind=="ingestion" and table_name:
        table=boto3.resource("dynamodb",region_name=os.getenv("AWS_REGION","us-east-2")).Table(table_name)
        table.update_item(
            Key={"tenant_id":message["tenant_id"],"record_id":message["dataset_id"]},
            UpdateExpression="SET ingestion_status=:failed, ingestion_error_type=:error, ingestion_failed_at=:at",
            ConditionExpression="ingestion_job_id = :job",
            ExpressionAttributeValues={":failed":"failed",":error":type(error).__name__,
                                       ":at":int(time.time()),":job":message.get("job_id","")})
    elif kind=="agent":
        save_job_result(message,{"error":type(error).__name__},status="failed")


def main() -> None:
    queue_url=os.environ["PLATFORM_JOBS_QUEUE_URL"]
    sqs=boto3.client("sqs",region_name=os.getenv("AWS_REGION","us-east-2"))
    while True:
        response=sqs.receive_message(QueueUrl=queue_url,MaxNumberOfMessages=1,
            WaitTimeSeconds=20,VisibilityTimeout=3600,AttributeNames=["ApproximateReceiveCount"])
        for item in response.get("Messages",[]):
            message=json.loads(item["Body"])
            try:
                handle(message)
                sqs.delete_message(QueueUrl=queue_url,ReceiptHandle=item["ReceiptHandle"])
            except Exception as exc:
                log.exception("Job failed; message retained for retry/DLQ")
                receive_count=int(item.get("Attributes",{}).get("ApproximateReceiveCount","1"))
                if receive_count>=4:
                    try:
                        mark_terminal_failure(message,exc)
                    except Exception:
                        log.exception("Could not persist terminal job failure")
        time.sleep(1)

if __name__=="__main__": main()

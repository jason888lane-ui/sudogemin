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

def main() -> None:
    queue_url=os.environ["PLATFORM_JOBS_QUEUE_URL"]
    sqs=boto3.client("sqs",region_name=os.getenv("AWS_REGION","us-east-2"))
    while True:
        response=sqs.receive_message(QueueUrl=queue_url,MaxNumberOfMessages=5,
            WaitTimeSeconds=20,VisibilityTimeout=180,AttributeNames=["ApproximateReceiveCount"])
        for item in response.get("Messages",[]):
            try:
                handle(json.loads(item["Body"]))
                sqs.delete_message(QueueUrl=queue_url,ReceiptHandle=item["ReceiptHandle"])
            except Exception:
                log.exception("Job failed; message retained for retry/DLQ")
        time.sleep(1)

if __name__=="__main__": main()

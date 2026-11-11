import json
import os
import unittest
from unittest.mock import patch, MagicMock

from platform_engine import inspect_csv_or_json, enqueue_job, run_agent


class IngestionValidationTests(unittest.TestCase):
    def test_csv_record_count_and_schema(self):
        result = inspect_csv_or_json(b"company,industry\nAcme,retail\nNorthwind,software\n", "text/csv")
        self.assertEqual(result["record_count"], 2)
        self.assertEqual(result["columns"], ["company", "industry"])
        self.assertEqual(len(result["schema_sha256"]), 64)

    def test_csv_rejects_duplicate_headers(self):
        with self.assertRaisesRegex(ValueError, "unique"):
            inspect_csv_or_json(b"company,Company\nAcme,retail\n", "text/csv")

    def test_csv_rejects_rows_with_wrong_field_count(self):
        with self.assertRaisesRegex(ValueError, "same number of fields"):
            inspect_csv_or_json(b"company,industry\nAcme\n", "text/csv")

    def test_json_lines_record_count(self):
        data = b'{"company":"Acme","industry":"retail"}\n{"company":"Northwind","industry":"software"}\n'
        result = inspect_csv_or_json(data, "application/x-ndjson")
        self.assertEqual(result["record_count"], 2)

    def test_json_array_record_count(self):
        data = b'[{"company":"Acme"},{"company":"Northwind"}]'
        result = inspect_csv_or_json(data, "application/json")
        self.assertEqual(result["record_count"], 2)

    def test_json_schema_drift_is_rejected(self):
        data = b'[{"company":"Acme"},{"organization":"Northwind"}]'
        with self.assertRaisesRegex(ValueError, "consistent schema"):
            inspect_csv_or_json(data, "application/json")

    def test_sensitive_columns_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "sensitive personal-data"):
            inspect_csv_or_json(b"company,email\nAcme,person@example.com\n", "text/csv")

    def test_invalid_json_is_rejected(self):
        with self.assertRaises(json.JSONDecodeError):
            inspect_csv_or_json(b'{"company":}\n', "application/json")

    def test_oversized_payload_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "100 MiB"):
            inspect_csv_or_json(b"x" * (100 * 1024 * 1024 + 1), "text/csv")


class JobDispatchTests(unittest.TestCase):
    @patch.dict(os.environ, {"PLATFORM_JOBS_QUEUE_URL": "https://sqs.example/queue"})
    @patch("platform_engine.boto3.client")
    def test_enqueue_preserves_preallocated_job_id(self, client):
        sqs = MagicMock()
        client.return_value = sqs
        job_id = enqueue_job("agent", {"tenant_id": "tenant-a", "job_id": "job-a"})
        self.assertEqual(job_id, "job-a")
        sent = json.loads(sqs.send_message.call_args.kwargs["MessageBody"])
        self.assertEqual(sent["job_id"], "job-a")
        self.assertEqual(sent["kind"], "agent")

    @patch.dict(os.environ, {"BEDROCK_MODEL_ID": "test-model"})
    @patch("platform_engine.boto3.client")
    def test_agent_output_is_forced_to_human_approval(self, client):
        bedrock = MagicMock()
        bedrock.converse.return_value = {"output":{"message":{"content":[{"text":
            '{"summary":"Review source","findings":[],"proposed_steps":["Check rights"],"risk_level":"low","requires_human_approval":false}'
        }]}}}
        client.return_value = bedrock
        result = run_agent({"agent_id":"governance","task":"Review a dataset license checklist"})
        self.assertTrue(result["requires_human_approval"])


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
from worker import claim_job, complete_job, decode_message, mark_terminal_failure, release_job


class WorkerMessageTests(unittest.TestCase):
    def test_decodes_json_object(self):
        self.assertEqual(decode_message('{"kind":"agent","job_id":"job-1"}'),
                         {"kind": "agent", "job_id": "job-1"})

    def test_rejects_non_object_json(self):
        for body in ("[]", "null", '"text"', "42"):
            with self.subTest(body=body):
                with self.assertRaisesRegex(ValueError, "JSON object"):
                    decode_message(body)

    def test_rejects_malformed_json(self):
        with self.assertRaises(ValueError):
            decode_message('{"kind":')


class WorkerLeaseTests(unittest.TestCase):
    def setUp(self):
        self.table = MagicMock()
        self.message = {"tenant_id": "tenant-a", "job_id": "job-a", "kind": "agent"}

    @patch("worker._jobs_table")
    def test_claim_is_atomic_and_returns_owner(self, get_table):
        get_table.return_value = self.table
        owner = claim_job(self.message, now=1000)
        self.assertTrue(owner)
        kwargs = self.table.update_item.call_args.kwargs
        self.assertIn("ConditionExpression", kwargs)
        self.assertIn("lease_expires_at", kwargs["UpdateExpression"])
        self.assertEqual(kwargs["ExpressionAttributeValues"][":expiry"], 1000 + 55 * 60)

    @patch("worker._jobs_table")
    def test_duplicate_active_or_completed_job_is_not_claimed(self, get_table):
        get_table.return_value = self.table
        self.table.update_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": "not claimable"}},
            "UpdateItem",
        )
        self.assertIsNone(claim_job(self.message, now=1000))

    @patch("worker._jobs_table")
    def test_other_claim_errors_are_not_suppressed(self, get_table):
        get_table.return_value = self.table
        self.table.update_item.side_effect = RuntimeError("ddb unavailable")
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            claim_job(self.message, now=1000)

    @patch("worker._jobs_table")
    def test_completion_requires_lease_owner(self, get_table):
        get_table.return_value = self.table
        complete_job(self.message, "owner-1")
        kwargs = self.table.update_item.call_args.kwargs
        self.assertEqual(kwargs["ExpressionAttributeValues"][":owner"], "owner-1")
        self.assertIn("lease_owner = :owner", kwargs["ConditionExpression"])

    @patch("worker._jobs_table")
    def test_release_returns_owned_job_to_queue(self, get_table):
        get_table.return_value = self.table
        release_job(self.message, "owner-1", ValueError("bad input"))
        kwargs = self.table.update_item.call_args.kwargs
        self.assertEqual(kwargs["ExpressionAttributeValues"][":queued"], "queued")
        self.assertEqual(kwargs["ExpressionAttributeValues"][":error"], "ValueError")
        self.assertIn("REMOVE lease_owner", kwargs["UpdateExpression"])


    @patch("worker._jobs_table")
    def test_terminal_agent_failure_is_fenced_and_removes_lease(self, get_table):
        get_table.return_value = self.table
        mark_terminal_failure(self.message, RuntimeError("model unavailable"), "owner-1")
        kwargs = self.table.update_item.call_args.kwargs
        self.assertIn("lease_owner = :owner", kwargs["ConditionExpression"])
        self.assertEqual(kwargs["ExpressionAttributeValues"][":failed"], "failed")
        self.assertIn("REMOVE lease_owner", kwargs["UpdateExpression"])

    @patch("worker._jobs_table")
    def test_terminal_failure_without_lease_owner_is_ignored(self, get_table):
        mark_terminal_failure(self.message, RuntimeError("failure"), None)
        get_table.assert_not_called()


    @patch("worker.boto3.resource")
    @patch("worker._jobs_table")
    def test_terminal_ingestion_failure_marks_dataset_only_for_matching_job(
        self, get_table, resource
    ):
        get_table.return_value = self.table
        dataset_table = MagicMock()
        resource.return_value.Table.return_value = dataset_table
        message = {
            **self.message, "kind": "ingestion", "dataset_id": "dataset-a"
        }
        with patch.dict("os.environ", {"DATASETS_TABLE": "datasets"}):
            mark_terminal_failure(message, ValueError("invalid file"), "owner-1")
        kwargs = dataset_table.update_item.call_args.kwargs
        self.assertEqual(kwargs["Key"], {
            "tenant_id": "tenant-a", "record_id": "dataset-a"
        })
        self.assertIn("ingestion_job_id = :job", kwargs["ConditionExpression"])
        self.assertEqual(kwargs["ExpressionAttributeValues"][":job"], "job-a")
        self.assertEqual(kwargs["ExpressionAttributeValues"][":failed"], "failed")



class WorkerApprovalGateTests(unittest.TestCase):
    def _message(self):
        return {"kind": "agent", "tenant_id": "tenant-a", "job_id": "job-1",
                "agent_id": "quality", "task": "Review dataset metadata"}

    def test_explicit_approval_overrides_human_approval_default(self):
        import worker
        table = MagicMock()
        table.get_item.side_effect = [
            {"Item": {"human_approval_required": True, "ai_enabled": True,
                      "modes": {"quality": "human_in_loop"}}},
            {"Item": {"approved_by": "reviewer-sub"}},
        ]
        with patch.object(worker, "_jobs_table", return_value=table), \
             patch.object(worker, "run_agent", return_value={"summary": "ok"}) as run, \
             patch.object(worker, "save_job_result") as save:
            worker.handle(self._message(), "lease-1")
        run.assert_called_once()
        save.assert_called_once()

    def test_unapproved_job_waits_when_human_approval_is_required(self):
        import worker
        table = MagicMock()
        table.get_item.side_effect = [
            {"Item": {"human_approval_required": True, "ai_enabled": True,
                      "modes": {"quality": "autonomous"}}},
            {"Item": {}},
        ]
        with patch.object(worker, "_jobs_table", return_value=table), \
             patch.object(worker, "run_agent") as run:
            worker.handle(self._message(), "lease-1")
        run.assert_not_called()
        update = table.update_item.call_args.kwargs
        self.assertIn("awaiting_approval", update["ExpressionAttributeValues"].values())

    def test_emergency_pause_blocks_even_an_approved_job(self):
        import worker
        table = MagicMock()
        table.get_item.side_effect = [
            {"Item": {"human_approval_required": False, "ai_enabled": True,
                      "paused": True, "modes": {"quality": "autonomous"}}},
            {"Item": {"approved_by": "reviewer-sub"}},
        ]
        with patch.dict("os.environ", {"AI_GLOBAL_PAUSE": "false"}), \
             patch.object(worker, "_jobs_table", return_value=table), \
             patch.object(worker, "run_agent") as run:
            worker.handle(self._message(), "lease-1")
        run.assert_not_called()
        update = table.update_item.call_args.kwargs
        self.assertIn("paused", update["ExpressionAttributeValues"].values())


if __name__ == "__main__":
    unittest.main()

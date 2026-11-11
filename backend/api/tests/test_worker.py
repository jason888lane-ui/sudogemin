import unittest
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
from worker import claim_job, complete_job, decode_message, release_job


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


if __name__ == "__main__":
    unittest.main()

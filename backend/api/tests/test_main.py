import os
import unittest
from unittest.mock import MagicMock, patch

# Import the application without requiring live AWS credentials or a real table.
os.environ.pop("COGNITO_USER_POOL_ID", None)
os.environ.pop("COGNITO_APP_CLIENT_ID", None)
os.environ.pop("STRIPE_WEBHOOK_SECRET", None)

with patch("boto3.resource") as mock_resource:
    mock_resource.return_value.Table.return_value = MagicMock()
    import main

from fastapi.testclient import TestClient


class ApiReadinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        # Keep these tests deterministic and fail closed when identity is absent.
        main._jwks_client = None
        main.COGNITO_CLIENT_ID = ""
        main.COGNITO_ISSUER = ""
        os.environ.pop("STRIPE_WEBHOOK_SECRET", None)

    def test_health_is_public_and_operational(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_api_fails_closed_when_cognito_is_not_configured(self):
        response = self.client.get("/api/agents")
        self.assertEqual(response.status_code, 503)
        self.assertIn("Authentication provider", response.json()["detail"])

    def test_dataset_api_fails_closed_without_identity_provider(self):
        response = self.client.get("/api/datasets")
        self.assertEqual(response.status_code, 503)

    def test_trade_creation_remains_disabled(self):
        item = main.TradeIn(buyer_id="buyer-test", dataset_id="dataset-test", amount_usd=10)
        with self.assertRaises(main.HTTPException) as raised:
            main.create_trade(item)
        self.assertEqual(raised.exception.status_code, 503)

    def test_webhook_fails_closed_without_signing_secret(self):
        response = self.client.post("/api/billing/webhook", content=b"{}")
        self.assertEqual(response.status_code, 503)

    def test_digest_is_deterministic(self):
        self.assertEqual(main.digest("sudogemini"), main.digest("sudogemini"))
        self.assertNotEqual(main.digest("sudogemini"), main.digest("different"))


if __name__ == "__main__":
    unittest.main()

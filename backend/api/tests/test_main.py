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

    def test_trade_requires_listing_and_explicit_license_acceptance(self):
        item = main.TradeIn(listing_id="listing-test")
        self.assertEqual(item.listing_id, "listing-test")
        self.assertFalse(item.accept_license)

    def test_webhook_fails_closed_without_signing_secret(self):
        response = self.client.post("/api/billing/webhook", content=b"{}")
        self.assertEqual(response.status_code, 503)

    def test_digest_is_deterministic(self):
        self.assertEqual(main.digest("sudogemini"), main.digest("sudogemini"))
        self.assertNotEqual(main.digest("sudogemini"), main.digest("different"))

    def test_home_serves_dashboard(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Sudogemini Nexus", response.text)
        self.assertIn("Access your workspace", response.text)

    def test_dashboard_module_is_served(self):
        response = self.client.get("/platform.js")
        self.assertEqual(response.status_code, 200)
        self.assertIn("initPlatform", response.text)

    def test_dataset_cannot_self_approve_license(self):
        with self.assertRaises(Exception):
            main.DatasetIn(name="test dataset", license_status="approved")

    def test_dataset_default_license_state_requires_review(self):
        item = main.DatasetIn(name="test dataset")
        self.assertEqual(item.license_status, "pending_review")


if __name__ == "__main__":
    unittest.main()

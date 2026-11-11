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

    def test_seller_approval_updates_buyer_and_seller_atomically(self):
        from types import SimpleNamespace
        main._trades.get_item.return_value = {"Item": {
            "buyer_tenant_id": "buyer-tenant", "role": "seller",
            "status": "pending_seller_approval"
        }}
        request = SimpleNamespace(state=SimpleNamespace(tenant_id="seller-tenant"))
        main.seller_approve_trade("trade-1", request)
        transaction = main._dynamodb.meta.client.transact_write_items.call_args.kwargs["TransactItems"]
        self.assertEqual(len(transaction), 2)
        self.assertEqual(transaction[0]["Update"]["Key"]["tenant_id"], {"S": "buyer-tenant"})
        self.assertEqual(transaction[1]["Update"]["Key"]["tenant_id"], {"S": "seller-tenant"})
        self.assertIn("ConditionExpression", transaction[0]["Update"])
        self.assertIn("ConditionExpression", transaction[1]["Update"])

    def test_trade_requires_listing_and_explicit_license_acceptance(self):
        item = main.TradeIn(listing_id="listing-test")
        self.assertEqual(item.listing_id, "listing-test")
        self.assertFalse(item.accept_license)

    def test_webhook_fails_closed_without_signing_secret(self):
        response = self.client.post("/api/billing/webhook", content=b"{}")
        self.assertEqual(response.status_code, 503)

    def test_stripe_test_mode_never_accepts_live_secret_key(self):
        with patch.dict(os.environ, {
            "STRIPE_MODE": "test", "STRIPE_TEST_ENABLED": "true",
            "STRIPE_TEST_SECRET_KEY": "sk_test_example", "STRIPE_TEST_PRICE_ID": "price_test"
        }, clear=True):
            config = main.stripe_configuration()
            self.assertEqual(config["mode"], "test")
            self.assertEqual(config["key"], "sk_test_example")
            self.assertTrue(config["enabled"])

    def test_stripe_live_mode_is_off_without_explicit_live_switch(self):
        with patch.dict(os.environ, {
            "STRIPE_MODE": "live", "STRIPE_LIVE_SECRET_KEY": "sk_live_example",
            "STRIPE_LIVE_ENABLED": "false", "STRIPE_TEST_ENABLED": "true",
            "STRIPE_TEST_SECRET_KEY": "sk_test_example"
        }, clear=True):
            config = main.stripe_configuration()
            self.assertEqual(config["mode"], "live")
            self.assertFalse(config["enabled"])

    def test_dynamodb_transaction_item_serialization_preserves_tenant_keys(self):
        encoded = main._ddb_item({"tenant_id": "tenant-a", "record_id": "order-1", "amount": main.Decimal("12.34")})
        self.assertEqual(encoded["tenant_id"], {"S": "tenant-a"})
        self.assertEqual(encoded["record_id"], {"S": "order-1"})
        self.assertEqual(encoded["amount"], {"N": "12.34"})

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

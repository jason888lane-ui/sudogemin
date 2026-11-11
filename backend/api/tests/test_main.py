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
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["x-frame-options"], "DENY")

    def test_api_fails_closed_when_cognito_is_not_configured(self):
        response = self.client.get("/api/agents")
        self.assertEqual(response.status_code, 503)
        self.assertIn("Authentication provider", response.json()["detail"])


    def test_membership_directory_outage_returns_503_not_401(self):
        from types import SimpleNamespace
        jwks = MagicMock()
        jwks.get_signing_key_from_jwt.return_value = SimpleNamespace(key="test-key")
        with patch.object(main, "_jwks_client", jwks), \
             patch.object(main, "COGNITO_CLIENT_ID", "client-id"), \
             patch.object(main, "COGNITO_ISSUER", "https://issuer.example"), \
             patch("main.jwt.decode", return_value={"sub": "user-a", "token_use": "id"}), \
             patch("main.resolve_tenant_id", side_effect=RuntimeError("DynamoDB unavailable")):
            response = self.client.get(
                "/api/agents", headers={"Authorization": "Bearer test-token"}
            )
        self.assertEqual(response.status_code, 503)
        self.assertIn("membership service", response.json()["detail"])


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

    def test_subscription_checkout_uses_tenant_scoped_idempotency_key(self):
        from types import SimpleNamespace
        sdk = MagicMock()
        sdk.checkout.Session.create.return_value = SimpleNamespace(
            url="https://checkout.stripe.test/session", id="cs_test_123"
        )
        request = SimpleNamespace(state=SimpleNamespace(tenant_id="tenant-a"))
        with patch("main.require_stripe", return_value=sdk), \
             patch("main.stripe_configuration", return_value={"price": "price_test"}), \
             patch.dict(os.environ, {"APP_BASE_URL": "https://app.sudogemini.com"}):
            result = main.create_subscription_checkout(
                main.SubscriptionCheckoutIn(customer_email="billing@example.com"), request
            )
        self.assertEqual(result["session_id"], "cs_test_123")
        self.assertEqual(
            sdk.checkout.Session.create.call_args.kwargs["idempotency_key"],
            "sudogemini-subscription-tenant-a",
        )

    def test_connect_account_creation_uses_tenant_scoped_idempotency_key(self):
        from types import SimpleNamespace
        sdk = MagicMock()
        sdk.Account.create.return_value = SimpleNamespace(id="acct_test_123")
        sdk.AccountLink.create.return_value = SimpleNamespace(
            url="https://connect.stripe.test/onboard", expires_at=1234567890
        )
        main._subscriptions.get_item.return_value = {"Item": {}}
        request = SimpleNamespace(state=SimpleNamespace(tenant_id="tenant-b"))
        with patch("main.require_stripe", return_value=sdk), \
             patch.dict(os.environ, {
                 "STRIPE_CONNECT_ENABLED": "true",
                 "APP_BASE_URL": "https://app.sudogemini.com",
             }):
            result = main.create_seller_onboarding(
                main.SellerOnboardingIn(customer_email="seller@example.com"), request
            )
        self.assertIn("onboarding_url", result)
        self.assertEqual(
            sdk.Account.create.call_args.kwargs["idempotency_key"],
            "sudogemini-connect-tenant-b",
        )

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

    def test_tenant_identity_ignores_untrusted_custom_tenant_claim(self):
        claims = {"sub": "cognito-user-a", "custom:tenant_id": "attacker-selected-org"}
        self.assertEqual(main.tenant_id_from_claims(claims), "cognito-user-a")

    def test_tenant_identity_requires_verified_subject(self):
        for claims in ({}, {"sub": ""}, {"sub": None}, {"sub": 123}):
            with self.subTest(claims=claims):
                with self.assertRaises(ValueError):
                    main.tenant_id_from_claims(claims)


    def test_tenant_membership_directory_maps_active_member(self):
        table = MagicMock()
        table.get_item.return_value = {"Item": {
            "member_sub": "cognito-user-a", "tenant_id": "company-123", "status": "active"
        }}
        with patch.dict(os.environ, {"TENANT_MEMBERSHIPS_TABLE": "memberships"}), \
             patch.object(main, "_tenant_memberships", table):
            self.assertEqual(main.resolve_tenant_id("cognito-user-a"), "company-123")
        table.get_item.assert_called_once_with(
            Key={"member_sub": "cognito-user-a"}, ConsistentRead=True
        )

    def test_revoked_membership_falls_back_to_individual_tenant(self):
        table = MagicMock()
        table.get_item.return_value = {"Item": {
            "member_sub": "cognito-user-a", "tenant_id": "company-123", "status": "revoked"
        }}
        with patch.dict(os.environ, {"TENANT_MEMBERSHIPS_TABLE": "memberships"}), \
             patch.object(main, "_tenant_memberships", table):
            self.assertEqual(main.resolve_tenant_id("cognito-user-a"), "cognito-user-a")

    def test_unconfigured_membership_directory_keeps_individual_isolation(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(main.resolve_tenant_id("cognito-user-a"), "cognito-user-a")

    def test_only_platform_admin_can_create_tenant_membership(self):
        table = MagicMock()
        request = type("Request", (), {"state": type("State", (), {"subject": "admin-sub"})()})()
        item = main.TenantMembershipIn(member_sub="user-sub", tenant_id="company-123")
        with patch.dict(os.environ, {"TENANT_MEMBERSHIPS_TABLE": "memberships"}), \
             patch.object(main, "_tenant_memberships", table), \
             patch.object(main, "require_platform_admin") as admin_check:
            result = main.create_tenant_membership(item, request)
        admin_check.assert_called_once_with(request)
        self.assertEqual(result["status"], "active")
        kwargs = table.put_item.call_args.kwargs
        self.assertEqual(kwargs["Item"]["tenant_id"], "company-123")
        self.assertEqual(kwargs["Item"]["created_by"], "admin-sub")
        self.assertEqual(kwargs["ConditionExpression"], "attribute_not_exists(member_sub)")

    def test_membership_revocation_is_conditional_and_admin_gated(self):
        table = MagicMock()
        request = type("Request", (), {"state": type("State", (), {"subject": "admin-sub"})()})()
        with patch.dict(os.environ, {"TENANT_MEMBERSHIPS_TABLE": "memberships"}), \
             patch.object(main, "_tenant_memberships", table), \
             patch.object(main, "require_platform_admin") as admin_check:
            result = main.revoke_tenant_membership("user-sub", request)
        admin_check.assert_called_once_with(request)
        self.assertEqual(result["status"], "revoked")
        kwargs = table.update_item.call_args.kwargs
        self.assertEqual(kwargs["Key"], {"member_sub": "user-sub"})
        self.assertIn("#status=:active", kwargs["ConditionExpression"])

    def test_digest_is_deterministic(self):
        self.assertEqual(main.digest("sudogemini"), main.digest("sudogemini"))
        self.assertNotEqual(main.digest("sudogemini"), main.digest("different"))

    def test_home_serves_dashboard(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Sudogemini Nexus", response.text)
        self.assertIn("Access your workspace", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")

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

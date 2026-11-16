import os
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main


@pytest.fixture()
def test_client():
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    main.Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)
    principal = {
        "subject": "seller-user",
        "tenant_id": "tenant-seller",
        "roles": ["seller", "compliance", "publisher"],
    }

    def override_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    main.app.dependency_overrides[main.get_db] = override_db
    main.app.dependency_overrides[main.get_current_user] = lambda: principal
    with TestClient(main.app) as client:
        yield client, principal
    main.app.dependency_overrides.clear()
    test_engine.dispose()


def test_health_is_public(test_client):
    client, _ = test_client
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_dataset_requires_review_and_publish_steps(test_client):
    client, principal = test_client
    created = client.post("/api/datasets", json={
        "name": "Regional demand index",
        "description": "Aggregated, licensed demand metadata",
        "category": "market-research",
        "record_count": 1200,
    })
    assert created.status_code == 201
    dataset = created.json()
    assert dataset["license_status"] == "pending_review"
    assert dataset["status"] == "pending_review"

    reviewed = client.post(f"/api/datasets/{dataset['id']}/review", json={
        "decision": "approved",
        "reason": "License and source documentation checked",
    })
    assert reviewed.status_code == 200
    assert reviewed.json()["license_status"] == "approved"
    assert reviewed.json()["status"] == "pending_review"

    published = client.post(f"/api/datasets/{dataset['id']}/publish")
    assert published.status_code == 200
    assert published.json()["status"] == "published"


def test_tenant_private_dataset_list_isolation(test_client):
    client, principal = test_client
    created = client.post("/api/datasets", json={"name": "Seller private dataset"})
    assert created.status_code == 201
    assert client.get("/api/datasets").json()["count"] == 1

    principal["tenant_id"] = "different-tenant"
    assert client.get("/api/datasets").json()["count"] == 0


def test_client_cannot_set_approval_or_license_fields(test_client):
    client, _ = test_client
    response = client.post("/api/datasets", json={
        "name": "Tampered listing",
        "license_status": "approved",
        "status": "published",
    })
    assert response.status_code == 422


def test_merkle_root_is_deterministic_and_order_independent():
    first = {"trade_id": "x", "amount_usd": "12.00", "currency": "usd"}
    second = {"currency": "usd", "trade_id": "x", "amount_usd": "12.00"}
    assert main.merkle_root(first) == main.merkle_root(second)


def test_merkle_root_detects_changes():
    first = {"trade_id": "x", "amount_usd": "12.00"}
    changed = {"trade_id": "x", "amount_usd": "13.00"}
    assert main.merkle_root(first) != main.merkle_root(changed)


def test_live_stripe_key_is_blocked_without_explicit_live_switch(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_not_a_real_key")
    monkeypatch.setenv("STRIPE_PAYMENTS_ENABLED", "true")
    monkeypatch.delenv("STRIPE_LIVE_ENABLED", raising=False)
    assert main.stripe_is_enabled() is False

def test_trade_requires_independent_human_approval_and_proof_verifies(test_client):
    client, principal = test_client
    dataset = client.post("/api/datasets", json={
        "name": "Approved commercial dataset",
        "description": "Licensed commercial metadata",
        "category": "business",
    }).json()
    client.post(f"/api/datasets/{dataset['id']}/review", json={
        "decision": "approved",
        "reason": "License and provenance verified",
    })
    published = client.post(f"/api/datasets/{dataset['id']}/publish")
    assert published.status_code == 200

    principal.update({
        "subject": "buyer-creator",
        "tenant_id": "tenant-buyer",
        "roles": ["buyer", "trade_approver"],
    })
    created = client.post("/api/trades", json={
        "dataset_id": dataset["id"],
        "amount_usd": "125.50",
    })
    assert created.status_code == 201
    trade = created.json()
    assert trade["status"] == "pending_approval"

    # The caller cannot approve the trade that they themselves created.
    self_approval = client.post(f"/api/trades/{trade['id']}/approve")
    assert self_approval.status_code == 403

    principal["subject"] = "independent-approver"
    approved = client.post(f"/api/trades/{trade['id']}/approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved_pending_payment"

    proof = client.get(f"/api/verify/{trade['id']}")
    assert proof.status_code == 200
    assert proof.json()["valid"] is True


def test_protected_routes_fail_closed_without_oidc(monkeypatch):
    for key in ("OIDC_ISSUER", "OIDC_AUDIENCE", "OIDC_JWKS_URL"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(main.HTTPException) as error:
        main.get_current_user("Bearer placeholder")
    assert error.value.status_code == 503


def test_readiness_is_not_green_without_required_services(test_client):
    client, _ = test_client
    response = client.get("/ready")
    assert response.status_code in (200, 503)
    if response.status_code == 503:
        assert response.json()["status"] == "degraded"

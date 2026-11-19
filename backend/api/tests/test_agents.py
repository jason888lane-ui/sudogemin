from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

import main
import agent_runtime
from agent_runtime import AgentTask, AgentControl, claim_one, store_result, store_failure


def setup():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    main.Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    principal = {"subject": "operator-1", "tenant_id": "tenant-one", "roles": ["tenant_admin", "agent_approver", "platform_admin"]}
    def db_override():
        db = sessions()
        try:
            yield db
        finally:
            db.close()
    main.app.dependency_overrides[main.get_db] = db_override
    main.app.dependency_overrides[main.get_current_user] = lambda: principal
    return TestClient(main.app), principal, sessions, engine


def test_enqueue_idempotency_tenant_and_status():
    client, principal, sessions, engine = setup()
    try:
        headers = {"Idempotency-Key": "idem-key-1234"}
        first = client.post("/api/agents/executive-intelligence/tasks", json={"prompt": "Summarize a dataset opportunity."}, headers=headers)
        second = client.post("/api/agents/executive-intelligence/tasks", json={"prompt": "Summarize a dataset opportunity."}, headers=headers)
        assert first.status_code == 202
        assert second.status_code == 202
        assert first.json()["id"] == second.json()["id"]
        assert client.get("/api/agent-tasks").json()["count"] == 1
        principal["tenant_id"] = "tenant-two"
        assert client.get("/api/agent-tasks").json()["count"] == 0
    finally:
        client.close()
        main.app.dependency_overrides.clear()
        engine.dispose()


def test_emergency_stop_and_worker_claim():
    client, _, sessions, engine = setup()
    try:
        assert client.put("/api/admin/agents/emergency-stop?enabled=true").json()["emergency_stop"] is True
        blocked = client.post("/api/agents/data-intelligence/tasks", json={"prompt": "Review a supplied schema."}, headers={"Idempotency-Key": "idem-key-5678"})
        assert blocked.status_code == 503
        with sessions() as db:
            assert claim_one(db, "test-worker") is None
    finally:
        client.close()
        main.app.dependency_overrides.clear()
        engine.dispose()


def test_worker_result_approval_failure_and_registry():
    client, _, sessions, engine = setup()
    try:
        created = client.post("/api/agents/marketplace-intelligence/tasks", json={"prompt": "Analyze marketplace metadata."}, headers={"Idempotency-Key": "idem-key-9012"})
        assert created.status_code == 202
        task_id = created.json()["id"]
        with sessions() as db:
            task = claim_one(db, "test-worker")
            assert task is not None
            assert task.id == task_id
            assert store_result(db, task_id, "test-worker", {"summary": "Analysis complete", "findings": [], "proposed_actions": [{"description": "Consider a listing update"}]})
            assert db.get(AgentTask, task_id).status == "approval_required"
        approved = client.post(f"/api/agent-tasks/{task_id}/approve")
        assert approved.status_code == 200
        assert approved.json()["status"] == "approved"
    finally:
        client.close()
        main.app.dependency_overrides.clear()
        engine.dispose()


def test_six_agents_registered():
    assert len(agent_runtime.AGENT_REGISTRY) == 6

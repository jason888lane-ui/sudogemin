import hashlib
import json
import os
import time
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

try:
    import stripe
except ImportError:
    stripe = None

try:
    import cudaq
except ImportError:
    cudaq = None

APP_NAME = "Sudogemin"
OWNER = "Jason Lane Davidson Jr"
SEED = os.getenv("MERKLE_SEED", "sudogemin-v1")
app = FastAPI(title=APP_NAME, version="1.0.0", description="B2B data brokerage API")

AGENTS = [
    {"id": "ingestion", "name": "Ingestion Agent", "role": "Register approved sources and preserve provenance and license evidence"},
    {"id": "quality", "name": "Data Quality and Enrichment Agent", "role": "Normalize metadata and validate completeness, freshness, and schema consistency"},
    {"id": "scoping", "name": "Customer Scoping Agent", "role": "Match buyer requirements to permitted dataset metadata"},
    {"id": "marketplace", "name": "Marketplace Agent", "role": "Prepare catalog listings, quotes, and entitlement checks"},
    {"id": "sales", "name": "Sales and Monetization Agent", "role": "Prepare sales workflows and billing events without bypassing payment controls"},
    {"id": "governance", "name": "Governance and Human Approval Agent", "role": "Enforce human approvals, audit, and release controls"},
]

DATASETS: dict[str, dict[str, Any]] = {}
TRADES: dict[str, dict[str, Any]] = {}

class DatasetIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=2000)
    category: str = Field(default="general", max_length=80)
    source: str = Field(default="user-submitted", max_length=200)
    record_count: int = Field(default=0, ge=0)
    license_status: str = Field(default="pending_review")

class MatchIn(BaseModel):
    buyer_id: str
    requirements: list[str] = []

class TradeIn(BaseModel):
    buyer_id: str
    dataset_id: str
    amount_usd: float = Field(gt=0, le=1000000)

def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def merkle_root(payload: str) -> str:
    # Deterministic leaf/root for an auditable record; never uses personal birth data.
    return digest(f"{SEED}:{payload}")

@app.get("/")
def home():
    return {"name": APP_NAME, "status": "operational", "version": app.version,
            "message": "Sudogemin API is running", "docs": "/docs"}

@app.get("/health")
def health():
    return {"status": "ok", "service": APP_NAME, "timestamp": int(time.time())}

@app.get("/api/agents")
def agents():
    return {"agents": AGENTS, "count": len(AGENTS), "mode": "workflow-scaffold",
            "note": "Agents expose defined responsibilities; connect model providers to enable LLM execution."}

@app.get("/api/dashboard")
def dashboard():
    return {"platform": APP_NAME, "owner": OWNER, "agents": len(AGENTS),
            "datasets": len(DATASETS), "trades": len(TRADES),
            "quantum": quantum_status(),
            "payments": stripe_status(),
            "approval_required": True}

@app.post("/api/datasets", status_code=201)
def create_dataset(item: DatasetIn):
    dataset_id = str(uuid.uuid4())
    record = item.model_dump()
    record.update({"id": dataset_id, "status": "pending_review",
                   "created_at": int(time.time())})
    DATASETS[dataset_id] = record
    return record

@app.get("/api/datasets")
def list_datasets():
    return {"items": list(DATASETS.values()), "count": len(DATASETS)}

@app.post("/api/match")
def match_buyer(item: MatchIn):
    terms = {term.lower() for term in item.requirements}
    results = []
    for ds in DATASETS.values():
        text = " ".join([ds["name"], ds["description"], ds["category"]]).lower()
        hits = sorted(term for term in terms if term in text)
        score = len(hits) / max(1, len(terms))
        results.append({"dataset": ds, "match_score": round(score, 4), "matched_terms": hits})
    return {"buyer_id": item.buyer_id, "matches": sorted(results, key=lambda x: x["match_score"], reverse=True)}

@app.post("/api/trades", status_code=503)
def create_trade(item: TradeIn):
    # Fail closed: the prototype has no authenticated approval workflow or durable
    # transaction store. A client-supplied boolean must never authorize a trade.
    raise HTTPException(
        status_code=503,
        detail="Trading is disabled until authenticated tenant-scoped approvals, durable storage, and payment reconciliation are implemented."
    )

@app.get("/api/trades")
def list_trades():
    return {"items": list(TRADES.values()), "count": len(TRADES)}

@app.get("/api/verify/{trade_id}")
def verify_trade(trade_id: str):
    trade = TRADES.get(trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    payload = json.dumps({"trade_id": trade["id"], "buyer_id": trade["buyer_id"],
                          "dataset_id": trade["dataset_id"], "amount_usd": trade["amount_usd"]},
                         sort_keys=True, separators=(",", ":"))
    return {"trade_id": trade_id, "valid": merkle_root(payload) == trade["merkle_root"],
            "merkle_root": trade["merkle_root"]}



class CheckoutIn(BaseModel):
    customer_email: str = Field(min_length=5, max_length=254)

class QuantumMatchIn(BaseModel):
    buyer_weights: list[float] = Field(min_length=1, max_length=16)
    dataset_weights: list[float] = Field(min_length=1, max_length=16)

def stripe_status():
    key = os.getenv("STRIPE_LIVE_SECRET_KEY", "")
    enabled = os.getenv("STRIPE_LIVE_ENABLED", "false").lower() == "true"
    ready = bool(stripe and enabled and key.startswith("sk_live_") and os.getenv("STRIPE_PRICE_ID"))
    return {"status": "live_ready" if ready else "configuration_required",
            "mode": "live" if key.startswith("sk_live_") else "off",
            "checkout_enabled": ready,
            "webhook_configured": bool(os.getenv("STRIPE_WEBHOOK_SECRET"))}

def quantum_status():
    enabled = os.getenv("CUDA_Q_ENABLED", "false").lower() == "true"
    ready = bool(cudaq and enabled)
    return {"status": "ready" if ready else ("package_missing" if enabled and not cudaq else "disabled"),
            "backend": os.getenv("CUDA_Q_TARGET", "qpp"),
            "runtime": "CUDA-Q" if cudaq else "not_installed",
            "note": "qpp is a CPU simulator; NVIDIA GPU execution requires a CUDA-capable host."}

def require_live_stripe():
    key = os.getenv("STRIPE_LIVE_SECRET_KEY", "")
    enabled = os.getenv("STRIPE_LIVE_ENABLED", "false").lower() == "true"
    if not stripe or not enabled or not key.startswith("sk_live_"):
        raise HTTPException(status_code=503, detail="Live Stripe is not configured.")
    stripe.api_key = key
    return stripe

@app.get("/api/integrations/status")
def integrations_status():
    return {"stripe": stripe_status(), "cuda_q": quantum_status()}

@app.post("/api/billing/checkout")
def create_subscription_checkout(item: CheckoutIn):
    sdk = require_live_stripe()
    price_id = os.getenv("STRIPE_PRICE_ID")
    if not price_id:
        raise HTTPException(status_code=503, detail="STRIPE_PRICE_ID is missing.")
    base = os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/")
    try:
        session = sdk.checkout.Session.create(
            mode="subscription",
            customer_email=item.customer_email,
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=base + "/?billing=success&session_id={CHECKOUT_SESSION_ID}",
            cancel_url=base + "/?billing=cancelled",
            metadata={"product": "Sudogemin"}
        )
        return {"checkout_url": session.url, "session_id": session.id, "mode": "subscription"}
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Stripe checkout failed: " + type(exc).__name__)

@app.post("/api/trades/{trade_id}/checkout")
def create_trade_checkout(trade_id: str):
    sdk = require_live_stripe()
    trade = TRADES.get(trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    if trade.get("status") != "approved_pending_payment":
        raise HTTPException(status_code=409, detail="Trade is not approved for checkout")
    base = os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/")
    try:
        session = sdk.checkout.Session.create(
            mode="payment",
            line_items=[{"price_data": {
                "currency": "usd",
                "unit_amount": int(round(trade["amount_usd"] * 100)),
                "product_data": {"name": "Sudogemin dataset purchase " + trade["dataset_id"]}
            }, "quantity": 1}],
            success_url=base + "/?trade=" + trade_id + "&payment=success",
            cancel_url=base + "/?trade=" + trade_id + "&payment=cancelled",
            metadata={"trade_id": trade_id, "merkle_root": trade["merkle_root"]}
        )
        trade["stripe_session_id"] = session.id
        trade["payment_status"] = "checkout_created"
        return {"checkout_url": session.url, "session_id": session.id}
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Stripe checkout failed: " + type(exc).__name__)

@app.post("/api/billing/webhook")
async def stripe_webhook(request: Request):
    if not stripe:
        raise HTTPException(status_code=503, detail="Stripe SDK is not installed")
    secret = os.getenv("STRIPE_WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(status_code=503, detail="STRIPE_WEBHOOK_SECRET is not configured")
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, signature, secret)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid Stripe webhook signature or payload")
    event_type = event["type"]
    obj = event["data"]["object"]
    if event_type == "checkout.session.completed":
        trade_id = (obj.get("metadata") or {}).get("trade_id")
        if trade_id in TRADES:
            TRADES[trade_id]["payment_status"] = "paid"
            TRADES[trade_id]["status"] = "paid"
    return {"received": True, "event_type": event_type}

@app.post("/api/quantum/match")
def quantum_match(item: QuantumMatchIn):
    if len(item.buyer_weights) != len(item.dataset_weights):
        raise HTTPException(status_code=422, detail="Buyer and dataset vectors must have equal length")
    if not cudaq or os.getenv("CUDA_Q_ENABLED", "false").lower() != "true":
        raise HTTPException(status_code=503, detail="CUDA-Q is not enabled; install the optional runtime and enable it.")
    target = os.getenv("CUDA_Q_TARGET", "qpp")
    try:
        cudaq.set_target(target)
        @cudaq.kernel
        def matching_kernel(buyer: list[float], dataset: list[float]):
            q = cudaq.qvector(len(buyer))
            for i in range(len(buyer)):
                ry(buyer[i], q[i])
                rz(dataset[i], q[i])
            for i in range(len(buyer) - 1):
                x.ctrl(q[i], q[i + 1])
            mz(q)
        counts = cudaq.sample(matching_kernel, item.buyer_weights,
                              item.dataset_weights, shots_count=1000)
        key = "0" * len(item.buyer_weights)
        score = float(counts.get(key, 0)) / 1000.0
        return {"engine": "CUDA-Q", "target": target, "shots": 1000,
                "zero_state_probability": score,
                "note": "Measurement statistic, not a validated commercial relevance score."}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="CUDA-Q execution failed: " + type(exc).__name__)

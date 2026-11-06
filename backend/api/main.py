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
    {"id": "ingestion", "name": "Ingestion Agent", "role": "Validate and register incoming data sources"},
    {"id": "enrichment", "name": "Enrichment Agent", "role": "Normalize metadata and enrich dataset profiles"},
    {"id": "quality", "name": "Data Quality Agent", "role": "Check completeness, freshness, and schema consistency"},
    {"id": "scoping", "name": "Customer Scoping Agent", "role": "Match buyer requirements to dataset metadata"},
    {"id": "marketplace", "name": "Marketplace Agent", "role": "Prepare listings and quote workflows"},
    {"id": "governance", "name": "Human Approval Agent", "role": "Require human approval before publishing or charging"},
]

DATASETS: dict[str, dict[str, Any]] = {}
TRADES: dict[str, dict[str, Any]] = {}
# Temporary process-local webhook state. Use a database-backed event ledger before production scale.
PROCESSED_STRIPE_EVENTS: set[str] = set()
SUBSCRIPTIONS: dict[str, dict[str, Any]] = {}

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
    human_approved: bool = False

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

@app.post("/api/trades", status_code=202)
def create_trade(item: TradeIn):
    if item.dataset_id not in DATASETS:
        raise HTTPException(status_code=404, detail="Dataset not found")
    if not item.human_approved:
        raise HTTPException(status_code=409, detail="Human approval required before trade or payment")
    trade_id = str(uuid.uuid4())
    payload = json.dumps({"trade_id": trade_id, "buyer_id": item.buyer_id,
                          "dataset_id": item.dataset_id, "amount_usd": item.amount_usd},
                         sort_keys=True, separators=(",", ":"))
    root = merkle_root(payload)
    trade = {"id": trade_id, "status": "approved_pending_payment",
             "buyer_id": item.buyer_id, "dataset_id": item.dataset_id,
             "amount_usd": item.amount_usd, "merkle_root": root,
             "payment_status": "not_configured", "created_at": int(time.time())}
    # No charge is initiated by this endpoint. Stripe checkout/payment integration is a later explicit step.
    TRADES[trade_id] = trade
    return trade

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
    base = os.getenv("APP_BASE_URL", "https://sudogemini.onrender.com").rstrip("/")
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
    base = os.getenv("APP_BASE_URL", "https://sudogemini.onrender.com").rstrip("/")
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
    """Receive signed Stripe events. Configure this exact URL in Stripe:
    https://sudogemini.onrender.com/api/billing/webhook
    """
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

    event_id = event.get("id")
    event_type = event.get("type", "unknown")
    if not event_id:
        raise HTTPException(status_code=400, detail="Stripe event ID is missing")
    if event_id in PROCESSED_STRIPE_EVENTS:
        return {"received": True, "duplicate": True, "event_type": event_type}

    obj = (event.get("data") or {}).get("object") or {}
    metadata = obj.get("metadata") or {}

    if event_type in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        trade_id = metadata.get("trade_id")
        if trade_id in TRADES:
            TRADES[trade_id]["payment_status"] = "paid"
            TRADES[trade_id]["status"] = "paid"
        subscription_id = obj.get("subscription")
        if subscription_id:
            SUBSCRIPTIONS[str(subscription_id)] = {
                "id": str(subscription_id),
                "customer_id": obj.get("customer"),
                "status": "active",
                "checkout_session_id": obj.get("id"),
                "updated_at": int(time.time())
            }

    elif event_type in (
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted"
    ):
        subscription_id = obj.get("id")
        if subscription_id:
            SUBSCRIPTIONS[str(subscription_id)] = {
                "id": str(subscription_id),
                "customer_id": obj.get("customer"),
                "status": obj.get("status", "unknown"),
                "price_ids": [
                    item.get("price", {}).get("id")
                    for item in (obj.get("items") or {}).get("data", [])
                    if item.get("price", {}).get("id")
                ],
                "cancel_at_period_end": bool(obj.get("cancel_at_period_end", False)),
                "updated_at": int(time.time())
            }

    elif event_type in ("invoice.paid", "invoice.payment_succeeded"):
        subscription_id = obj.get("subscription")
        if subscription_id and str(subscription_id) in SUBSCRIPTIONS:
            SUBSCRIPTIONS[str(subscription_id)]["last_invoice_status"] = "paid"
            SUBSCRIPTIONS[str(subscription_id)]["updated_at"] = int(time.time())

    elif event_type == "invoice.payment_failed":
        subscription_id = obj.get("subscription")
        if subscription_id and str(subscription_id) in SUBSCRIPTIONS:
            SUBSCRIPTIONS[str(subscription_id)]["last_invoice_status"] = "payment_failed"
            SUBSCRIPTIONS[str(subscription_id)]["updated_at"] = int(time.time())

    # Mark only after successful handling so Stripe retries events that fail above.
    PROCESSED_STRIPE_EVENTS.add(event_id)
    return {"received": True, "duplicate": False, "event_type": event_type}

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

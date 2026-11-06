import hashlib
import json
import os
import time
import uuid
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
import jwt
from jwt import PyJWKClient
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from personal_intelligence import executive_guidance

try:
    import stripe
except ImportError:
    stripe = None

try:
    import cudaq
except ImportError:
    cudaq = None

APP_NAME = "Sudogemini"
OWNER = "Jason Lane Davidson Jr"
SEED = os.getenv("MERKLE_SEED", "sudogemini-v1")
AWS_REGION = os.getenv("AWS_REGION", "us-east-2")
DATASETS_TABLE = os.getenv("DATASETS_TABLE", "sudogemini-staging-datasets")
TRADES_TABLE = os.getenv("TRADES_TABLE", "sudogemini-staging-trades")
COGNITO_POOL_ID = os.getenv("COGNITO_USER_POOL_ID", "")
COGNITO_CLIENT_ID = os.getenv("COGNITO_APP_CLIENT_ID", "")
COGNITO_ISSUER = f"https://cognito-idp.{AWS_REGION}.amazonaws.com/{COGNITO_POOL_ID}" if COGNITO_POOL_ID else ""
COGNITO_JWKS_URL = f"{COGNITO_ISSUER}/.well-known/jwks.json" if COGNITO_ISSUER else ""
_jwks_client = PyJWKClient(COGNITO_JWKS_URL, cache_keys=True) if COGNITO_JWKS_URL else None
_dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
_datasets = _dynamodb.Table(DATASETS_TABLE)
_trades = _dynamodb.Table(TRADES_TABLE)

app = FastAPI(title=APP_NAME, version="1.1.0", description="Tenant-authenticated B2B data brokerage API")

AGENTS = [
    {"id": "ingestion", "name": "Ingestion Agent", "role": "Register approved sources and preserve provenance and license evidence"},
    {"id": "quality", "name": "Data Quality and Enrichment Agent", "role": "Normalize metadata and validate completeness, freshness, and schema consistency"},
    {"id": "scoping", "name": "Customer Scoping Agent", "role": "Match buyer requirements to permitted dataset metadata"},
    {"id": "marketplace", "name": "Marketplace Agent", "role": "Prepare catalog listings, quotes, and entitlement checks"},
    {"id": "sales", "name": "Sales and Monetization Agent", "role": "Prepare sales workflows and billing events without bypassing payment controls"},
    {"id": "governance", "name": "Governance and Human Approval Agent", "role": "Enforce human approvals, audit, and release controls"},
]

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
    return digest(f"{SEED}:{payload}")

def tenant(request: Request) -> str:
    value = getattr(request.state, "tenant_id", None)
    if not value:
        raise HTTPException(status_code=401, detail="Authenticated tenant context is required")
    return value

@app.middleware("http")
async def cognito_authentication(request: Request, call_next):
    path = request.url.path
    # Health probes and the signed Stripe webhook are the only unauthenticated API exceptions.
    if path.startswith("/api/") and path != "/api/billing/webhook":
        if not _jwks_client or not COGNITO_CLIENT_ID or not COGNITO_ISSUER:
            return await _auth_unavailable()
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return _unauthorized()
        token = auth[7:].strip()
        try:
            signing_key = _jwks_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token, signing_key.key, algorithms=["RS256"],
                audience=COGNITO_CLIENT_ID, issuer=COGNITO_ISSUER,
                options={"require": ["exp", "iat", "sub", "aud", "iss"]}
            )
            if claims.get("token_use") != "id":
                return _unauthorized()
            tenant_id = claims.get("custom:tenant_id")
            if not isinstance(tenant_id, str) or not tenant_id.strip():
                return _unauthorized()
            request.state.tenant_id = tenant_id
            request.state.subject = claims["sub"]
        except Exception:
            return _unauthorized()
    return await call_next(request)

async def _auth_unavailable():
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=503, content={"detail": "Authentication provider is not configured"})

def _unauthorized():
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=401, content={"detail": "A valid Cognito ID token with tenant identity is required"})

@app.get("/")
def home():
    return {"name": APP_NAME, "status": "operational", "version": app.version,
            "message": "Sudogemini API is running", "docs": "/docs"}

@app.get("/health")
def health():
    return {"status": "ok", "service": APP_NAME, "timestamp": int(time.time())}

@app.get("/api/agents")
def agents():
    return {"agents": AGENTS, "count": len(AGENTS), "mode": "workflow-scaffold",
            "note": "Agents expose defined responsibilities; connect model providers to enable LLM execution."}

@app.get("/api/dashboard")
def dashboard(request: Request):
    tid = tenant(request)
    ds_count = _datasets.query(Select="COUNT", KeyConditionExpression=Key("tenant_id").eq(tid))["Count"]
    trade_count = _trades.query(Select="COUNT", KeyConditionExpression=__import__("boto3").dynamodb.conditions.Key("tenant_id").eq(tid))["Count"]
    return {"platform": APP_NAME, "owner": OWNER, "agents": len(AGENTS),
            "datasets": ds_count, "trades": trade_count,
            "quantum": quantum_status(), "payments": stripe_status(),
            "approval_required": True, "tenant_scoped": True}

@app.get("/api/personal-intelligence")
def personal_intelligence(request: Request, year: int | None = None):
    # This is private owner data, never shared with ordinary customer tenants.
    owner_sub = os.getenv("OWNER_COGNITO_SUB", "").strip()
    if not owner_sub:
        raise HTTPException(status_code=503, detail="Owner profile access is not configured")
    if getattr(request.state, "subject", None) != owner_sub:
        raise HTTPException(status_code=403, detail="Owner access required")
    return executive_guidance(year)

@app.post("/api/datasets", status_code=201)
def create_dataset(item: DatasetIn, request: Request):
    tid = tenant(request)
    record_id = str(uuid.uuid4())
    record = item.model_dump()
    record.update({"id": record_id, "record_id": record_id, "tenant_id": tid,
                   "status": "pending_review", "created_at": int(time.time())})
    _datasets.put_item(Item=record)
    return {k: v for k, v in record.items() if k != "tenant_id"}

@app.get("/api/datasets")
def list_datasets(request: Request):
    tid = tenant(request)
    response = _datasets.query(KeyConditionExpression=__import__("boto3").dynamodb.conditions.Key("tenant_id").eq(tid))
    items = [{k: v for k, v in item.items() if k != "tenant_id"} for item in response.get("Items", [])]
    return {"items": items, "count": len(items)}

@app.post("/api/match")
def match_buyer(item: MatchIn, request: Request):
    tid = tenant(request)
    terms = {term.lower() for term in item.requirements}
    response = _datasets.query(KeyConditionExpression=__import__("boto3").dynamodb.conditions.Key("tenant_id").eq(tid))
    results = []
    for ds in response.get("Items", []):
        text = " ".join([ds["name"], ds.get("description", ""), ds.get("category", "")]).lower()
        hits = sorted(term for term in terms if term in text)
        score = len(hits) / max(1, len(terms))
        safe_ds = {k: v for k, v in ds.items() if k != "tenant_id"}
        results.append({"dataset": safe_ds, "match_score": round(score, 4), "matched_terms": hits})
    return {"buyer_id": item.buyer_id, "matches": sorted(results, key=lambda x: x["match_score"], reverse=True)}

@app.post("/api/trades", status_code=503)
def create_trade(item: TradeIn):
    raise HTTPException(status_code=503, detail="Trading is disabled until authenticated approval, durable transaction, and payment reconciliation workflows are implemented.")

@app.get("/api/trades")
def list_trades(request: Request):
    tid = tenant(request)
    response = _trades.query(KeyConditionExpression=__import__("boto3").dynamodb.conditions.Key("tenant_id").eq(tid))
    items = [{k: v for k, v in item.items() if k != "tenant_id"} for item in response.get("Items", [])]
    return {"items": items, "count": len(items)}

@app.get("/api/verify/{trade_id}")
def verify_trade(trade_id: str, request: Request):
    tid = tenant(request)
    response = _trades.get_item(Key={"tenant_id": tid, "record_id": trade_id})
    trade = response.get("Item")
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
        session = sdk.checkout.Session.create(mode="subscription", customer_email=item.customer_email,
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=base + "/?billing=success&session_id={CHECKOUT_SESSION_ID}",
            cancel_url=base + "/?billing=cancelled", metadata={"product": "Sudogemini"})
        return {"checkout_url": session.url, "session_id": session.id, "mode": "subscription"}
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Stripe checkout failed: " + type(exc).__name__)

@app.post("/api/trades/{trade_id}/checkout")
def create_trade_checkout(trade_id: str, request: Request):
    sdk = require_live_stripe()
    tid = tenant(request)
    response = _trades.get_item(Key={"tenant_id": tid, "record_id": trade_id})
    trade = response.get("Item")
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    if trade.get("status") != "approved_pending_payment":
        raise HTTPException(status_code=409, detail="Trade is not approved for checkout")
    base = os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/")
    try:
        session = sdk.checkout.Session.create(mode="payment",
            line_items=[{"price_data":{"currency":"usd","unit_amount":int(round(float(trade["amount_usd"])*100)),
            "product_data":{"name":"Sudogemini dataset purchase "+trade["dataset_id"]}},"quantity":1}],
            success_url=base+"/?trade="+trade_id+"&payment=success",
            cancel_url=base+"/?trade="+trade_id+"&payment=cancelled",
            metadata={"trade_id":trade_id,"tenant_id":tid,"merkle_root":trade["merkle_root"]})
        _trades.update_item(Key={"tenant_id":tid,"record_id":trade_id},
            UpdateExpression="SET stripe_session_id=:sid, payment_status=:status",
            ExpressionAttributeValues={":sid":session.id,":status":"checkout_created"})
        return {"checkout_url":session.url,"session_id":session.id}
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Stripe checkout failed: "+type(exc).__name__)

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
        metadata = obj.get("metadata") or {}
        trade_id, tid = metadata.get("trade_id"), metadata.get("tenant_id")
        if trade_id and tid:
            _trades.update_item(Key={"tenant_id":tid,"record_id":trade_id},
                UpdateExpression="SET payment_status=:paid, #s=:status",
                ExpressionAttributeNames={"#s":"status"},
                ExpressionAttributeValues={":paid":"paid",":status":"paid"})
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
            for i in range(len(buyer)-1):
                x.ctrl(q[i], q[i+1])
            mz(q)
        counts = cudaq.sample(matching_kernel, item.buyer_weights, item.dataset_weights, shots_count=1000)
        key = "0" * len(item.buyer_weights)
        return {"engine":"CUDA-Q","target":target,"shots":1000,
                "zero_state_probability":float(counts.get(key,0))/1000.0,
                "note":"Measurement statistic, not a validated commercial relevance score."}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="CUDA-Q execution failed: "+type(exc).__name__)

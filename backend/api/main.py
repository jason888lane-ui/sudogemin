import hashlib
import json
import os
import time
import uuid
from typing import Any, Literal
from decimal import Decimal, ROUND_HALF_UP

import boto3
from boto3.dynamodb.conditions import Attr, Key
import jwt
from jwt import PyJWKClient
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from personal_intelligence import executive_guidance
from platform_engine import create_upload_url, enqueue_job

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
_marketplace = _dynamodb.Table(os.getenv("MARKETPLACE_TABLE", "sudogemini-staging-marketplace"))
_agent_jobs = _dynamodb.Table(os.getenv("AGENT_JOBS_TABLE", "sudogemini-agent-jobs"))
_subscriptions = _dynamodb.Table(os.getenv("SUBSCRIPTIONS_TABLE", "sudogemini-subscriptions"))
_stripe_events = _dynamodb.Table(os.getenv("STRIPE_EVENTS_TABLE", "sudogemini-stripe-events"))

app = FastAPI(title=APP_NAME, version="1.2.0", description="Tenant-authenticated B2B data brokerage API")

_allowed_origins = [x.strip() for x in os.getenv(
    "CORS_ORIGINS",
    "https://sudogemini.com,https://www.sudogemini.com,https://app.sudogemini.com"
).split(",") if x.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

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
    license_evidence_url: str = Field(default="", max_length=2000)
    permitted_use: str = Field(default="", max_length=1000)
    license_status: Literal["pending_review"] = "pending_review"

class MatchIn(BaseModel):
    buyer_id: str
    requirements: list[str] = []

class TradeIn(BaseModel):
    listing_id: str = Field(min_length=1, max_length=100)
    accept_license: bool = False

class ListingIn(BaseModel):
    dataset_id: str = Field(min_length=1, max_length=100)
    price_usd: Decimal = Field(gt=0, le=1000000)
    license_summary: str = Field(min_length=10, max_length=2000)

class UploadUrlIn(BaseModel):
    filename: str = Field(min_length=1, max_length=200)
    content_type: str

class AgentRunIn(BaseModel):
    agent_id: Literal["ingestion", "quality", "scoping", "marketplace", "sales", "governance"]
    task: str = Field(min_length=1, max_length=6000)

class SubscriptionCheckoutIn(BaseModel):
    customer_email: str = Field(min_length=5, max_length=254)

class SellerOnboardingIn(BaseModel):
    customer_email: str = Field(min_length=5, max_length=254)

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
            # Users without an administrator-assigned organization claim get an isolated tenant keyed by verified Cognito sub.
            # Never accept tenant IDs from request bodies or query parameters.
            tenant_id = claims.get("custom:tenant_id") or claims["sub"]
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
    index = os.path.join(os.path.dirname(__file__), "static", "index.html")
    if os.path.isfile(index):
        return FileResponse(index, media_type="text/html")
    return {"name": APP_NAME, "status": "operational", "version": app.version, "docs": "/docs"}

@app.get("/platform.js")
def platform_dashboard_script():
    script = os.path.join(os.path.dirname(__file__), "static", "platform.js")
    if not os.path.isfile(script):
        raise HTTPException(status_code=404, detail="Dashboard module not found.")
    return FileResponse(script, media_type="application/javascript")

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
    trade_count = _trades.query(Select="COUNT", KeyConditionExpression=Key("tenant_id").eq(tid))["Count"]
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
                   "status": "pending_review", "license_status": "pending_review",
                   "created_at": int(time.time())})
    _datasets.put_item(Item=record)
    return {k: v for k, v in record.items() if k != "tenant_id"}

@app.get("/api/datasets")
def list_datasets(request: Request):
    tid = tenant(request)
    response = _datasets.query(KeyConditionExpression=Key("tenant_id").eq(tid))
    items = [{k: v for k, v in item.items() if k != "tenant_id"} for item in response.get("Items", [])]
    return {"items": items, "count": len(items)}

@app.post("/api/match")
def match_buyer(item: MatchIn, request: Request):
    tid = tenant(request)
    terms = {term.lower() for term in item.requirements}
    response = _datasets.query(KeyConditionExpression=Key("tenant_id").eq(tid))
    results = []
    for ds in response.get("Items", []):
        text = " ".join([ds["name"], ds.get("description", ""), ds.get("category", "")]).lower()
        hits = sorted(term for term in terms if term in text)
        score = len(hits) / max(1, len(terms))
        safe_ds = {k: v for k, v in ds.items() if k != "tenant_id"}
        results.append({"dataset": safe_ds, "match_score": round(score, 4), "matched_terms": hits})
    return {"buyer_id": item.buyer_id, "matches": sorted(results, key=lambda x: x["match_score"], reverse=True)}

def enforce_paid_entitlement(request: Request) -> None:
    if os.getenv("REQUIRE_ACTIVE_SUBSCRIPTION", "false").lower() != "true":
        return
    tid = tenant(request)
    subscription = _subscriptions.get_item(Key={"tenant_id":tid}).get("Item")
    if not subscription or subscription.get("status") not in {"active","trialing"} or subscription.get("last_invoice_status") == "failed":
        raise HTTPException(status_code=402,detail="An active Sudogemini subscription is required for this operation.")

def require_platform_admin(request: Request) -> None:
    subject = getattr(request.state, "subject", "")
    allowed = {x.strip() for x in os.getenv("PLATFORM_ADMIN_SUBS", "").split(",") if x.strip()}
    if not subject or subject not in allowed:
        raise HTTPException(status_code=403, detail="Platform administrator approval is required.")

@app.get("/api/datasets/review-queue")
def dataset_review_queue(request: Request):
    require_platform_admin(request)
    try:
        result = _datasets.scan(FilterExpression=Attr("license_status").eq("pending_review"), Limit=100)
        return {"items": [{k:v for k,v in x.items() if k != "tenant_id"} for x in result.get("Items", [])],
                "count": len(result.get("Items", []))}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Dataset review queue unavailable: "+type(exc).__name__)

@app.post("/api/datasets/{dataset_id}/approve")
def approve_dataset(dataset_id: str, request: Request):
    require_platform_admin(request)
    try:
        result = _datasets.scan(FilterExpression=Attr("record_id").eq(dataset_id), Limit=100)
        matches = result.get("Items", [])
        item = next((x for x in matches if x.get("license_status") == "pending_review"), None)
        if not item:
            raise HTTPException(status_code=404, detail="Pending dataset not found.")
        if not item.get("license_evidence_url") or not item.get("permitted_use"):
            raise HTTPException(status_code=409, detail="License evidence URL and permitted-use statement are required.")
        owner_tid = item["tenant_id"]
        _datasets.update_item(Key={"tenant_id": owner_tid, "record_id": dataset_id},
            UpdateExpression="SET license_status=:approved, #s=:approved, approved_at=:at, approved_by=:actor",
            ExpressionAttributeNames={"#s":"status"},
            ExpressionAttributeValues={":approved":"approved",":pending":"pending_review",":at":int(time.time()),":actor":request.state.subject},
            ConditionExpression="license_status = :pending",
            )
        return {"dataset_id":dataset_id,"tenant_id":owner_tid,"status":"approved","license_status":"approved"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Dataset approval failed: "+type(exc).__name__)

@app.post("/api/ingestion/{dataset_id}/upload-url")
def ingestion_upload_url(dataset_id: str, item: UploadUrlIn, request: Request):
    tid = tenant(request)
    dataset = _datasets.get_item(Key={"tenant_id":tid,"record_id":dataset_id}).get("Item")
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    if dataset.get("status") != "approved" or dataset.get("license_status") != "approved":
        raise HTTPException(status_code=409, detail="A platform administrator must approve documented data rights before upload.")
    enforce_paid_entitlement(request)
    try:
        return create_upload_url(tenant_id=tid,dataset_id=dataset_id,
            filename=item.filename,content_type=item.content_type)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503 if isinstance(exc,RuntimeError) else 422, detail=str(exc))

@app.post("/api/ingestion/{dataset_id}/complete", status_code=202)
def ingestion_complete(dataset_id: str, object_key: str, request: Request):
    tid = tenant(request)
    dataset = _datasets.get_item(Key={"tenant_id":tid,"record_id":dataset_id}).get("Item")
    if not dataset or dataset.get("license_status") != "approved":
        raise HTTPException(status_code=404, detail="Approved dataset not found.")
    expected_prefix = f"incoming/{tid}/{dataset_id}/"
    if not object_key.startswith(expected_prefix) or ".." in object_key:
        raise HTTPException(status_code=422, detail="Object key is outside this tenant's dataset upload prefix.")
    try:
        job_id = enqueue_job("ingestion",{"tenant_id":tid,"dataset_id":dataset_id,"object_key":object_key})
        _datasets.update_item(Key={"tenant_id":tid,"record_id":dataset_id},
            UpdateExpression="SET ingestion_status=:queued, ingestion_job_id=:job",
            ExpressionAttributeValues={":queued":"queued",":job":job_id})
        return {"job_id":job_id,"status":"queued"}
    except RuntimeError as exc:
        raise HTTPException(status_code=503,detail=str(exc))

@app.post("/api/agents/run", status_code=202)
def run_agent(item: AgentRunIn, request: Request):
    tid=tenant(request); job_id=str(uuid.uuid4())
    record={"tenant_id":tid,"job_id":job_id,"agent_id":item.agent_id,
            "status":"queued","task":item.task,"created_at":int(time.time()),
            "requires_human_approval":True}
    try:
        _agent_jobs.put_item(Item=record,ConditionExpression="attribute_not_exists(job_id)")
        queued_id=enqueue_job("agent",{"tenant_id":tid,"agent_id":item.agent_id,
                                      "task":item.task,"job_id":job_id})
        return {"job_id":queued_id,"status":"queued","requires_human_approval":True}
    except Exception as exc:
        try: _agent_jobs.delete_item(Key={"tenant_id":tid,"job_id":job_id})
        except Exception: pass
        raise HTTPException(status_code=503,detail="Agent queue is unavailable: "+type(exc).__name__)

@app.get("/api/agents/jobs/{job_id}")
def get_agent_job(job_id: str, request: Request):
    tid=tenant(request)
    job=_agent_jobs.get_item(Key={"tenant_id":tid,"job_id":job_id}).get("Item")
    if not job: raise HTTPException(status_code=404,detail="Agent job not found.")
    job.pop("tenant_id",None)
    return job

@app.get("/api/marketplace/review-queue")
def marketplace_review_queue(request: Request):
    require_platform_admin(request)
    try:
        rows=_marketplace.query(KeyConditionExpression=Key("status").eq("pending_review"),
            ScanIndexForward=False,Limit=100)
        return {"items":rows.get("Items",[]),"count":len(rows.get("Items",[]))}
    except Exception as exc:
        raise HTTPException(status_code=503,detail="Marketplace review queue is unavailable: "+type(exc).__name__)

@app.get("/api/marketplace/listings")
def marketplace_listings():
    try:
        result=_marketplace.query(KeyConditionExpression=Key("status").eq("approved"),
            ScanIndexForward=False,Limit=100)
        return {"items":result.get("Items",[]),"count":len(result.get("Items",[]))}
    except Exception as exc:
        raise HTTPException(status_code=503,detail="Marketplace catalog is not provisioned: "+type(exc).__name__)

@app.post("/api/marketplace/listings",status_code=202)
def create_listing(item: ListingIn,request: Request):
    tid=tenant(request)
    enforce_paid_entitlement(request)
    dataset=_datasets.get_item(Key={"tenant_id":tid,"record_id":item.dataset_id}).get("Item")
    if not dataset or dataset.get("status")!="approved" or dataset.get("license_status")!="approved":
        raise HTTPException(status_code=409,detail="Only approved, licensed datasets can be submitted for listing.")
    listing_id=str(uuid.uuid4()); created=int(time.time())
    record={"status":"pending_review","created_at_listing_id":f"{created:020d}#{listing_id}",
        "listing_id":listing_id,"seller_tenant_id":tid,"dataset_id":item.dataset_id,
        "title":dataset["name"],"description":dataset.get("description",""),
        "category":dataset.get("category","general"),"price_usd":item.price_usd,
        "license_summary":item.license_summary,"created_at":created}
    try: _marketplace.put_item(Item=record)
    except Exception as exc: raise HTTPException(status_code=503,detail="Marketplace is not provisioned: "+type(exc).__name__)
    return {"listing_id":listing_id,"status":"pending_review"}

@app.post("/api/marketplace/listings/{listing_id}/approve")
def approve_listing(listing_id: str,request: Request):
    require_platform_admin(request)
    rows=_marketplace.query(KeyConditionExpression=Key("status").eq("pending_review"))
    listing=next((x for x in rows.get("Items",[]) if x.get("listing_id")==listing_id),None)
    if not listing: raise HTTPException(status_code=404,detail="Pending listing not found.")
    _marketplace.delete_item(Key={"status":"pending_review","created_at_listing_id":listing["created_at_listing_id"]})
    listing["status"]="approved"
    _marketplace.put_item(Item=listing)
    return {"listing_id":listing_id,"status":"approved"}

@app.post("/api/trades",status_code=201)
def create_trade(item: TradeIn,request: Request):
    buyer_tid=tenant(request)
    enforce_paid_entitlement(request)
    if not item.accept_license:
        raise HTTPException(status_code=422,detail="Explicit acceptance of the listing license terms is required.")
    rows=_marketplace.query(KeyConditionExpression=Key("status").eq("approved"))
    listing=next((x for x in rows.get("Items",[]) if x.get("listing_id")==item.listing_id),None)
    if not listing: raise HTTPException(status_code=404,detail="Approved marketplace listing not found.")
    if listing["seller_tenant_id"]==buyer_tid:
        raise HTTPException(status_code=409,detail="A seller cannot purchase its own listing.")
    trade_id=str(uuid.uuid4()); now=int(time.time())
    payload={"trade_id":trade_id,"buyer_tenant_id":buyer_tid,
        "seller_tenant_id":listing["seller_tenant_id"],"listing_id":item.listing_id,
        "dataset_id":listing["dataset_id"],"amount_usd":format(Decimal(str(listing["price_usd"])).quantize(Decimal("0.01")), ".2f"),
        "accepted_license_terms":listing["license_summary"],"license_accepted_at":now}
    proof=json.dumps(payload,sort_keys=True,separators=(",",":"))
    base={"id":trade_id,"buyer_id":buyer_tid,"dataset_id":listing["dataset_id"],
        "listing_id":item.listing_id,"amount_usd":Decimal(str(listing["price_usd"])).quantize(Decimal("0.01")),
        "seller_tenant_id":listing["seller_tenant_id"],"buyer_tenant_id":buyer_tid,
        "accepted_license_terms":listing["license_summary"],"license_accepted_at":now,
        "status":"pending_seller_approval","payment_status":"not_started",
        "merkle_root":merkle_root(proof),"created_at":now}
    seller_copy={**base,"record_id":"seller#"+trade_id,"role":"seller"}
    buyer_copy={**base,"record_id":trade_id,"role":"buyer"}
    try:
        _trades.put_item(Item={**buyer_copy,"tenant_id":buyer_tid},
            ConditionExpression="attribute_not_exists(record_id)")
        _trades.put_item(Item={**seller_copy,"tenant_id":listing["seller_tenant_id"]},
            ConditionExpression="attribute_not_exists(record_id)")
    except Exception as exc:
        try: _trades.delete_item(Key={"tenant_id":buyer_tid,"record_id":trade_id})
        except Exception: pass
        raise HTTPException(status_code=503,detail="Could not persist marketplace order: "+type(exc).__name__)
    return {"trade_id":trade_id,"status":"pending_seller_approval","amount_usd":base["amount_usd"]}

@app.post("/api/trades/{trade_id}/seller-approve")
def seller_approve_trade(trade_id: str,request: Request):
    seller_tid=tenant(request)
    key={"tenant_id":seller_tid,"record_id":"seller#"+trade_id}
    trade=_trades.get_item(Key=key).get("Item")
    if not trade: raise HTTPException(status_code=404,detail="Seller order not found.")
    if trade.get("role")!="seller" or trade.get("status")!="pending_seller_approval":
        raise HTTPException(status_code=409,detail="Order is not awaiting seller approval.")
    buyer_tid=trade["buyer_tenant_id"]
    _trades.update_item(Key={"tenant_id":buyer_tid,"record_id":trade_id},
        UpdateExpression="SET #s=:approved",ExpressionAttributeNames={"#s":"status"},
        ExpressionAttributeValues={":approved":"approved_pending_payment"})
    _trades.update_item(Key=key,UpdateExpression="SET #s=:approved",
        ExpressionAttributeNames={"#s":"status"},
        ExpressionAttributeValues={":approved":"approved_pending_payment"})
    return {"trade_id":trade_id,"status":"approved_pending_payment"}

@app.get("/api/trades")
def list_trades(request: Request):
    tid = tenant(request)
    response = _trades.query(KeyConditionExpression=Key("tenant_id").eq(tid))
    items = [{k: v for k, v in item.items() if k != "tenant_id"} for item in response.get("Items", [])]
    return {"items": items, "count": len(items)}

@app.get("/api/verify/{trade_id}")
def verify_trade(trade_id: str, request: Request):
    tid = tenant(request)
    response = _trades.get_item(Key={"tenant_id": tid, "record_id": trade_id})
    trade = response.get("Item")
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    payload = json.dumps({"trade_id":trade["id"],"buyer_tenant_id":trade["buyer_tenant_id"],
        "seller_tenant_id":trade["seller_tenant_id"],"listing_id":trade["listing_id"],
        "dataset_id":trade["dataset_id"],"amount_usd":format(Decimal(str(trade["amount_usd"])).quantize(Decimal("0.01")), ".2f"),
        "accepted_license_terms":trade["accepted_license_terms"],
        "license_accepted_at":trade["license_accepted_at"]},
        sort_keys=True,separators=(",",":"))
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
            "webhook_configured": bool(os.getenv("STRIPE_WEBHOOK_SECRET")),
            "subscription_entitlements": bool(os.getenv("SUBSCRIPTIONS_TABLE")),
            "idempotency_store": bool(os.getenv("STRIPE_EVENTS_TABLE"))}

def quantum_status():
    enabled = os.getenv("CUDA_Q_ENABLED", "false").lower() == "true"
    ready = bool(cudaq and enabled)
    return {"status": "ready" if ready else ("package_missing" if enabled and not cudaq else "disabled"),
            "backend": os.getenv("CUDA_Q_TARGET", "qpp-cpu"),
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
def create_subscription_checkout(item: SubscriptionCheckoutIn, request: Request):
    tid = tenant(request)
    sdk = require_live_stripe()
    price_id = os.getenv("STRIPE_PRICE_ID")
    if not price_id:
        raise HTTPException(status_code=503, detail="STRIPE_PRICE_ID is missing.")
    base = os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/")
    try:
        session = sdk.checkout.Session.create(mode="subscription", customer_email=item.customer_email,
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=base + "/?billing=success&session_id={CHECKOUT_SESSION_ID}",
            cancel_url=base + "/?billing=cancelled", metadata={"product": "Sudogemini", "tenant_id": tid},
            subscription_data={"metadata":{"tenant_id":tid,"product":"Sudogemini"}})
        return {"checkout_url": session.url, "session_id": session.id, "mode": "subscription"}
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Stripe checkout failed: " + type(exc).__name__)

@app.post("/api/marketplace/seller/onboarding")
def create_seller_onboarding(item: SellerOnboardingIn, request: Request):
    if os.getenv("STRIPE_CONNECT_ENABLED","false").lower() != "true":
        raise HTTPException(status_code=503,detail="Stripe Connect onboarding is not enabled.")
    sdk=require_live_stripe()
    tid=tenant(request)
    base=os.getenv("APP_BASE_URL","https://app.sudogemini.com").rstrip("/")
    try:
        existing=_subscriptions.get_item(Key={"tenant_id":tid}).get("Item") or {}
        account_id=existing.get("stripe_account_id")
        if not account_id:
            account=sdk.Account.create(type="express",country="US",email=item.customer_email,
                capabilities={"transfers":{"requested":True}},metadata={"tenant_id":tid})
            account_id=account.id
            _subscriptions.update_item(Key={"tenant_id":tid},
                UpdateExpression="SET stripe_account_id=:a, seller_onboarding_status=:s",
                ExpressionAttributeValues={":a":account_id,":s":"onboarding_started"})
        link=sdk.AccountLink.create(account=account_id,
            refresh_url=base+"/?connect=refresh",return_url=base+"/?connect=complete",
            type="account_onboarding")
        return {"onboarding_url":link.url,"expires_at":link.expires_at}
    except Exception as exc:
        raise HTTPException(status_code=502,detail="Stripe Connect onboarding failed: "+type(exc).__name__)

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
    seller_tid = trade.get("seller_tenant_id")
    seller = _subscriptions.get_item(Key={"tenant_id":seller_tid}).get("Item") if seller_tid else None
    connected_account = (seller or {}).get("stripe_account_id")
    if not connected_account or not (seller or {}).get("charges_enabled") or not (seller or {}).get("payouts_enabled"):
        raise HTTPException(status_code=409,detail="Seller must complete Stripe Connect onboarding and enable charges and payouts before checkout.")
    amount_cents = int(Decimal(str(trade["amount_usd"]))*100)
    fee_raw = os.getenv("MARKETPLACE_FEE_PERCENT","").strip()
    if not fee_raw:
        raise HTTPException(status_code=503,detail="MARKETPLACE_FEE_PERCENT must be explicitly configured.")
    try: fee_percent = Decimal(fee_raw)
    except Exception: raise HTTPException(status_code=503,detail="Marketplace fee configuration is invalid.")
    if not fee_percent.is_finite() or fee_percent < 0 or fee_percent > 30:
        raise HTTPException(status_code=503,detail="Marketplace fee configuration is invalid.")
    application_fee = int((Decimal(amount_cents)*fee_percent/Decimal(100)).quantize(Decimal("1"),rounding=ROUND_HALF_UP))
    base = os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/")
    try:
        session = sdk.checkout.Session.create(mode="payment",payment_method_types=["card"],
            line_items=[{"price_data":{"currency":"usd","unit_amount":amount_cents,
            "product_data":{"name":"Sudogemini dataset purchase "+trade["dataset_id"]}},"quantity":1}],
            payment_intent_data={"application_fee_amount":application_fee,
                "transfer_data":{"destination":connected_account},
                "metadata":{"trade_id":trade_id,"tenant_id":tid,"seller_tenant_id":seller_tid}},
            success_url=base+"/?trade="+trade_id+"&payment=success",
            cancel_url=base+"/?trade="+trade_id+"&payment=cancelled",
            metadata={"trade_id":trade_id,"tenant_id":tid,"seller_tenant_id":seller_tid,
                "merkle_root":trade["merkle_root"]},
            idempotency_key="sudogemini-marketplace-"+trade_id)
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
    event_id = event["id"]
    try:
        _stripe_events.put_item(Item={"event_id":event_id,"status":"processing","created_at":int(time.time()),
            "lease_until":int(time.time())+300,"expires_at":int(time.time())+60*60*24*90},
            ConditionExpression="attribute_not_exists(event_id)")
    except Exception:
        # Duplicate deliveries are acknowledged only when the event record already exists.
        existing = _stripe_events.get_item(Key={"event_id":event_id}).get("Item")
        if existing and existing.get("status") == "processed":
            return {"received":True,"duplicate":True,"event_type":event_type}
        if existing and existing.get("status") == "processing":
            now = int(time.time())
            try:
                _stripe_events.update_item(Key={"event_id":event_id},
                    UpdateExpression="SET lease_until=:lease, created_at=:now",
                    ConditionExpression="attribute_not_exists(lease_until) OR lease_until < :now",
                    ExpressionAttributeValues={":lease":now+300,":now":now})
            except Exception:
                raise HTTPException(status_code=503,detail="Stripe event is already being processed; retry shortly.")
        else:
            raise HTTPException(status_code=503,detail="Stripe event idempotency store is unavailable.")
    try:
        metadata = obj.get("metadata") or {}
        if event_type in ("checkout.session.completed","checkout.session.async_payment_succeeded"):
            trade_id, tid = metadata.get("trade_id"), metadata.get("tenant_id")
            if trade_id and tid and obj.get("payment_status") == "paid":
                trade = _trades.get_item(Key={"tenant_id":tid,"record_id":trade_id}).get("Item")
                if trade and trade.get("status") == "approved_pending_payment":
                    _trades.update_item(Key={"tenant_id":tid,"record_id":trade_id},
                        UpdateExpression="SET payment_status=:paid, #s=:status, stripe_payment_intent=:pi",
                        ExpressionAttributeNames={"#s":"status"},
                        ExpressionAttributeValues={":paid":"paid",":status":"paid_pending_fulfillment",
                            ":pi":obj.get("payment_intent","")})
                    seller_tid = metadata.get("seller_tenant_id") or trade.get("seller_tenant_id")
                    if seller_tid:
                        _trades.update_item(Key={"tenant_id":seller_tid,"record_id":"seller#"+trade_id},
                            UpdateExpression="SET payment_status=:paid, #s=:status",
                            ExpressionAttributeNames={"#s":"status"},
                            ExpressionAttributeValues={":paid":"paid",":status":"paid_pending_fulfillment"})
            elif obj.get("mode") == "subscription" and tid:
                _subscriptions.update_item(Key={"tenant_id":tid},
                    UpdateExpression="SET customer_id=:c, subscription_id=:sid, #s=:status, updated_at=:t",
                    ExpressionAttributeNames={"#s":"status"},
                    ExpressionAttributeValues={":c":obj.get("customer",""),":sid":obj.get("subscription",""),
                        ":status":"checkout_completed_pending_webhook",":t":int(time.time())})
        elif event_type == "account.updated":
            tid = metadata.get("tenant_id")
            if tid:
                _subscriptions.update_item(Key={"tenant_id":tid},
                    UpdateExpression="SET charges_enabled=:c, payouts_enabled=:p, seller_onboarding_status=:s, updated_at=:t",
                    ExpressionAttributeValues={":c":bool(obj.get("charges_enabled")),":p":bool(obj.get("payouts_enabled")),
                        ":s":"complete" if obj.get("details_submitted") and obj.get("payouts_enabled") else "incomplete",":t":int(time.time())})
        elif event_type in ("customer.subscription.created","customer.subscription.updated","customer.subscription.deleted"):
            tid = metadata.get("tenant_id")
            if tid:
                _subscriptions.update_item(Key={"tenant_id":tid},
                    UpdateExpression="SET customer_id=:c, subscription_id=:sid, #s=:status, price_ids=:prices, current_period_end=:period, cancel_at_period_end=:cancel, updated_at=:t",
                    ExpressionAttributeNames={"#s":"status"},
                    ExpressionAttributeValues={":c":obj.get("customer",""),":sid":obj.get("id",""),
                        ":status":obj.get("status","unknown"),
                        ":prices":[x.get("price",{}).get("id","") for x in obj.get("items",{}).get("data",[])],
                        ":period":obj.get("current_period_end",0),":cancel":bool(obj.get("cancel_at_period_end")),
                        ":t":int(time.time())})
        elif event_type in ("charge.refunded","charge.dispute.created"):
            trade_id, tid = metadata.get("trade_id"), metadata.get("tenant_id")
            # Charge and dispute event objects do not consistently carry PaymentIntent
            # metadata. Resolve the PaymentIntent metadata before changing an order.
            if not (trade_id and tid):
                payment_intent_id = obj.get("payment_intent")
                if not payment_intent_id and obj.get("charge") and stripe:
                    stripe.api_key = os.getenv("STRIPE_LIVE_SECRET_KEY", "")
                    charge = stripe.Charge.retrieve(obj.get("charge"))
                    payment_intent_id = charge.get("payment_intent")
                if payment_intent_id and stripe:
                    stripe.api_key = os.getenv("STRIPE_LIVE_SECRET_KEY", "")
                    intent = stripe.PaymentIntent.retrieve(payment_intent_id)
                    metadata = intent.get("metadata") or {}
                    trade_id, tid = metadata.get("trade_id"), metadata.get("tenant_id")
            if trade_id and tid:
                state = "refund_review" if event_type == "charge.refunded" else "dispute_review"
                payment_state = "refunded" if event_type == "charge.refunded" else "disputed"
                trade = _trades.get_item(Key={"tenant_id":tid,"record_id":trade_id}).get("Item")
                if trade:
                    _trades.update_item(Key={"tenant_id":tid,"record_id":trade_id},
                        UpdateExpression="SET #s=:s, payment_status=:p",
                        ExpressionAttributeNames={"#s":"status"},
                        ExpressionAttributeValues={":s":state,":p":payment_state})
                    seller_tid=trade.get("seller_tenant_id")
                    if seller_tid:
                        _trades.update_item(Key={"tenant_id":seller_tid,"record_id":"seller#"+trade_id},
                            UpdateExpression="SET #s=:s, payment_status=:p",
                            ExpressionAttributeNames={"#s":"status"},
                            ExpressionAttributeValues={":s":state,":p":payment_state})
        elif event_type in ("invoice.payment_failed","invoice.payment_succeeded"):
            subscription_id = obj.get("subscription")
            if subscription_id:
                matched = _subscriptions.query(IndexName="subscription-id-index",
                    KeyConditionExpression=Key("subscription_id").eq(subscription_id)).get("Items",[])
                for subscription in matched:
                    _subscriptions.update_item(Key={"tenant_id":subscription["tenant_id"]},
                        UpdateExpression="SET last_invoice_status=:s, last_invoice_id=:i, updated_at=:t",
                        ExpressionAttributeValues={":s":"failed" if event_type.endswith("failed") else "paid",
                            ":i":obj.get("id",""),":t":int(time.time())})
        _stripe_events.update_item(Key={"event_id":event_id},
            UpdateExpression="SET #s=:done, processed_at=:at",
            ExpressionAttributeNames={"#s":"status"},
            ExpressionAttributeValues={":done":"processed",":at":int(time.time())})
    except Exception as exc:
        try: _stripe_events.delete_item(Key={"event_id":event_id})
        except Exception: pass
        raise HTTPException(status_code=500,detail="Webhook processing failed; Stripe may retry: "+type(exc).__name__)
    return {"received": True, "event_type": event_type}

@app.post("/api/trades/{trade_id}/download")
def download_trade_dataset(trade_id: str, request: Request):
    buyer_tid=tenant(request)
    trade=_trades.get_item(Key={"tenant_id":buyer_tid,"record_id":trade_id}).get("Item")
    if not trade or trade.get("role")=="seller":
        raise HTTPException(status_code=404,detail="Buyer order not found.")
    if trade.get("status") not in {"paid_pending_fulfillment","fulfilled"} or trade.get("payment_status")!="paid":
        raise HTTPException(status_code=409,detail="Dataset access is available only after verified payment and seller approval.")
    seller_tid=trade.get("seller_tenant_id")
    dataset=_datasets.get_item(Key={"tenant_id":seller_tid,"record_id":trade["dataset_id"]}).get("Item")
    if not dataset or dataset.get("license_status")!="approved" or dataset.get("ingestion_status")!="validated" or not dataset.get("object_key"):
        raise HTTPException(status_code=409,detail="The licensed dataset has not passed ingestion validation.")
    try:
        bucket=os.getenv("INGESTION_BUCKET","")
        if not bucket: raise RuntimeError("INGESTION_BUCKET is not configured")
        url=boto3.client("s3",region_name=AWS_REGION).generate_presigned_url(
            "get_object",Params={"Bucket":bucket,"Key":dataset["object_key"]},ExpiresIn=300)
        if trade.get("status")=="paid_pending_fulfillment":
            _trades.update_item(Key={"tenant_id":buyer_tid,"record_id":trade_id},
                UpdateExpression="SET #s=:fulfilled, fulfilled_at=:at",
                ExpressionAttributeNames={"#s":"status"},
                ExpressionAttributeValues={":fulfilled":"fulfilled",":at":int(time.time())})
            _trades.update_item(Key={"tenant_id":seller_tid,"record_id":"seller#"+trade_id},
                UpdateExpression="SET #s=:fulfilled, fulfilled_at=:at",
                ExpressionAttributeNames={"#s":"status"},
                ExpressionAttributeValues={":fulfilled":"fulfilled",":at":int(time.time())})
        return {"download_url":url,"expires_in":300,"license_summary":"Use only within the license terms accepted at checkout."}
    except Exception as exc:
        raise HTTPException(status_code=503,detail="Dataset delivery is unavailable: "+type(exc).__name__)

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

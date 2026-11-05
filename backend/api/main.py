import hashlib
import json
import os
import time
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

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
            "quantum": {"status": "optional", "backend": "not_configured"},
            "payments": {"status": "disabled_until_configured"},
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


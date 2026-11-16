from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable
from urllib.parse import quote, urlparse
from urllib.request import Request as URLRequest, urlopen

import jwt
import stripe
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import JSON, ForeignKey, Index, Numeric, String, create_engine, func, or_, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from jwt import PyJWKClient

APP_NAME = "Sudogemin"
APP_VERSION = "2.0.0"
APP_ENV = os.getenv("APP_ENV", "production").strip().lower()
DOCS_ENABLED = os.getenv("ENABLE_API_DOCS", "false" if APP_ENV == "production" else "true").lower() == "true"

AGENTS = [
    {"id": "ingestion", "name": "Ingestion Agent", "role": "Validate and register incoming data-source metadata"},
    {"id": "enrichment", "name": "Enrichment Agent", "role": "Normalize metadata and prepare dataset profiles"},
    {"id": "quality", "name": "Data Quality Agent", "role": "Check completeness, freshness, and schema consistency"},
    {"id": "scoping", "name": "Customer Scoping Agent", "role": "Match buyer requirements to published dataset metadata"},
    {"id": "marketplace", "name": "Marketplace Agent", "role": "Prepare listing and quotation workflows"},
    {"id": "governance", "name": "Human Approval Agent", "role": "Enforce independent human review before publication and payment"},
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _database_url() -> str | None:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value and APP_ENV != "production":
        value = "sqlite:///./sudogemin.db"
    if not value:
        return None
    if value.startswith("postgres://"):
        value = "postgresql+psycopg://" + value[len("postgres://"):]
    elif value.startswith("postgresql://"):
        value = "postgresql+psycopg://" + value[len("postgresql://"):]
    return value


class Base(DeclarativeBase):
    pass


class Dataset(Base):
    __tablename__ = "datasets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(160), index=True, nullable=False)
    seller_subject: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(String(2000), nullable=False, default="")
    category: Mapped[str] = mapped_column(String(80), nullable=False, default="general")
    source: Mapped[str] = mapped_column(String(200), nullable=False, default="user-submitted")
    record_count: Mapped[int] = mapped_column(nullable=False, default=0)
    license_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending_review")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending_review")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class Trade(Base):
    __tablename__ = "trades"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    buyer_tenant_id: Mapped[str] = mapped_column(String(160), index=True, nullable=False)
    buyer_subject: Mapped[str] = mapped_column(String(200), nullable=False)
    seller_tenant_id: Mapped[str] = mapped_column(String(160), index=True, nullable=False)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), index=True, nullable=False)
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="usd")
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending_approval")
    payment_status: Mapped[str] = mapped_column(String(32), nullable=False, default="not_started")
    approval_subject: Mapped[str | None] = mapped_column(String(200), nullable=True)
    approved_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    merkle_root: Mapped[str] = mapped_column(String(64), nullable=False)
    stripe_session_id: Mapped[str | None] = mapped_column(String(200), unique=True, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(160), index=True, nullable=False)
    actor_subject: Mapped[str | None] = mapped_column(String(200), nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(36), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class StripeEvent(Base):
    __tablename__ = "stripe_events"
    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    received_at: Mapped[str] = mapped_column(String(40), nullable=False)


class Prospect(Base):
    __tablename__ = "prospects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    domain: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    website: Mapped[str] = mapped_column(String(500), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False, default="US")
    industry: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(String(2000), nullable=False, default="")
    buyer_fit: Mapped[int] = mapped_column(nullable=False, default=0)
    seller_fit: Mapped[int] = mapped_column(nullable=False, default=0)
    fit_score: Mapped[int] = mapped_column(nullable=False, default=0)
    recommended_role: Mapped[str] = mapped_column(String(20), nullable=False, default="buyer")
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    public_contact: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="needs_review")
    first_seen_at: Mapped[str] = mapped_column(String(40), nullable=False)
    last_seen_at: Mapped[str] = mapped_column(String(40), nullable=False)
    seen_count: Mapped[int] = mapped_column(nullable=False, default=1)
    reviewed_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reviewed_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


Index("ix_datasets_marketplace", Dataset.status, Dataset.license_status)
Index("ix_trades_buyer_status", Trade.buyer_tenant_id, Trade.status)
Index("ix_trades_seller_status", Trade.seller_tenant_id, Trade.status)


DB_URL = _database_url()
engine = None
SessionLocal = None
if DB_URL:
    engine_options: dict[str, Any] = {"pool_pre_ping": True}
    if DB_URL.startswith("sqlite"):
        engine_options["connect_args"] = {"check_same_thread": False}
    engine = create_engine(DB_URL, **engine_options)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    if SessionLocal is None:
        raise HTTPException(status_code=503, detail="Persistent database is not configured.")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


_jwks_clients: dict[str, PyJWKClient] = {}


def identity_configured() -> bool:
    return all(os.getenv(key) for key in ("OIDC_ISSUER", "OIDC_AUDIENCE", "OIDC_JWKS_URL"))


def get_current_user(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if not identity_configured():
        raise HTTPException(status_code=503, detail="Identity provider is not configured. Set OIDC_ISSUER, OIDC_AUDIENCE, and OIDC_JWKS_URL.")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="A valid bearer access token is required.")
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="A valid bearer access token is required.")
    try:
        jwks_url = os.environ["OIDC_JWKS_URL"]
        client = _jwks_clients.get(jwks_url)
        if client is None:
            client = PyJWKClient(jwks_url, cache_jwk_set=True, lifespan=300)
            _jwks_clients[jwks_url] = client
        signing_key = client.get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256", "ES256"],
            issuer=os.environ["OIDC_ISSUER"],
            options={"require": ["exp", "iat", "sub", "iss"], "verify_aud": False},
            leeway=30,
        )
        expected_audience = os.environ["OIDC_AUDIENCE"]
        token_audience = claims.get("aud")
        if token_audience:
            audience_values = token_audience if isinstance(token_audience, list) else [token_audience]
            if expected_audience not in audience_values:
                raise HTTPException(status_code=401, detail="Access token audience is invalid.")
        elif claims.get("client_id") != expected_audience:
            # Cognito access tokens use client_id; standard OIDC tokens normally use aud.
            raise HTTPException(status_code=401, detail="Access token audience is invalid.")
        tenant_id = claims.get("tenant_id") or claims.get("custom:tenant_id")
        subject = claims.get("sub")
        if not tenant_id or not subject:
            raise HTTPException(status_code=401, detail="The identity token is missing required tenant claims.")
        roles = claims.get("roles", claims.get("groups", claims.get("cognito:groups", [])))
        if isinstance(roles, str):
            roles = [roles]
        if not isinstance(roles, list):
            roles = []
        return {"subject": str(subject), "tenant_id": str(tenant_id), "roles": [str(role) for role in roles]}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Access token validation failed.") from None


def require_roles(*allowed_roles: str) -> Callable[..., dict[str, Any]]:
    def dependency(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
        roles = set(user.get("roles", []))
        if "platform_admin" not in roles and not roles.intersection(allowed_roles):
            raise HTTPException(status_code=403, detail="Your account is not authorized for this action.")
        return user
    return dependency


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def merkle_root(fields: dict[str, Any]) -> str:
    """Build a deterministic binary Merkle root with domain-separated leaf/node hashes."""
    leaves = [
        hashlib.sha256(b"\x00" + key.encode("utf-8") + b":" + _canonical_json(value).encode("utf-8")).digest()
        for key, value in sorted(fields.items())
    ]
    if not leaves:
        return hashlib.sha256(b"\x00").hexdigest()
    while len(leaves) > 1:
        if len(leaves) % 2:
            leaves.append(leaves[-1])
        leaves = [
            hashlib.sha256(b"\x01" + leaves[index] + leaves[index + 1]).digest()
            for index in range(0, len(leaves), 2)
        ]
    return leaves[0].hex()


def record_audit(
    db: Session,
    tenant_id: str,
    actor: str | None,
    action: str,
    resource_type: str,
    resource_id: str,
    details: dict[str, Any] | None = None,
) -> None:
    db.add(AuditEvent(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        actor_subject=actor,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details or {},
        created_at=utc_now(),
    ))


def dataset_content_hash(item: dict[str, Any]) -> str:
    stable = {key: item.get(key) for key in ("name", "description", "category", "source", "record_count")}
    return sha256_hex(_canonical_json(stable).encode("utf-8"))


def trade_proof_fields(trade: Trade) -> dict[str, Any]:
    return {
        "trade_id": trade.id,
        "buyer_tenant_id": trade.buyer_tenant_id,
        "seller_tenant_id": trade.seller_tenant_id,
        "dataset_id": trade.dataset_id,
        "amount_usd": format(Decimal(trade.amount_usd).quantize(Decimal("0.01")), "f"),
        "currency": trade.currency,
        "created_at": trade.created_at,
    }


def _normalized_domain(website: str) -> tuple[str, str] | None:
    candidate = website.strip()
    if not candidate:
        return None
    if "://" not in candidate:
        candidate = "https://" + candidate
    parsed = urlparse(candidate)
    host = (parsed.hostname or "").lower().strip(".")
    if not host or "." not in host or len(host) > 255:
        return None
    if host.startswith("www."):
        host = host[4:]
    if host in {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com", "linkedin.com", "facebook.com", "instagram.com", "google.com"}:
        return None
    if not re.fullmatch(r"[a-z0-9.-]+", host):
        return None
    clean = f"https://{host}"
    return host, clean


def _gemini_discover_companies(db: Session, max_results: int = 12) -> dict[str, Any]:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    model = os.getenv("GEMINI_PROSPECT_MODEL", "").strip()
    if not api_key or not model:
        raise HTTPException(status_code=503, detail="Autonomous discovery requires GEMINI_API_KEY and GEMINI_PROSPECT_MODEL.")
    industries = [s.strip() for s in os.getenv("PROSPECTING_INDUSTRIES", "market research, logistics, manufacturing, healthcare technology, retail analytics, financial technology, SaaS").split(",") if s.strip()]
    geography = os.getenv("PROSPECTING_GEOGRAPHY", "United States").strip()
    prompt = (
        "Find real, currently operating B2B companies that could plausibly buy or lawfully supply licensed business datasets "
        "for a B2B data marketplace. Research company websites and reliable public sources using web search. "
        f"Target geography: {geography}. Target industries: {', '.join(industries)}. "
        f"Return up to {max_results} distinct companies. Do not invent companies, domains, contact details, revenue, or claims. "
        "Only include a company when the official website/domain can be identified and at least one public source URL supports it. "
        "Do not infer private contact details. Return JSON only with a top-level companies array. "
        "Each company item must have: company_name, website, country (ISO-2), industry, description, buyer_fit (0-100), "
        "seller_fit (0-100), public_contact (optional generic business email only when published), "
        "evidence (array of objects with title and url). Explain the specific public signal supporting buyer/seller fit."
    )
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{quote(model, safe='')}:generateContent?key={quote(api_key, safe='')}"
    request = URLRequest(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=60) as response:
            raw = response.read(2_000_000)
        result = json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=502, detail="The configured Gemini discovery request failed. Check the API key, model, quota, and Search grounding availability.") from None
    candidates = result.get("candidates") or []
    if not candidates:
        raise HTTPException(status_code=502, detail="Gemini returned no discovery candidates.")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    generated = "\n".join(str(p.get("text", "")) for p in parts if isinstance(p, dict))
    try:
        parsed = json.loads(generated)
    except Exception:
        match = re.search(r"\{[\s\S]*\}", generated)
        if not match:
            raise HTTPException(status_code=502, detail="Gemini returned results that were not valid JSON.") from None
        try:
            parsed = json.loads(match.group(0))
        except Exception:
            raise HTTPException(status_code=502, detail="Gemini returned malformed prospect data.") from None
    grounding = (candidates[0].get("groundingMetadata") or {}).get("groundingChunks") or []
    grounded_sources = []
    for chunk in grounding:
        web = chunk.get("web") or {}
        uri = web.get("uri")
        title = web.get("title") or "Public source"
        if isinstance(uri, str) and uri.startswith("https://"):
            grounded_sources.append({"title": str(title)[:300], "url": uri[:1000]})
    companies = parsed.get("companies") if isinstance(parsed, dict) else None
    if not isinstance(companies, list):
        raise HTTPException(status_code=502, detail="Gemini response did not contain a companies array.")
    discovered = 0
    updated = 0
    rejected = 0
    for item in companies[:max_results]:
        if not isinstance(item, dict):
            rejected += 1
            continue
        name = str(item.get("company_name") or "").strip()[:200]
        normalized = _normalized_domain(str(item.get("website") or ""))
        evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
        evidence = [
            {"title": str(e.get("title") or "Public source")[:300], "url": str(e.get("url"))[:1000]}
            for e in evidence if isinstance(e, dict) and str(e.get("url") or "").startswith("https://")
        ][:8]
        evidence = evidence or grounded_sources[:8]
        if not name or not normalized or not evidence:
            rejected += 1
            continue
        domain, website = normalized
        buyer_fit = max(0, min(100, int(item.get("buyer_fit", 0))))
        seller_fit = max(0, min(100, int(item.get("seller_fit", 0))))
        fit_score = max(buyer_fit, seller_fit)
        role = "buyer" if buyer_fit >= seller_fit else "seller"
        if fit_score < int(os.getenv("PROSPECTING_MIN_SCORE", "45")):
            rejected += 1
            continue
        row = db.scalar(select(Prospect).where(Prospect.domain == domain))
        if row:
            row.last_seen_at = utc_now()
            row.seen_count += 1
            if row.status == "needs_review":
                row.company_name = name
                row.industry = str(item.get("industry") or "unknown")[:120]
                row.description = str(item.get("description") or "")[:2000]
                row.buyer_fit = buyer_fit
                row.seller_fit = seller_fit
                row.fit_score = fit_score
                row.recommended_role = role
                row.evidence = evidence
                row.public_contact = str(item.get("public_contact") or "")[:255] or None
            updated += 1
            continue
        db.add(Prospect(
            id=str(uuid.uuid4()),
            company_name=name,
            domain=domain,
            website=website,
            country=str(item.get("country") or "US").upper()[:2],
            industry=str(item.get("industry") or "unknown")[:120],
            description=str(item.get("description") or "")[:2000],
            buyer_fit=buyer_fit,
            seller_fit=seller_fit,
            fit_score=fit_score,
            recommended_role=role,
            evidence=evidence,
            public_contact=str(item.get("public_contact") or "")[:255] or None,
            status="needs_review",
            first_seen_at=utc_now(),
            last_seen_at=utc_now(),
            seen_count=1,
        ))
        discovered += 1
    db.commit()
    return {
        "status": "completed",
        "discovered": discovered,
        "refreshed": updated,
        "rejected_or_unqualified": rejected,
        "sources": len(grounded_sources),
        "model": model,
        "next_step": "Review qualified prospects in the acquisition pipeline. No company account or outreach was created automatically.",
    }


def serialize_dataset(row: Dataset) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "name": row.name,
        "description": row.description,
        "category": row.category,
        "source": row.source,
        "record_count": row.record_count,
        "license_status": row.license_status,
        "status": row.status,
        "content_hash": row.content_hash,
        "created_at": row.created_at,
    }


def serialize_trade(row: Trade) -> dict[str, Any]:
    return {
        "id": row.id,
        "dataset_id": row.dataset_id,
        "buyer_tenant_id": row.buyer_tenant_id,
        "seller_tenant_id": row.seller_tenant_id,
        "amount_usd": format(Decimal(row.amount_usd).quantize(Decimal("0.01")), "f"),
        "currency": row.currency,
        "status": row.status,
        "payment_status": row.payment_status,
        "approved_at": row.approved_at,
        "merkle_root": row.merkle_root,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


class DatasetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=2000)
    category: str = Field(default="general", min_length=1, max_length=80)
    source: str = Field(default="user-submitted", min_length=1, max_length=200)
    record_count: int = Field(default=0, ge=0, le=2_000_000_000)


class DatasetReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str = Field(pattern="^(approved|rejected)$")
    reason: str = Field(min_length=3, max_length=1000)


class MatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirements: list[str] = Field(default_factory=list, max_length=30)


class TradeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_id: str = Field(min_length=36, max_length=36)
    amount_usd: Decimal = Field(gt=0, le=1_000_000, max_digits=12, decimal_places=2)


app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    description="Tenant-isolated B2B data brokerage API with human approvals and cryptographic transaction proofs.",
    docs_url="/docs" if DOCS_ENABLED else None,
    redoc_url="/redoc" if DOCS_ENABLED else None,
    openapi_url="/openapi.json" if DOCS_ENABLED else None,
)


if os.getenv("CORS_ALLOW_ORIGINS"):
    allowed_origins = [origin.strip() for origin in os.getenv("CORS_ALLOW_ORIGINS", "").split(",") if origin.strip()]
    if allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "Stripe-Signature"],
        )


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.on_event("startup")
def initialize_local_database() -> None:
    # Local development only. Production schema changes are applied by Alembic in the container entrypoint.
    if APP_ENV != "production" and engine is not None:
        Base.metadata.create_all(bind=engine)


@app.get("/")
def home():
    return {
        "name": APP_NAME,
        "status": "running",
        "version": APP_VERSION,
        "environment": APP_ENV,
        "docs": "/docs" if DOCS_ENABLED else None,
        "health": "/health",
        "readiness": "/ready",
    }


@app.head("/")
def head_home():
    # Render's default HTTP probe uses HEAD on the root path.
    return Response(status_code=200, headers={"Cache-Control": "no-store"})


@app.get("/health")
def health():
    return {"status": "ok", "service": APP_NAME, "timestamp": int(time.time())}


@app.get("/ready")
def readiness():
    checks = {
        "database_configured": engine is not None,
        "identity_provider_configured": identity_configured(),
    }
    if engine is not None:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            checks["database_reachable"] = True
        except SQLAlchemyError:
            checks["database_reachable"] = False
    else:
        checks["database_reachable"] = False
    ready = all(checks.values())
    payload = {"status": "ready" if ready else "degraded", "checks": checks}
    return JSONResponse(status_code=200 if ready else 503, content=payload)


@app.get("/api/agents")
def agents(_: dict[str, Any] = Depends(get_current_user)):
    return {
        "agents": AGENTS,
        "count": len(AGENTS),
        "mode": "workflow-specifications",
        "execution_enabled": False,
        "note": "Agent responsibilities are defined. Connect and validate an AI provider and worker runtime before enabling autonomous execution.",
    }


@app.get("/auth/config")
def auth_config(request: Request):
    return {
        "enabled": all(os.getenv(k) for k in ("OIDC_AUTHORIZATION_URL", "OIDC_CLIENT_ID")),
        "authorization_url": os.getenv("OIDC_AUTHORIZATION_URL"),
        "client_id": os.getenv("OIDC_CLIENT_ID"),
        "redirect_uri": os.getenv("OIDC_REDIRECT_URI", str(request.base_url).rstrip("/") + "/auth/callback"),
        "scope": os.getenv("OIDC_SCOPE", "openid profile email"),
        "signup_url": os.getenv("OIDC_SIGNUP_URL") or os.getenv("OIDC_AUTHORIZATION_URL"),
    }


@app.get("/api/marketplace/public")
def public_marketplace(db: Session = Depends(get_db)):
    rows = db.scalars(select(Dataset).where(
        Dataset.status == "published", Dataset.license_status == "approved"
    ).order_by(Dataset.created_at.desc()).limit(200)).all()
    items = []
    for row in rows:
        items.append({
            "id": row.id,
            "name": row.name,
            "description": row.description,
            "category": row.category,
            "record_count": row.record_count,
            "price_usd": format(Decimal(row.price_usd), "f") if getattr(row, "price_usd", None) is not None else None,
            "content_hash": row.content_hash,
            "created_at": row.created_at,
        })
    return {"items": items, "count": len(items)}


@app.get("/api/prospecting/prospects")
def prospect_pipeline(
    status: str = Query(default="needs_review", max_length=30),
    limit: int = Query(default=100, ge=1, le=500),
    user: dict[str, Any] = Depends(require_roles("platform_admin", "business_development")),
    db: Session = Depends(get_db),
):
    statement = select(Prospect)
    if status != "all":
        statement = statement.where(Prospect.status == status)
    rows = db.scalars(statement.order_by(Prospect.fit_score.desc(), Prospect.last_seen_at.desc()).limit(limit)).all()
    return {"items": [{
        "id": row.id, "company_name": row.company_name, "domain": row.domain,
        "website": row.website, "country": row.country, "industry": row.industry,
        "description": row.description, "buyer_fit": row.buyer_fit, "seller_fit": row.seller_fit,
        "fit_score": row.fit_score, "recommended_role": row.recommended_role,
        "evidence": row.evidence, "public_contact": row.public_contact,
        "status": row.status, "first_seen_at": row.first_seen_at,
        "last_seen_at": row.last_seen_at, "seen_count": row.seen_count,
    } for row in rows], "count": len(rows)}


@app.post("/internal/prospecting/run")
def run_autonomous_prospecting(
    x_prospecting_token: str | None = Header(default=None, alias="X-Prospecting-Token"),
    db: Session = Depends(get_db),
):
    expected = os.getenv("PROSPECTING_JOB_TOKEN", "")
    if not expected or not x_prospecting_token or not hmac.compare_digest(expected, x_prospecting_token):
        raise HTTPException(status_code=401, detail="Invalid prospecting job token.")
    return _gemini_discover_companies(db, max_results=max(1, min(int(os.getenv("PROSPECTING_BATCH_SIZE", "12")), 30)))


@app.post("/api/prospecting/prospects/{prospect_id}/review")
def review_prospect(
    prospect_id: str,
    decision: str = Query(pattern="^(approve|dismiss)$"),
    user: dict[str, Any] = Depends(require_roles("platform_admin", "business_development")),
    db: Session = Depends(get_db),
):
    row = db.get(Prospect, prospect_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Prospect not found.")
    if row.status != "needs_review":
        raise HTTPException(status_code=409, detail="Only prospects awaiting review can be changed.")
    row.status = "approved_for_outreach" if decision == "approve" else "dismissed"
    row.reviewed_by = user["subject"]
    row.reviewed_at = utc_now()
    record_audit(db, "platform", user["subject"], "prospect." + decision, "prospect", prospect_id, {"domain": row.domain})
    db.commit()
    return {"id": row.id, "domain": row.domain, "status": row.status}


@app.get("/api/integrations/status")
def integrations_status(_: dict[str, Any] = Depends(get_current_user)):
    key = os.getenv("STRIPE_SECRET_KEY", "")
    live_key = key.startswith("sk_live_")
    stripe_enabled = os.getenv("STRIPE_PAYMENTS_ENABLED", "false").lower() == "true"
    stripe_live_allowed = os.getenv("STRIPE_LIVE_ENABLED", "false").lower() == "true"
    return {
        "database": "configured" if engine is not None else "not_configured",
        "identity": "configured" if identity_configured() else "not_configured",
        "stripe": {
            "status": "enabled" if stripe_enabled and key and (not live_key or stripe_live_allowed) else "disabled_or_incomplete",
            "mode": "live" if live_key else ("test" if key.startswith("sk_test_") else "off"),
            "webhook_configured": bool(os.getenv("STRIPE_WEBHOOK_SECRET")),
        },
        "ai_execution": "not_configured",
        "cuda_q_execution": "not_configured",
    }


@app.get("/api/dashboard")
def dashboard(user: dict[str, Any] = Depends(get_current_user), db: Session = Depends(get_db)):
    dataset_count = db.scalar(select(func.count()).select_from(Dataset).where(Dataset.tenant_id == user["tenant_id"])) or 0
    trade_count = db.scalar(select(func.count()).select_from(Trade).where(
        or_(Trade.buyer_tenant_id == user["tenant_id"], Trade.seller_tenant_id == user["tenant_id"])
    )) or 0
    published_count = db.scalar(select(func.count()).select_from(Dataset).where(
        Dataset.status == "published", Dataset.license_status == "approved"
    )) or 0
    return {
        "platform": APP_NAME,
        "version": APP_VERSION,
        "tenant_id": user["tenant_id"],
        "agents_defined": len(AGENTS),
        "datasets_in_tenant": dataset_count,
        "trades_visible_to_tenant": trade_count,
        "published_marketplace_listings": published_count,
        "approval_required": True,
        "persistent_storage": True,
    }


@app.post("/api/datasets", status_code=201)
def create_dataset(
    item: DatasetIn,
    user: dict[str, Any] = Depends(require_roles("seller", "tenant_admin")),
    db: Session = Depends(get_db),
):
    dataset_id = str(uuid.uuid4())
    stable = item.model_dump()
    row = Dataset(
        id=dataset_id,
        tenant_id=user["tenant_id"],
        seller_subject=user["subject"],
        name=item.name,
        description=item.description,
        category=item.category,
        source=item.source,
        record_count=item.record_count,
        license_status="pending_review",
        status="pending_review",
        content_hash=dataset_content_hash(stable),
        created_at=utc_now(),
    )
    db.add(row)
    record_audit(db, user["tenant_id"], user["subject"], "dataset.created", "dataset", dataset_id)
    db.commit()
    db.refresh(row)
    return serialize_dataset(row)


@app.get("/api/datasets")
def list_datasets(user: dict[str, Any] = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Dataset).where(Dataset.tenant_id == user["tenant_id"]).order_by(Dataset.created_at.desc())).all()
    return {"items": [serialize_dataset(row) for row in rows], "count": len(rows)}


@app.post("/api/datasets/{dataset_id}/review")
def review_dataset(
    dataset_id: str,
    item: DatasetReviewIn,
    user: dict[str, Any] = Depends(require_roles("compliance", "tenant_admin")),
    db: Session = Depends(get_db),
):
    row = db.scalar(select(Dataset).where(Dataset.id == dataset_id, Dataset.tenant_id == user["tenant_id"]))
    if row is None:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    if row.status != "pending_review":
        raise HTTPException(status_code=409, detail="Only pending datasets can be reviewed.")
    row.license_status = item.decision
    if item.decision == "rejected":
        row.status = "rejected"
    record_audit(
        db, user["tenant_id"], user["subject"], "dataset.reviewed", "dataset", dataset_id,
        {"decision": item.decision, "reason": item.reason},
    )
    db.commit()
    db.refresh(row)
    return serialize_dataset(row)


@app.post("/api/datasets/{dataset_id}/publish")
def publish_dataset(
    dataset_id: str,
    user: dict[str, Any] = Depends(require_roles("publisher", "tenant_admin")),
    db: Session = Depends(get_db),
):
    row = db.scalar(select(Dataset).where(Dataset.id == dataset_id, Dataset.tenant_id == user["tenant_id"]))
    if row is None:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    if row.license_status != "approved" or row.status != "pending_review":
        raise HTTPException(status_code=409, detail="A compliance-approved pending dataset is required before publication.")
    row.status = "published"
    record_audit(db, user["tenant_id"], user["subject"], "dataset.published", "dataset", dataset_id)
    db.commit()
    db.refresh(row)
    return serialize_dataset(row)


@app.get("/api/marketplace/listings")
def marketplace_listings(
    _: dict[str, Any] = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.scalars(select(Dataset).where(
        Dataset.status == "published", Dataset.license_status == "approved"
    ).order_by(Dataset.created_at.desc()).limit(500)).all()
    return {
        "items": [{
            "id": row.id,
            "seller_tenant_id": row.tenant_id,
            "name": row.name,
            "description": row.description,
            "category": row.category,
            "record_count": row.record_count,
            "content_hash": row.content_hash,
            "created_at": row.created_at,
        } for row in rows],
        "count": len(rows),
    }


@app.post("/api/match")
def match_buyer(
    item: MatchIn,
    _: dict[str, Any] = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    terms = {term.strip().lower() for term in item.requirements if term.strip()}
    rows = db.scalars(select(Dataset).where(
        Dataset.status == "published", Dataset.license_status == "approved"
    ).limit(500)).all()
    results = []
    for row in rows:
        searchable = " ".join((row.name, row.description, row.category)).lower()
        hits = sorted(term for term in terms if term in searchable)
        score = len(hits) / max(1, len(terms))
        results.append({
            "dataset": {
                "id": row.id, "name": row.name, "description": row.description,
                "category": row.category, "seller_tenant_id": row.tenant_id,
            },
            "match_score": round(score, 4),
            "matched_terms": hits,
        })
    results.sort(key=lambda entry: entry["match_score"], reverse=True)
    return {"matches": results}


@app.post("/api/trades", status_code=201)
def create_trade(
    item: TradeIn,
    user: dict[str, Any] = Depends(require_roles("buyer", "tenant_admin")),
    db: Session = Depends(get_db),
):
    dataset = db.scalar(select(Dataset).where(
        Dataset.id == item.dataset_id,
        Dataset.status == "published",
        Dataset.license_status == "approved",
    ))
    if dataset is None:
        raise HTTPException(status_code=404, detail="Published marketplace listing not found.")
    amount = Decimal(item.amount_usd).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if amount <= 0:
        raise HTTPException(status_code=422, detail="Trade amount must be greater than zero.")
    trade_id = str(uuid.uuid4())
    created_at = utc_now()
    proof = {
        "trade_id": trade_id,
        "buyer_tenant_id": user["tenant_id"],
        "seller_tenant_id": dataset.tenant_id,
        "dataset_id": dataset.id,
        "amount_usd": format(amount, "f"),
        "currency": "usd",
        "created_at": created_at,
    }
    row = Trade(
        id=trade_id,
        buyer_tenant_id=user["tenant_id"],
        buyer_subject=user["subject"],
        seller_tenant_id=dataset.tenant_id,
        dataset_id=dataset.id,
        amount_usd=amount,
        currency="usd",
        status="pending_approval",
        payment_status="not_started",
        merkle_root=merkle_root(proof),
        created_at=created_at,
        updated_at=created_at,
    )
    db.add(row)
    record_audit(db, user["tenant_id"], user["subject"], "trade.created", "trade", trade_id, {"dataset_id": dataset.id})
    db.commit()
    db.refresh(row)
    return serialize_trade(row)


@app.post("/api/trades/{trade_id}/approve")
def approve_trade(
    trade_id: str,
    user: dict[str, Any] = Depends(require_roles("trade_approver", "tenant_admin")),
    db: Session = Depends(get_db),
):
    row = db.scalar(select(Trade).where(
        Trade.id == trade_id, Trade.buyer_tenant_id == user["tenant_id"]
    ))
    if row is None:
        raise HTTPException(status_code=404, detail="Trade not found.")
    if row.status != "pending_approval":
        raise HTTPException(status_code=409, detail="Trade is not awaiting approval.")
    if row.buyer_subject == user["subject"]:
        raise HTTPException(status_code=403, detail="The trade creator cannot approve their own trade.")
    row.status = "approved_pending_payment"
    row.approval_subject = user["subject"]
    row.approved_at = utc_now()
    row.updated_at = utc_now()
    record_audit(db, user["tenant_id"], user["subject"], "trade.approved", "trade", trade_id)
    db.commit()
    db.refresh(row)
    return serialize_trade(row)


def stripe_is_enabled() -> bool:
    secret = os.getenv("STRIPE_SECRET_KEY", "")
    enabled = os.getenv("STRIPE_PAYMENTS_ENABLED", "false").lower() == "true"
    if not enabled or not secret.startswith(("sk_test_", "sk_live_")):
        return False
    if secret.startswith("sk_live_") and os.getenv("STRIPE_LIVE_ENABLED", "false").lower() != "true":
        return False
    return True


@app.post("/api/trades/{trade_id}/checkout")
def create_trade_checkout(
    trade_id: str,
    user: dict[str, Any] = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.scalar(select(Trade).where(
        Trade.id == trade_id, Trade.buyer_tenant_id == user["tenant_id"]
    ))
    if row is None:
        raise HTTPException(status_code=404, detail="Trade not found.")
    if row.status == "checkout_created" and row.stripe_session_id:
        if not stripe_is_enabled():
            raise HTTPException(status_code=503, detail="Stripe payments are disabled or not fully configured.")
        stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
        try:
            existing_session = stripe.checkout.Session.retrieve(row.stripe_session_id)
        except Exception:
            raise HTTPException(status_code=502, detail="The existing Stripe Checkout session could not be retrieved.") from None
        if existing_session.status == "open":
            return {"checkout_url": existing_session.url, "session_id": existing_session.id, "trade_id": trade_id}
        raise HTTPException(status_code=409, detail="This Checkout session is no longer open. Contact support before retrying payment.")
    if row.status != "approved_pending_payment":
        raise HTTPException(status_code=409, detail="An independently approved trade is required before checkout.")
    if not stripe_is_enabled():
        raise HTTPException(status_code=503, detail="Stripe payments are disabled or not fully configured.")
    if not os.getenv("STRIPE_WEBHOOK_SECRET"):
        raise HTTPException(status_code=503, detail="Stripe webhook verification is not configured.")
    base_url = os.getenv("APP_BASE_URL", "").rstrip("/")
    if not base_url.startswith("https://"):
        raise HTTPException(status_code=503, detail="APP_BASE_URL must be configured as the HTTPS customer return URL.")
    if not os.getenv("STRIPE_SECRET_KEY"):
        raise HTTPException(status_code=503, detail="Stripe secret key is missing.")
    dataset = db.get(Dataset, row.dataset_id)
    if dataset is None:
        raise HTTPException(status_code=409, detail="The dataset listing no longer exists.")
    stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
    amount_cents = int((Decimal(row.amount_usd) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "unit_amount": amount_cents,
                    "product_data": {"name": "B2B dataset purchase", "description": dataset.name[:500]},
                },
                "quantity": 1,
            }],
            success_url=base_url + "/billing/success?trade_id=" + row.id,
            cancel_url=base_url + "/billing/cancel?trade_id=" + row.id,
            client_reference_id=row.id,
            metadata={
                "trade_id": row.id,
                "buyer_tenant_id": row.buyer_tenant_id,
                "merkle_root": row.merkle_root,
            },
            idempotency_key="sudogemin-trade-" + row.id,
        )
    except Exception:
        raise HTTPException(status_code=502, detail="Stripe could not create checkout. Review the Stripe account and configuration.") from None
    row.stripe_session_id = session.id
    row.status = "checkout_created"
    row.payment_status = "checkout_created"
    row.updated_at = utc_now()
    record_audit(db, user["tenant_id"], user["subject"], "trade.checkout_created", "trade", trade_id, {"stripe_session_id": session.id})
    db.commit()
    return {"checkout_url": session.url, "session_id": session.id, "trade_id": trade_id}


@app.post("/api/billing/webhook")
async def stripe_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    if not secret:
        raise HTTPException(status_code=503, detail="Stripe webhook verification is not configured.")
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, signature, secret)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid Stripe webhook signature or payload.") from None

    event_id = str(event["id"])
    event_type = str(event["type"])
    existing = db.get(StripeEvent, event_id)
    if existing is not None:
        return {"received": True, "duplicate": True}

    if event_type in {
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
        "checkout.session.async_payment_failed",
        "checkout.session.expired",
    }:
        obj = event["data"]["object"]
        metadata = obj.get("metadata") or {}
        trade_id = metadata.get("trade_id")
        row = db.get(Trade, trade_id) if trade_id else None
        if row is None:
            raise HTTPException(status_code=400, detail="Webhook does not reference a known trade.")
        if (
            metadata.get("buyer_tenant_id") != row.buyer_tenant_id
            or metadata.get("merkle_root") != row.merkle_root
            or obj.get("id") != row.stripe_session_id
        ):
            raise HTTPException(status_code=400, detail="Webhook metadata does not match the recorded trade.")
        expected_cents = int((Decimal(row.amount_usd) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        if obj.get("amount_total") is not None and int(obj["amount_total"]) != expected_cents:
            raise HTTPException(status_code=400, detail="Webhook amount does not match the approved trade amount.")
        if obj.get("currency") and obj["currency"].lower() != row.currency:
            raise HTTPException(status_code=400, detail="Webhook currency does not match the approved trade.")
        if event_type in {"checkout.session.completed", "checkout.session.async_payment_succeeded"}:
            if obj.get("payment_status") != "paid" and event_type == "checkout.session.completed":
                raise HTTPException(status_code=409, detail="Checkout is not marked paid by Stripe.")
            row.payment_status = "paid"
            row.status = "paid"
            row.updated_at = utc_now()
            record_audit(db, row.buyer_tenant_id, None, "trade.payment_confirmed", "trade", row.id, {"stripe_event_id": event_id})
        elif event_type == "checkout.session.async_payment_failed":
            row.payment_status = "failed"
            row.status = "payment_failed"
            row.updated_at = utc_now()
            record_audit(db, row.buyer_tenant_id, None, "trade.payment_failed", "trade", row.id, {"stripe_event_id": event_id})
        elif event_type == "checkout.session.expired" and row.payment_status != "paid":
            row.payment_status = "expired"
            row.status = "checkout_expired"
            row.updated_at = utc_now()
            record_audit(db, row.buyer_tenant_id, None, "trade.checkout_expired", "trade", row.id, {"stripe_event_id": event_id})

    db.add(StripeEvent(id=event_id, event_type=event_type, received_at=utc_now()))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if db.get(StripeEvent, event_id) is not None:
            return {"received": True, "duplicate": True}
        raise HTTPException(status_code=409, detail="Webhook event could not be committed safely.") from None
    return {"received": True, "event_type": event_type}


@app.get("/api/audit")
def list_audit_events(
    limit: int = Query(default=100, ge=1, le=500),
    user: dict[str, Any] = Depends(require_roles("audit_reader", "tenant_admin")),
    db: Session = Depends(get_db),
):
    rows = db.scalars(select(AuditEvent).where(
        AuditEvent.tenant_id == user["tenant_id"]
    ).order_by(AuditEvent.created_at.desc()).limit(limit)).all()
    return {
        "items": [{
            "id": row.id,
            "actor_subject": row.actor_subject,
            "action": row.action,
            "resource_type": row.resource_type,
            "resource_id": row.resource_id,
            "details": row.details,
            "created_at": row.created_at,
        } for row in rows],
        "count": len(rows),
    }


@app.get("/api/trades")
def list_trades(user: dict[str, Any] = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Trade).where(
        or_(Trade.buyer_tenant_id == user["tenant_id"], Trade.seller_tenant_id == user["tenant_id"])
    ).order_by(Trade.created_at.desc()).limit(500)).all()
    return {"items": [serialize_trade(row) for row in rows], "count": len(rows)}


@app.get("/api/verify/{trade_id}")
def verify_trade(
    trade_id: str,
    user: dict[str, Any] = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.scalar(select(Trade).where(
        Trade.id == trade_id,
        or_(Trade.buyer_tenant_id == user["tenant_id"], Trade.seller_tenant_id == user["tenant_id"]),
    ))
    if row is None:
        raise HTTPException(status_code=404, detail="Trade not found.")
    computed = merkle_root(trade_proof_fields(row))
    return {
        "trade_id": row.id,
        "valid": computed == row.merkle_root,
        "merkle_root": row.merkle_root,
        "algorithm": "SHA-256 binary Merkle tree",
        "verified_at": utc_now(),
    }

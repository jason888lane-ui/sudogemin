# Sudogemin

Sudogemin is a tenant-isolated B2B data brokerage platform. The repository includes a FastAPI service and a customer-portal foundation. The portal covers core customer workflows, but a production-complete data brokerage and executing autonomous AI worker fleet still require the integrations and controls listed below.

## Production architecture

- FastAPI API service, packaged as a Docker image.\n- Static customer portal in `frontend/`, served by the Cloudflare Worker when its assets binding is configured.
- PostgreSQL-backed datasets, trades, audit events, and Stripe webhook idempotency records.
- OIDC bearer-token verification using a configured issuer, audience, and JWKS endpoint.
- Tenant-scoped access for private datasets and trades; only approved and published listings appear in the marketplace.
- Independent human approval before a trade can create a Stripe Checkout session.
- Signed Stripe webhook verification, amount/currency/tenant/session checks, and idempotent event recording.
- SHA-256 Merkle roots for immutable trade proof fields.
- Alembic migrations, CI checks, and health/readiness endpoints.

## Local development

Use Python 3.12 or newer.

```bash
cd backend/api
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
APP_ENV=development uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```

Development mode uses a local SQLite database at `backend/api/sudogemin.db` if DATABASE_URL is not set. The API still requires a real OIDC configuration for protected routes; do not add a production bypass or hard-coded demo credentials.

## Required production configuration

Set these values in the Render service's Environment page. Never commit secret values.

| Variable | Required | Purpose |
|---|---|---|
| `APP_ENV` | Yes | Set to `production` |
| `DATABASE_URL` | Yes | Persistent PostgreSQL connection string |
| `OIDC_ISSUER` | Yes | Exact issuer URL from your identity provider |
| `OIDC_AUDIENCE` | Yes | API audience configured in that provider |
| `OIDC_JWKS_URL` | Yes | HTTPS JWKS endpoint for JWT signature verification |
| `CORS_ALLOW_ORIGINS` | When browser UI is hosted | Comma-separated exact HTTPS frontend origins |
| `APP_BASE_URL` | For Checkout | HTTPS customer-facing frontend URL for success/cancel redirects |
| `STRIPE_SECRET_KEY` | For payments | Stripe secret key; store as a Render secret |
| `STRIPE_WEBHOOK_SECRET` | For payments | Stripe endpoint signing secret; store as a Render secret |
| `STRIPE_PAYMENTS_ENABLED` | For payments | Explicitly set `true` only after test-mode validation |
| `STRIPE_LIVE_ENABLED` | Live payments only | Explicitly set `true` only after separate live-mode approval |

Live Stripe secret keys are rejected unless `STRIPE_LIVE_ENABLED=true`. Payments remain disabled by default. Do not use publishable keys in the backend.

The OIDC access token must contain a stable `sub` and a tenant claim named `tenant_id` or `custom:tenant_id`. It must also contain role strings in `roles` or `groups`. Supported roles include `seller`, `buyer`, `compliance`, `publisher`, `trade_approver`, `tenant_admin`, and `platform_admin`.

## Customer portal\n\nThe browser application lives in `frontend/`. It uses OIDC Authorization Code + PKCE and the existing API routes for marketplace listings, dataset metadata registration, compliance review/publication, trade requests, independent approval, Stripe Checkout handoff, and Merkle proof verification. Configure the public OIDC client values in `frontend/config.js`; never place client secrets or backend credentials in browser files. See [`frontend/README.md`](frontend/README.md) for local serving and provider setup.\n\nThe Cloudflare Worker serves the static portal and proxies `/api/*`, `/health`, and `/ready` to the FastAPI backend. Set the Worker `BACKEND_URL` secret to the backend HTTPS origin. The Worker forwards the user's bearer token so FastAPI can verify it.\n\n## API flow

1. A seller with the `seller` role submits dataset metadata to `POST /api/datasets`. New records are always pending compliance review.
2. A `compliance` reviewer approves or rejects the dataset using `POST /api/datasets/{id}/review`.
3. A `publisher` publishes only compliance-approved data using `POST /api/datasets/{id}/publish`.
4. An authenticated buyer browses `GET /api/marketplace/listings` and submits a trade request to `POST /api/trades`.
5. A separate `trade_approver` approves the trade with `POST /api/trades/{id}/approve`. The trade creator cannot approve their own request.
6. Only an approved trade can create a Stripe Checkout session. The API marks a trade paid only after a valid signed Stripe event passes all trade/amount/currency checks.

Useful endpoints: `/health` (liveness), `/ready` (database and identity readiness), `/api/dashboard`, `/api/agents`, `/api/datasets`, `/api/trades`, and `/api/verify/{trade_id}`.

## Database migrations

The container runs `alembic upgrade head` before starting Uvicorn whenever DATABASE_URL is configured. For manual development migrations:

```bash
cd backend/api
alembic upgrade head
```

## Honest capability boundaries

The six agent roles are workflow specifications, not autonomous agents currently executing LLM tasks. The portal displays this limitation rather than claiming workers are running. The matcher is metadata keyword matching, not an AI model. CUDA-Q is not installed or enabled in this service. Raw data-file ingestion, scanning/validation, secure delivery, seller payout reconciliation, subscription billing, and a durable autonomous worker runtime still require implementation and provider configuration; the API does not claim these are active.

## Before accepting production traffic

- Provision a managed PostgreSQL database with backups and recovery testing.
- Configure an OIDC provider and create/test the role and tenant claims.
- Validate browser CORS against the actual frontend domain.
- Configure Stripe test mode and webhook delivery; test duplicate, replayed, wrong-amount, and wrong-tenant events.
- Configure and deploy the customer portal with HTTPS, OIDC redirect origins, API CORS, and the Cloudflare `BACKEND_URL` secret.\n- Implement a controlled raw-file ingestion, validation, storage, and secure delivery system.\n- Implement and validate a durable AI worker runtime with scoped tools, persistent state, retries/idempotency, audit trails, emergency stop, and human approval gates.
- Configure rate limiting, alerting, log retention, backup monitoring, and a documented incident process.
- Run a security review and end-to-end tests with separate buyer, seller, compliance, publisher, and approver tenants.

The API fails closed when PostgreSQL or OIDC is not configured. This is intentional: a deployed container is not the same thing as a production-ready marketplace.

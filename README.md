# Sudogemin AI

**Sudogemin** is an enterprise B2B data intelligence and brokerage platform in development.

- **Platform:** Sudogemin Nexus
- **Owner:** Jason Lane Davidson Jr
- **Primary cloud region:** AWS US East (Ohio), `us-east-2`
- **AI layer:** Amazon Bedrock Runtime (Converse API), subject to account model access and quotas
- **API:** FastAPI

## Current repository

The current API is a development scaffold. It includes six workflow definitions, dataset and trade endpoints, a SHA-256 integrity digest, optional Stripe checkout routes, and an optional CUDA-Q experiment.

It is **not production-ready yet**. In particular, the current dataset and trade stores are process-local memory, API routes do not yet enforce user authentication or tenant isolation, and payment state requires a production-grade webhook and reconciliation lifecycle. Do not use it to store customer data or process live payments until those controls are implemented and tested.

## Six workflow agents

1. **Ingestion** — source registration, provenance, licensing evidence, and ingestion orchestration.
2. **Enrichment** — normalization and metadata enrichment.
3. **Data Quality** — schema, freshness, completeness, and quality scoring.
4. **Customer Scoping** — map buyer requirements to permitted dataset metadata.
5. **Marketplace** — catalog, listing, quote, and entitlement workflows.
6. **Governance** — approval gates, audit trails, policy enforcement, and release controls.

These are currently workflow definitions, not six independently deployed autonomous agents.

## Local development

From `backend/api`:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

Health check: `GET /health`  
OpenAPI docs: `/docs`

## Configuration

Use environment variables or a managed secret store. Never commit credentials.

- `AWS_REGION=us-east-2`
- `BEDROCK_MODEL_ID` — set to an inference profile enabled for this account
- `APP_BASE_URL` — the real public HTTPS application URL
- `DATABASE_URL` — managed PostgreSQL connection string once persistence is implemented
- `STRIPE_LIVE_ENABLED=false` until billing, webhook verification, idempotency, and reconciliation tests pass
- `CUDA_Q_ENABLED=false` unless the optional CUDA-Q runtime and supported execution host are installed and validated

## Production plan

See [Production hardening and AWS architecture](docs/production-hardening.md) for the implementation sequence, security gates, AWS target architecture, and acceptance criteria.

## Data governance

Only ingest data with documented rights and a lawful basis. Preserve source provenance, license terms, permitted purposes, retention rules, and deletion/opt-out obligations. Do not ingest or resell sensitive personal data without a documented legal basis and appropriate controls.

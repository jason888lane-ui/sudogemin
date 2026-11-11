# Sudogemini AI

**Sudogemini** is an enterprise B2B data intelligence and brokerage platform in development.

- **Platform:** Sudogemini Nexus
- **Owner:** Jason Lane Davidson Jr
- **Primary cloud region:** AWS US East (Ohio), `us-east-2`
- **AI layer:** Amazon Bedrock Runtime (Converse API), subject to account model access and quotas
- **API:** FastAPI

## Current repository

The current staging API includes Cognito JWT authentication, tenant-scoped DynamoDB dataset/trade metadata, a static sign-up/sign-in dashboard, six governed workflow definitions, a SHA-256 integrity digest, optional Stripe checkout scaffolding, and an optional CUDA-Q CPU-simulator experiment.

It is **not production-ready yet**. Dataset registration stores metadata only and forces submissions into pending review; it does not ingest source records. The six agents are workflow definitions, not independent autonomous model workers. Trade creation is disabled, Stripe live billing is disabled, and enterprise shared-tenant membership, MFA, recovery, subscription reconciliation, and production operations controls still need completion. Do not use it to process live payments or claim data is licensed/ingested until those controls are implemented and tested.

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
Dashboard: `/`  
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

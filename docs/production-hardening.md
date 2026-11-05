# Sudogemini production hardening and AWS architecture

## Executive status

The repository is a FastAPI prototype, not a production B2B marketplace. Keep the existing service running only as a development/test scaffold until the release gates below are complete.

### Verified code facts

- Six agent roles are declared in `backend/api/main.py`; they are workflow descriptions, not independently deployed autonomous agents.
- Dataset and trade records live in Python dictionaries. A process restart loses them, and multiple service instances will not share state.
- The API currently has no authentication, authorization, or tenant isolation.
- Dataset registration accepts a caller-supplied source and license status but does not verify either.
- Trade creation requires a boolean `human_approved` supplied in the request. A caller-controlled boolean is not a valid approval workflow.
- Merkle-style hashes are generated, but the current helper is a SHA-256 digest of a seeded payload, not a full Merkle tree with inclusion proofs or an externally anchored immutable ledger.
- Stripe checkout endpoints exist, but payment processing must not be considered production-ready until verified webhook events, idempotency, subscription lifecycle handling, reconciliation, and authorization are implemented.
- CUDA-Q is optional. The existing qpp target is a CPU simulator; it is not NVIDIA GPU execution or a demonstrated quantum advantage.
- The default checkout host references an old Render hostname. Production must set `APP_BASE_URL` explicitly.

## Target AWS architecture

Use a staged architecture; do not create every service before the application boundaries and data model are stable.

1. **Edge and identity**
   - Route 53 + ACM for DNS and TLS.
   - CloudFront and AWS WAF for public edge protection where appropriate.
   - Amazon Cognito for customer identity, MFA, and token issuance.
   - API Gateway or an ALB in front of the application.
2. **Application**
   - FastAPI on ECS Fargate for a continuously running API, or Lambda only if request patterns and package/runtime constraints fit.
   - Separate worker service for long-running ingestion/enrichment jobs.
   - Amazon SQS for durable job queues and dead-letter queues.
3. **Data**
   - Amazon S3 for raw, normalized, and curated data zones with versioning, encryption, lifecycle rules, and access logging.
   - Amazon Aurora PostgreSQL for tenant, dataset, license, catalog, order, approval, and billing metadata.
   - DynamoDB only for well-defined high-scale key/value workloads, not as a substitute for a relational transaction model.
   - Glue Data Catalog and Athena for governed analytics when required.
4. **AI**
   - Amazon Bedrock Runtime Converse API for model calls.
   - Use model IDs/inference profiles that are explicitly enabled for the account and region.
   - Treat model output as untrusted. Validate all structured output before it can trigger tools or change records.
   - Keep agent actions read-only by default. Require policy checks and human approval for publication, licensing decisions, data export, and financial actions.
5. **Security and operations**
   - IAM task roles with least privilege; no static AWS access keys in the app.
   - KMS encryption for S3, databases, queues, and logs where supported.
   - Secrets Manager for Stripe and other application secrets if permitted by account policy.
   - CloudWatch metrics/logs/alarms, CloudTrail, backups, and tested restore procedures.
   - Separate dev, staging, and production environments/accounts where feasible.

## Six-agent orchestration contract

| Agent | Allowed responsibilities | Must not do autonomously |
|---|---|---|
| Ingestion | Register approved sources; fetch only through approved connectors; record provenance | Bypass access controls, site terms, or source licenses |
| Enrichment | Normalize, deduplicate, classify permitted metadata | Infer or expose sensitive attributes |
| Data Quality | Validate schemas, freshness, completeness, and anomalies | Silently rewrite source facts |
| Customer Scoping | Match buyer requirements against permitted catalog metadata | Reveal another tenant's data or private source records |
| Marketplace | Prepare listings, quotes, and entitlement checks | Publish unapproved listings or release raw data |
| Governance | Enforce policy, approvals, audit, retention, and deletion | Approve its own high-risk actions |

Every agent task should carry: `tenant_id`, `actor_id`, `request_id`, `dataset_id` where applicable, source/license references, policy version, and an immutable audit event. The API—not the language model—must enforce authorization.

## Required data model

Minimum relational entities:

- `tenants`, `users`, `tenant_memberships`, `roles`
- `data_sources`, `source_licenses`, `source_evidence`
- `datasets`, `dataset_versions`, `dataset_fields`, `quality_reports`
- `marketplace_listings`, `buyer_requirements`, `matches`
- `approval_requests`, `approval_decisions`, `audit_events`
- `orders`, `payments`, `subscriptions`, `webhook_events`
- `data_entitlements`, `exports`, `deletion_requests`

Every tenant-owned row must be scoped by tenant ID and enforced in the database/application layer. Add automated cross-tenant access tests before enabling customer accounts.

## Release gates (in order)

### Gate 1 — API and application correctness
- Add automated tests for every route, validation boundary, and error response.
- Add structured logging with request IDs and redact credentials and sensitive payloads.
- Set explicit CORS origins, trusted proxy behavior, request size limits, and rate limits.
- Add readiness/liveness endpoints and graceful shutdown behavior.

### Gate 2 — Identity and tenant isolation
- Integrate Cognito JWT validation.
- Derive tenant identity from validated claims and membership records, never from an untrusted request body alone.
- Add role-based authorization for ingestion, review, publication, export, billing, and administration.
- Enforce tenant-scoped database access and test for IDOR/cross-tenant leaks.

### Gate 3 — Durable data and provenance
- Replace in-memory dictionaries with Aurora PostgreSQL transactions.
- Store large data objects in S3; keep metadata and entitlements in PostgreSQL.
- Implement source evidence, license terms, allowed purposes, retention, deletion, and opt-out workflows.
- Implement real Merkle trees with canonical serialization, inclusion proofs, versioning, and signed/externally anchored roots if tamper evidence is required.

### Gate 4 — Bedrock
- Verify model use-case onboarding, account quotas, and inference permissions.
- Use a configured inference profile via `BEDROCK_MODEL_ID`; do not hard-code a model ID.
- Set explicit output token limits and adaptive retries.
- Add prompt/version tracking, output schemas, evaluations, safety controls, and cost budgets.
- Keep all agent tools behind deterministic server-side authorization.

### Gate 5 — Billing
- Keep live billing disabled until test-mode coverage passes.
- Verify Stripe webhook signatures against the raw request body.
- Persist event IDs and process events idempotently.
- Handle subscription created/updated/deleted, invoice paid/failed, checkout completion, refunds, and disputes as applicable.
- Reconcile Stripe state with internal entitlements; never grant access based only on a browser redirect.
- Never allow the caller to self-assert approval or payment completion.

### Gate 6 — Production deployment
- Build immutable container images and scan dependencies/images.
- Deploy to staging first; run migrations as a controlled release step.
- Add alarms for error rate, latency, queue age, failed jobs, database saturation, and Bedrock throttling.
- Test backup restoration, rollback, incident response, and data deletion.
- Promote only after the acceptance checklist passes.

## Bedrock account test result (2026-11-05)

- AWS identity was successfully verified in account `821460119057`, region `us-east-2`.
- The `us.anthropic.claude-sonnet-4-6` inference profile was present, but invocation returned a provider onboarding error: Anthropic use-case details have not been submitted for this account.
- A test invocation of `us.amazon.nova-lite-v1:0` returned a Bedrock throttling error indicating the account's daily token quota was exhausted.
- Therefore, model availability in the profile list is not proof that inference is currently usable. Do not claim that Bedrock inference is live until an actual successful test call is recorded.

## Brand and naming rules

- Product/brand spelling: **Sudogemini**.
- Platform name: **Sudogemini Nexus**.
- Avoid introducing the misspelling `Sudogeminii` in customer-facing names.
- Do not rename existing AWS resources blindly. Resource identifiers may be referenced by policies, deployments, or data; migrate them deliberately.

## Definition of done

A release is production-ready only when identity, tenant isolation, durable storage, source licensing/provenance, approvals, billing reconciliation, observability, recovery, and security tests all pass. A running health endpoint alone is not a production readiness signal.

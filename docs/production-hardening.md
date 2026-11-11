# Sudogemini production hardening and AWS architecture

## Current implementation status

The staging application now has:
- Cognito JWT verification using the configured user pool, app client, issuer, and JWKS.
- Tenant-scoped DynamoDB dataset and trade queries.
- An isolated default tenant ID derived from the verified Cognito `sub` claim when no administrator-assigned `custom:tenant_id` claim exists. Clients cannot choose tenant IDs in request bodies.
- A static Cognito sign-up/sign-in dashboard served by FastAPI.
- Dataset metadata registration with a server-enforced `pending_review` license state.
- A six-role workflow catalogue and integration-status endpoints.
- Stripe checkout and signed webhook scaffolding, with live billing disabled.
- An optional CUDA-Q package and CPU-simulator smoke test.

The dashboard and API are a staging MVP, not a fully operational enterprise data brokerage. Dataset registration stores metadata only; it does not fetch or ingest source records. The six agent entries are workflow responsibilities, not six independent autonomous model workers. Trading remains deliberately disabled. No paid customer access or payment entitlement should be enabled yet.

## Verified AWS staging topology

- Region: `us-east-2`.
- ECS Fargate cluster: `sudogemini-staging`.
- ECS service: `sudogemini-api`, desired/running count 1.
- Container port: 8000; health check: `/health`.
- ECR repository: `sudogemini-api`; immutable tags and scan-on-push enabled.
- Application Load Balancer: `sudogemini-public`; target group `sudogemini-api-tg`.
- DynamoDB: `sudogemini-staging-datasets` and `sudogemini-staging-trades`, partitioned by `tenant_id` and sorted by `record_id`, on-demand billing.
- Cognito User Pool: `us-east-2_yeNn9tWY7`; app client `5kl6i9mong804ft96sp5c4fdm1`.
- The task definition is configured with the Cognito pool/client IDs and the two DynamoDB table names.
- The application container was healthy at last inspection. On 2026-11-11, CodeBuild build 10 produced ECR image `staging-10` (about 93 MB) and ECS task definition revision 3 was deployed. The new Fargate task reached `RUNNING` and `HEALTHY`; the ALB target group reported it healthy. The container serves the static dashboard at `/` and passed CI tests.

The existing API Gateway/Lambda data-exchange service is a separate, narrower service. It is not the FastAPI application served by ECS and must not be mistaken for the complete app.

## Current blockers to public access

- The ALB listener on port 80 currently returns HTTP 503 with the message `TLS setup pending`.
- No HTTPS listener has been installed yet.
- A new ACM certificate has been requested for `sudogemini.com`, `www.sudogemini.com`, and `app.sudogemini.com`.
- DNS validation CNAMEs have been written to the Route 53 hosted zone, but ACM still reports `PENDING_VALIDATION`. A public DNS query returned NXDOMAIN for `sudogemini.com` and `app.sudogemini.com`; a Verisign .com RDAP lookup returned HTTP 404 for `sudogemini.com`. The domain therefore appears not to be currently registered/active in the .com registry. The Route 53 hosted zone is only a DNS configuration and does not register or reserve the domain. Confirm the exact registered domain with the registrar, restore/renew it if expired or on hold, or register the desired domain before continuing.
- The hosted zone's assigned name servers are:
  - `ns-931.awsdns-52.net`
  - `ns-1963.awsdns-53.co.uk`
  - `ns-491.awsdns-61.com`
  - `ns-1094.awsdns-08.org`
- The domain registrar's name-server settings must delegate the domain to these Route 53 name servers. The AWS account's Route 53 Domains API is unavailable under its current Free Tier restriction, so registrar delegation cannot be changed from this AWS session.
- The Route 53 zone has no final apex, `www`, or `app` alias records yet. Do not add them until the certificate is issued and the HTTPS listener is working.

## Required next deployment sequence

1. Wait for ACM to issue the multi-domain certificate after public DNS delegation is correct.
2. Create an HTTPS 443 ALB listener using the issued certificate and forwarding to `sudogemini-api-tg`.
3. Change HTTP 80 to redirect to HTTPS 443.
4. Add Route 53 alias records for `sudogemini.com`, `www.sudogemini.com`, and `app.sudogemini.com` to the ALB.
5. Set `APP_BASE_URL=https://app.sudogemini.com` in the ECS task definition and redeploy.
6. Verify browser sign-up, email confirmation, login, dashboard, tenant-scoped dataset registration, and sign-out.
7. Configure MFA and account recovery, organization membership, and administrator-controlled tenant assignment before onboarding multiple users into shared enterprise tenants.
8. Configure Secrets Manager and Stripe test-mode products/webhooks. Keep `STRIPE_LIVE_ENABLED=false` until subscription state, event idempotency, entitlement updates, refunds, and reconciliation are implemented and tested.
9. Implement durable ingestion jobs and licensed source connectors. A dataset record is not evidence that data was ingested or licensed.
10. Implement actual agent execution with Bedrock or another approved model provider, structured output validation, tool authorization, task queues, cost budgets, and human approval gates.
11. Add monitoring alarms, backups/restore tests, rate limits, WAF rules, dependency/image scanning, and incident procedures.

## Identity and tenancy

The current API validates Cognito ID tokens and derives the tenant from the verified `custom:tenant_id` claim when one is present, otherwise from the signed `sub` claim. This gives each user an isolated workspace by default. Shared enterprise organizations require a server-controlled membership model and administrator-managed tenant assignment; never allow users to set their own tenant ID.

## Data governance

Only ingest data with documented rights and a lawful basis. Preserve source provenance, license terms, permitted purposes, retention rules, and deletion/opt-out obligations. Do not ingest or resell sensitive personal data without a documented legal basis and appropriate controls.

Dataset submissions must remain `pending_review` until a human reviewer verifies source permissions and licensing evidence. Do not make license approval a caller-controlled field. Trade creation is disabled until approval, payment, delivery, and reconciliation are implemented.

## Six-agent orchestration contract

| Agent | Allowed responsibilities | Must not do autonomously |
|---|---|---|
| Ingestion | Register approved sources; fetch only through approved connectors; record provenance | Bypass access controls, site terms, or source licenses |
| Enrichment | Normalize, deduplicate, classify permitted metadata | Infer or expose sensitive attributes |
| Data Quality | Validate schemas, freshness, completeness, and anomalies | Silently rewrite source facts |
| Customer Scoping | Match buyer requirements against permitted catalog metadata | Reveal another tenant's data or private source records |
| Marketplace | Prepare listings, quotes, and entitlement checks | Publish unapproved listings or release raw data |
| Governance | Enforce policy, approvals, audit, retention, and deletion | Approve its own high-risk actions |

Every agent task should carry `tenant_id`, `actor_id`, `request_id`, dataset/source/license references, policy version, and an audit event. The API—not the language model—must enforce authorization.

## Billing controls

Live billing is disabled. Keep it disabled until:
- Stripe webhook signatures are verified against the raw body.
- Event IDs are persisted and processed idempotently.
- Subscription created/updated/deleted, invoice paid/failed, checkout completion, refunds, and disputes are handled as applicable.
- Stripe state is reconciled with internal tenant entitlements.
- Browser redirects never grant paid access by themselves.
- Secrets are stored in AWS Secrets Manager, never Git.

## AI and CUDA-Q controls

The six agents are currently role definitions. Model execution must be added behind a configurable provider interface, with validated structured output, prompt/version tracking, evaluations, safety controls, rate limits, and cost budgets. Keep tools read-only by default and require human approval for publication, licensing decisions, data export, and financial actions.

CUDA-Q is optional. The configured `qpp-cpu` target is a CPU simulator, not NVIDIA GPU acceleration and not evidence of quantum advantage. Cryptographic hashing and Merkle verification must continue to use established classical algorithms.

## Bedrock account test history

- The `us.anthropic.claude-sonnet-4-6` inference profile was present, but invocation returned a provider onboarding error: Anthropic use-case details had not been submitted for this account.
- A test invocation of `us.amazon.nova-lite-v1:0` returned a Bedrock throttling error indicating the account's daily token quota was exhausted.
- Model listing is not proof that inference is usable. Do not claim live Bedrock execution until a successful call is recorded.

## Definition of done

A release is production-ready only when identity, tenant isolation, durable storage, source licensing/provenance, approvals, billing reconciliation, agent execution controls, observability, recovery, and security tests all pass. A healthy container or a successful `/health` response alone is not a production readiness signal.

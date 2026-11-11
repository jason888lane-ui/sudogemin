# Sudogemini marketplace platform implementation

This branch adds the first code implementation for ingestion, queued AI workflow execution, marketplace listings/orders, Stripe Connect, and payment event handling. It is a deployable code foundation, not a live marketplace until the AWS supporting resources and worker service are configured.

## API workflows

All endpoints under `/api` require a valid Cognito ID token, except the signed Stripe webhook.

### Tenant identity boundary

The API currently maps each verified Cognito `sub` to an isolated tenant. It deliberately ignores `custom:tenant_id` and other client-provided organization identifiers. This prevents an editable profile attribute from becoming an authorization boundary. The trade-off is that multiple users cannot yet share one organization workspace. Organization membership must be added through a server-controlled membership directory with administrator-only membership changes, audit history, and cross-tenant authorization tests. Do not re-enable custom-claim-based organization sharing until the user-pool schema and every app client's `WriteAttributes` permissions have been verified. AWS documents that app clients can have write access to custom attributes by default when `WriteAttributes` is not explicitly restricted; see [Cognito attribute permissions](https://docs.aws.amazon.com/cognito/latest/developerguide/user-pool-settings-attributes.html).


| Endpoint | Purpose | Gate |
|---|---|---|
| `POST /api/agents/run` | Queue one of six governed agent tasks | SQS + Bedrock worker configured |
| `GET /api/agents/jobs/{job_id}` | Read the tenant-owned job result | Tenant-scoped DynamoDB key |
| `POST /api/datasets` | Submit dataset metadata and rights evidence | Always pending review |
| `POST /api/datasets/{id}/approve` | Approve dataset rights | Subject must be in `PLATFORM_ADMIN_SUBS` |
| `POST /api/ingestion/{id}/upload-url` | Request 10-minute private S3 upload URL | Dataset approved; optional subscription gate |
| `POST /api/ingestion/{id}/complete` | Queue file validation | Tenant-specific S3 prefix |
| `POST /api/marketplace/listings` | Submit a price and license summary | Dataset approved |
| `POST /api/marketplace/listings/{id}/approve` | Publish reviewed listing | Platform administrator |
| `GET /api/marketplace/listings` | Browse approved listings | Marketplace table provisioned |
| `POST /api/trades` | Create buyer order from approved listing | Buyer explicitly accepts license terms |
| `POST /api/trades/{id}/seller-approve` | Seller accepts the order | Authenticated seller tenant |
| `POST /api/trades/{id}/checkout` | Start a destination-charge checkout | Seller Connect transfers and payouts enabled |
| `POST /api/trades/{id}/download` | Issue short-lived signed download URL | Paid, approved, validated dataset |

## Agent model

The SQS worker uses the Bedrock Converse API and the configured `BEDROCK_MODEL_ID`. It returns a constrained JSON plan and always sets `requires_human_approval=true`. The model has no tools and cannot approve licensing, publish marketplace listings, export data, or initiate payments. API and worker IAM permissions are the actual authorization boundary.

The six roles are ingestion, quality, customer scoping, marketplace, sales, and governance. These are one worker runtime dispatching role-specific prompts, not six separately trained foundation models.

## Ingestion contract

1. A tenant submits dataset metadata, a license-evidence URL, and a permitted-use statement.
2. A platform administrator approves the rights record.
3. The API issues a short-lived presigned S3 PUT URL restricted to CSV, JSON, or NDJSON.
4. The tenant uploads directly to its own S3 prefix.
5. The API queues a job. The worker reads the object, calculates SHA-256, checks file structure and schema, counts records, and stores provenance metadata.
6. Files with common sensitive-data columns such as email, phone, SSN, passport, or bank account fields are rejected for separate legal/security review.

After validation, the worker promotes the object from the expiring `incoming/` prefix to an immutable content-addressed `datasets/` key and updates dataset metadata to that retained key. The initial worker has a 100 MiB processing limit and holds the complete file in memory during validation. It receives one SQS job at a time with a one-hour visibility timeout and records terminal failure state when a job reaches its retry limit. This is an MVP constraint, not the final architecture for billion-record scale. Large-scale ingestion needs streaming/multipart processing, partitioned columnar storage, Glue/EMR or equivalent distributed processing, malware scanning, content inspection, retention enforcement, and a documented deletion workflow.

The header-based sensitive-data check is only a guardrail; it is not a legal determination or a complete PII detector. Do not upload regulated or sensitive personal data until a dedicated review and control program is implemented.

## Billing and seller payouts

- Stripe starts in test mode only when `STRIPE_MODE=test`, `STRIPE_TEST_ENABLED=true`, a valid `sk_test_` key, a test price ID, and a test webhook secret are configured. Live mode separately requires `STRIPE_MODE=live`, `STRIPE_LIVE_ENABLED=true`, a valid `sk_live_` key, a live price ID, and a live webhook secret. Keep live mode disabled until test transactions and owner approval are complete.
- Stripe Connect onboarding is disabled unless `STRIPE_CONNECT_ENABLED=true`. It creates Express connected accounts and onboarding links only when the authenticated seller calls the endpoint.
- Checkout uses a destination charge and an explicitly configured `MARKETPLACE_FEE_PERCENT` (0–30; no default fee is assumed) only after Stripe reports that seller transfers capability and payouts are enabled.
- Signed test/live webhook events use a mode-specific signing secret and are recorded by event ID before processing. Subscription changes, invoice status, Connect account readiness, paid orders, refunds, and disputes are handled.
- Paid entitlements can be enforced with `REQUIRE_ACTIVE_SUBSCRIPTION=true`. Leave false until pricing, trial policy, grace periods, cancellation, tax, refund, and access-revocation policy are approved.
- A browser redirect is never treated as payment proof. Fulfillment checks the persisted webhook-updated payment state and returns a five-minute signed download URL.

This implementation does not create a Stripe product/price, connect a seller, activate live billing, or charge a customer. No live Stripe API mutation was performed while preparing this branch.

## AWS resources and runtime configuration

Review and deploy `infra/marketplace-platform.yaml` after confirming account, region, task role, existing datasets/trades table ARNs, and expected AWS costs. Supply `DatasetsTableArn` and `TradesTableArn` as stack parameters. It provisions a private encrypted/versioned S3 bucket, SQS queue + DLQ, and DynamoDB tables for marketplace listings, agent jobs, Stripe events, and subscriptions. It attaches a runtime policy to the supplied ECS task role. These resources are not deployed by this PR.

Configure these environment values from CloudFormation outputs:
- `INGESTION_BUCKET`
- `PLATFORM_JOBS_QUEUE_URL`
- `AGENT_JOBS_TABLE`
- `MARKETPLACE_TABLE`
- `STRIPE_EVENTS_TABLE`
- `SUBSCRIPTIONS_TABLE`
- Existing `DATASETS_TABLE` and `TRADES_TABLE`
- `PLATFORM_ADMIN_SUBS` — comma-separated Cognito subject IDs for platform administrators
- `BEDROCK_MODEL_ID` — inference profile/model that has actually passed a Converse smoke test
- `APP_BASE_URL` — verified HTTPS URL
- Test mode: `STRIPE_TEST_SECRET_KEY`, `STRIPE_TEST_WEBHOOK_SECRET`, `STRIPE_TEST_PRICE_ID`, and `STRIPE_TEST_ENABLED=true` only for sandbox verification
- Live mode: `STRIPE_LIVE_SECRET_KEY`, `STRIPE_LIVE_WEBHOOK_SECRET`, `STRIPE_PRICE_ID`, and `STRIPE_LIVE_ENABLED=false` until the owner approves release
- `STRIPE_MODE=test` by default; `STRIPE_CONNECT_ENABLED=false`, `REQUIRE_ACTIVE_SUBSCRIPTION=false`, and an explicit `MARKETPLACE_FEE_PERCENT` remain required controls

Run the worker as a separate ECS service using the same image and command `python worker.py`. The API image includes the worker module, but this PR does not create a new Fargate worker service or change the existing ECS service.

## Release gates still outstanding

- CI must pass on the latest PR commit.
- Deploy supporting resources and worker service only after cost review.
- Set the platform administrator Cognito subject and test approval controls.
- Verify Bedrock Converse with the configured model in this AWS account; previous account tests had provider onboarding/quota failures.
- Run Stripe test-mode checkout, Connect onboarding, destination charge, subscription lifecycle, webhook retry/idempotency, refunds, disputes, and entitlement revocation tests before live mode.
- Add a durable seller payout/reconciliation process and periodic Stripe-to-DynamoDB reconciliation.
- Add order fulfillment/download audit events, customer deletion/export/retention workflows, security review, alarms, backups, restore tests, rate limiting, and WAF.
- Confirm domain registration/delegation, ACM issuance, HTTPS listener, and public browser login before inviting customers.

The order evidence field is a deterministic SHA-256 integrity commitment over one canonical order payload; it is not a multi-record Merkle tree or a Merkle inclusion proof. A successful CI run and healthy ECS task do not, by themselves, mean Sudogemini is ready to transact with customers.

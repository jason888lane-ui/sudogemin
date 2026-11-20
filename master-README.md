# Sudogemin Master Engine

The root file named \`master\` is the standalone FastAPI master service. This engine is separate from the existing \`backend/api\` service so it can be reviewed and integrated without silently replacing the current production API.

## Setup
Use Python 3.11+ (3.12 recommended).

\`\`\`bash
python -m venv .venv
source .venv/bin/activate
pip install -r master-requirements.txt
export APP_ENV=development
export DATABASE_URL=sqlite:///./sudogemin.db
export OIDC_ISSUER='https://YOUR-ISSUER/'
export OIDC_AUDIENCE='YOUR-API-AUDIENCE'
export OIDC_JWKS_URL='https://YOUR-ISSUER/.well-known/jwks.json'
uvicorn master:app --host 0.0.0.0 --port 8000
\`\`\`

Because the root entrypoint is named \`master\` without a \`.py\` suffix, for direct execution use \`python master\`. For Uvicorn import, rename it to \`master.py\` in your deployment image or load it with an explicit module loader.

## Configure
- \`OPENAI_API_KEY\`, optional \`OPENAI_MODEL\`: enables six model-backed agent roles.
- \`STRIPE_SECRET_KEY\`, \`STRIPE_PRICE_ID\`, \`STRIPE_WEBHOOK_SECRET\`: Stripe subscription Checkout and signed webhooks. Start in Stripe test mode.
- \`OIDC_ISSUER\`, \`OIDC_AUDIENCE\`, \`OIDC_JWKS_URL\`: verified access tokens must contain a tenant claim and role claims.
- \`DATABASE_URL\`: PostgreSQL in production. SQLite is for local development only.
- \`CUDAQ_TARGET\`: optional CUDA-Q backend, default \`qpp-cpu\`.

## Roles
The OIDC token role claim must contain the required role: \`seller\`, \`compliance\`, \`buyer\`, or \`tenant_admin\`. Marketplace publication and sales outreach remain human-controlled. No agent has arbitrary shell, SQL, network-fetch, or email-sending tools.

## Security and mathematical limits
- Merkle leaves use SHA-256(0x00 || canonical JSON); internal nodes use SHA-256(0x01 || left || right); the empty tree has a distinct domain value. Under SHA-256 collision-resistance assumptions, the root commits to the ordered records.
- This does not prove that records are true, complete, current, lawfully collected, or licensed.
- The source evidence hash detects changes to the submitted evidence; it does not authenticate the source or establish legal rights.
- Stripe webhooks are verified against raw request bytes and event IDs are unique for duplicate suppression.
- PostgreSQL RLS uses transaction-local \`app.tenant_id\` derived from verified OIDC claims. Tenant-scoped ORM predicates are also required.
- CUDA-Q is used only for an optional Bell-state sampling demonstration. It is not used for cryptography and no quantum speedup is claimed.
- Agent outputs are probabilistic proposals, not proofs or trusted policy decisions.

## Production checklist
1. Apply \`master-tenant-rls.sql\` as a migration owner.
2. Use a separate runtime role with no table ownership, SUPERUSER, or BYPASSRLS.
3. Add independent review and legal verification for each data license.
4. Add webhook reconciliation, transactional outbox, retries, monitoring and a dunning policy.
5. Run concurrency, property-based, adversarial, integration and penetration tests.
6. Model tenant/billing state machines in TLA+ or Alloy and commission an independent security review before production cutover.

The code is an engineering baseline, not a formal proof or production certification.

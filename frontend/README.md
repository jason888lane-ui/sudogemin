# Sudogemin customer portal

This is a dependency-free browser application for the existing FastAPI API. It adds a customer workspace for marketplace discovery, seller dataset metadata registration, compliance review/publication, buyer trade requests, independent approvals, Stripe Checkout handoff, and SHA-256 Merkle proof verification.

## Authentication setup

1. Configure an OIDC provider (for example, Amazon Cognito or another standards-compatible provider) with an SPA/public client.
2. Enable Authorization Code flow with PKCE (S256). Do not create or expose a client secret for this browser app.
3. Configure the provider to issue an access token with a stable `sub`, a tenant claim (`tenant_id` or `custom:tenant_id`), and role strings in `roles`, `groups`, or `cognito:groups`.
4. Set `oidcIssuer`, `oidcClientId`, `oidcRedirectUri`, and any provider-specific `oidcAudience` in `config.js`.
5. Add the exact frontend origin to the API's `CORS_ALLOW_ORIGINS` when the API is hosted separately. Set `apiBaseUrl` to the HTTPS API/proxy base URL.
6. Ensure the OIDC provider permits browser CORS for its discovery and token endpoints.

Only public OIDC configuration belongs in `config.js`. Never put client secrets, Stripe keys, API keys, or database credentials in browser files.

## Serve locally

From the repository root:

```bash
python -m http.server 4173 --directory frontend
```

Open `http://localhost:4173`. For local HTTP testing, the OIDC provider must explicitly allow the localhost redirect URI. The production deployment must use HTTPS.

## API integration

The portal uses existing API routes: `/api/dashboard`, `/api/datasets`, `/api/datasets/{id}/review`, `/api/datasets/{id}/publish`, `/api/marketplace/listings`, `/api/trades`, `/api/trades/{id}/approve`, `/api/trades/{id}/checkout`, `/api/agents`, and `/api/verify/{trade_id}`.

The portal intentionally does not upload or deliver raw data files. The backend currently registers dataset metadata only. It also does not claim that the six listed AI worker roles are executing: the current API reports `execution_enabled: false`. A production worker fleet still requires a configured AI provider, durable job queue, isolated worker runtime, scoped tool permissions, persistent job state, retries, audit events, emergency stop, and human approval gates.

## Important production boundaries

- The browser UI is not a substitute for backend authorization; the API remains authoritative.
- Seller review and publication controls are shown only for matching token roles, and the API independently checks those roles.
- Trade creators cannot approve their own trades; checkout remains unavailable until independent approval and verified Stripe configuration.
- Payment state is authoritative only after a verified Stripe webhook. A browser redirect is not proof of payment.
- Do not enable live Stripe payments until test-mode end-to-end, webhook replay, amount mismatch, and tenant-isolation tests pass.
- The frontend has not been deployed by this change. Configure hosting, HTTPS, OIDC redirect origins, API CORS, and provider secrets before customer access.

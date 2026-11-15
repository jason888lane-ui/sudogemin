# Sudogemini Stripe integration foundation

Includes hosted Checkout for recurring SaaS plans and one-time dataset purchases, Billing Portal, Connect seller onboarding, a feature-flagged destination-charge path, enterprise invoices, automatic tax configuration, and signed webhook verification with event deduplication.

## Safety and production requirements

- No live products, prices, payments, connected accounts, or payouts were created by this code change.
- Create and validate Stripe products/prices in test mode first; populate STRIPE_PRICE_* with test Price IDs.
- Store Stripe keys and JWT_SECRET in AWS Secrets Manager. Never commit .env.
- JWT must be issued by the trusted auth service and contain sub, tenant_id, and exp. Never trust a client-provided tenant header.
- Replace startup create_all with Alembic migrations before production.
- Webhook currently records event IDs and associates Checkout customers. Implement durable entitlement processing for invoice.paid, invoice.payment_failed, customer.subscription.updated and customer.subscription.deleted before granting/revoking access.
- Connect marketplace payments are disabled by default. Confirm Stripe marketplace eligibility, data licensing/content policies, seller countries, tax, refunds, disputes, and merchant-of-record responsibilities before enabling. Connect balances are not escrow.
- Tax collection requires correct tax codes, customer location collection, registrations, and Stripe Tax settings. Confirm jurisdictional obligations before enabling.
- Review current Connect account configuration and Stripe API requirements before onboarding real sellers.

## Local development

Create a virtual environment, install requirements, copy .env.example to .env, replace placeholders with Stripe test credentials and test Price IDs, then run:
uvicorn app.main:app --reload

## API routes

GET /health
POST /v1/billing/checkout-session
POST /v1/billing/portal-session
POST /v1/marketplace/connect/onboarding-link
POST /v1/marketplace/checkout-session (disabled until compliance review)
POST /v1/invoices
POST /v1/stripe/webhook

All billing routes require a trusted bearer JWT with a tenant claim.

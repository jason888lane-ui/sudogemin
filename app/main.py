import stripe
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.auth import current_tenant
from app.core import get_settings
from app.db import Base, engine, get_db
from app.models import ProcessedStripeEvent, TenantStripeCustomer

s = get_settings()
stripe.api_key = s.stripe_secret_key
app = FastAPI(title=s.app_name, version="0.1.0")

@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)

class CheckoutRequest(BaseModel):
    plan: str = Field(pattern="^(starter|professional|business|enterprise|dataset)$")
    quantity: int = Field(default=1, ge=1, le=100)

class InvoiceRequest(BaseModel):
    plan: str = Field(pattern="^(business|enterprise)$")
    days_until_due: int = Field(default=30, ge=1, le=90)

def get_or_create_customer(db: Session, tenant_id: str):
    row = db.query(TenantStripeCustomer).filter_by(tenant_id=tenant_id).first()
    if row:
        return row.stripe_customer_id
    customer = stripe.Customer.create(metadata={"sudogemini_tenant_id": tenant_id},
        idempotency_key=f"sudogemini-customer-{tenant_id}")
    db.add(TenantStripeCustomer(tenant_id=tenant_id, stripe_customer_id=customer.id))
    db.commit()
    return customer.id

@app.get("/health")
def health():
    return {"status": "ok", "service": "sudogemini-stripe"}

@app.post("/v1/billing/checkout-session")
def checkout(body: CheckoutRequest, tenant_id: str = Depends(current_tenant), db: Session = Depends(get_db)):
    price_id = s.stripe_price_dataset if body.plan == "dataset" else s.subscription_prices[body.plan]
    mode = "payment" if body.plan == "dataset" else "subscription"
    customer_id = get_or_create_customer(db, tenant_id)
    params = {"mode": mode, "customer": customer_id, "line_items": [{"price": price_id, "quantity": body.quantity}],
        "success_url": s.stripe_success_url, "cancel_url": s.stripe_cancel_url,
        "automatic_tax": {"enabled": s.stripe_automatic_tax}, "allow_promotion_codes": True,
        "metadata": {"sudogemini_tenant_id": tenant_id, "plan": body.plan}}
    if mode == "subscription":
        params["subscription_data"] = {"metadata": {"sudogemini_tenant_id": tenant_id, "plan": body.plan}}
    try:
        session = stripe.checkout.Session.create(**params, idempotency_key=f"checkout-{tenant_id}-{body.plan}")
        return {"checkout_url": session.url, "session_id": session.id}
    except stripe.StripeError as exc:
        raise HTTPException(status_code=502, detail="Stripe Checkout unavailable") from exc

@app.post("/v1/billing/portal-session")
def portal(tenant_id: str = Depends(current_tenant), db: Session = Depends(get_db)):
    row = db.query(TenantStripeCustomer).filter_by(tenant_id=tenant_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="No billing customer for this tenant")
    try:
        session = stripe.billing_portal.Session.create(customer=row.stripe_customer_id, return_url=s.stripe_portal_return_url)
        return {"portal_url": session.url}
    except stripe.StripeError as exc:
        raise HTTPException(status_code=502, detail="Billing portal unavailable") from exc

@app.post("/v1/marketplace/connect/onboarding-link")
def connect_onboarding(tenant_id: str = Depends(current_tenant)):
    try:
        account = stripe.Account.create(controller={"fees": {"payer": "application"},
            "losses": {"payments": "application"}, "stripe_dashboard": {"type": "express"}},
            capabilities={"transfers": {"requested": True}},
            metadata={"sudogemini_tenant_id": tenant_id}, idempotency_key=f"connect-{tenant_id}")
        link = stripe.AccountLink.create(account=account.id, refresh_url=s.connect_refresh_url,
            return_url=s.connect_return_url, type="account_onboarding")
        return {"account_id": account.id, "onboarding_url": link.url}
    except stripe.StripeError as exc:
        raise HTTPException(status_code=502, detail="Connect onboarding unavailable") from exc

@app.post("/v1/marketplace/checkout-session")
def marketplace_checkout(body: CheckoutRequest, seller_account_id: str,
                         tenant_id: str = Depends(current_tenant)):
    if not s.connect_marketplace_enabled:
        raise HTTPException(status_code=403, detail="Marketplace payments are disabled pending compliance review")
    if body.plan != "dataset":
        raise HTTPException(status_code=400, detail="Marketplace checkout only supports dataset purchases")
    try:
        price = stripe.Price.retrieve(s.stripe_price_dataset)
        amount = int(price.unit_amount or 0) * body.quantity
        fee = amount * s.connect_commission_bps // 10000
        session = stripe.checkout.Session.create(mode="payment",
            line_items=[{"price": s.stripe_price_dataset, "quantity": body.quantity}],
            success_url=s.stripe_success_url, cancel_url=s.stripe_cancel_url,
            automatic_tax={"enabled": s.stripe_automatic_tax},
            payment_intent_data={"application_fee_amount": fee,
                "transfer_data": {"destination": seller_account_id},
                "metadata": {"sudogemini_tenant_id": tenant_id}},
            metadata={"sudogemini_tenant_id": tenant_id, "seller_account_id": seller_account_id})
        return {"checkout_url": session.url, "session_id": session.id}
    except stripe.StripeError as exc:
        raise HTTPException(status_code=502, detail="Marketplace checkout unavailable") from exc

@app.post("/v1/invoices")
def enterprise_invoice(body: InvoiceRequest, tenant_id: str = Depends(current_tenant), db: Session = Depends(get_db)):
    customer_id = get_or_create_customer(db, tenant_id)
    try:
        invoice = stripe.Invoice.create(customer=customer_id, collection_method="send_invoice",
            days_until_due=body.days_until_due, automatic_tax={"enabled": s.stripe_automatic_tax},
            metadata={"sudogemini_tenant_id": tenant_id, "plan": body.plan},
            idempotency_key=f"invoice-{tenant_id}-{body.plan}")
        stripe.InvoiceItem.create(customer=customer_id, invoice=invoice.id,
            pricing={"price": s.subscription_prices[body.plan]}, description=f"Sudogemini {body.plan.title()}")
        final_invoice = stripe.Invoice.finalize_invoice(invoice.id)
        sent = stripe.Invoice.send_invoice(final_invoice.id)
        return {"invoice_id": sent.id, "hosted_invoice_url": sent.hosted_invoice_url}
    except stripe.StripeError as exc:
        raise HTTPException(status_code=502, detail="Invoice creation unavailable") from exc

@app.post("/v1/stripe/webhook")
async def webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.body()
    signature = request.headers.get("stripe-signature")
    if not signature:
        raise HTTPException(status_code=400, detail="Missing Stripe signature")
    try:
        event = stripe.Webhook.construct_event(payload, signature, s.stripe_webhook_secret)
    except (ValueError, stripe.SignatureVerificationError) as exc:
        raise HTTPException(status_code=400, detail="Invalid webhook signature") from exc
    if db.query(ProcessedStripeEvent).filter_by(stripe_event_id=event.id).first():
        return JSONResponse({"received": True, "duplicate": True})
    try:
        db.add(ProcessedStripeEvent(stripe_event_id=event.id, event_type=event.type))
        db.commit()
    except IntegrityError:
        db.rollback()
        return JSONResponse({"received": True, "duplicate": True})
    if event.type == "checkout.session.completed":
        obj = event.data.object
        tenant_id = (obj.get("metadata") or {}).get("sudogemini_tenant_id")
        customer_id = obj.get("customer")
        if tenant_id and customer_id and not db.query(TenantStripeCustomer).filter_by(tenant_id=tenant_id).first():
            db.add(TenantStripeCustomer(tenant_id=tenant_id, stripe_customer_id=customer_id))
            db.commit()
    # Add durable entitlement handling for invoice.paid, invoice.payment_failed,
    # customer.subscription.updated and customer.subscription.deleted before production.
    return JSONResponse({"received": True})

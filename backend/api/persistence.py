"""Durable Stripe webhook event ledger and subscription projection."""
import json
import os
from typing import Any

import psycopg
from psycopg.types.json import Jsonb


def database_ready() -> bool:
    return bool(os.getenv("DATABASE_URL"))


def _connect():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not configured")
    # Render Postgres requires TLS for external connections; internal connections
    # may also safely use verify-full when the platform certificate chain is available.
    return psycopg.connect(url, connect_timeout=5)


def persist_stripe_event(event_id: str, event_type: str, obj: dict[str, Any]) -> bool:
    """Atomically record an event and update the subscription projection.
    Returns True when the event was already processed.
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS stripe_webhook_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    payload JSONB NOT NULL,
                    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS stripe_subscriptions (
                    subscription_id TEXT PRIMARY KEY,
                    customer_id TEXT,
                    status TEXT NOT NULL,
                    price_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                    cancel_at_period_end BOOLEAN NOT NULL DEFAULT FALSE,
                    last_invoice_status TEXT,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute(
                """INSERT INTO stripe_webhook_events(event_id, event_type, payload)
                   VALUES (%s, %s, %s) ON CONFLICT (event_id) DO NOTHING
                   RETURNING event_id""",
                (event_id, event_type, Jsonb(obj))
            )
            if cur.fetchone() is None:
                return True

            if event_type in (
                "checkout.session.completed",
                "checkout.session.async_payment_succeeded",
            ):
                subscription_id = obj.get("subscription")
                if subscription_id:
                    cur.execute("""
                        INSERT INTO stripe_subscriptions
                            (subscription_id, customer_id, status)
                        VALUES (%s, %s, 'active')
                        ON CONFLICT (subscription_id) DO UPDATE SET
                            customer_id = EXCLUDED.customer_id,
                            updated_at = NOW()
                    """, (str(subscription_id), obj.get("customer")))

            elif event_type in (
                "customer.subscription.created",
                "customer.subscription.updated",
                "customer.subscription.deleted",
            ):
                subscription_id = obj.get("id")
                if subscription_id:
                    prices = [
                        item.get("price", {}).get("id")
                        for item in (obj.get("items") or {}).get("data", [])
                        if item.get("price", {}).get("id")
                    ]
                    cur.execute("""
                        INSERT INTO stripe_subscriptions
                            (subscription_id, customer_id, status, price_ids, cancel_at_period_end)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (subscription_id) DO UPDATE SET
                            customer_id = EXCLUDED.customer_id,
                            status = EXCLUDED.status,
                            price_ids = EXCLUDED.price_ids,
                            cancel_at_period_end = EXCLUDED.cancel_at_period_end,
                            updated_at = NOW()
                    """, (
                        str(subscription_id), obj.get("customer"),
                        obj.get("status", "unknown"), Jsonb(prices),
                        bool(obj.get("cancel_at_period_end", False))
                    ))

            elif event_type in ("invoice.paid", "invoice.payment_succeeded", "invoice.payment_failed"):
                subscription_id = obj.get("subscription")
                if subscription_id:
                    status = "payment_failed" if event_type == "invoice.payment_failed" else "paid"
                    cur.execute("""
                        UPDATE stripe_subscriptions
                        SET last_invoice_status = %s, updated_at = NOW()
                        WHERE subscription_id = %s
                    """, (status, str(subscription_id)))
    return False

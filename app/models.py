from datetime import datetime, timezone
from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base

class TenantStripeCustomer(Base):
    __tablename__ = "tenant_stripe_customers"
    __table_args__ = (UniqueConstraint("tenant_id", name="uq_tenant_stripe_customer_tenant"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    stripe_customer_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class ProcessedStripeEvent(Base):
    __tablename__ = "processed_stripe_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    stripe_event_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(128))
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

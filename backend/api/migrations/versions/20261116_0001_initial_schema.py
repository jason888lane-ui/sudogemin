"""Create initial durable brokerage schema.

Revision ID: 20261116_0001
Revises:
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261116_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "datasets",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=160), nullable=False),
        sa.Column("seller_subject", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("source", sa.String(length=200), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("license_status", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
    )
    op.create_index("ix_datasets_tenant_id", "datasets", ["tenant_id"])
    op.create_index("ix_datasets_marketplace", "datasets", ["status", "license_status"])

    op.create_table(
        "trades",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("buyer_tenant_id", sa.String(length=160), nullable=False),
        sa.Column("buyer_subject", sa.String(length=200), nullable=False),
        sa.Column("seller_tenant_id", sa.String(length=160), nullable=False),
        sa.Column("dataset_id", sa.String(length=36), sa.ForeignKey("datasets.id"), nullable=False),
        sa.Column("amount_usd", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("payment_status", sa.String(length=32), nullable=False),
        sa.Column("approval_subject", sa.String(length=200), nullable=True),
        sa.Column("approved_at", sa.String(length=40), nullable=True),
        sa.Column("merkle_root", sa.String(length=64), nullable=False),
        sa.Column("stripe_session_id", sa.String(length=200), nullable=True, unique=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
    )
    op.create_index("ix_trades_buyer_tenant_id", "trades", ["buyer_tenant_id"])
    op.create_index("ix_trades_seller_tenant_id", "trades", ["seller_tenant_id"])
    op.create_index("ix_trades_dataset_id", "trades", ["dataset_id"])
    op.create_index("ix_trades_buyer_status", "trades", ["buyer_tenant_id", "status"])
    op.create_index("ix_trades_seller_status", "trades", ["seller_tenant_id", "status"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=160), nullable=False),
        sa.Column("actor_subject", sa.String(length=200), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("resource_type", sa.String(length=50), nullable=False),
        sa.Column("resource_id", sa.String(length=36), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
    )
    op.create_index("ix_audit_events_tenant_id", "audit_events", ["tenant_id"])

    op.create_table(
        "stripe_events",
        sa.Column("id", sa.String(length=200), primary_key=True),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("received_at", sa.String(length=40), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("stripe_events")
    op.drop_index("ix_audit_events_tenant_id", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_trades_seller_status", table_name="trades")
    op.drop_index("ix_trades_buyer_status", table_name="trades")
    op.drop_index("ix_trades_dataset_id", table_name="trades")
    op.drop_index("ix_trades_seller_tenant_id", table_name="trades")
    op.drop_index("ix_trades_buyer_tenant_id", table_name="trades")
    op.drop_table("trades")
    op.drop_index("ix_datasets_marketplace", table_name="datasets")
    op.drop_index("ix_datasets_tenant_id", table_name="datasets")
    op.drop_table("datasets")

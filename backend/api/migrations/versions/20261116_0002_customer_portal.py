"""Customer portal, asset delivery, and prospect discovery schema.

Revision ID: 20261116_0002
Revises: 20261116_0001
"""
from alembic import op
import sqlalchemy as sa

revision = "20261116_0002"
down_revision = "20261116_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("datasets", sa.Column("price_usd", sa.Numeric(12, 2), nullable=True))
    op.add_column("datasets", sa.Column("reviewer_subject", sa.String(length=200), nullable=True))
    op.add_column("trades", sa.Column("stripe_payment_intent_id", sa.String(length=200), nullable=True))

    op.create_table(
        "tenants",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False, unique=True),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("website", sa.String(length=255), nullable=True),
        sa.Column("created_by_subject", sa.String(length=200), nullable=False),
        sa.Column("stripe_account_id", sa.String(length=100), nullable=True, unique=True),
        sa.Column("stripe_charges_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("stripe_payouts_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.String(length=40), nullable=False),
    )
    op.create_index("ix_tenants_slug", "tenants", ["slug"])

    op.create_table(
        "tenant_memberships",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("roles", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.UniqueConstraint("tenant_id", "subject", name="uq_tenant_membership_subject"),
    )
    op.create_index("ix_tenant_memberships_tenant_id", "tenant_memberships", ["tenant_id"])
    op.create_index("ix_tenant_memberships_subject", "tenant_memberships", ["subject"])

    op.create_table(
        "organization_invites",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("roles", sa.JSON(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("created_by_subject", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("expires_at", sa.String(length=40), nullable=False),
        sa.Column("accepted_at", sa.String(length=40), nullable=True),
    )
    op.create_index("ix_organization_invites_tenant_id", "organization_invites", ["tenant_id"])

    op.create_table(
        "data_assets",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=160), nullable=False),
        sa.Column("dataset_id", sa.String(length=36), sa.ForeignKey("datasets.id"), nullable=False),
        sa.Column("uploader_subject", sa.String(length=200), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False, unique=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
    )
    op.create_index("ix_data_assets_tenant_id", "data_assets", ["tenant_id"])
    op.create_index("ix_data_assets_dataset_id", "data_assets", ["dataset_id"])
    op.create_index("ix_data_assets_dataset_status", "data_assets", ["dataset_id", "status"])

    op.create_table(
        "prospects",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("company_name", sa.String(length=200), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False, unique=True),
        sa.Column("website", sa.String(length=512), nullable=False),
        sa.Column("industry", sa.String(length=120), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=False),
        sa.Column("buyer_fit_score", sa.Integer(), nullable=False),
        sa.Column("seller_fit_score", sa.Integer(), nullable=False),
        sa.Column("fit_score", sa.Integer(), nullable=False),
        sa.Column("fit_reason", sa.String(length=1000), nullable=False),
        sa.Column("source_url", sa.String(length=1024), nullable=False),
        sa.Column("source_title", sa.String(length=500), nullable=False),
        sa.Column("source_evidence", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("suppressed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
    )
    op.create_index("ix_prospects_domain", "prospects", ["domain"])
    op.create_index("ix_prospects_score_status", "prospects", ["fit_score", "status"])

    op.create_table(
        "discovery_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("queries_count", sa.Integer(), nullable=False),
        sa.Column("sources_count", sa.Integer(), nullable=False),
        sa.Column("prospects_added", sa.Integer(), nullable=False),
        sa.Column("prospects_updated", sa.Integer(), nullable=False),
        sa.Column("error_summary", sa.String(length=2000), nullable=False),
        sa.Column("started_at", sa.String(length=40), nullable=False),
        sa.Column("finished_at", sa.String(length=40), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("discovery_runs")
    op.drop_index("ix_prospects_score_status", table_name="prospects")
    op.drop_index("ix_prospects_domain", table_name="prospects")
    op.drop_table("prospects")
    op.drop_index("ix_data_assets_dataset_status", table_name="data_assets")
    op.drop_index("ix_data_assets_dataset_id", table_name="data_assets")
    op.drop_index("ix_data_assets_tenant_id", table_name="data_assets")
    op.drop_table("data_assets")
    op.drop_index("ix_organization_invites_tenant_id", table_name="organization_invites")
    op.drop_table("organization_invites")
    op.drop_index("ix_tenant_memberships_subject", table_name="tenant_memberships")
    op.drop_index("ix_tenant_memberships_tenant_id", table_name="tenant_memberships")
    op.drop_table("tenant_memberships")
    op.drop_index("ix_tenants_slug", table_name="tenants")
    op.drop_table("tenants")
    op.drop_column("trades", "stripe_payment_intent_id")
    op.drop_column("datasets", "reviewer_subject")
    op.drop_column("datasets", "price_usd")

"""Durable governed agent runtime schema.

Revision ID: 20261119_0002
Revises: 20261116_0001
"""
from alembic import op
import sqlalchemy as sa

revision = "20261119_0002"
down_revision = "20261116_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(160), nullable=False),
        sa.Column("agent_id", sa.String(64), nullable=False),
        sa.Column("requested_by", sa.String(200), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("prompt", sa.String(4000), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("run_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column("approved_by", sa.String(200), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_agent_task_tenant_idempotency"),
    )
    op.create_index("ix_agent_tasks_queue", "agent_tasks", ["status", "run_after", "created_at"])
    op.create_index("ix_agent_tasks_tenant_created", "agent_tasks", ["tenant_id", "created_at"])

    op.create_table(
        "agent_controls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("emergency_stop", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_by", sa.String(200), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("agent_controls")
    op.drop_index("ix_agent_tasks_tenant_created", table_name="agent_tasks")
    op.drop_index("ix_agent_tasks_queue", table_name="agent_tasks")
    op.drop_table("agent_tasks")

"""create webhook_deliveries table

Revision ID: i9j0k1l2m3n4
Revises: h8i9j0k1l2m3
Create Date: 2026-03-22

Tracks outgoing webhook delivery attempts to merchant endpoints.
Status lifecycle: pending → delivered | failed.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "i9j0k1l2m3n4"
down_revision = "h8i9j0k1l2m3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TYPE webhookdeliverystatus AS ENUM ('pending', 'delivered', 'failed')"
    )

    op.create_table(
        "webhook_deliveries",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column(
            "merchant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("merchants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "transaction_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transactions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "pending",
                "delivered",
                "failed",
                name="webhookdeliverystatus",
                create_type=False,
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "attempt_count", sa.SmallInteger(), nullable=False, server_default="0"
        ),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_response_code", sa.SmallInteger(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_index(
        "ix_webhook_deliveries_merchant_id", "webhook_deliveries", ["merchant_id"]
    )
    op.create_index(
        "ix_webhook_deliveries_transaction_id",
        "webhook_deliveries",
        ["transaction_id"],
    )
    op.create_index(
        "ix_webhook_deliveries_status", "webhook_deliveries", ["status"]
    )
    # Composite index for the retry sweep query: status=pending AND next_retry_at <= now()
    op.create_index(
        "ix_webhook_deliveries_retry_sweep",
        "webhook_deliveries",
        ["status", "next_retry_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_webhook_deliveries_retry_sweep", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_status", table_name="webhook_deliveries")
    op.drop_index(
        "ix_webhook_deliveries_transaction_id", table_name="webhook_deliveries"
    )
    op.drop_index(
        "ix_webhook_deliveries_merchant_id", table_name="webhook_deliveries"
    )
    op.drop_table("webhook_deliveries")
    op.execute("DROP TYPE webhookdeliverystatus")

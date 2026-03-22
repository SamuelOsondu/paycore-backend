"""create transactions table

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-03-22

"""

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TYPE transactiontype AS ENUM "
        "('funding', 'transfer', 'merchant_payment', 'withdrawal', 'reversal')"
    )
    op.execute(
        "CREATE TYPE transactionstatus AS ENUM "
        "('pending', 'processing', 'completed', 'failed', 'reversed')"
    )

    op.create_table(
        "transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reference", sa.String(50), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "funding",
                "transfer",
                "merchant_payment",
                "withdrawal",
                "reversal",
                name="transactiontype",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "processing",
                "completed",
                "failed",
                "reversed",
                name="transactionstatus",
                create_type=False,
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("amount", sa.Numeric(precision=20, scale=2), nullable=False),
        sa.Column(
            "currency", sa.String(3), nullable=False, server_default="NGN"
        ),
        sa.Column(
            "source_wallet_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column(
            "destination_wallet_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column(
            "initiated_by_user_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("provider_reference", sa.String(100), nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["source_wallet_id"], ["wallets.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["destination_wallet_id"], ["wallets.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["initiated_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reference", name="uq_transactions_reference"),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_transactions_idempotency_key"
        ),
    )

    # Lookup indexes
    op.create_index("ix_transactions_reference", "transactions", ["reference"])
    op.create_index(
        "ix_transactions_provider_reference",
        "transactions",
        ["provider_reference"],
    )
    op.create_index(
        "ix_transactions_idempotency_key",
        "transactions",
        ["idempotency_key"],
    )
    op.create_index(
        "ix_transactions_source_wallet_id",
        "transactions",
        ["source_wallet_id"],
    )
    op.create_index(
        "ix_transactions_destination_wallet_id",
        "transactions",
        ["destination_wallet_id"],
    )
    op.create_index(
        "ix_transactions_initiated_by_user_id",
        "transactions",
        ["initiated_by_user_id"],
    )
    op.create_index("ix_transactions_status", "transactions", ["status"])
    op.create_index("ix_transactions_type", "transactions", ["type"])
    op.create_index("ix_transactions_created_at", "transactions", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_transactions_created_at", table_name="transactions")
    op.drop_index("ix_transactions_type", table_name="transactions")
    op.drop_index("ix_transactions_status", table_name="transactions")
    op.drop_index(
        "ix_transactions_initiated_by_user_id", table_name="transactions"
    )
    op.drop_index(
        "ix_transactions_destination_wallet_id", table_name="transactions"
    )
    op.drop_index(
        "ix_transactions_source_wallet_id", table_name="transactions"
    )
    op.drop_index(
        "ix_transactions_idempotency_key", table_name="transactions"
    )
    op.drop_index(
        "ix_transactions_provider_reference", table_name="transactions"
    )
    op.drop_index("ix_transactions_reference", table_name="transactions")
    op.drop_table("transactions")
    op.execute("DROP TYPE transactionstatus")
    op.execute("DROP TYPE transactiontype")

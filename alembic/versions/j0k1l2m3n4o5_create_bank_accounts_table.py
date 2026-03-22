"""create bank_accounts table

Revision ID: j0k1l2m3n4o5
Revises: i9j0k1l2m3n4
Create Date: 2026-03-22

Stores user-registered Nigerian bank accounts used for withdrawals.
Soft-deleted (deleted_at) to preserve audit history.
paystack_recipient_code is cached on first withdrawal to avoid repeated
Paystack API calls for the same account.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "j0k1l2m3n4o5"
down_revision = "i9j0k1l2m3n4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bank_accounts",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("account_name", sa.String(255), nullable=False),
        sa.Column("account_number", sa.String(20), nullable=False),
        sa.Column("bank_code", sa.String(20), nullable=False),
        sa.Column("bank_name", sa.String(100), nullable=False),
        sa.Column(
            "is_default", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("paystack_recipient_code", sa.String(100), nullable=True),
        sa.Column(
            "deleted_at", sa.DateTime(timezone=True), nullable=True
        ),
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

    op.create_index("ix_bank_accounts_user_id", "bank_accounts", ["user_id"])
    op.create_index("ix_bank_accounts_deleted_at", "bank_accounts", ["deleted_at"])
    # Composite index for "get default account for user" query
    op.create_index(
        "ix_bank_accounts_user_default",
        "bank_accounts",
        ["user_id", "is_default"],
    )


def downgrade() -> None:
    op.drop_index("ix_bank_accounts_user_default", table_name="bank_accounts")
    op.drop_index("ix_bank_accounts_deleted_at", table_name="bank_accounts")
    op.drop_index("ix_bank_accounts_user_id", table_name="bank_accounts")
    op.drop_table("bank_accounts")

"""add fraud risk flag fields and index to transactions

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-03-22

Adds risk_flagged / risk_flag_reason to transactions for admin review.
Adds composite index on (initiated_by_user_id, type, status, created_at)
for efficient daily fraud volume queries.
"""

from alembic import op
import sqlalchemy as sa

revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column("risk_flagged", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "transactions",
        sa.Column("risk_flag_reason", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_transactions_fraud_check",
        "transactions",
        ["initiated_by_user_id", "type", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_transactions_fraud_check", table_name="transactions")
    op.drop_column("transactions", "risk_flag_reason")
    op.drop_column("transactions", "risk_flagged")

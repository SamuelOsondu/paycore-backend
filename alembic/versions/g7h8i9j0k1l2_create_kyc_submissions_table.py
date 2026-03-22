"""create kyc_submissions table

Revision ID: g7h8i9j0k1l2
Revises: f6a7b8c9d0e1
Create Date: 2026-03-22

Tracks KYC document submissions, their review status, and reviewer info.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "g7h8i9j0k1l2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TYPE kycstatus AS ENUM ('pending', 'approved', 'rejected')")

    op.create_table(
        "kyc_submissions",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("requested_tier", sa.SmallInteger(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM("pending", "approved", "rejected", name="kycstatus", create_type=False),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("document_key", sa.String(500), nullable=False),
        sa.Column(
            "reviewer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
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

    op.create_index("ix_kyc_submissions_user_id", "kyc_submissions", ["user_id"])
    op.create_index("ix_kyc_submissions_status", "kyc_submissions", ["status"])


def downgrade() -> None:
    op.drop_index("ix_kyc_submissions_status", table_name="kyc_submissions")
    op.drop_index("ix_kyc_submissions_user_id", table_name="kyc_submissions")
    op.drop_table("kyc_submissions")
    op.execute("DROP TYPE kycstatus")

"""create users table

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-03-22 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE TYPE userrole AS ENUM ('user', 'merchant', 'admin')")

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column(
            "role",
            postgresql.ENUM("user", "merchant", "admin", name="userrole", create_type=False),
            nullable=False,
            server_default="user",
        ),
        sa.Column("kyc_tier", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "is_email_verified", sa.Boolean(), nullable=False, server_default="false"
        ),
        # Timestamps
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
        # Soft delete
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_phone", "users", ["phone"], unique=True)
    op.create_index("ix_users_role", "users", ["role"])
    op.create_index("ix_users_kyc_tier", "users", ["kyc_tier"])
    op.create_index("ix_users_deleted_at", "users", ["deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_users_deleted_at", table_name="users")
    op.drop_index("ix_users_kyc_tier", table_name="users")
    op.drop_index("ix_users_role", table_name="users")
    op.drop_index("ix_users_phone", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
    op.execute("DROP TYPE userrole")

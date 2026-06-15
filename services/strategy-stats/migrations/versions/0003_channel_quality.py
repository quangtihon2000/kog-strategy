"""add channel quality verdict columns

Revision ID: 0003_channel_quality
Revises: 0002_account_dimension
Create Date: 2026-06-15

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_channel_quality"
down_revision = "0002_account_dimension"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "channels",
        sa.Column(
            "quality_status",
            sa.String(length=16),
            nullable=False,
            server_default="PENDING",
        ),
    )
    op.add_column("channels", sa.Column("quality_note", sa.Text(), nullable=True))
    op.add_column(
        "channels",
        sa.Column("quality_updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "channels",
        sa.Column("quality_updated_by", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("channels", "quality_updated_by")
    op.drop_column("channels", "quality_updated_at")
    op.drop_column("channels", "quality_note")
    op.drop_column("channels", "quality_status")

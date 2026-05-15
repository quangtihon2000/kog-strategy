"""add composite account+signal_ts indexes for conde and gvfx hot-path queries

Revision ID: 0002_account_dimension
Revises: 0001_initial
Create Date: 2026-05-15

"""
from __future__ import annotations

from alembic import op

revision = "0002_account_dimension"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_conde_outcomes_account_signal",
        "conde_outcomes",
        ["account", "signal_ts"],
    )
    op.create_index(
        "ix_gvfx_outcomes_account_signal",
        "gvfx_outcomes",
        ["account", "signal_ts"],
    )


def downgrade() -> None:
    op.drop_index("ix_gvfx_outcomes_account_signal", table_name="gvfx_outcomes")
    op.drop_index("ix_conde_outcomes_account_signal", table_name="conde_outcomes")

"""Seed Channel A and Channel B with their v1 regex_tables.

Revision ID: 002
Revises: 001
Create Date: 2026-05-10

Imports regex_table constants from tg_listener.db.seed_data — that module is
the single source of truth referenced by both this migration and the parity tests.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

from tg_listener.db.seed_data import (
    CHANNEL_A_ID,
    CHANNEL_A_REGEX_TABLE,
    CHANNEL_B_ID,
    CHANNEL_B_REGEX_TABLE,
)

revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW = datetime.now(UTC)


def upgrade() -> None:
    conn = op.get_bind()

    # Insert channels.
    conn.execute(
        sa.text(
            "INSERT INTO channels (id, name, auto_approve, created_at, updated_at) "
            "VALUES (:id, :name, false, :now, :now)"
        ),
        {"id": CHANNEL_A_ID, "name": "Channel A Pro", "now": _NOW},
    )
    conn.execute(
        sa.text(
            "INSERT INTO channels (id, name, auto_approve, created_at, updated_at) "
            "VALUES (:id, :name, false, :now, :now)"
        ),
        {"id": CHANNEL_B_ID, "name": "Channel B VN", "now": _NOW},
    )

    import json

    # Insert parsers (version=1, status='active', source='seed').
    conn.execute(
        sa.text(
            "INSERT INTO parsers "
            "(channel_id, version, status, regex_table, source, notes, created_at, activated_at) "
            "VALUES (:channel_id, 1, 'active', :regex_table::jsonb, 'seed', :notes, :now, :now)"
        ),
        {
            "channel_id": CHANNEL_A_ID,
            "regex_table": json.dumps(CHANNEL_A_REGEX_TABLE),
            "notes": "Seed from channel_a.py v1 — clean English-format signals",
            "now": _NOW,
        },
    )
    conn.execute(
        sa.text(
            "INSERT INTO parsers "
            "(channel_id, version, status, regex_table, source, notes, created_at, activated_at) "
            "VALUES (:channel_id, 1, 'active', :regex_table::jsonb, 'seed', :notes, :now, :now)"
        ),
        {
            "channel_id": CHANNEL_B_ID,
            "regex_table": json.dumps(CHANNEL_B_REGEX_TABLE),
            "notes": "Seed from channel_b.py v1 — Vietnamese-format signals",
            "now": _NOW,
        },
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text("DELETE FROM channels WHERE id IN (:a, :b)"),
        {"a": CHANNEL_A_ID, "b": CHANNEL_B_ID},
    )

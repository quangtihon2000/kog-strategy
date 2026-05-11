"""Initial schema: channels, parsers, parser_samples, parser_eval_runs.

Revision ID: 001
Revises:
Create Date: 2026-05-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "channels",
        sa.Column("id", sa.BigInteger(), nullable=False, comment="Telegram chat_id"),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("auto_approve", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "parsers",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("regex_table", postgresql.JSONB(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("activated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('proposed','shadow','active','rejected','retired')",
            name="ck_parsers_status",
        ),
        sa.CheckConstraint(
            "source IN ('seed','llm_induced','manual')",
            name="ck_parsers_source",
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"], ["channels.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel_id", "version", name="uq_parsers_channel_version"),
    )
    # Partial unique index: only one active parser per channel.
    op.create_index(
        "one_active_parser_per_channel",
        "parsers",
        ["channel_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "parser_samples",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("text_hash", sa.CHAR(64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("parsed_by", sa.Text(), nullable=False),
        sa.Column("parsed_signal", postgresql.JSONB(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("minhash", sa.LargeBinary(), nullable=True),
        sa.Column(
            "collected_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "parsed_by IN ('regex','llm')",
            name="ck_samples_parsed_by",
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"], ["channels.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel_id", "text_hash", name="uq_samples_channel_hash"),
    )
    op.create_index(
        "parser_samples_channel_collected_idx",
        "parser_samples",
        ["channel_id", sa.text("collected_at DESC")],
    )

    op.create_table(
        "parser_eval_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("parser_id", sa.BigInteger(), nullable=False),
        sa.Column("samples_total", sa.Integer(), nullable=False),
        sa.Column("samples_matched", sa.Integer(), nullable=False),
        sa.Column("match_rate", sa.Float(), nullable=False),
        sa.Column(
            "disagreements",
            postgresql.JSONB(),
            nullable=False,
            server_default="'[]'::jsonb",
        ),
        sa.Column(
            "ran_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["parser_id"], ["parsers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("parser_eval_runs")
    op.drop_index("parser_samples_channel_collected_idx", table_name="parser_samples")
    op.drop_table("parser_samples")
    op.drop_index("one_active_parser_per_channel", table_name="parsers")
    op.drop_table("parsers")
    op.drop_table("channels")

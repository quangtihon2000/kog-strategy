"""initial schema: strategies, channels, conde/gvfx/zone signals + outcomes

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-11

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------------- lookup ----------------
    op.create_table(
        "strategies",
        sa.Column("code", sa.String(length=32), primary_key=True),
        sa.Column("display_name", sa.String(length=64), nullable=False),
    )
    op.execute(
        "INSERT INTO strategies (code, display_name) VALUES "
        "('conde', 'Conde Auto Entry'), "
        "('gvfx', 'GVFX Signal'), "
        "('zone', 'Zone Signal')"
    )

    op.create_table(
        "channels",
        sa.Column("channel_id", sa.BigInteger(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("name_history", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    # ---------------- conde ----------------
    op.create_table(
        "conde_signals",
        sa.Column("signal_ts", sa.BigInteger(), primary_key=True),
        sa.Column("symbol", sa.String(length=16), primary_key=True),
        sa.Column("direction", sa.String(length=4), nullable=False),
        sa.Column("entry_price", sa.Float(), nullable=False),
        sa.Column("sl", sa.Float(), nullable=False),
        sa.Column("tps", postgresql.ARRAY(sa.Float()), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=True),
        sa.Column("channel_name", sa.Text(), nullable=True),
        sa.Column("redis_msg_id", sa.String(length=64), nullable=True),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_conde_signals_symbol_ts", "conde_signals", ["symbol", "signal_ts"])
    op.create_index("ix_conde_signals_channel_ts", "conde_signals", ["channel_id", "signal_ts"])

    op.create_table(
        "conde_outcomes",
        sa.Column("position_id", sa.BigInteger(), primary_key=True),
        sa.Column("account", sa.BigInteger(), nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("signal_ts", sa.BigInteger(), nullable=True),
        sa.Column("direction", sa.String(length=4), nullable=False),
        sa.Column("magic", sa.BigInteger(), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False),
        sa.Column("entry_price", sa.Float(), nullable=False),
        sa.Column("exit_price", sa.Float(), nullable=False),
        sa.Column("profit", sa.Float(), nullable=False),
        sa.Column("swap", sa.Float(), nullable=False, server_default="0"),
        sa.Column("commission", sa.Float(), nullable=False, server_default="0"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("close_reason", sa.String(length=32), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_conde_outcomes_closed_at", "conde_outcomes", ["closed_at"])
    op.create_index("ix_conde_outcomes_signal", "conde_outcomes", ["signal_ts", "symbol"])
    op.create_index(
        "ix_conde_outcomes_account_closed", "conde_outcomes", ["account", "closed_at"]
    )

    # ---------------- gvfx ----------------
    op.create_table(
        "gvfx_signals",
        sa.Column("signal_ts", sa.BigInteger(), primary_key=True),
        sa.Column("symbol", sa.String(length=16), primary_key=True),
        sa.Column("direction", sa.String(length=4), nullable=False),
        sa.Column("target", sa.Float(), nullable=False),
        sa.Column("step_pts", sa.Float(), nullable=True),
        sa.Column("tp_pts", sa.Float(), nullable=True),
        sa.Column("low_price", sa.Float(), nullable=True),
        sa.Column("high_price", sa.Float(), nullable=True),
        sa.Column("use_atr", sa.Boolean(), nullable=True),
        sa.Column("redis_msg_id", sa.String(length=64), nullable=True),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_gvfx_signals_symbol_ts", "gvfx_signals", ["symbol", "signal_ts"])

    op.create_table(
        "gvfx_outcomes",
        sa.Column("position_id", sa.BigInteger(), primary_key=True),
        sa.Column("account", sa.BigInteger(), nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("signal_ts", sa.BigInteger(), nullable=True),
        sa.Column("direction", sa.String(length=4), nullable=False),
        sa.Column("mode_tag", sa.String(length=4), nullable=True),
        sa.Column("magic", sa.BigInteger(), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False),
        sa.Column("entry_price", sa.Float(), nullable=False),
        sa.Column("exit_price", sa.Float(), nullable=False),
        sa.Column("profit", sa.Float(), nullable=False),
        sa.Column("swap", sa.Float(), nullable=False, server_default="0"),
        sa.Column("commission", sa.Float(), nullable=False, server_default="0"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("close_reason", sa.String(length=32), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_gvfx_outcomes_closed_at", "gvfx_outcomes", ["closed_at"])
    op.create_index("ix_gvfx_outcomes_signal", "gvfx_outcomes", ["signal_ts", "symbol"])
    op.create_index(
        "ix_gvfx_outcomes_account_closed", "gvfx_outcomes", ["account", "closed_at"]
    )
    op.create_index("ix_gvfx_outcomes_mode_tag", "gvfx_outcomes", ["mode_tag"])

    # ---------------- zone ----------------
    op.create_table(
        "zone_signals",
        sa.Column("signal_ts", sa.BigInteger(), primary_key=True),
        sa.Column("symbol", sa.String(length=16), primary_key=True),
        sa.Column("redbox_upper", sa.Float(), nullable=False),
        sa.Column("redbox_lower", sa.Float(), nullable=False),
        sa.Column("targets_above", postgresql.ARRAY(sa.Float()), nullable=False),
        sa.Column("targets_below", postgresql.ARRAY(sa.Float()), nullable=False),
        sa.Column("redis_msg_id", sa.String(length=64), nullable=True),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_zone_signals_symbol_ts", "zone_signals", ["symbol", "signal_ts"])

    op.create_table(
        "zone_outcomes",
        sa.Column("position_id", sa.BigInteger(), primary_key=True),
        sa.Column("account", sa.BigInteger(), nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("signal_ts", sa.BigInteger(), nullable=True),
        sa.Column("direction", sa.String(length=4), nullable=False),
        sa.Column("tier", sa.String(length=8), nullable=True),
        sa.Column("slot_index", sa.Integer(), nullable=True),
        sa.Column("magic", sa.BigInteger(), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False),
        sa.Column("entry_price", sa.Float(), nullable=False),
        sa.Column("exit_price", sa.Float(), nullable=False),
        sa.Column("profit", sa.Float(), nullable=False),
        sa.Column("swap", sa.Float(), nullable=False, server_default="0"),
        sa.Column("commission", sa.Float(), nullable=False, server_default="0"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("close_reason", sa.String(length=32), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_zone_outcomes_closed_at", "zone_outcomes", ["closed_at"])
    op.create_index("ix_zone_outcomes_signal", "zone_outcomes", ["signal_ts", "symbol"])
    op.create_index(
        "ix_zone_outcomes_account_closed", "zone_outcomes", ["account", "closed_at"]
    )
    op.create_index("ix_zone_outcomes_tier", "zone_outcomes", ["tier"])


def downgrade() -> None:
    op.drop_table("zone_outcomes")
    op.drop_table("zone_signals")
    op.drop_table("gvfx_outcomes")
    op.drop_table("gvfx_signals")
    op.drop_table("conde_outcomes")
    op.drop_table("conde_signals")
    op.drop_table("channels")
    op.drop_table("strategies")

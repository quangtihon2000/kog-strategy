"""SQLAlchemy 2.0 ORM models — per-strategy tables, JSONB raw for forensics."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[float]: ARRAY(Float)}


class Strategy(Base):
    __tablename__ = "strategies"
    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)


class Channel(Base):
    """Telegram source channel for conde signals.

    `channel_id` is producer-supplied (telegram channel ID, BIGINT). `name` is the
    display name at last-seen; when the producer reports a new name for an existing
    channel_id we append the previous name to `name_history` so we can still
    correlate old stats rows.
    """

    __tablename__ = "channels"
    channel_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    name_history: Mapped[list[dict] | None] = mapped_column(JSONB, nullable=True)


# ---------------------------------------------------------------------------
# Conde
# ---------------------------------------------------------------------------
class CondeSignal(Base):
    __tablename__ = "conde_signals"
    signal_ts: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    direction: Mapped[str] = mapped_column(String(4), nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    sl: Mapped[float] = mapped_column(Float, nullable=False)
    tps: Mapped[list[float]] = mapped_column(ARRAY(Float), nullable=False)
    channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    channel_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    redis_msg_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (
        Index("ix_conde_signals_symbol_ts", "symbol", "signal_ts"),
        Index("ix_conde_signals_channel_ts", "channel_id", "signal_ts"),
    )


class CondeOutcome(Base):
    __tablename__ = "conde_outcomes"
    position_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account: Mapped[int] = mapped_column(BigInteger, nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    signal_ts: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    direction: Mapped[str] = mapped_column(String(4), nullable=False)
    magic: Mapped[int] = mapped_column(BigInteger, nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_price: Mapped[float] = mapped_column(Float, nullable=False)
    profit: Mapped[float] = mapped_column(Float, nullable=False)
    swap: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    commission: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    close_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (
        Index("ix_conde_outcomes_closed_at", "closed_at"),
        Index("ix_conde_outcomes_signal", "signal_ts", "symbol"),
        Index("ix_conde_outcomes_account_closed", "account", "closed_at"),
    )


# ---------------------------------------------------------------------------
# GVFX
# ---------------------------------------------------------------------------
class GvfxSignal(Base):
    __tablename__ = "gvfx_signals"
    signal_ts: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    direction: Mapped[str] = mapped_column(String(4), nullable=False)
    target: Mapped[float] = mapped_column(Float, nullable=False)
    step_pts: Mapped[float | None] = mapped_column(Float, nullable=True)
    tp_pts: Mapped[float | None] = mapped_column(Float, nullable=True)
    low_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    high_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    use_atr: Mapped[bool | None] = mapped_column(nullable=True)
    redis_msg_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (Index("ix_gvfx_signals_symbol_ts", "symbol", "signal_ts"),)


class GvfxOutcome(Base):
    __tablename__ = "gvfx_outcomes"
    position_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account: Mapped[int] = mapped_column(BigInteger, nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    signal_ts: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    direction: Mapped[str] = mapped_column(String(4), nullable=False)
    mode_tag: Mapped[str | None] = mapped_column(String(4), nullable=True)  # A / F / S / ?
    magic: Mapped[int] = mapped_column(BigInteger, nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_price: Mapped[float] = mapped_column(Float, nullable=False)
    profit: Mapped[float] = mapped_column(Float, nullable=False)
    swap: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    commission: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    close_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)  # incl. EOD
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (
        Index("ix_gvfx_outcomes_closed_at", "closed_at"),
        Index("ix_gvfx_outcomes_signal", "signal_ts", "symbol"),
        Index("ix_gvfx_outcomes_account_closed", "account", "closed_at"),
        Index("ix_gvfx_outcomes_mode_tag", "mode_tag"),
    )


# ---------------------------------------------------------------------------
# Zone
# ---------------------------------------------------------------------------
class ZoneSignal(Base):
    __tablename__ = "zone_signals"
    signal_ts: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    redbox_upper: Mapped[float] = mapped_column(Float, nullable=False)
    redbox_lower: Mapped[float] = mapped_column(Float, nullable=False)
    targets_above: Mapped[list[float]] = mapped_column(ARRAY(Float), nullable=False)
    targets_below: Mapped[list[float]] = mapped_column(ARRAY(Float), nullable=False)
    redis_msg_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (Index("ix_zone_signals_symbol_ts", "symbol", "signal_ts"),)


class ZoneOutcome(Base):
    __tablename__ = "zone_outcomes"
    position_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account: Mapped[int] = mapped_column(BigInteger, nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    signal_ts: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    direction: Mapped[str] = mapped_column(String(4), nullable=False)
    tier: Mapped[str | None] = mapped_column(String(8), nullable=True)  # SCALP / NORMAL / MID / UNKNOWN
    slot_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    magic: Mapped[int] = mapped_column(BigInteger, nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_price: Mapped[float] = mapped_column(Float, nullable=False)
    profit: Mapped[float] = mapped_column(Float, nullable=False)
    swap: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    commission: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    close_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (
        Index("ix_zone_outcomes_closed_at", "closed_at"),
        Index("ix_zone_outcomes_signal", "signal_ts", "symbol"),
        Index("ix_zone_outcomes_account_closed", "account", "closed_at"),
        Index("ix_zone_outcomes_tier", "tier"),
    )

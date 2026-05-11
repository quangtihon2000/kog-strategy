"""SQLAlchemy 2.0 ORM models mirroring the Alembic-managed schema.

Tables: channels, parsers, parser_samples, parser_eval_runs.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Telegram chat_id
    name: Mapped[str] = mapped_column(Text, nullable=False)
    auto_approve: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now(), onupdate=func.now()
    )

    parsers: Mapped[list[Parser]] = relationship(
        back_populates="channel", cascade="all, delete-orphan"
    )
    samples: Mapped[list[ParserSample]] = relationship(
        back_populates="channel", cascade="all, delete-orphan"
    )


class Parser(Base):
    __tablename__ = "parsers"
    __table_args__ = (
        UniqueConstraint("channel_id", "version", name="uq_parsers_channel_version"),
        Index(
            "one_active_parser_per_channel",
            "channel_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        CheckConstraint(
            "status IN ('proposed','shadow','active','rejected','retired')",
            name="ck_parsers_status",
        ),
        CheckConstraint(
            "source IN ('seed','llm_induced','manual')",
            name="ck_parsers_source",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("channels.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    regex_table: Mapped[dict] = mapped_column(JSONB, nullable=False)  # type: ignore[type-arg]
    source: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )
    activated_at: Mapped[datetime | None] = mapped_column(nullable=True)

    channel: Mapped[Channel] = relationship(back_populates="parsers")
    eval_runs: Mapped[list[ParserEvalRun]] = relationship(
        back_populates="parser", cascade="all, delete-orphan"
    )


class ParserSample(Base):
    __tablename__ = "parser_samples"
    __table_args__ = (
        UniqueConstraint("channel_id", "text_hash", name="uq_samples_channel_hash"),
        Index(
            "parser_samples_channel_collected_idx",
            "channel_id",
            "collected_at",
            postgresql_ops={"collected_at": "DESC"},
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("channels.id", ondelete="CASCADE"), nullable=False
    )
    text_hash: Mapped[str] = mapped_column(Text, nullable=False)  # sha256 hex, 64 chars
    text: Mapped[str] = mapped_column(Text, nullable=False)
    parsed_by: Mapped[str] = mapped_column(Text, nullable=False)
    parsed_signal: Mapped[dict] = mapped_column(JSONB, nullable=False)  # type: ignore[type-arg]
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    minhash: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    collected_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )

    channel: Mapped[Channel] = relationship(back_populates="samples")


class ParserEvalRun(Base):
    __tablename__ = "parser_eval_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    parser_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("parsers.id", ondelete="CASCADE"), nullable=False
    )
    samples_total: Mapped[int] = mapped_column(Integer, nullable=False)
    samples_matched: Mapped[int] = mapped_column(Integer, nullable=False)
    match_rate: Mapped[float] = mapped_column(Float, nullable=False)
    disagreements: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)  # type: ignore[type-arg]
    ran_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())

    parser: Mapped[Parser] = relationship(back_populates="eval_runs")

"""Widen samples.parsed_by CHECK constraint to all 5 tier sources.

Revision ID: 003
Revises: 002
Create Date: 2026-05-11

The original constraint only allowed ('regex','llm') but the 5-tier cascade
pipeline writes tier-prefixed labels (tier0_metadata, tier1_heuristic,
tier2_regex, tier3_llm, tier4_validator).  This migration replaces the old
constraint with one that covers all five sources.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "parser_samples"
_CONSTRAINT = "ck_samples_parsed_by"

_OLD_CHECK = "parsed_by IN ('regex','llm')"
_NEW_CHECK = (
    "parsed_by IN ("
    "'tier0_metadata','tier1_heuristic','tier2_regex','tier3_llm','tier4_validator'"
    ")"
)


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _NEW_CHECK)


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _OLD_CHECK)

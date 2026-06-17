"""parser induce subcommand — auto-induce a RegexTable from stored samples.

Flow:
  1. Load samples from DB via SampleRepo.
  2. Synthesize a RegexTable via the configured provider.
  3. Evaluate the table against the samples.
  4. If acceptable: propose via ParserRepo (unless --dry-run).
  5. Print a JSON summary to stdout.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tg_listener.db.repos.channels import ChannelRepo
from tg_listener.db.repos.eval_runs import EvalRunRepo
from tg_listener.db.repos.parsers import ParserRepo
from tg_listener.db.repos.samples import SampleRepo
from tg_listener.induction.alerts import maybe_emit_low_match_rate
from tg_listener.induction.evaluator import evaluate_detailed, is_acceptable
from tg_listener.induction.synth_provider import (
    AnthropicSynthProvider,
    RegexTableSynthProvider,
    StubSynthProvider,
)
from tg_listener.induction.synthesizer import SynthesizerError, synthesize

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

_DEFAULT_LIMIT = 200
_EVAL_THRESHOLD = 0.95


# ── Provider factory ──────────────────────────────────────────────────────────


def _make_synth_provider(provider_name: str) -> RegexTableSynthProvider:
    """Build a synth provider from INDUCTION_PROVIDER env var."""
    if provider_name == "stub":
        return StubSynthProvider()
    if provider_name == "anthropic":
        api_key = os.environ.get("TIER3_ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            print(
                "ERROR: TIER3_ANTHROPIC_API_KEY or ANTHROPIC_API_KEY must be set"
                " for anthropic provider.",
                file=sys.stderr,
            )
            sys.exit(1)
        return AnthropicSynthProvider(api_key=api_key)
    print(f"ERROR: Unknown INDUCTION_PROVIDER: {provider_name!r}", file=sys.stderr)
    sys.exit(1)


# ── DB session factory ────────────────────────────────────────────────────────


def _make_session_factory() -> async_sessionmaker[AsyncSession]:
    """Build an async_sessionmaker from DATABASE_URL env var."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        print(
            "ERROR: DATABASE_URL env var is required.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Normalise URL: plain postgresql:// → postgresql+asyncpg://.
    if url.startswith("postgresql://") and not url.startswith("postgresql+asyncpg://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(url, echo=False, future=True)
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


# ── Core async logic ──────────────────────────────────────────────────────────


async def _run_induce(
    *,
    channel_id: int,
    limit: int,
    source: str,
    dry_run: bool,
    provider_name: str,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    """Execute the induce pipeline. Returns exit code (0/2)."""
    # Build session factory nếu chưa được inject (CLI mode).
    factory = session_factory or _make_session_factory()

    # 1. Load samples.
    async with factory() as session:
        repo = SampleRepo(session)
        samples = await repo.list_for_channel(channel_id, limit=limit, offset=0)

    if not samples:
        print(
            json.dumps({"status": "no_samples", "channel_id": channel_id}),
            flush=True,
        )
        return 2

    # 2. Build provider.
    provider = _make_synth_provider(provider_name)

    # 3. Synthesize.
    try:
        table = await synthesize(samples, provider)
    except SynthesizerError as exc:
        print(
            json.dumps({"status": "synthesizer_error", "error": str(exc)}),
            flush=True,
        )
        return 2

    # 4. Evaluate (detailed để thu thập disagreements cho DB).
    report, disagreements = evaluate_detailed(table, samples)

    report_dict = {
        "total": report.total,
        "matched": report.matched,
        "mismatched": report.mismatched,
        "parse_failed": report.parse_failed,
        "timeouts": report.timeouts,
        "match_rate": round(report.match_rate, 4),
    }

    # Alert hook — diagnostic, không blocking, gọi trước khi kiểm tra acceptable.
    maybe_emit_low_match_rate(
        channel_id=channel_id,
        parser_id=None,  # chưa có parser_id ở bước này
        report=report,
        threshold=_EVAL_THRESHOLD,
    )

    if not is_acceptable(report, threshold=_EVAL_THRESHOLD):
        print(
            json.dumps(
                {
                    "status": "not_acceptable",
                    "channel_id": channel_id,
                    "eval": report_dict,
                }
            ),
            flush=True,
        )
        return 2

    # 5. Propose (unless dry-run).
    if dry_run:
        print(
            json.dumps(
                {
                    "status": "dry_run",
                    "channel_id": channel_id,
                    "eval": report_dict,
                }
            ),
            flush=True,
        )
        return 0

    # Persist via ParserRepo.
    notes = f"auto-induced from {len(samples)} samples via {provider_name}"
    async with factory() as session:
        parser_repo = ParserRepo(session)
        parser = await parser_repo.propose(
            channel_id=channel_id,
            regex_table=table.model_dump(mode="json"),
            source=source,
            notes=notes,
        )
        await session.flush()

        # Ghi eval run ngay sau propose, trước auto-approve.
        eval_run_repo = EvalRunRepo(session)
        await eval_run_repo.record(
            parser_id=parser.id,
            samples_total=report.total,
            samples_matched=report.matched,
            disagreements=disagreements,
        )

        # Auto-approve: kiểm tra channel có bật auto_approve không.
        channel_repo = ChannelRepo(session)
        channel = await channel_repo.get(channel_id)
        final_status = "proposed"
        if channel is not None and channel.auto_approve:
            parser = await parser_repo.activate(parser.id)
            final_status = "activated"

            await session.commit()
        else:
            await session.commit()

    print(
        json.dumps(
            {
                "status": final_status,
                "channel_id": channel_id,
                "parser_id": parser.id,
                "version": parser.version,
                "eval": report_dict,
            }
        ),
        flush=True,
    )
    return 0


# ── Public entry point called by __main__.py ──────────────────────────────────


def run_induce(args: object) -> int:
    """Synchronous wrapper around _run_induce for argparse dispatch.

    Args:
        args: namespace from argparse with attributes:
              channel_id, limit, source, dry_run.

    Returns:
        Exit code: 0 = success / proposed, 2 = failure / not-acceptable.
    """
    provider_name = os.environ.get("INDUCTION_PROVIDER", "stub")
    return asyncio.run(
        _run_induce(
            channel_id=args.channel_id,  # type: ignore[attr-defined]
            limit=args.limit or _DEFAULT_LIMIT,  # type: ignore[attr-defined]
            source=args.source or "llm_induced",  # type: ignore[attr-defined]
            dry_run=args.dry_run,  # type: ignore[attr-defined]
            provider_name=provider_name,
        )
    )


# ── parser list ───────────────────────────────────────────────────────────────


async def _run_list(
    *,
    channel_id: int,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    """List all parser versions for a channel. Returns exit code (0/2)."""
    factory = session_factory or _make_session_factory()

    async with factory() as session:
        repo = ParserRepo(session)
        versions = await repo.list_versions(channel_id)

    def _dt(dt: object) -> str | None:
        """Serialize datetime to ISO string or None."""
        from datetime import datetime

        if dt is None:
            return None
        assert isinstance(dt, datetime)
        return dt.isoformat()

    print(
        json.dumps(
            {
                "status": "ok",
                "channel_id": channel_id,
                "versions": [
                    {
                        "id": p.id,
                        "version": p.version,
                        "status": p.status,
                        "source": p.source,
                        "created_at": _dt(p.created_at),
                        "activated_at": _dt(p.activated_at),
                        "notes": p.notes,
                    }
                    for p in versions
                ],
            }
        ),
        flush=True,
    )
    return 0


def run_list(args: object) -> int:
    """Synchronous wrapper around _run_list for argparse dispatch.

    Args:
        args: namespace from argparse with attribute channel_id.

    Returns:
        Exit code: 0 = success, 2 = failure.
    """
    return asyncio.run(
        _run_list(
            channel_id=args.channel_id,  # type: ignore[attr-defined]
        )
    )


# ── parser diff ───────────────────────────────────────────────────────────────


async def _run_diff(
    *,
    channel_id: int,
    from_version: int,
    to_version: int,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    """Shallow field-level diff between two parser versions. Returns exit code (0/2)."""
    factory = session_factory or _make_session_factory()

    async with factory() as session:
        repo = ParserRepo(session)
        all_versions = await repo.list_versions(channel_id)

    by_version = {p.version: p for p in all_versions}
    v1 = by_version.get(from_version)
    v2 = by_version.get(to_version)

    if v1 is None or v2 is None:
        missing = []
        if v1 is None:
            missing.append(from_version)
        if v2 is None:
            missing.append(to_version)
        print(
            json.dumps(
                {
                    "status": "not_found",
                    "channel_id": channel_id,
                    "missing_versions": missing,
                }
            ),
            flush=True,
        )
        return 2

    # Shallow diff over top-level keys of regex_table JSONB dicts.
    t1: dict[str, object] = v1.regex_table or {}
    t2: dict[str, object] = v2.regex_table or {}
    all_keys = set(t1) | set(t2)
    diff: dict[str, object] = {}
    for key in all_keys:
        val1 = t1.get(key)
        val2 = t2.get(key)
        if val1 != val2:
            diff[key] = {"from": val1, "to": val2}

    print(
        json.dumps(
            {
                "status": "ok",
                "channel_id": channel_id,
                "from": from_version,
                "to": to_version,
                "diff": diff,
            }
        ),
        flush=True,
    )
    return 0


def run_diff(args: object) -> int:
    """Synchronous wrapper around _run_diff for argparse dispatch.

    Args:
        args: namespace from argparse with attributes:
              channel_id, from_version, to_version.

    Returns:
        Exit code: 0 = success, 2 = failure / not found.
    """
    return asyncio.run(
        _run_diff(
            channel_id=args.channel_id,  # type: ignore[attr-defined]
            from_version=args.from_version,  # type: ignore[attr-defined]
            to_version=args.to_version,  # type: ignore[attr-defined]
        )
    )


# ── parser approve ────────────────────────────────────────────────────────────


async def _run_approve(
    *,
    parser_id: int,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    """Activate a proposed parser. Returns exit code (0/2)."""
    factory = session_factory or _make_session_factory()

    async with factory() as session:
        repo = ParserRepo(session)
        try:
            parser = await repo.activate(parser_id)
            await session.commit()
        except ValueError:
            print(
                json.dumps({"status": "not_found", "parser_id": parser_id}),
                flush=True,
            )
            return 2

    print(
        json.dumps(
            {
                "status": "activated",
                "parser_id": parser.id,
                "channel_id": parser.channel_id,
                "version": parser.version,
            }
        ),
        flush=True,
    )
    return 0


def run_approve(args: object) -> int:
    """Synchronous wrapper around _run_approve for argparse dispatch.

    Args:
        args: namespace from argparse with attribute parser_id.

    Returns:
        Exit code: 0 = success, 2 = not found.
    """
    return asyncio.run(
        _run_approve(
            parser_id=args.parser_id,  # type: ignore[attr-defined]
        )
    )


# ── parser reject ─────────────────────────────────────────────────────────────


async def _run_reject(
    *,
    parser_id: int,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    """Reject a proposed parser. Returns exit code (0/2)."""
    factory = session_factory or _make_session_factory()

    async with factory() as session:
        repo = ParserRepo(session)
        try:
            parser = await repo.reject(parser_id)
            await session.commit()
        except ValueError:
            print(
                json.dumps({"status": "not_found", "parser_id": parser_id}),
                flush=True,
            )
            return 2

    print(
        json.dumps(
            {
                "status": "rejected",
                "parser_id": parser.id,
                "channel_id": parser.channel_id,
                "version": parser.version,
            }
        ),
        flush=True,
    )
    return 0


def run_reject(args: object) -> int:
    """Synchronous wrapper around _run_reject for argparse dispatch.

    Args:
        args: namespace from argparse with attribute parser_id.

    Returns:
        Exit code: 0 = success, 2 = not found.
    """
    return asyncio.run(
        _run_reject(
            parser_id=args.parser_id,  # type: ignore[attr-defined]
        )
    )


# ── parser stats ──────────────────────────────────────────────────────────────


async def _run_stats(
    *,
    channel_id: int,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    """Aggregate induction metrics for a channel. Returns exit code (0/2)."""
    from datetime import datetime

    factory = session_factory or _make_session_factory()

    try:
        async with factory() as session:
            sample_repo = SampleRepo(session)
            parser_repo = ParserRepo(session)
            eval_run_repo = EvalRunRepo(session)

            samples_total = await sample_repo.count(channel_id)
            samples_by_parsed_by = await sample_repo.count_by_parsed_by(channel_id)
            active_parser = await parser_repo.get_active(channel_id)

            active_parser_dict: dict[str, object] | None = None
            if active_parser is not None:
                latest_run = await eval_run_repo.latest_for_parser(active_parser.id)
                latest_eval_dict: dict[str, object] | None = None
                if latest_run is not None:
                    # Giới hạn disagreement_sample tối đa 3 entry để giữ output gọn.
                    disagreement_sample = (latest_run.disagreements or [])[:3]
                    ran_at_str = (
                        latest_run.ran_at.isoformat()
                        if isinstance(latest_run.ran_at, datetime)
                        else str(latest_run.ran_at)
                    )
                    latest_eval_dict = {
                        "samples_total": latest_run.samples_total,
                        "samples_matched": latest_run.samples_matched,
                        "match_rate": round(latest_run.match_rate, 4),
                        "ran_at": ran_at_str,
                        "disagreement_sample": disagreement_sample,
                    }

                activated_at_str = (
                    active_parser.activated_at.isoformat()
                    if isinstance(active_parser.activated_at, datetime)
                    else str(active_parser.activated_at)
                ) if active_parser.activated_at is not None else None

                active_parser_dict = {
                    "id": active_parser.id,
                    "version": active_parser.version,
                    "source": active_parser.source,
                    "activated_at": activated_at_str,
                    "latest_eval": latest_eval_dict,
                }
    except Exception as exc:
        log.exception("Unexpected error in parser stats: %s", exc)
        print(
            json.dumps({"status": "error", "channel_id": channel_id, "error": str(exc)}),
            flush=True,
        )
        return 2

    print(
        json.dumps(
            {
                "status": "ok",
                "channel_id": channel_id,
                "samples_total": samples_total,
                "samples_by_parsed_by": samples_by_parsed_by,
                "active_parser": active_parser_dict,
            }
        ),
        flush=True,
    )
    return 0


def run_stats(args: object) -> int:
    """Synchronous wrapper around _run_stats for argparse dispatch.

    Args:
        args: namespace from argparse with attribute channel_id.

    Returns:
        Exit code: 0 = success, 2 = unexpected error.
    """
    return asyncio.run(
        _run_stats(
            channel_id=args.channel_id,  # type: ignore[attr-defined]
        )
    )

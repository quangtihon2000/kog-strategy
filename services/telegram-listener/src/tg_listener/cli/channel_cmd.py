"""channel set-auto-approve subcommand — manage per-channel auto_approve flag.

Flow:
  1. Upsert the channel row (so it exists in DB).
  2. Set auto_approve to the requested value.
  3. Commit and print a JSON summary to stdout.
"""

from __future__ import annotations

import asyncio
import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_listener.cli.parser_cmd import _make_session_factory
from tg_listener.db.repos.channels import ChannelRepo

log = logging.getLogger(__name__)


# ── bool value parser (argparse type= callable) ───────────────────────────────


def _parse_bool(value: str) -> bool:
    """Parse 'true'/'false'/'1'/'0' (case-insensitive) into bool.

    Args:
        value: raw string from argparse --value argument.

    Returns:
        Python bool.

    Raises:
        argparse.ArgumentTypeError: khi value không hợp lệ.
    """
    import argparse

    normalized = value.strip().lower()
    if normalized in ("true", "1"):
        return True
    if normalized in ("false", "0"):
        return False
    raise argparse.ArgumentTypeError(
        f"Invalid boolean value {value!r}. Use: true / false / 1 / 0"
    )


# ── Core async logic ──────────────────────────────────────────────────────────


async def _run_set_auto_approve(
    *,
    channel_id: int,
    value: bool,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    """Set auto_approve flag for a channel. Returns exit code (0/2).

    Upsert the channel row first to ensure it exists, then update the flag.
    """
    factory = session_factory or _make_session_factory()

    async with factory() as session:
        repo = ChannelRepo(session)
        # Upsert đảm bảo row tồn tại trước khi gọi set_auto_approve.
        await repo.upsert(channel_id, name=f"channel_{channel_id}")
        channel = await repo.set_auto_approve(channel_id, value=value)
        await session.commit()

    print(
        json.dumps(
            {
                "status": "ok",
                "channel_id": channel.id,
                "auto_approve": channel.auto_approve,
            }
        ),
        flush=True,
    )
    return 0


# ── Public entry point called by __main__.py ──────────────────────────────────


def run_set_auto_approve(args: object) -> int:
    """Synchronous wrapper around _run_set_auto_approve for argparse dispatch.

    Args:
        args: namespace from argparse with attributes: channel_id, value.

    Returns:
        Exit code: 0 = success, 2 = failure.
    """
    return asyncio.run(
        _run_set_auto_approve(
            channel_id=args.channel_id,  # type: ignore[attr-defined]
            value=args.value,  # type: ignore[attr-defined]
        )
    )

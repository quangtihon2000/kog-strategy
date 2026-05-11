"""tg-listener CLI entry point.

Usage:
  tg-listener parser induce --channel-id N [--source NAME] [--limit N] [--dry-run]
  tg-listener parser list --channel-id N
  tg-listener parser diff --channel-id N --from V1 --to V2
  tg-listener parser approve <parser-id>
  tg-listener parser reject <parser-id>
  tg-listener parser stats --channel-id N
  tg-listener channel set-auto-approve --channel-id N --value true|false
"""

from __future__ import annotations

import argparse
import logging
import sys

# ── Logging setup ──────────────────────────────────────────────────────────────


def _setup_logging() -> None:
    try:
        from tg_listener.logging_setup import configure_logging

        configure_logging("INFO")
    except Exception:
        logging.basicConfig(level=logging.INFO)


# ── Parser subcommand ──────────────────────────────────────────────────────────


def _add_parser_subcommand(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    parser_cmd = subparsers.add_parser("parser", help="Parser management commands.")
    parser_sub = parser_cmd.add_subparsers(dest="parser_action")
    parser_sub.required = True

    # induce
    induce_cmd = parser_sub.add_parser(
        "induce",
        help="Auto-induce a RegexTable from stored samples for a channel.",
    )
    induce_cmd.add_argument(
        "--channel-id",
        type=int,
        required=True,
        dest="channel_id",
        help="Telegram channel ID (as stored in DB, e.g. -100123).",
    )
    induce_cmd.add_argument(
        "--source",
        type=str,
        default=None,
        dest="source",
        help="Source label for the proposed parser (default: llm_induced).",
    )
    induce_cmd.add_argument(
        "--limit",
        type=int,
        default=None,
        dest="limit",
        help="Max number of samples to load (default: 200).",
    )
    induce_cmd.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        dest="dry_run",
        help="Evaluate only; do not write to DB.",
    )

    # list
    list_cmd = parser_sub.add_parser(
        "list",
        help="List all parser versions for a channel.",
    )
    list_cmd.add_argument(
        "--channel-id",
        type=int,
        required=True,
        dest="channel_id",
        help="Telegram channel ID.",
    )

    # diff
    diff_cmd = parser_sub.add_parser(
        "diff",
        help="Show field-level diff of two parser versions for a channel.",
    )
    diff_cmd.add_argument(
        "--channel-id",
        type=int,
        required=True,
        dest="channel_id",
        help="Telegram channel ID.",
    )
    diff_cmd.add_argument(
        "--from",
        type=int,
        required=True,
        dest="from_version",
        help="Version number of the base parser.",
    )
    diff_cmd.add_argument(
        "--to",
        type=int,
        required=True,
        dest="to_version",
        help="Version number of the target parser.",
    )

    # approve
    approve_cmd = parser_sub.add_parser(
        "approve",
        help="Activate a proposed parser by its ID.",
    )
    approve_cmd.add_argument(
        "parser_id",
        type=int,
        help="Parser row ID to activate.",
    )

    # reject
    reject_cmd = parser_sub.add_parser(
        "reject",
        help="Reject a proposed parser by its ID.",
    )
    reject_cmd.add_argument(
        "parser_id",
        type=int,
        help="Parser row ID to reject.",
    )

    # stats
    stats_cmd = parser_sub.add_parser(
        "stats",
        help="Show induction metrics for a channel (sample counts, active parser, latest eval).",
    )
    stats_cmd.add_argument(
        "--channel-id",
        type=int,
        required=True,
        dest="channel_id",
        help="Telegram channel ID.",
    )


# ── Channel subcommand ─────────────────────────────────────────────────────────


def _add_channel_subcommand(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    from tg_listener.cli.channel_cmd import _parse_bool

    channel_cmd = subparsers.add_parser("channel", help="Channel management commands.")
    channel_sub = channel_cmd.add_subparsers(dest="channel_action")
    channel_sub.required = True

    set_auto_cmd = channel_sub.add_parser(
        "set-auto-approve",
        help="Enable or disable auto-approve for a channel.",
    )
    set_auto_cmd.add_argument(
        "--channel-id",
        type=int,
        required=True,
        dest="channel_id",
        help="Telegram channel ID.",
    )
    set_auto_cmd.add_argument(
        "--value",
        type=_parse_bool,
        required=True,
        dest="value",
        help="true / false / 1 / 0 (case-insensitive).",
    )


# ── Main ──────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: argument list (defaults to sys.argv[1:]).

    Returns:
        Exit code (0 = success, non-zero = error).
    """
    _setup_logging()

    root_parser = argparse.ArgumentParser(
        prog="tg-listener",
        description="tg-listener management CLI.",
    )
    subparsers = root_parser.add_subparsers(dest="command")
    subparsers.required = True

    _add_parser_subcommand(subparsers)
    _add_channel_subcommand(subparsers)

    args = root_parser.parse_args(argv)

    if args.command == "parser":
        if args.parser_action == "induce":
            from tg_listener.cli.parser_cmd import run_induce

            return run_induce(args)
        if args.parser_action == "list":
            from tg_listener.cli.parser_cmd import run_list

            return run_list(args)
        if args.parser_action == "diff":
            from tg_listener.cli.parser_cmd import run_diff

            return run_diff(args)
        if args.parser_action == "approve":
            from tg_listener.cli.parser_cmd import run_approve

            return run_approve(args)
        if args.parser_action == "reject":
            from tg_listener.cli.parser_cmd import run_reject

            return run_reject(args)
        if args.parser_action == "stats":
            from tg_listener.cli.parser_cmd import run_stats

            return run_stats(args)

    if args.command == "channel":
        if args.channel_action == "set-auto-approve":
            from tg_listener.cli.channel_cmd import run_set_auto_approve

            return run_set_auto_approve(args)

    root_parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())

"""Alert hooks for the parser-induction loop.

Phát cảnh báo khi match_rate thấp hơn ngưỡng cho phép.
Chỉ emit log — không gọi bất kỳ external client nào (Slack, HTTP, v.v.).
"""

from __future__ import annotations

import logging

from tg_listener.induction.evaluator import EvalReport

_log = logging.getLogger("tg_listener.induction.alerts")


# ── Alert hooks ───────────────────────────────────────────────────────────────


def maybe_emit_low_match_rate(
    *,
    channel_id: int,
    parser_id: int | None,
    report: EvalReport,
    threshold: float = 0.95,
) -> None:
    """Emit a WARNING log khi match_rate thấp hơn threshold và total > 0.

    Hàm này là diagnostic hook — không block pipeline, không ghi DB.
    Được gọi sau evaluation ở cả acceptable lẫn not_acceptable branch.

    Args:
        channel_id: Telegram channel ID đang được induct.
        parser_id: ID của parser vừa propose, hoặc None nếu induction thất bại.
        report: EvalReport chứa kết quả evaluation.
        threshold: Ngưỡng match_rate tối thiểu (default: 0.95).
    """
    if report.total == 0:
        return
    if report.match_rate >= threshold:
        return

    _log.warning(
        "low_match_rate detected",
        extra={
            "event": "low_match_rate",
            "channel_id": channel_id,
            "parser_id": parser_id,
            "match_rate": report.match_rate,
            "total": report.total,
            "mismatched": report.mismatched,
            "parse_failed": report.parse_failed,
            "timeouts": report.timeouts,
            "threshold": threshold,
        },
    )

"""Time formatting helpers cho KOG Strategy agents.

Convention:
- Storage: unix epoch seconds (int, UTC) — dùng `now_unix()` thay `int(time.time())`
- Display: Asia/Ho_Chi_Minh (UTC+7, không có DST) — dùng `fmt_ict*()` cho mọi output

Không import ngoài stdlib.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ICT = ZoneInfo("Asia/Ho_Chi_Minh")

_MAX_TS = 32503680000  # 3000-01-01 UTC, giới hạn hợp lý


def _validate(ts: int) -> None:
    if ts < 0:
        raise ValueError(f"ts phải >= 0, nhận được {ts}")


def now_unix() -> int:
    """Trả về unix epoch seconds, UTC-aware. Dùng thay int(time.time())."""
    return int(datetime.now(tz=timezone.utc).timestamp())


def to_ict(ts: int) -> datetime:
    """Convert unix seconds → aware datetime ở Asia/Ho_Chi_Minh."""
    _validate(ts)
    return datetime.fromtimestamp(ts, tz=ICT)


def fmt_ict(ts: int) -> str:
    """Format full: '2026-05-13 18:53:42 ICT'. Dùng cho table cell, tooltip."""
    return to_ict(ts).strftime("%Y-%m-%d %H:%M:%S ICT")


def fmt_ict_compact(ts: int) -> str:
    """Format compact: '05-13 18:53'. Dùng cho list view, mobile."""
    return to_ict(ts).strftime("%m-%d %H:%M")


def fmt_ict_date(ts: int) -> str:
    """Chỉ ngày: '2026-05-13'. Dùng cho group header."""
    return to_ict(ts).strftime("%Y-%m-%d")


def fmt_relative(ts: int, *, now: int | None = None) -> str:
    """Relative time tiếng Việt.

    Args:
        ts: Unix timestamp giây (UTC).
        now: Inject clock cho test; mặc định `now_unix()`.

    Returns:
        - < 60s  → 'vừa xong'
        - < 60m  → '{n} phút trước'
        - < 24h  → '{n} giờ trước'
        - < 30d  → '{n} ngày trước'
        - >= 30d → fallback fmt_ict_date(ts)
    """
    _validate(ts)
    ref = now if now is not None else now_unix()
    delta = ref - ts

    if delta < 60:
        return "vừa xong"
    if delta < 3600:
        return f"{delta // 60} phút trước"
    if delta < 86400:
        return f"{delta // 3600} giờ trước"
    days = delta // 86400
    if days < 30:
        return f"{days} ngày trước"
    return fmt_ict_date(ts)

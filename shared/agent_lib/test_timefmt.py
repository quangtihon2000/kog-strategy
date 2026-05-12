"""Tests cho shared/agent_lib/timefmt.py."""

import time
from datetime import datetime, timezone

import pytest

from timefmt import (
    fmt_ict,
    fmt_ict_compact,
    fmt_ict_date,
    fmt_relative,
    now_unix,
    to_ict,
)

# 1747162380 = 2025-05-13 18:53:00 UTC = 2025-05-14 01:53:00 ICT (+7)
_FIXED_TS = 1747162380


class TestNowUnix:
    def test_returns_int(self):
        assert isinstance(now_unix(), int)

    def test_close_to_time_time(self):
        assert abs(now_unix() - int(time.time())) <= 2

    def test_aware_utc_no_raise(self):
        dt = datetime.fromtimestamp(now_unix(), tz=timezone.utc)
        assert dt.year >= 2025

    def test_utc_timezone(self):
        dt = datetime.fromtimestamp(now_unix(), tz=timezone.utc)
        assert dt.tzinfo is not None


class TestToIct:
    def test_offset_plus7(self):
        # UTC 18:53 → ICT 01:53 ngày hôm sau
        dt = to_ict(_FIXED_TS)
        assert dt.hour == 1
        assert dt.minute == 53
        assert dt.day == 14
        assert dt.month == 5
        assert dt.year == 2025

    def test_returns_aware_datetime(self):
        dt = to_ict(_FIXED_TS)
        assert dt.tzinfo is not None

    def test_negative_ts_raises(self):
        with pytest.raises(ValueError):
            to_ict(-1)


class TestFmtIct:
    def test_format_shape(self):
        result = fmt_ict(_FIXED_TS)
        # Kiểm tra pattern: YYYY-MM-DD HH:MM:SS ICT
        assert result == "2025-05-14 01:53:00 ICT"

    def test_ends_with_ict(self):
        assert fmt_ict(_FIXED_TS).endswith("ICT")

    def test_length(self):
        # "2025-05-14 01:53:00 ICT" = 23 chars
        assert len(fmt_ict(_FIXED_TS)) == 23


class TestFmtIctCompact:
    def test_format_shape(self):
        result = fmt_ict_compact(_FIXED_TS)
        assert result == "05-14 01:53"

    def test_pattern_mm_dd_hh_mm(self):
        result = fmt_ict_compact(_FIXED_TS)
        parts = result.split(" ")
        assert len(parts) == 2
        assert len(parts[0]) == 5  # MM-DD
        assert len(parts[1]) == 5  # HH:MM


class TestFmtIctDate:
    def test_date_only(self):
        assert fmt_ict_date(_FIXED_TS) == "2025-05-14"

    def test_length(self):
        assert len(fmt_ict_date(_FIXED_TS)) == 10


class TestFmtRelative:
    _BASE = 1_700_000_000  # arbitrary reference "now"

    def test_just_now_0s(self):
        assert fmt_relative(self._BASE, now=self._BASE) == "vừa xong"

    def test_just_now_30s(self):
        assert fmt_relative(self._BASE - 30, now=self._BASE) == "vừa xong"

    def test_minutes(self):
        assert fmt_relative(self._BASE - 5 * 60, now=self._BASE) == "5 phút trước"

    def test_hours(self):
        assert fmt_relative(self._BASE - 3 * 3600, now=self._BASE) == "3 giờ trước"

    def test_days(self):
        assert fmt_relative(self._BASE - 5 * 86400, now=self._BASE) == "5 ngày trước"

    def test_fallback_date_60d(self):
        ts = self._BASE - 60 * 86400
        result = fmt_relative(ts, now=self._BASE)
        # Phải là ngày tháng format YYYY-MM-DD, không phải relative
        assert len(result) == 10
        assert result.count("-") == 2

    def test_negative_ts_raises(self):
        with pytest.raises(ValueError):
            fmt_relative(-1, now=self._BASE)

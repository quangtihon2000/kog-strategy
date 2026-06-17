"""Hand-curated fixture corpus for Channel B parser (Tier 2).

Channel B uses messy Vietnamese-format signals with mixed keywords, emojis,
and flexible ordering (symbol before or after the side keyword).
"""

from __future__ import annotations

from dataclasses import dataclass

from tg_listener.models import ParsedSignalFields


@dataclass(frozen=True)
class ChannelBFixture:
    id: str
    text: str
    expected: ParsedSignalFields | None


CHANNEL_B_FIXTURES: tuple[ChannelBFixture, ...] = (
    # ── Valid signals — Vietnamese keywords ──────────────────────────────
    ChannelBFixture(
        id="b_mua_symbol_before",
        text="BTC mua vùng 67400 cắt lỗ 66900 mục tiêu 68200",
        expected=ParsedSignalFields(
            symbol="BTC", side="LONG", entry=67400.0, sl=66900.0,
            tp=[68200.0],
        ),
    ),
    ChannelBFixture(
        id="b_mua_symbol_after",
        text="Mua BTC vùng 67400 cắt lỗ 66900 mục tiêu 68200 và 69000",
        expected=ParsedSignalFields(
            symbol="BTC", side="LONG", entry=67400.0, sl=66900.0,
            tp=[68200.0, 69000.0],
        ),
    ),
    ChannelBFixture(
        id="b_ban_symbol_before",
        text="XAUUSD bán entry 2358 cắt lỗ 2365 mục tiêu 2350 2342",
        expected=ParsedSignalFields(
            symbol="XAUUSD", side="SHORT", entry=2358.0, sl=2365.0,
            tp=[2350.0, 2342.0],
        ),
    ),
    ChannelBFixture(
        id="b_ban_symbol_after",
        text="Bán ETHUSDT entry 3520 sl 3580 target 3450",
        expected=ParsedSignalFields(
            symbol="ETHUSDT", side="SHORT", entry=3520.0, sl=3580.0,
            tp=[3450.0],
        ),
    ),
    ChannelBFixture(
        id="b_emoji_decorated_long",
        text="🟢 Mua XAUUSD 🟢 entry 2350 sl 2342 tp 2362 và 2370",
        expected=ParsedSignalFields(
            symbol="XAUUSD", side="LONG", entry=2350.0, sl=2342.0,
            tp=[2362.0, 2370.0],
        ),
    ),
    ChannelBFixture(
        id="b_emoji_decorated_short",
        text="🔴 Bán ETH 🔴 entry 3520 cắt lỗ 3580 mục tiêu 3450/3380",
        expected=ParsedSignalFields(
            symbol="ETH", side="SHORT", entry=3520.0, sl=3580.0,
            tp=[3450.0, 3380.0],
        ),
    ),
    ChannelBFixture(
        id="b_em_dash_separator",
        text="Mua XAUUSD — entry 2350 — cắt lỗ 2342 — mục tiêu 2362 và 2375",
        expected=ParsedSignalFields(
            symbol="XAUUSD", side="LONG", entry=2350.0, sl=2342.0,
            tp=[2362.0, 2375.0],
        ),
    ),
    ChannelBFixture(
        id="b_tp_slash_separated",
        text="BTC mua vùng 67400 sl 66900 mục tiêu 68200/69000/70000",
        expected=ParsedSignalFields(
            symbol="BTC", side="LONG", entry=67400.0, sl=66900.0,
            tp=[68200.0, 69000.0, 70000.0],
        ),
    ),
    ChannelBFixture(
        id="b_tp_comma_separated",
        text="ETH mua entry 3520 cắt lỗ 3460 target 3580,3640,3720",
        expected=ParsedSignalFields(
            symbol="ETH", side="LONG", entry=3520.0, sl=3460.0,
            tp=[3580.0, 3640.0, 3720.0],
        ),
    ),
    ChannelBFixture(
        id="b_with_don_bay",
        text="BTC mua vùng 67400 cắt lỗ 66900 mục tiêu 68200 đòn bẩy x10",
        expected=ParsedSignalFields(
            symbol="BTC", side="LONG", entry=67400.0, sl=66900.0,
            tp=[68200.0], leverage=10,
        ),
    ),
    ChannelBFixture(
        id="b_with_lev",
        text="Bán SOLUSDT entry 142 sl 148 target 138 lev 5",
        expected=ParsedSignalFields(
            symbol="SOLUSDT", side="SHORT", entry=142.0, sl=148.0,
            tp=[138.0], leverage=5,
        ),
    ),
    ChannelBFixture(
        id="b_long_keyword",
        text="Long BTCUSDT entry 67500 sl 66800 tp 68200 và 69000",
        expected=ParsedSignalFields(
            symbol="BTCUSDT", side="LONG", entry=67500.0, sl=66800.0,
            tp=[68200.0, 69000.0],
        ),
    ),
    ChannelBFixture(
        id="b_short_keyword",
        text="Short ETHUSDT entry 3520 sl 3580 tp 3450 3380",
        expected=ParsedSignalFields(
            symbol="ETHUSDT", side="SHORT", entry=3520.0, sl=3580.0,
            tp=[3450.0, 3380.0],
        ),
    ),
    ChannelBFixture(
        id="b_buy_keyword",
        text="buy XAUUSD entry 2350 sl 2342 tp 2362",
        expected=ParsedSignalFields(
            symbol="XAUUSD", side="LONG", entry=2350.0, sl=2342.0,
            tp=[2362.0],
        ),
    ),
    ChannelBFixture(
        id="b_sell_keyword",
        text="sell BTC entry 67500 sl 68200 tp 66800",
        expected=ParsedSignalFields(
            symbol="BTC", side="SHORT", entry=67500.0, sl=68200.0,
            tp=[66800.0],
        ),
    ),
    ChannelBFixture(
        id="b_comma_thousands_vn",
        text="Mua XAUUSD vùng 2,350 cắt lỗ 2,342 mục tiêu 2,362 và 2,375",
        expected=ParsedSignalFields(
            symbol="XAUUSD", side="LONG", entry=2350.0, sl=2342.0,
            tp=[2362.0, 2375.0],
        ),
    ),
    ChannelBFixture(
        id="b_entry_zone_vn",
        text="Mua BTC vùng 67300-67500 cắt lỗ 66900 mục tiêu 68200",
        expected=ParsedSignalFields(
            symbol="BTC", side="LONG", entry=(67300.0, 67500.0), sl=66900.0,
            tp=[68200.0],
        ),
    ),
    # ── Invalid / unparseable ─────────────────────────────────────────────
    ChannelBFixture(
        id="b_missing_sl",
        text="Mua BTC vùng 67400 mục tiêu 68200",
        expected=None,
    ),
    ChannelBFixture(
        id="b_missing_tp",
        text="Mua BTC vùng 67400 cắt lỗ 66900",
        expected=None,
    ),
    ChannelBFixture(
        id="b_missing_entry",
        text="Mua BTC cắt lỗ 66900 mục tiêu 68200",
        expected=None,
    ),
    ChannelBFixture(
        id="b_missing_side",
        text="BTC vùng 67400 cắt lỗ 66900 mục tiêu 68200",
        expected=None,
    ),
    ChannelBFixture(
        id="b_plain_chat_vn",
        text="Mọi người ơi BTC hôm nay lên hay xuống nhỉ?",
        expected=None,
    ),
    ChannelBFixture(
        id="b_update_message",
        text="BTC mua từ 67400 — tp1 đã hit, dời sl về entry",
        expected=None,
    ),
    ChannelBFixture(
        id="b_channel_a_format",
        # Channel B also accepts Long/Short/tp/entry/sl keywords, so it CAN
        # parse this clean EN format — both parsers can handle it.
        text="LONG XAUUSD\nEntry: 2350\nSL: 2342\nTP1: 2362\nTP2: 2370",
        expected=ParsedSignalFields(
            symbol="XAUUSD", side="LONG", entry=2350.0, sl=2342.0,
            tp=[2362.0, 2370.0],
        ),
    ),
    ChannelBFixture(
        id="b_empty_string",
        text="",
        expected=None,
    ),
    ChannelBFixture(
        id="b_emoji_only",
        text="🟢🟢🟢🚀🚀🚀",
        expected=None,
    ),
)


def valid_fixtures() -> tuple[ChannelBFixture, ...]:
    return tuple(f for f in CHANNEL_B_FIXTURES if f.expected is not None)


def invalid_fixtures() -> tuple[ChannelBFixture, ...]:
    return tuple(f for f in CHANNEL_B_FIXTURES if f.expected is None)

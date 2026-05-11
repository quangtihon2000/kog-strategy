"""Hand-curated fixture corpus for Channel A parser (Tier 2).

Channel A uses a clean, structured English-format layout.
Each fixture: (id, text, expected: ParsedSignalFields | None).
"""

from __future__ import annotations

from dataclasses import dataclass

from tg_listener.models import ParsedSignalFields


@dataclass(frozen=True)
class ChannelAFixture:
    id: str
    text: str
    expected: ParsedSignalFields | None


CHANNEL_A_FIXTURES: tuple[ChannelAFixture, ...] = (
    # ── Valid signals ─────────────────────────────────────────────────────
    ChannelAFixture(
        id="a_basic_long",
        text="LONG XAUUSD\nEntry: 2350\nSL: 2342\nTP1: 2362\nTP2: 2370",
        expected=ParsedSignalFields(
            symbol="XAUUSD", side="LONG", entry=2350.0, sl=2342.0,
            tp=[2362.0, 2370.0],
        ),
    ),
    ChannelAFixture(
        id="a_basic_short",
        text="SHORT BTCUSDT\nEntry: 67500\nSL: 68200\nTP1: 66800\nTP2: 66000",
        expected=ParsedSignalFields(
            symbol="BTCUSDT", side="SHORT", entry=67500.0, sl=68200.0,
            tp=[66800.0, 66000.0],
        ),
    ),
    ChannelAFixture(
        id="a_with_leverage",
        text="LONG ETHUSDT\nEntry: 3520\nSL: 3460\nTP1: 3580\nTP2: 3640\nTP3: 3720\nLeverage: 10",
        expected=ParsedSignalFields(
            symbol="ETHUSDT", side="LONG", entry=3520.0, sl=3460.0,
            tp=[3580.0, 3640.0, 3720.0], leverage=10,
        ),
    ),
    ChannelAFixture(
        id="a_entry_zone",
        text="LONG XAUUSD\nEntry zone 2350-2355\nSL 2342\nTP1 2362\nTP2 2370",
        expected=ParsedSignalFields(
            symbol="XAUUSD", side="LONG", entry=(2350.0, 2355.0), sl=2342.0,
            tp=[2362.0, 2370.0],
        ),
    ),
    ChannelAFixture(
        id="a_entry_zone_colon",
        text="LONG XAUUSD\nEntry: 2350-2355\nSL: 2342\nTP1: 2362",
        expected=ParsedSignalFields(
            symbol="XAUUSD", side="LONG", entry=(2350.0, 2355.0), sl=2342.0,
            tp=[2362.0],
        ),
    ),
    ChannelAFixture(
        id="a_single_tp",
        text="SHORT SOLUSDT\nEntry: 142\nSL: 148\nTP: 138",
        expected=ParsedSignalFields(
            symbol="SOLUSDT", side="SHORT", entry=142.0, sl=148.0,
            tp=[138.0],
        ),
    ),
    ChannelAFixture(
        id="a_five_tps",
        text=(
            "LONG BTCUSDT\nEntry: 67500\nSL: 66800\n"
            "TP1: 68200\nTP2: 69000\nTP3: 70000\nTP4: 71000\nTP5: 72000"
        ),
        expected=ParsedSignalFields(
            symbol="BTCUSDT", side="LONG", entry=67500.0, sl=66800.0,
            tp=[68200.0, 69000.0, 70000.0, 71000.0, 72000.0],
        ),
    ),
    ChannelAFixture(
        id="a_comma_thousands",
        text="LONG XAUUSD\nEntry: 2,350\nSL: 2,342\nTP1: 2,362\nTP2: 2,370",
        expected=ParsedSignalFields(
            symbol="XAUUSD", side="LONG", entry=2350.0, sl=2342.0,
            tp=[2362.0, 2370.0],
        ),
    ),
    ChannelAFixture(
        id="a_lowercase_keywords",
        text="long btcusdt\nentry: 67500\nsl: 66800\ntp1: 68200\ntp2: 69000",
        expected=ParsedSignalFields(
            symbol="BTCUSDT", side="LONG", entry=67500.0, sl=66800.0,
            tp=[68200.0, 69000.0],
        ),
    ),
    ChannelAFixture(
        id="a_equals_separator",
        text="LONG ETHUSDT\nEntry=3520\nSL=3460\nTP1=3580\nTP2=3640",
        expected=ParsedSignalFields(
            symbol="ETHUSDT", side="LONG", entry=3520.0, sl=3460.0,
            tp=[3580.0, 3640.0],
        ),
    ),
    ChannelAFixture(
        id="a_dash_separator",
        text="LONG ETHUSDT\nEntry-3520\nSL-3460\nTP1-3580",
        expected=ParsedSignalFields(
            symbol="ETHUSDT", side="LONG", entry=3520.0, sl=3460.0,
            tp=[3580.0],
        ),
    ),
    ChannelAFixture(
        id="a_k_suffix_entry",
        text="LONG BTCUSDT\nEntry: 67.5k\nSL: 66.8k\nTP1: 68.2k",
        expected=ParsedSignalFields(
            symbol="BTCUSDT", side="LONG", entry=67500.0, sl=66800.0,
            tp=[68200.0],
        ),
    ),
    ChannelAFixture(
        id="a_lev_shorthand",
        text="LONG SOLUSDT\nEntry: 142.5\nSL: 138\nTP: 148\nTP2: 154\nLev: 20",
        expected=ParsedSignalFields(
            symbol="SOLUSDT", side="LONG", entry=142.5, sl=138.0,
            tp=[148.0, 154.0], leverage=20,
        ),
    ),
    ChannelAFixture(
        id="a_lev_x_prefix",
        text="SHORT BNBUSDT\nEntry: 612\nSL: 624\nTP1: 600\nTP2: 588\nx20",
        expected=ParsedSignalFields(
            symbol="BNBUSDT", side="SHORT", entry=612.0, sl=624.0,
            tp=[600.0, 588.0], leverage=20,
        ),
    ),
    ChannelAFixture(
        id="a_emoji_decorated",
        text="🟢 LONG ETH 🟢\nEntry: 3520\nSL: 3460\nTP1: 3580\nTP2: 3640\nTP3: 3720",
        expected=ParsedSignalFields(
            symbol="ETH", side="LONG", entry=3520.0, sl=3460.0,
            tp=[3580.0, 3640.0, 3720.0],
        ),
    ),
    ChannelAFixture(
        id="a_inline_format",
        text="LONG BTCUSDT Entry: 67500 SL: 66800 TP1: 68200 TP2: 69000 TP3: 70000",
        expected=ParsedSignalFields(
            symbol="BTCUSDT", side="LONG", entry=67500.0, sl=66800.0,
            tp=[68200.0, 69000.0, 70000.0],
        ),
    ),
    ChannelAFixture(
        id="a_decimal_entry",
        text="LONG XAUUSD\nEntry: 2350.50\nSL: 2342.00\nTP1: 2362.00\nTP2: 2375.50",
        expected=ParsedSignalFields(
            symbol="XAUUSD", side="LONG", entry=2350.5, sl=2342.0,
            tp=[2362.0, 2375.5],
        ),
    ),
    # ── Invalid / unparseable ─────────────────────────────────────────────
    ChannelAFixture(
        id="a_missing_sl",
        text="LONG XAUUSD\nEntry: 2350\nTP1: 2362\nTP2: 2370",
        expected=None,
    ),
    ChannelAFixture(
        id="a_missing_tp",
        text="LONG XAUUSD\nEntry: 2350\nSL: 2342",
        expected=None,
    ),
    ChannelAFixture(
        id="a_missing_entry",
        text="LONG XAUUSD\nSL: 2342\nTP1: 2362",
        expected=None,
    ),
    ChannelAFixture(
        id="a_missing_side",
        text="XAUUSD\nEntry: 2350\nSL: 2342\nTP1: 2362",
        expected=None,
    ),
    ChannelAFixture(
        id="a_plain_chat",
        text="What do you think about BTC today? Looks bullish to me!",
        expected=None,
    ),
    ChannelAFixture(
        id="a_tp_update_only",
        text="TP1 hit at 2362 🎯 Moving SL to breakeven",
        expected=None,
    ),
    ChannelAFixture(
        id="a_wrong_format_vn",
        text="Mua XAUUSD vùng 2350 cắt lỗ 2342 mục tiêu 2362",
        expected=None,
    ),
    ChannelAFixture(
        id="a_empty_string",
        text="",
        expected=None,
    ),
)


def valid_fixtures() -> tuple[ChannelAFixture, ...]:
    return tuple(f for f in CHANNEL_A_FIXTURES if f.expected is not None)


def invalid_fixtures() -> tuple[ChannelAFixture, ...]:
    return tuple(f for f in CHANNEL_A_FIXTURES if f.expected is None)

"""Hand-curated fixture corpus for Tier 1 (and later tiers).

Each fixture is `(id, text, expected_pass, kind, reason)`:
- `expected_pass=True`  → Tier 1 should let it through.
- `expected_pass=False` → Tier 1 should reject; `reason` is the expected
  `Tier1Decision.reason` label (used to verify the cascade rejects for the
  *right* reason, not just any reason).

Texts are anonymised paraphrases of real channel content seen in
`signals:rejected_sample` audits and the channels listed in spec §1.
Vietnamese + English mix is intentional — the listener is bilingual.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tier1Fixture:
    id: str
    text: str
    expected_pass: bool
    kind: str
    reason: str | None  # expected Tier1Decision.reason when expected_pass=False


FIXTURES: tuple[Tier1Fixture, ...] = (
    # ── Real signals (should PASS) ────────────────────────────────────────
    Tier1Fixture(
        id="signal_en_clean",
        text="LONG BTCUSDT\nEntry: 67500\nSL: 66800\nTP1: 68200\nTP2: 69000\nTP3: 70000",
        expected_pass=True,
        kind="signal_en",
        reason=None,
    ),
    Tier1Fixture(
        id="signal_en_short",
        text="SHORT ETHUSDT\nEntry 3520\nSL 3580\nTP1 3450\nTP2 3380",
        expected_pass=True,
        kind="signal_en",
        reason=None,
    ),
    Tier1Fixture(
        id="signal_en_lev",
        text="LONG SOLUSDT Entry: 142.5 SL: 138 TP: 148 154 Leverage: 10",
        expected_pass=True,
        kind="signal_en",
        reason=None,
    ),
    Tier1Fixture(
        id="signal_en_zone",
        text="LONG XAUUSD\nEntry zone 2350-2355\nSL 2342\nTP1 2362\nTP2 2370",
        expected_pass=True,
        kind="signal_en",
        reason=None,
    ),
    Tier1Fixture(
        id="signal_vn_basic",
        text="Mua BTC vùng 67400 cắt lỗ 66900 mục tiêu 68200 và 69000",
        expected_pass=True,
        kind="signal_vn",
        reason=None,
    ),
    Tier1Fixture(
        id="signal_vn_sell",
        text="Bán XAUUSD entry 2358 sl 2365 tp 2350 2342 — ưu tiên scalp ngắn",
        expected_pass=True,
        kind="signal_vn",
        reason=None,
    ),
    Tier1Fixture(
        id="signal_mix_lang",
        text="LONG BTCUSDT — entry vùng 67200, cắt lỗ 66500, target 68000 / 69500",
        expected_pass=True,
        kind="signal_mix",
        reason=None,
    ),
    Tier1Fixture(
        id="signal_with_emoji_decor",
        text="🟢 LONG ETH 🟢\nEntry: 3520\nStop: 3460\nTP1: 3580\nTP2: 3640\nTP3: 3720",
        expected_pass=True,
        kind="signal_decor",
        reason=None,
    ),
    Tier1Fixture(
        id="signal_lowercase",
        text="long btc entry 67500 sl 66800 tp 68500 69500 70500",
        expected_pass=True,
        kind="signal_en",
        reason=None,
    ),
    Tier1Fixture(
        id="signal_with_lev_short",
        text="SHORT BNBUSDT entry 612 sl 624 tp1 600 tp2 588 lev x20",
        expected_pass=True,
        kind="signal_en",
        reason=None,
    ),
    # ── Anti-head rejects ─────────────────────────────────────────────────
    Tier1Fixture(
        id="anti_head_gm",
        text="GM team! hôm nay BTC entry 67500 sl 66800 tp 68500",
        expected_pass=False,
        kind="greeting",
        reason="anti_head_keyword",
    ),
    Tier1Fixture(
        id="anti_head_recap",
        text="Recap tuần qua: BTC long entry 67000 đã chạm tp 69000",
        expected_pass=False,
        kind="recap",
        reason="anti_head_keyword",
    ),
    Tier1Fixture(
        id="anti_head_phantich",
        text="Phân tích BTC chu kỳ 4h cho thấy cấu trúc tích lũy 67000-68000",
        expected_pass=False,
        kind="analysis",
        reason="anti_head_keyword",
    ),
    Tier1Fixture(
        id="anti_head_vip",
        text="VIP signal channel — long ETH entry 3520 sl 3460 tp 3600",
        expected_pass=False,
        kind="promo",
        reason="anti_head_keyword",
    ),
    Tier1Fixture(
        id="anti_head_inbox",
        text="Inbox em để nhận sl tp entry chi tiết của lệnh long BTC 67500",
        expected_pass=False,
        kind="promo",
        reason="anti_head_keyword",
    ),
    Tier1Fixture(
        id="anti_head_view_tuan",
        text="View tuần BTC: kỳ vọng long từ 65000, sl 63500, tp 70000",
        expected_pass=False,
        kind="weekly_view",
        reason="anti_head_keyword",
    ),
    # ── Anti-anywhere rejects (position updates) ──────────────────────────
    Tier1Fixture(
        id="anti_any_tp1_hit",
        text="BTC long từ 67500 — TP1 hit ở 68200, đã dời sl về entry",
        expected_pass=False,
        kind="update",
        reason="anti_anywhere_keyword",
    ),
    Tier1Fixture(
        id="anti_any_sl_hit",
        text="ETH short entry 3580 — SL hit ở 3620, dừng lệnh",
        expected_pass=False,
        kind="update",
        reason="anti_anywhere_keyword",
    ),
    Tier1Fixture(
        id="anti_any_dong_lenh",
        text="BTC long 67500 — đóng lệnh tại 68500, lời 1000 pip",
        expected_pass=False,
        kind="update",
        reason="anti_anywhere_keyword",
    ),
    Tier1Fixture(
        id="anti_any_move_sl",
        text="BTC long entry 67500 sl 66800 tp 68500 — move sl về 67500 sau khi chạm 68000",
        expected_pass=False,
        kind="update",
        reason="anti_anywhere_keyword",
    ),
    Tier1Fixture(
        id="anti_any_breakeven",
        text="ETH long entry 3520 — sl breakeven, target 3600 và 3700",
        expected_pass=False,
        kind="update",
        reason="anti_anywhere_keyword",
    ),
    Tier1Fixture(
        id="anti_any_cancel",
        text="Cancel order BTC 67500 vì đã phá cấu trúc — entry sl tp đều invalid",
        expected_pass=False,
        kind="update",
        reason="anti_anywhere_keyword",
    ),
    # ── Plain chat / off-topic ────────────────────────────────────────────
    Tier1Fixture(
        id="chat_question",
        text="Mọi người ơi BTC hôm nay sao thế? Có ai đang giữ lệnh không?",
        expected_pass=False,
        kind="chat",
        reason="no_side_keyword",
    ),
    Tier1Fixture(
        id="chat_news_headline",
        text="Fed quyết định giữ nguyên lãi suất, thị trường phản ứng tích cực ngắn hạn",
        expected_pass=False,
        kind="news",
        reason="no_side_keyword",
    ),
    Tier1Fixture(
        id="chat_thanks",
        # "hôm qua" lands inside the 40-char head window, so this trips
        # anti_head before reaching the side-keyword check — the desired
        # outcome (recap-style thanks).
        text="Cảm ơn admin lệnh hôm qua chốt được nhiều quá ạ 🙏🙏🙏",
        expected_pass=False,
        kind="chat",
        reason="anti_head_keyword",
    ),
    Tier1Fixture(
        id="chat_emoji_only",
        text="🔥🔥🔥🚀🚀🚀💎💎💎",
        expected_pass=False,
        kind="chat",
        reason="too_short",
    ),
    Tier1Fixture(
        id="chat_one_word",
        text="ok",
        expected_pass=False,
        kind="chat",
        reason="too_short",
    ),
    # ── Edge cases ────────────────────────────────────────────────────────
    Tier1Fixture(
        id="edge_empty",
        text="",
        expected_pass=False,
        kind="empty",
        reason="empty_text",
    ),
    Tier1Fixture(
        id="edge_whitespace_only",
        text="          ",
        expected_pass=False,
        kind="empty",
        reason="too_short",
    ),
    Tier1Fixture(
        id="edge_side_no_price_kw",
        text="long term holders đang accumulate BTC trong vùng giá hiện tại 67000",
        expected_pass=False,
        kind="edge",
        reason="no_price_keyword",
    ),
    Tier1Fixture(
        id="edge_price_kw_no_side",
        text="Đặt lệnh entry 67500 sl 66800 tp 68500 — chờ confirm rồi vào",
        expected_pass=False,
        kind="edge",
        reason="no_side_keyword",
    ),
    Tier1Fixture(
        id="edge_no_number",
        text="Long BTC entry sl tp đã có trong file pinned bên trên kênh nhé team",
        expected_pass=False,
        kind="edge",
        reason="no_number",
    ),
    Tier1Fixture(
        id="edge_single_digit_only",
        text="Long BTC entry 7 sl 6 tp 8 — copy demo cho ai mới vào",
        expected_pass=False,
        kind="edge",
        reason="no_number",
    ),
    Tier1Fixture(
        id="edge_too_long",
        # 1500+ chars wall of text containing all positives.
        text=("long BTC entry 67500 sl 66800 tp 68500 " + "lorem ipsum " * 200),
        expected_pass=False,
        kind="edge",
        reason="too_long",
    ),
    Tier1Fixture(
        id="edge_substring_buy_in_word",
        # "buy" must not match inside "buying" — but spec POSITIVE_SIDE
        # uses substring; we use word boundaries. This fixture should
        # therefore reject because no real side keyword and no price kw.
        text="People are buying ABC token aggressively this week, volume up",
        expected_pass=False,
        kind="edge",
        reason="no_side_keyword",
    ),
)


def signals() -> tuple[Tier1Fixture, ...]:
    return tuple(f for f in FIXTURES if f.expected_pass)


def non_signals() -> tuple[Tier1Fixture, ...]:
    return tuple(f for f in FIXTURES if not f.expected_pass)

"""Tier 0 unit tests — covers the eight decision branches in spec §5.2.

The tier is Telethon-agnostic, so each test builds an `EventMetadata` +
`ChannelPolicy` directly. Branch coverage is the goal: every `return` in
`evaluate` plus the policy/chat mismatch guard.
"""

from __future__ import annotations

import pytest

from tg_listener.tiers.tier0_metadata import (
    ChannelPolicy,
    EventMetadata,
    Tier0Decision,
    evaluate,
)

CHAT_ID = 1001
ADMIN_ID = 555
NON_ADMIN_ID = 999


def _meta(**overrides: object) -> EventMetadata:
    base: dict[str, object] = {
        "chat_id": CHAT_ID,
        "message_id": 42,
        "sender_id": ADMIN_ID,
        "text": "LONG BTCUSDT entry 67500 sl 66800 tp 68500",
    }
    base.update(overrides)
    return EventMetadata(**base)  # type: ignore[arg-type]


def _broadcast_policy(**overrides: object) -> ChannelPolicy:
    base: dict[str, object] = {
        "chat_id": CHAT_ID,
        "is_broadcast": True,
        "allow_forward": False,
    }
    base.update(overrides)
    return ChannelPolicy(**base)  # type: ignore[arg-type]


def _group_policy(**overrides: object) -> ChannelPolicy:
    base: dict[str, object] = {
        "chat_id": CHAT_ID,
        "is_broadcast": False,
        "allow_forward": False,
        "admin_user_ids": frozenset({ADMIN_ID}),
    }
    base.update(overrides)
    return ChannelPolicy(**base)  # type: ignore[arg-type]


# ── Happy paths ──────────────────────────────────────────────────────────


def test_broadcast_normal_message_passes() -> None:
    d = evaluate(_meta(), _broadcast_policy())
    assert d == Tier0Decision(action="pass", reason="ok")


def test_group_admin_sender_passes() -> None:
    d = evaluate(_meta(sender_id=ADMIN_ID), _group_policy())
    assert d.action == "pass"
    assert d.reason == "ok"


def test_broadcast_ignores_admin_set() -> None:
    # Even with an unknown sender, broadcast channels skip admin filter.
    policy = _broadcast_policy()
    d = evaluate(_meta(sender_id=NON_ADMIN_ID), policy)
    assert d.action == "pass"


# ── Drops ────────────────────────────────────────────────────────────────


def test_service_message_drops_first() -> None:
    # Service messages drop even if they look like signals — they are
    # always Telegram chrome (joins, pins, etc.), never trade content.
    d = evaluate(_meta(is_service_message=True), _broadcast_policy())
    assert d.action == "drop"
    assert d.reason == "service_message"


def test_empty_no_media_drops() -> None:
    d = evaluate(_meta(text=""), _broadcast_policy())
    assert d.action == "drop"
    assert d.reason == "empty_no_media"


def test_whitespace_only_drops() -> None:
    d = evaluate(_meta(text="   \n\t   "), _broadcast_policy())
    assert d.action == "drop"
    assert d.reason == "empty_no_media"


def test_empty_text_with_photo_routes_to_ocr() -> None:
    d = evaluate(_meta(text="", has_photo=True), _broadcast_policy())
    assert d.action == "needs_ocr"
    assert d.reason == "empty_text_with_photo"


def test_forwarded_disallowed_drops() -> None:
    d = evaluate(_meta(is_forwarded=True), _broadcast_policy())
    assert d.action == "drop"
    assert d.reason == "forwarded_disallowed"


def test_forwarded_allowed_passes() -> None:
    policy = _broadcast_policy(allow_forward=True)
    d = evaluate(_meta(is_forwarded=True), policy)
    assert d.action == "pass"


def test_non_admin_sender_drops_in_group() -> None:
    d = evaluate(_meta(sender_id=NON_ADMIN_ID), _group_policy())
    assert d.action == "drop"
    assert d.reason == "non_admin_sender"


def test_missing_sender_drops_in_group() -> None:
    d = evaluate(_meta(sender_id=None), _group_policy())
    assert d.action == "drop"
    assert d.reason == "non_admin_sender"


# ── Update fork ──────────────────────────────────────────────────────────


def test_reply_routes_to_update() -> None:
    d = evaluate(_meta(reply_to_msg_id=99), _broadcast_policy())
    assert d.action == "update"
    assert d.reason == "reply_to_message"


def test_reply_takes_priority_over_forward() -> None:
    # A reply that is also a forward is still an update — replies are
    # explicitly modelled as position updates regardless of forward state.
    d = evaluate(
        _meta(reply_to_msg_id=99, is_forwarded=True),
        _broadcast_policy(),
    )
    assert d.action == "update"


def test_service_takes_priority_over_reply() -> None:
    d = evaluate(
        _meta(is_service_message=True, reply_to_msg_id=99),
        _broadcast_policy(),
    )
    assert d.action == "drop"
    assert d.reason == "service_message"


# ── Wiring guards ────────────────────────────────────────────────────────


def test_mismatched_chat_id_raises() -> None:
    meta = _meta(chat_id=CHAT_ID + 1)
    with pytest.raises(ValueError, match="policy/chat mismatch"):
        evaluate(meta, _broadcast_policy())


def test_event_metadata_is_frozen() -> None:
    meta = _meta()
    with pytest.raises(Exception):  # noqa: B017
        meta.text = "mutated"  # type: ignore[misc]


def test_channel_policy_rejects_unknown_field() -> None:
    # extra="forbid" — guards against typos in callers building policies
    # from yaml/admin loader output.
    with pytest.raises(Exception):  # noqa: B017
        ChannelPolicy(  # type: ignore[call-arg]
            chat_id=CHAT_ID,
            is_broadcast=True,
            unknown_field=True,
        )

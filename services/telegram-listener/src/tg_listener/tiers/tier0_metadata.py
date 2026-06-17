"""Tier 0 — Telegram metadata filter. Spec section 5.2.

Reject before reading text: service messages, empty media-less posts,
non-admin posters in groups, forwards (unless allowed), replies (treated as
updates).

The tier is intentionally Telethon-agnostic: it operates on the small
`EventMetadata` shape defined here so unit tests can run without an MTProto
client and so the listener can construct the metadata once and pass it
through the cascade.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Tier0Action = Literal["pass", "drop", "needs_ocr", "update"]


class EventMetadata(BaseModel):
    """Telethon event fields needed by Tier 0.

    Built once per event by the listener wrapper around Telethon's
    `events.NewMessage.Event` — keeping the tier decoupled from Telethon
    types makes it trivially testable.
    """

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    chat_id: int
    message_id: int
    sender_id: int | None = None
    text: str = ""
    has_photo: bool = False
    has_other_media: bool = False
    is_service_message: bool = False
    is_forwarded: bool = False
    reply_to_msg_id: int | None = None


class ChannelPolicy(BaseModel):
    """Per-channel runtime policy, derived from `channels.yaml` + admin loader.

    `is_broadcast=True` skips sender admin filtering (spec 5.2 rule 4).
    `admin_user_ids` is the cached admin set for groups (refreshed every 6h).
    """

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    chat_id: int
    is_broadcast: bool
    allow_forward: bool = False
    admin_user_ids: frozenset[int] = Field(default_factory=frozenset)


class Tier0Decision(BaseModel):
    """Outcome of Tier 0 evaluation.

    `action` mirrors the four exits in the spec: pass into Tier 1, drop
    silently, fork to `signals:needs_ocr`, or fork to `signals:updates`.
    `reason` is a stable label suitable for Prometheus
    `tg_listener_messages_rejected_total{tier="t0",reason=...}`.
    """

    model_config = ConfigDict(strict=True, frozen=True)

    action: Tier0Action
    reason: str


def evaluate(meta: EventMetadata, policy: ChannelPolicy) -> Tier0Decision:
    """Apply the spec 5.2 rules in order.

    Returns the first matching decision. Order matters: replies trump
    forward/empty checks (they are explicitly modelled as updates), and
    service messages always drop first.
    """
    if meta.chat_id != policy.chat_id:
        # Programmer error: wrong policy passed in. Surface loudly so the
        # listener wiring is the thing that gets fixed, not silently routed.
        raise ValueError(
            f"policy/chat mismatch: meta.chat_id={meta.chat_id} policy.chat_id={policy.chat_id}"
        )

    if meta.is_service_message:
        return Tier0Decision(action="drop", reason="service_message")

    has_text = bool(meta.text and meta.text.strip())

    if not has_text:
        if meta.has_photo:
            return Tier0Decision(action="needs_ocr", reason="empty_text_with_photo")
        return Tier0Decision(action="drop", reason="empty_no_media")

    if meta.reply_to_msg_id is not None:
        return Tier0Decision(action="update", reason="reply_to_message")

    if meta.is_forwarded and not policy.allow_forward:
        return Tier0Decision(action="drop", reason="forwarded_disallowed")

    if not policy.is_broadcast:
        if meta.sender_id is None or meta.sender_id not in policy.admin_user_ids:
            return Tier0Decision(action="drop", reason="non_admin_sender")

    return Tier0Decision(action="pass", reason="ok")

"""Inline keyboard builders.

When a command is sent without a service argument, reply with a tappable
button per (vps, service) from fleet.yaml so the user doesn't have to
remember exact names. callback_data format: "{action}:{vps}:{service}".
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from ..config import Settings


def service_keyboard(settings: Settings, action: str) -> InlineKeyboardMarkup:
    rows = []
    multi_vps = len(settings.fleet.vpses) > 1
    for vps, svc in settings.fleet.all_services():
        label = f"{vps.name}/{svc.name}" if multi_vps else svc.name
        rows.append([InlineKeyboardButton(label, callback_data=f"{action}:{vps.name}:{svc.name}")])
    return InlineKeyboardMarkup(rows)

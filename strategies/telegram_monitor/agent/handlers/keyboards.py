"""Inline keyboard builders.

When a command is sent without a service argument, reply with a tappable
button per (vps, service) from fleet.yaml so the user doesn't have to
remember exact names. callback_data format: "{action}:{vps}:{service}".
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from ..config import Mt5LogTarget, Settings


LOG_LINES_PRESETS = (10, 30, 50, 100)


def service_keyboard(settings: Settings, action: str) -> InlineKeyboardMarkup:
    rows = []
    multi_vps = len(settings.fleet.vpses) > 1
    for vps, svc in settings.fleet.all_services():
        label = f"{vps.name}/{svc.name}" if multi_vps else svc.name
        rows.append([InlineKeyboardButton(label, callback_data=f"{action}:{vps.name}:{svc.name}")])
    return InlineKeyboardMarkup(rows)


def account_keyboard(vps_name: str, svc_name: str, accounts: tuple[Mt5LogTarget, ...]) -> InlineKeyboardMarkup:
    """Picker shown when /logs targets a service with multiple MT5 accounts.

    callback_data: "logs_acct:{vps}:{service}:{account}" (last segment is the
    MT5 account number — short, no colons).
    """
    rows = [
        [InlineKeyboardButton(
            f"acct {m.account}",
            callback_data=f"logs_acct:{vps_name}:{svc_name}:{m.account}",
        )]
        for m in accounts
    ]
    return InlineKeyboardMarkup(rows)


def lines_keyboard(vps_name: str, svc_name: str, account: str | None) -> InlineKeyboardMarkup:
    """Preset picker for log line count (last step of /logs inline flow).

    callback_data: "logs_n:{vps}:{service}:{account_or_empty}:{n}". Empty
    account slot keeps the segment count fixed at 5 so the dispatcher can
    parse positionally.
    """
    acct = account or ""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            str(n),
            callback_data=f"logs_n:{vps_name}:{svc_name}:{acct}:{n}",
        )
        for n in LOG_LINES_PRESETS
    ]])

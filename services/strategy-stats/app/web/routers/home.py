"""Home overview — three KPI cards (conde / gvfx / zone) for the selected window."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_session
from app.models import (
    CondeOutcome,
    CondeSignal,
    GvfxOutcome,
    GvfxSignal,
    ZoneOutcome,
    ZoneSignal,
)
from app.stats import conde as conde_stats
from app.stats import gvfx as gvfx_stats
from app.stats import zone as zone_stats
from app.web.since import SINCE_CHOICES, normalize_since, since_to_epoch

router = APIRouter()

_RECENT_SIGNALS_LIMIT = 15


def _fmt_ts(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _pnl(outs) -> float:
    return sum(o.profit + (o.swap or 0.0) + (o.commission or 0.0) for o in outs)


@router.get("/", response_class=HTMLResponse)
async def home(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    since: str | None = None,
) -> HTMLResponse:
    since_code = normalize_since(since)
    since_epoch = since_to_epoch(since_code)

    conde_by_ch = await conde_stats.aggregate_since(session, since_epoch)
    gvfx_by_sym = await gvfx_stats.aggregate_since(session, since_epoch)
    zone_by_acct = await zone_stats.aggregate_since(session, since_epoch)

    conde_signals = sum(s.n_signals for s in conde_by_ch.values())
    conde_wins = sum(s.n_win for s in conde_by_ch.values())
    conde_classified = sum(s.n_classified for s in conde_by_ch.values())
    conde_pnl = sum(s.total_pnl for s in conde_by_ch.values())
    conde_winrate = conde_wins / conde_classified if conde_classified else None

    gvfx_signals = sum(s.n_signals for s in gvfx_by_sym.values())
    gvfx_positions = sum(s.n_positions for s in gvfx_by_sym.values())
    gvfx_pnl = sum(s.total_pnl for s in gvfx_by_sym.values())

    zone_signals = sum(s.n_signals for s in zone_by_acct.values())
    zone_positions = sum(s.n_positions for s in zone_by_acct.values())
    zone_pnl = sum(s.total_pnl for s in zone_by_acct.values())

    recent_conde = await _recent_conde(session, since_epoch)
    recent_gvfx = await _recent_gvfx(session, since_epoch)
    recent_zone = await _recent_zone(session, since_epoch)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "now_str": request.state.now_str,
            "since_choices": SINCE_CHOICES,
            "since": since_code,
            "conde": {
                "n_signals": conde_signals,
                "n_classified": conde_classified,
                "win_rate": conde_winrate,
                "total_pnl": conde_pnl,
                "channels": len(conde_by_ch),
            },
            "gvfx": {
                "n_signals": gvfx_signals,
                "n_positions": gvfx_positions,
                "total_pnl": gvfx_pnl,
                "symbols": len(gvfx_by_sym),
            },
            "zone": {
                "n_signals": zone_signals,
                "n_positions": zone_positions,
                "total_pnl": zone_pnl,
                "accounts": len(zone_by_acct),
            },
            "recent_conde": recent_conde,
            "recent_gvfx": recent_gvfx,
            "recent_zone": recent_zone,
        },
    )


async def _recent_conde(session: AsyncSession, since_epoch: int) -> list[dict]:
    executed_exists = (
        select(CondeOutcome.position_id)
        .where(CondeOutcome.signal_ts == CondeSignal.signal_ts)
        .where(CondeOutcome.symbol == CondeSignal.symbol)
        .exists()
    )
    sig_rows = (
        await session.execute(
            select(CondeSignal)
            .where(CondeSignal.signal_ts >= since_epoch)
            .where(executed_exists)
            .order_by(CondeSignal.signal_ts.desc())
            .limit(_RECENT_SIGNALS_LIMIT)
        )
    ).scalars().all()
    if not sig_rows:
        return []

    ts_set = {s.signal_ts for s in sig_rows}
    out_rows = (
        await session.execute(
            select(CondeOutcome).where(CondeOutcome.signal_ts.in_(ts_set))
        )
    ).scalars().all()
    out_by_key: dict[tuple[int, str], list[CondeOutcome]] = {}
    for o in out_rows:
        if o.signal_ts is None:
            continue
        out_by_key.setdefault((o.signal_ts, o.symbol), []).append(o)

    result = []
    for s in sig_rows:
        outs = out_by_key.get((s.signal_ts, s.symbol), [])
        sig_dict = {"entry_price": s.entry_price, "sl": s.sl, "direction": s.direction}
        kinds = [
            conde_stats.classify_outcome(
                {
                    "close_reason": o.close_reason,
                    "direction": o.direction,
                    "exit_price": o.exit_price,
                },
                sig_dict,
            )
            for o in outs
        ]
        result.append(
            {
                "signal_ts": s.signal_ts,
                "time_str": _fmt_ts(s.signal_ts),
                "channel_id": s.channel_id,
                "channel_name": s.channel_name or f"channel:{s.channel_id}",
                "symbol": s.symbol,
                "direction": s.direction,
                "entry_price": s.entry_price,
                "sl": s.sl,
                "n_positions": len(outs),
                "pnl": _pnl(outs),
                "classification": conde_stats.classify_signal(kinds),
            }
        )
    return result


async def _recent_gvfx(session: AsyncSession, since_epoch: int) -> list[dict]:
    executed_exists = (
        select(GvfxOutcome.position_id)
        .where(GvfxOutcome.signal_ts == GvfxSignal.signal_ts)
        .where(GvfxOutcome.symbol == GvfxSignal.symbol)
        .exists()
    )
    sig_rows = (
        await session.execute(
            select(GvfxSignal)
            .where(GvfxSignal.signal_ts >= since_epoch)
            .where(executed_exists)
            .order_by(GvfxSignal.signal_ts.desc())
            .limit(_RECENT_SIGNALS_LIMIT)
        )
    ).scalars().all()
    if not sig_rows:
        return []

    ts_set = {s.signal_ts for s in sig_rows}
    out_rows = (
        await session.execute(
            select(GvfxOutcome).where(GvfxOutcome.signal_ts.in_(ts_set))
        )
    ).scalars().all()
    out_by_key: dict[tuple[int, str], list[GvfxOutcome]] = {}
    for o in out_rows:
        if o.signal_ts is None:
            continue
        out_by_key.setdefault((o.signal_ts, o.symbol), []).append(o)

    result = []
    for s in sig_rows:
        outs = out_by_key.get((s.signal_ts, s.symbol), [])
        result.append(
            {
                "signal_ts": s.signal_ts,
                "time_str": _fmt_ts(s.signal_ts),
                "symbol": s.symbol,
                "direction": s.direction,
                "target": s.target,
                "n_positions": len(outs),
                "pnl": _pnl(outs),
            }
        )
    return result


async def _recent_zone(session: AsyncSession, since_epoch: int) -> list[dict]:
    executed_exists = (
        select(ZoneOutcome.position_id)
        .where(ZoneOutcome.signal_ts == ZoneSignal.signal_ts)
        .where(ZoneOutcome.symbol == ZoneSignal.symbol)
        .exists()
    )
    sig_rows = (
        await session.execute(
            select(ZoneSignal)
            .where(ZoneSignal.signal_ts >= since_epoch)
            .where(executed_exists)
            .order_by(ZoneSignal.signal_ts.desc())
            .limit(_RECENT_SIGNALS_LIMIT)
        )
    ).scalars().all()
    if not sig_rows:
        return []

    ts_set = {s.signal_ts for s in sig_rows}
    out_rows = (
        await session.execute(
            select(ZoneOutcome).where(ZoneOutcome.signal_ts.in_(ts_set))
        )
    ).scalars().all()
    out_by_key: dict[tuple[int, str], list[ZoneOutcome]] = {}
    for o in out_rows:
        if o.signal_ts is None:
            continue
        out_by_key.setdefault((o.signal_ts, o.symbol), []).append(o)

    result = []
    for s in sig_rows:
        outs = out_by_key.get((s.signal_ts, s.symbol), [])
        # Pick any executing account so the row can deeplink to /zone/account/<n>.
        account = outs[0].account if outs else None
        result.append(
            {
                "signal_ts": s.signal_ts,
                "time_str": _fmt_ts(s.signal_ts),
                "symbol": s.symbol,
                "redbox_upper": s.redbox_upper,
                "redbox_lower": s.redbox_lower,
                "account": account,
                "n_positions": len(outs),
                "pnl": _pnl(outs),
            }
        )
    return result

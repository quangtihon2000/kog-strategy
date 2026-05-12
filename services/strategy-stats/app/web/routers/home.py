"""Home overview — three KPI cards (conde / gvfx / zone) for the selected window."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_session
from app.models import CondeOutcome, CondeSignal
from app.stats import conde as conde_stats
from app.stats import gvfx as gvfx_stats
from app.stats import zone as zone_stats
from app.web.since import SINCE_CHOICES, normalize_since, since_to_epoch

router = APIRouter()

_RECENT_SIGNALS_LIMIT = 30


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

    # Fetch recent conde signals
    recent_sig_rows = (
        await session.execute(
            select(CondeSignal)
            .where(CondeSignal.signal_ts >= since_epoch)
            .order_by(CondeSignal.signal_ts.desc())
            .limit(_RECENT_SIGNALS_LIMIT)
        )
    ).scalars().all()

    recent_ts_set = {s.signal_ts for s in recent_sig_rows}
    recent_out_rows: list[CondeOutcome] = []
    if recent_ts_set:
        recent_out_rows = (
            await session.execute(
                select(CondeOutcome).where(CondeOutcome.signal_ts.in_(recent_ts_set))
            )
        ).scalars().all()

    out_by_ts: dict[int, list[CondeOutcome]] = {}
    for o in recent_out_rows:
        if o.signal_ts is not None:
            out_by_ts.setdefault(o.signal_ts, []).append(o)

    recent_signals = []
    for s in recent_sig_rows:
        outs = out_by_ts.get(s.signal_ts, [])
        pnl = sum(o.profit + (o.swap or 0.0) + (o.commission or 0.0) for o in outs)

        # Classification
        sig_dict = {
            "entry_price": s.entry_price,
            "sl": s.sl,
            "direction": s.direction,
        }
        kinds = [conde_stats.classify_outcome(
            {
                "close_reason": o.close_reason,
                "direction": o.direction,
                "exit_price": o.exit_price,
            },
            sig_dict,
        ) for o in outs]
        classification = conde_stats.classify_signal(kinds)

        time_str = datetime.fromtimestamp(s.signal_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        recent_signals.append(
            {
                "signal_ts": s.signal_ts,
                "time_str": time_str,
                "channel_id": s.channel_id,
                "channel_name": s.channel_name or f"channel:{s.channel_id}",
                "symbol": s.symbol,
                "direction": s.direction,
                "entry_price": s.entry_price,
                "sl": s.sl,
                "n_positions": len(outs),
                "pnl": pnl,
                "classification": classification,
            }
        )

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
            "recent_signals": recent_signals,
        },
    )

"""Conde per-channel dashboard."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_session
from app.models import CondeOutcome, CondeSignal
from app.stats import conde as conde_stats
from app.web.since import SINCE_CHOICES, normalize_since, since_to_epoch

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def overview(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    since: str | None = None,
) -> HTMLResponse:
    since_code = normalize_since(since)
    by_channel = await conde_stats.aggregate_since(session, since_to_epoch(since_code))
    rows = sorted(
        by_channel.values(),
        key=lambda s: (s.n_classified, s.confidence_lo95),
        reverse=True,
    )
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "conde_overview.html",
        {
            "now_str": request.state.now_str,
            "since_choices": SINCE_CHOICES,
            "since": since_code,
            "rows": rows,
        },
    )


@router.get("/channel/{name}", response_class=HTMLResponse)
async def channel_detail(
    request: Request,
    name: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    since: str | None = None,
) -> HTMLResponse:
    since_code = normalize_since(since)
    since_epoch = since_to_epoch(since_code)

    sig_rows = (
        await session.execute(
            select(CondeSignal)
            .where(CondeSignal.channel_name == name)
            .where(CondeSignal.signal_ts >= since_epoch)
            .order_by(CondeSignal.signal_ts.desc())
            .limit(500)
        )
    ).scalars().all()
    if not sig_rows:
        raise HTTPException(status_code=404, detail=f"channel '{name}' not found")

    ts_set = {s.signal_ts for s in sig_rows}
    out_rows = (
        await session.execute(
            select(CondeOutcome).where(CondeOutcome.signal_ts.in_(ts_set))
        )
    ).scalars().all()
    out_by_ts: dict[int, list[CondeOutcome]] = {}
    for o in out_rows:
        if o.signal_ts is not None:
            out_by_ts.setdefault(o.signal_ts, []).append(o)

    signals = []
    for s in sig_rows:
        outs = out_by_ts.get(s.signal_ts, [])
        pnl = sum(o.profit + (o.swap or 0.0) + (o.commission or 0.0) for o in outs)
        signals.append(
            {
                "signal_ts": s.signal_ts,
                "symbol": s.symbol,
                "direction": s.direction,
                "entry_price": s.entry_price,
                "sl": s.sl,
                "tps": s.tps,
                "outcomes": outs,
                "n_positions": len(outs),
                "pnl": pnl,
            }
        )

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "conde_channel.html",
        {
            "now_str": request.state.now_str,
            "since_choices": SINCE_CHOICES,
            "since": since_code,
            "channel_name": name,
            "signals": signals,
        },
    )

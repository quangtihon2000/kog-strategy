"""Conde per-channel dashboard."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_session
from app.models import Channel, CondeOutcome, CondeSignal
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


@router.get("/signal/{signal_ts}")
async def signal_lookup(
    signal_ts: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RedirectResponse:
    """Resolve a signal_ts to its channel and redirect to the channel deeplink.

    Lets external notifiers (e.g. telegram_monitor) link to a signal without
    knowing channel_id — the bot only has the JSON file, which lacks it.
    """
    row = (
        await session.execute(
            select(CondeSignal)
            .where(CondeSignal.signal_ts == signal_ts)
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None or row.channel_id is None:
        raise HTTPException(status_code=404, detail=f"signal_ts {signal_ts} not found")
    return RedirectResponse(
        url=f"/conde/channel/{row.channel_id}?signal_ts={signal_ts}",
        status_code=302,
    )


@router.get("/channel/{channel_id}", response_class=HTMLResponse)
async def channel_detail(
    request: Request,
    channel_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    since: str | None = None,
    signal_ts: int | None = None,
) -> HTMLResponse:
    since_code = normalize_since(since)
    since_epoch = since_to_epoch(since_code)

    # Resolve display name from Channel table first
    channel_row = (
        await session.execute(
            select(Channel).where(Channel.channel_id == channel_id)
        )
    ).scalar_one_or_none()

    if signal_ts is not None:
        # Deeplink mode: fetch only the specific signal, ignore window
        sig_rows = (
            await session.execute(
                select(CondeSignal)
                .where(CondeSignal.channel_id == channel_id)
                .where(CondeSignal.signal_ts == signal_ts)
                .order_by(CondeSignal.signal_ts.desc())
            )
        ).scalars().all()
    else:
        sig_rows = (
            await session.execute(
                select(CondeSignal)
                .where(CondeSignal.channel_id == channel_id)
                .where(CondeSignal.signal_ts >= since_epoch)
                .order_by(CondeSignal.signal_ts.desc())
                .limit(500)
            )
        ).scalars().all()

    if not sig_rows and channel_row is None:
        raise HTTPException(status_code=404, detail=f"channel_id {channel_id!r} not found")

    # Resolve display name: prefer Channel table, fall back to most-recent signal's channel_name
    if channel_row is not None:
        channel_name = channel_row.name
    elif sig_rows:
        channel_name = sig_rows[0].channel_name or f"channel:{channel_id}"
    else:
        channel_name = f"channel:{channel_id}"

    ts_set = {s.signal_ts for s in sig_rows}
    out_rows: list[CondeOutcome] = []
    if ts_set:
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
            "channel_id": channel_id,
            "channel_name": channel_name,
            "signals": signals,
            "signal_ts_filter": signal_ts,
        },
    )

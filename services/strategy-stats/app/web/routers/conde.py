"""Conde per-channel dashboard."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_session
from app.models import Channel, CondeOutcome, CondeSignal
from app.stats.conde import aggregate_by_account, aggregate_since
from app.web.since import SINCE_CHOICES, normalize_since, since_to_epoch

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def overview(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    since: str | None = None,
    signal_ts: int | None = None,
    executed: int | None = None,
) -> HTMLResponse:
    since_code = normalize_since(since)
    since_epoch = since_to_epoch(since_code)
    executed_only = bool(executed)

    if signal_ts is not None:
        sig_rows = (
            await session.execute(
                select(CondeSignal)
                .where(CondeSignal.signal_ts == signal_ts)
                .order_by(CondeSignal.signal_ts.desc())
            )
        ).scalars().all()
    else:
        sig_rows = (
            await session.execute(
                select(CondeSignal)
                .where(CondeSignal.signal_ts >= since_epoch)
                .order_by(CondeSignal.signal_ts.desc())
                .limit(500)
            )
        ).scalars().all()

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

    channel_ids = {s.channel_id for s in sig_rows if s.channel_id is not None}
    channel_name_by_id: dict[int, str] = {}
    if channel_ids:
        ch_rows = (
            await session.execute(
                select(Channel).where(Channel.channel_id.in_(channel_ids))
            )
        ).scalars().all()
        channel_name_by_id = {c.channel_id: c.name for c in ch_rows}

    signals = []
    for s in sig_rows:
        outs = out_by_ts.get(s.signal_ts, [])
        if executed_only and not outs:
            continue
        pnl = sum(o.profit + (o.swap or 0.0) + (o.commission or 0.0) for o in outs)
        ch_name = (
            channel_name_by_id.get(s.channel_id)
            or s.channel_name
            or (f"channel:{s.channel_id}" if s.channel_id is not None else "—")
        )
        signals.append(
            {
                "signal_ts": s.signal_ts,
                "symbol": s.symbol,
                "direction": s.direction,
                "entry_price": s.entry_price,
                "sl": s.sl,
                "tps": s.tps,
                "channel_id": s.channel_id,
                "channel_name": ch_name,
                "outcomes": outs,
                "n_positions": len(outs),
                "pnl": pnl,
            }
        )

    # Top accounts card — chỉ tính khi không filter theo signal_ts cụ thể
    top_accounts: list[dict] = []
    if signal_ts is None:
        acct_summaries = await aggregate_by_account(session, since_epoch)
        top_accounts = sorted(
            [
                {
                    "account": s.account,
                    "n_signals": s.n_signals,
                    "n_positions": s.n_positions,
                    "total_pnl": s.total_pnl,
                }
                for s in acct_summaries.values()
            ],
            key=lambda x: x["total_pnl"],
            reverse=True,
        )[:5]

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "conde_overview.html",
        {
            "now_str": request.state.now_str,
            "since_choices": SINCE_CHOICES,
            "since": since_code,
            "signals": signals,
            "signal_ts_filter": signal_ts,
            "executed_only": executed_only,
            "top_accounts": top_accounts,
        },
    )


@router.get("/channel-stats", response_class=HTMLResponse)
async def channel_stats(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    since: str | None = None,
) -> HTMLResponse:
    since_code = normalize_since(since)
    by_channel = await aggregate_since(session, since_to_epoch(since_code))
    rows = sorted(
        by_channel.values(),
        key=lambda s: (s.n_classified, s.confidence_lo95),
        reverse=True,
    )
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "conde_channel_stats.html",
        {
            "now_str": request.state.now_str,
            "since_choices": SINCE_CHOICES,
            "since": since_code,
            "rows": rows,
        },
    )


@router.get("/account/{account}", response_class=HTMLResponse)
async def account_detail(
    request: Request,
    account: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    since: str | None = None,
    signal_ts: int | None = None,
    executed: int | None = None,
) -> HTMLResponse:
    since_code = normalize_since(since)
    since_epoch = since_to_epoch(since_code)
    executed_only = bool(executed)

    if signal_ts is not None:
        # Deeplink mode: fetch only the specific signal's outcomes, ignore window.
        out_rows = (
            await session.execute(
                select(CondeOutcome)
                .where(CondeOutcome.account == account)
                .where(CondeOutcome.signal_ts == signal_ts)
                .order_by(CondeOutcome.closed_at.desc())
            )
        ).scalars().all()
    else:
        out_rows = (
            await session.execute(
                select(CondeOutcome)
                .where(CondeOutcome.account == account)
                .where(CondeOutcome.signal_ts >= since_epoch)
                .order_by(CondeOutcome.signal_ts.desc(), CondeOutcome.closed_at.desc())
                .limit(1000)
            )
        ).scalars().all()
        if not out_rows:
            raise HTTPException(status_code=404, detail=f"account {account} has no outcomes")

    # Fetch matching signals để lấy channel_name, direction, entry/sl/tps
    ts_set = {o.signal_ts for o in out_rows if o.signal_ts is not None}
    sig_by_ts: dict[int, CondeSignal] = {}
    if ts_set:
        sig_rows = (
            await session.execute(
                select(CondeSignal).where(CondeSignal.signal_ts.in_(ts_set))
            )
        ).scalars().all()
        sig_by_ts = {s.signal_ts: s for s in sig_rows}

    # Resolve channel display names từ Channel table
    channel_ids = {s.channel_id for s in sig_by_ts.values() if s.channel_id is not None}
    channel_name_by_id: dict[int, str] = {}
    if channel_ids:
        ch_rows = (
            await session.execute(
                select(Channel).where(Channel.channel_id.in_(channel_ids))
            )
        ).scalars().all()
        channel_name_by_id = {c.channel_id: c.name for c in ch_rows}

    # Group outcomes by signal_ts
    by_signal: dict[int | None, list[CondeOutcome]] = {}
    for o in out_rows:
        by_signal.setdefault(o.signal_ts, []).append(o)

    signals = []
    for sig_ts, outs in by_signal.items():
        if executed_only and not outs:
            continue
        sig = sig_by_ts.get(sig_ts) if sig_ts is not None else None
        pnl = sum(o.profit + (o.swap or 0.0) + (o.commission or 0.0) for o in outs)
        ch_name = None
        ch_id = None
        if sig is not None:
            ch_id = sig.channel_id
            ch_name = (
                channel_name_by_id.get(sig.channel_id)
                or sig.channel_name
                or (f"channel:{sig.channel_id}" if sig.channel_id is not None else "—")
            )
        signals.append(
            {
                "signal_ts": sig_ts,
                "symbol": sig.symbol if sig else (outs[0].symbol if outs else "—"),
                "direction": sig.direction if sig else None,
                "entry_price": sig.entry_price if sig else None,
                "sl": sig.sl if sig else None,
                "tps": sig.tps if sig else None,
                "channel_id": ch_id,
                "channel_name": ch_name,
                "n_positions": len(outs),
                "pnl": pnl,
                "outcomes": sorted(outs, key=lambda o: o.closed_at or o.position_id),
            }
        )
    signals.sort(key=lambda x: (x["signal_ts"] or 0), reverse=True)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "conde_account.html",
        {
            "now_str": request.state.now_str,
            "since_choices": SINCE_CHOICES,
            "since": since_code,
            "account": account,
            "signals": signals,
            "signal_ts_filter": signal_ts,
            "executed_only": executed_only,
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
    executed: int | None = None,
) -> HTMLResponse:
    since_code = normalize_since(since)
    since_epoch = since_to_epoch(since_code)
    executed_only = bool(executed)

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
        if executed_only and not outs:
            continue
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
            "executed_only": executed_only,
        },
    )

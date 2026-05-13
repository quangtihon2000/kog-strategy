"""Zone per-account dashboard."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_session
from app.models import ZoneOutcome, ZoneSignal
from app.web.since import SINCE_CHOICES, normalize_since, since_to_epoch

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def overview(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    since: str | None = None,
    signal_ts: int | None = None,
) -> HTMLResponse:
    since_code = normalize_since(since)
    since_epoch = since_to_epoch(since_code)
    since_dt = datetime.fromtimestamp(since_epoch, tz=timezone.utc)

    # zone_signal agent re-stamps `timestamp` at write time, so
    # ZoneOutcome.signal_ts (from EA comment) almost never matches
    # ZoneSignal.signal_ts (from upstream Redis stream). Iterate from
    # outcomes so positions are never dropped; signal metadata (redbox)
    # is best-effort and may be absent.
    if signal_ts is not None:
        out_rows = (
            await session.execute(
                select(ZoneOutcome)
                .where(ZoneOutcome.signal_ts == signal_ts)
                .order_by(ZoneOutcome.closed_at.desc())
            )
        ).scalars().all()
    else:
        out_rows = (
            await session.execute(
                select(ZoneOutcome)
                .where(ZoneOutcome.closed_at >= since_dt)
                .order_by(ZoneOutcome.closed_at.desc())
                .limit(2000)
            )
        ).scalars().all()

    by_key: dict[tuple[int | None, str], list[ZoneOutcome]] = {}
    for o in out_rows:
        by_key.setdefault((o.signal_ts, o.symbol), []).append(o)

    ts_set = {sig_ts for sig_ts, _ in by_key if sig_ts is not None}
    sig_by_key: dict[tuple[int, str], ZoneSignal] = {}
    if ts_set:
        sig_rows = (
            await session.execute(
                select(ZoneSignal).where(ZoneSignal.signal_ts.in_(ts_set))
            )
        ).scalars().all()
        sig_by_key = {(s.signal_ts, s.symbol): s for s in sig_rows}

    def _tier_order(o: ZoneOutcome) -> tuple:
        return (
            o.tier or "￿",
            o.slot_index if o.slot_index is not None else -1,
            o.account,
            o.position_id,
        )

    signals = []
    for (sig_ts, sym), outs in by_key.items():
        sig = sig_by_key.get((sig_ts, sym)) if sig_ts is not None else None
        pnl = sum(o.profit + (o.swap or 0.0) + (o.commission or 0.0) for o in outs)
        signals.append(
            {
                "signal_ts": sig_ts,
                "symbol": sym,
                "redbox_upper": sig.redbox_upper if sig else None,
                "redbox_lower": sig.redbox_lower if sig else None,
                "n_positions": len(outs),
                "pnl": pnl,
                "outcomes": sorted(outs, key=_tier_order),
            }
        )
    signals.sort(key=lambda x: (x["signal_ts"] or 0), reverse=True)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "zone_overview.html",
        {
            "now_str": request.state.now_str,
            "since_choices": SINCE_CHOICES,
            "since": since_code,
            "signals": signals,
            "signal_ts_filter": signal_ts,
        },
    )


@router.get("/account/{account}", response_class=HTMLResponse)
async def account_detail(
    request: Request,
    account: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    since: str | None = None,
    signal_ts: int | None = None,
) -> HTMLResponse:
    since_code = normalize_since(since)
    since_epoch = since_to_epoch(since_code)

    if signal_ts is not None:
        # Deeplink mode: fetch only the specific signal's outcomes, ignore window.
        out_rows = (
            await session.execute(
                select(ZoneOutcome)
                .where(ZoneOutcome.account == account)
                .where(ZoneOutcome.signal_ts == signal_ts)
                .order_by(ZoneOutcome.closed_at.desc())
            )
        ).scalars().all()
    else:
        out_rows = (
            await session.execute(
                select(ZoneOutcome)
                .where(ZoneOutcome.account == account)
                .where(ZoneOutcome.signal_ts >= since_epoch)
                .order_by(ZoneOutcome.signal_ts.desc(), ZoneOutcome.closed_at.desc())
                .limit(1000)
            )
        ).scalars().all()
        if not out_rows:
            raise HTTPException(status_code=404, detail=f"account {account} has no outcomes")

    # Collect signal_ts to fetch matching signal rows for context.
    ts_keys = {(o.signal_ts, o.symbol) for o in out_rows if o.signal_ts is not None}
    sig_by_key: dict[tuple[int, str], ZoneSignal] = {}
    if ts_keys:
        sig_rows = (
            await session.execute(
                select(ZoneSignal).where(
                    ZoneSignal.signal_ts.in_({t for t, _ in ts_keys})
                )
            )
        ).scalars().all()
        sig_by_key = {(s.signal_ts, s.symbol): s for s in sig_rows}

    # Group outcomes by signal; flatten positions within each signal so the
    # template iterates a single list (tier becomes a column, matching the
    # conde channel page layout).
    by_signal: dict[tuple[int | None, str], list[ZoneOutcome]] = {}
    for o in out_rows:
        by_signal.setdefault((o.signal_ts, o.symbol), []).append(o)

    def _tier_order(o: ZoneOutcome) -> tuple:
        return (o.tier or "￿", o.slot_index if o.slot_index is not None else -1, o.position_id)

    signals = []
    for (sig_ts, sym), outs in by_signal.items():
        pnl = sum(o.profit + (o.swap or 0.0) + (o.commission or 0.0) for o in outs)
        sig = sig_by_key.get((sig_ts, sym)) if sig_ts is not None else None
        signals.append(
            {
                "signal_ts": sig_ts,
                "symbol": sym,
                "redbox_upper": sig.redbox_upper if sig else None,
                "redbox_lower": sig.redbox_lower if sig else None,
                "n_positions": len(outs),
                "pnl": pnl,
                "outcomes": sorted(outs, key=_tier_order),
            }
        )
    signals.sort(key=lambda x: (x["signal_ts"] or 0), reverse=True)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "zone_account.html",
        {
            "now_str": request.state.now_str,
            "since_choices": SINCE_CHOICES,
            "since": since_code,
            "account": account,
            "signals": signals,
            "signal_ts_filter": signal_ts,
        },
    )

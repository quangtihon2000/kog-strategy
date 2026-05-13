"""GVFX per-symbol dashboard."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_session
from app.models import GvfxOutcome, GvfxSignal
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
                select(GvfxSignal)
                .where(GvfxSignal.signal_ts == signal_ts)
                .order_by(GvfxSignal.signal_ts.desc())
            )
        ).scalars().all()
    else:
        sig_rows = (
            await session.execute(
                select(GvfxSignal)
                .where(GvfxSignal.signal_ts >= since_epoch)
                .order_by(GvfxSignal.signal_ts.desc())
                .limit(500)
            )
        ).scalars().all()

    ts_set = {s.signal_ts for s in sig_rows}
    out_rows: list[GvfxOutcome] = []
    if ts_set:
        out_rows = (
            await session.execute(
                select(GvfxOutcome).where(GvfxOutcome.signal_ts.in_(ts_set))
            )
        ).scalars().all()
    out_by_key: dict[tuple[int, str], list[GvfxOutcome]] = {}
    for o in out_rows:
        if o.signal_ts is not None:
            out_by_key.setdefault((o.signal_ts, o.symbol), []).append(o)

    def _tag_order(o: GvfxOutcome) -> tuple:
        return (o.mode_tag or "￿", o.account, o.position_id)

    signals = []
    for s in sig_rows:
        outs = out_by_key.get((s.signal_ts, s.symbol), [])
        if executed_only and not outs:
            continue
        pnl = sum(o.profit + (o.swap or 0.0) + (o.commission or 0.0) for o in outs)
        signals.append(
            {
                "signal_ts": s.signal_ts,
                "symbol": s.symbol,
                "direction": s.direction,
                "target": s.target,
                "step_pts": s.step_pts,
                "tp_pts": s.tp_pts,
                "low_price": s.low_price,
                "high_price": s.high_price,
                "n_positions": len(outs),
                "pnl": pnl,
                "outcomes": sorted(outs, key=_tag_order),
            }
        )

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "gvfx_overview.html",
        {
            "now_str": request.state.now_str,
            "since_choices": SINCE_CHOICES,
            "since": since_code,
            "signals": signals,
            "signal_ts_filter": signal_ts,
            "executed_only": executed_only,
        },
    )


@router.get("/symbol/{symbol}", response_class=HTMLResponse)
async def symbol_detail(
    request: Request,
    symbol: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    since: str | None = None,
    signal_ts: int | None = None,
    executed: int | None = None,
) -> HTMLResponse:
    since_code = normalize_since(since)
    since_epoch = since_to_epoch(since_code)
    executed_only = bool(executed)

    if signal_ts is not None:
        # Deeplink mode: fetch only the specific signal, ignore window.
        sig_rows = (
            await session.execute(
                select(GvfxSignal)
                .where(GvfxSignal.symbol == symbol)
                .where(GvfxSignal.signal_ts == signal_ts)
                .order_by(GvfxSignal.signal_ts.desc())
            )
        ).scalars().all()
    else:
        sig_rows = (
            await session.execute(
                select(GvfxSignal)
                .where(GvfxSignal.symbol == symbol)
                .where(GvfxSignal.signal_ts >= since_epoch)
                .order_by(GvfxSignal.signal_ts.desc())
                .limit(500)
            )
        ).scalars().all()
        if not sig_rows:
            raise HTTPException(status_code=404, detail=f"symbol '{symbol}' not found")

    ts_set = {s.signal_ts for s in sig_rows}
    out_rows: list[GvfxOutcome] = []
    if ts_set:
        out_rows = (
            await session.execute(
                select(GvfxOutcome)
                .where(GvfxOutcome.symbol == symbol)
                .where(GvfxOutcome.signal_ts.in_(ts_set))
            )
        ).scalars().all()
    out_by_ts: dict[int, list[GvfxOutcome]] = {}
    for o in out_rows:
        if o.signal_ts is not None:
            out_by_ts.setdefault(o.signal_ts, []).append(o)

    def _tag_order(o: GvfxOutcome) -> tuple:
        return (o.mode_tag or "￿", o.account, o.position_id)

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
                "target": s.target,
                "step_pts": s.step_pts,
                "tp_pts": s.tp_pts,
                "low_price": s.low_price,
                "high_price": s.high_price,
                "n_positions": len(outs),
                "pnl": pnl,
                "outcomes": sorted(outs, key=_tag_order),
            }
        )

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "gvfx_symbol.html",
        {
            "now_str": request.state.now_str,
            "since_choices": SINCE_CHOICES,
            "since": since_code,
            "symbol": symbol,
            "signals": signals,
            "signal_ts_filter": signal_ts,
            "executed_only": executed_only,
        },
    )

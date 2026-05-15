"""GVFX per-symbol dashboard."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_session
from app.models import GvfxOutcome, GvfxSignal
from app.stats.gvfx import aggregate_by_account
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
        "gvfx_overview.html",
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
                select(GvfxOutcome)
                .where(GvfxOutcome.account == account)
                .where(GvfxOutcome.signal_ts == signal_ts)
                .order_by(GvfxOutcome.closed_at.desc())
            )
        ).scalars().all()
    else:
        out_rows = (
            await session.execute(
                select(GvfxOutcome)
                .where(GvfxOutcome.account == account)
                .where(GvfxOutcome.signal_ts >= since_epoch)
                .order_by(GvfxOutcome.signal_ts.desc(), GvfxOutcome.closed_at.desc())
                .limit(1000)
            )
        ).scalars().all()
        if not out_rows:
            raise HTTPException(status_code=404, detail=f"account {account} has no outcomes")

    # Fetch matching signals để lấy symbol, direction, target, step_pts, tp_pts
    ts_sym_keys = {(o.signal_ts, o.symbol) for o in out_rows if o.signal_ts is not None}
    ts_set = {t for t, _ in ts_sym_keys}
    sig_by_key: dict[tuple[int, str], GvfxSignal] = {}
    if ts_set:
        sig_rows = (
            await session.execute(
                select(GvfxSignal).where(GvfxSignal.signal_ts.in_(ts_set))
            )
        ).scalars().all()
        sig_by_key = {(s.signal_ts, s.symbol): s for s in sig_rows}

    # Group outcomes by (signal_ts, symbol)
    by_signal: dict[tuple[int | None, str], list[GvfxOutcome]] = {}
    for o in out_rows:
        by_signal.setdefault((o.signal_ts, o.symbol), []).append(o)

    def _tag_order(o: GvfxOutcome) -> tuple:
        return (o.mode_tag or "￿", o.position_id)

    signals = []
    for (sig_ts, sym), outs in by_signal.items():
        if executed_only and not outs:
            continue
        sig = sig_by_key.get((sig_ts, sym)) if sig_ts is not None else None
        pnl = sum(o.profit + (o.swap or 0.0) + (o.commission or 0.0) for o in outs)
        signals.append(
            {
                "signal_ts": sig_ts,
                "symbol": sym,
                "direction": sig.direction if sig else None,
                "target": sig.target if sig else None,
                "step_pts": sig.step_pts if sig else None,
                "tp_pts": sig.tp_pts if sig else None,
                "low_price": sig.low_price if sig else None,
                "high_price": sig.high_price if sig else None,
                "n_positions": len(outs),
                "pnl": pnl,
                "outcomes": sorted(outs, key=_tag_order),
            }
        )
    signals.sort(key=lambda x: (x["signal_ts"] or 0), reverse=True)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "gvfx_account.html",
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

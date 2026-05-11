"""Home overview — three KPI cards (conde / gvfx / zone) for the selected window."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_session
from app.stats import conde as conde_stats
from app.stats import gvfx as gvfx_stats
from app.stats import zone as zone_stats
from app.web.since import SINCE_CHOICES, normalize_since, since_to_epoch

router = APIRouter()


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
        },
    )

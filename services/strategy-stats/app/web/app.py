"""FastAPI app factory.

Single-process server with Jinja2 templates + HTMX. Dashboard is currently
open (no auth); `verify_basic_auth` in app.deps is retained so re-enabling
is a one-line change on the dashboard router below.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from agent_lib.timefmt import fmt_ict, now_unix
from app.web.routers import conde, gvfx, home, zone

_TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


def _fmt_ts(value: int | float | None) -> str:
    if value is None:
        return "—"
    try:
        return fmt_ict(int(value))
    except (TypeError, ValueError, OSError):
        return "—"


templates.env.filters["fmt_ts"] = _fmt_ts


def _now_str() -> str:
    return fmt_ict(now_unix())


def create_app() -> FastAPI:
    app = FastAPI(title="Strategy Stats", default_response_class=HTMLResponse)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> JSONResponse:  # pragma: no cover — trivial
        return JSONResponse({"status": "ok"})

    # Dashboard is currently open. To re-enable Basic Auth, re-import
    # `Depends` and `app.deps.verify_basic_auth` and pass
    # `dependencies=[Depends(verify_basic_auth)]` here.
    dashboard = APIRouter()
    dashboard.include_router(home.router)
    dashboard.include_router(conde.router, prefix="/conde", tags=["conde"])
    dashboard.include_router(gvfx.router, prefix="/gvfx", tags=["gvfx"])
    dashboard.include_router(zone.router, prefix="/zone", tags=["zone"])
    app.include_router(dashboard)

    # Routers reach the shared Jinja2Templates instance via app.state so they
    # don't have to import this module (avoids a circular import with deps).
    app.state.templates = templates

    @app.middleware("http")
    async def _inject_now(request: Request, call_next):
        request.state.now_str = _now_str()
        return await call_next(request)

    return app


app = create_app()

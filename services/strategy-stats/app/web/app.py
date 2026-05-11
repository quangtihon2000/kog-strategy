"""FastAPI app factory.

Single-process server with Jinja2 templates + HTMX. Basic Auth is applied
globally to the dashboard router; `/healthz` is mounted outside that router
so Docker's healthcheck doesn't need credentials.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.deps import verify_basic_auth
from app.web.routers import conde, gvfx, home, zone

_TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def create_app() -> FastAPI:
    app = FastAPI(title="Strategy Stats", default_response_class=HTMLResponse)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> JSONResponse:  # pragma: no cover — trivial
        return JSONResponse({"status": "ok"})

    # All dashboard routers sit behind a single Basic Auth dependency.
    dashboard = APIRouter(dependencies=[Depends(verify_basic_auth)])
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

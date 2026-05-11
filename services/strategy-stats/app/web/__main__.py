"""Uvicorn entry point: `python -m app.web`."""
from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "app.web.app:app",
        host=os.getenv("WEB_HOST", "0.0.0.0"),
        port=int(os.getenv("WEB_PORT", "8080")),
        log_level=os.getenv("WEB_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()

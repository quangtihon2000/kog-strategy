"""Process entrypoint. Run as `python main.py` (NSSM) or `python -m agent.main`.

Lifecycle:
    1. Load .env + fleet.yaml → Settings
    2. Build Application (handlers + monitors wired)
    3. run_polling() — blocks until SIGTERM/Ctrl-C

Note on imports: the package uses relative imports (`from .bot import …`).
When NSSM launches `python.exe main.py`, Python adds *this directory* to
sys.path, making the package un-importable. We fix sys.path so `agent`
resolves as a package, then re-import ourselves as `agent.main` so the
relative imports throughout the package work.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

if __name__ == "__main__" and __package__ in (None, ""):
    # Running as a bare script — graft the parent dir so `agent` is a package.
    _here = Path(__file__).resolve()
    sys.path.insert(0, str(_here.parent.parent))  # strategies/telegram_monitor/
    sys.path.insert(0, str(_here.parents[3] / "shared"))  # repo/shared/
    from agent.main import main  # type: ignore
    main()
    sys.exit(0)


from .bot import build_app
from .config import load_settings


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
        stream=sys.stdout,
    )
    # python-telegram-bot is chatty at INFO; quiet the HTTP layer.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext.Application").setLevel(logging.WARNING)


def main() -> None:
    settings = load_settings()
    _setup_logging(settings.log_level)

    log = logging.getLogger("telegram_monitor")
    log.info(
        "starting: vpses=%d services=%d allowed_users=%d",
        len(settings.fleet.vpses),
        sum(len(v.services) for v in settings.fleet.vpses),
        len(settings.allowed_user_ids),
    )
    if not settings.allowed_user_ids:
        log.warning("TELEGRAM_ALLOWED_USER_IDS is empty — bot will reject all users")

    app = build_app(settings)
    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()

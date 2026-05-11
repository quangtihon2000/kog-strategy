"""Database connection settings loaded from environment / .env.

Env prefix: DB_
Example:
    DB_URL=postgresql+asyncpg://tg_listener:tg_listener@localhost:5432/tg_listener
    DB_ECHO=false
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class DBSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DB_",
        env_file=".env",
        extra="ignore",
    )

    url: str = "postgresql+asyncpg://tg_listener:tg_listener@localhost:5432/tg_listener"
    echo: bool = False


# Module-level singleton — override in tests via env var DATABASE_URL or by
# re-instantiating with model_validate.
db_settings = DBSettings()

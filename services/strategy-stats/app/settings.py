"""App settings — Pydantic v2 Settings reading from environment."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_user: str = "stats"
    postgres_password: str = "changeme"
    postgres_db: str = "strategy_stats"
    postgres_host: str = "postgres"
    postgres_port: int = 5432

    upstream_redis_url: str = "redis://localhost:6379"

    basic_auth_user: str = "admin"
    basic_auth_password: str = "changeme"

    ingest_batch_count: int = Field(default=64, ge=1, le=1000)
    ingest_block_ms: int = Field(default=5000, ge=100, le=60_000)
    ingest_consumer_name: str = "stats-worker-1"

    @property
    def postgres_async_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def postgres_sync_url(self) -> str:
        # Alembic uses sync driver
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

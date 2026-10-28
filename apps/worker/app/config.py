"""Worker settings (env-driven, mirrors apps/api/.env.example plus worker knobs)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    ENV: str = "dev"
    LOG_LEVEL: str = "INFO"

    DATABASE_URL: str = "postgresql+asyncpg://dataloom:CHANGE_ME@localhost:5432/dataloom"
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- consumer group ----------------------------------------------------
    CONSUMER_GROUP: str = "dataloom-workers"
    CONSUMER_NAME: str = "worker-1"
    #: How many stream entries to read per call (PRD 9.4: 1000-5000 or 2 s flush).
    READ_COUNT: int = 1000
    #: Block timeout for XREADGROUP; a timeout is a normal "no data" tick.
    READ_BLOCK_MS: int = 2000
    #: Pending entries older than this are claimed back from dead consumers.
    CLAIM_IDLE_MS: int = 60_000
    #: Upper bound per Bronze write, keeps Polars/pyarrow peak memory bounded.
    MAX_ROWS_PER_BATCH: int = 5000

    # --- object storage ----------------------------------------------------
    S3_ENDPOINT_URL: str = ""
    S3_BUCKET: str = "dataloom-dev"
    S3_REGION: str = "auto"
    S3_ACCESS_KEY_ID: str = "CHANGE_ME"
    S3_SECRET_ACCESS_KEY: str = "CHANGE_ME"

    # --- duckdb ------------------------------------------------------------
    DUCKDB_PATH: str = "/data/duckdb"
    DUCKDB_MEMORY_LIMIT: str = "400MB"
    DUCKDB_THREADS: int = 1

    # --- pipeline execution ------------------------------------------------
    NODE_TIMEOUT_SECONDS: int = 45
    NODE_MAX_RETRIES: int = 2
    DEFAULT_QUARANTINE_RATIO: float = 0.05

    # --- pollers (P1) ------------------------------------------------------
    ENABLE_POLLERS: bool = False
    POLLER_TICK_SECONDS: int = 30

    # --- api callback (SSE events are read from DB, so this is only health) --
    API_INTERNAL_URL: str = "http://api:8000"


@lru_cache(maxsize=1)
def get_settings() -> WorkerSettings:
    return WorkerSettings()

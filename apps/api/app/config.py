"""Application settings.

Every value comes from the environment (or `.env`). No secret has a usable
default: the defaults are obvious placeholders so a misconfigured deployment
fails loudly instead of silently using a known key.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Names match the root `.env.example`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- app ---------------------------------------------------------------
    ENV: Literal["dev", "staging", "production"] = "dev"
    APP_NAME: str = "DataLoom API"
    APP_BASE_URL: str = "http://localhost:3000"
    LOG_LEVEL: str = "INFO"

    # --- datastores --------------------------------------------------------
    DATABASE_URL: str = "postgresql+asyncpg://dataloom:CHANGE_ME@localhost:5432/dataloom"
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- secrets -----------------------------------------------------------
    #: Used to derive the Fernet key that encrypts source configs (FR-015).
    SECRET_KEY: str = "CHANGE_ME_32_BYTES_MINIMUM_SECRET_KEY_VALUE"
    #: Dedicated key for the installation-wide LLM API key (D-05). Kept
    #: separate from ``SECRET_KEY`` so rotating one does not invalidate the
    #: other: a source config and the deployment LLM key are different blast
    #: radii. The default is a deliberate placeholder; it MUST be replaced in
    #: staging and production (``ENV != dev``) or every stored LLM key becomes
    #: readable by anyone with the repository default.
    INSTALLATION_SECRET_KEY: str = "CHANGE_ME_INSTALLATION_SECRET_KEY_VALUE"

    # --- sessions / cookies ------------------------------------------------
    SESSION_COOKIE_NAME: str = "dl_session"
    CSRF_COOKIE_NAME: str = "dl_csrf"
    CSRF_HEADER_NAME: str = "X-CSRF-Token"
    SESSION_TTL_HOURS: int = 24 * 7
    COOKIE_SECURE: bool = False  # forced True when ENV != dev

    # --- CORS --------------------------------------------------------------
    CORS_ORIGINS: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # --- ingest hot path ---------------------------------------------------
    INGEST_MAX_BODY_BYTES: int = 262_144  # 256 KB (FR-012)
    INGEST_MAX_ROWS: int = 1_000  # FR-012
    INGEST_MAX_STREAM_LEN: int = 200_000  # MAXLEN ~ (bounded queue)
    INGEST_BACKPRESSURE_THRESHOLD: int = 150_000  # XLEN above this -> 429
    INGEST_BATCH_HINT: str = "1000"
    INGEST_IDEMPOTENCY_TTL_HOURS: int = 24

    # --- rate limiting -----------------------------------------------------
    RATE_LIMIT_INGEST_PER_SEC: int = 500  # per API key
    RATE_LIMIT_API_PER_MIN: int = 600  # per session/IP
    RATE_LIMIT_LOGIN_PER_MIN: int = 10  # brute force guard
    #: LLM-backed endpoints (``/v1/agents/*``). Cost abuse = DoS (SEC-40).
    RATE_LIMIT_AGENT_PER_MIN: int = 20  # per user, per org

    # --- uploads -----------------------------------------------------------
    UPLOAD_MAX_BYTES: int = 524_288_000  # 500 MB (FR-013)

    # --- object storage (S3 compatible: R2 / S3 / MinIO) -------------------
    S3_ENDPOINT_URL: str = ""  # empty -> AWS default endpoint
    S3_BUCKET: str = "dataloom-dev"
    S3_REGION: str = "auto"
    S3_ACCESS_KEY_ID: str = "CHANGE_ME"
    S3_SECRET_ACCESS_KEY: str = "CHANGE_ME"
    S3_PRESIGN_EXPIRES_SECONDS: int = 900

    # --- installation admin (deployment-wide, NOT an organization role) ----
    #: Bootstrap list of emails that are treated as installation admins even
    #: without an ``install_admins`` row. This is how the FIRST installation
    #: admin is created: set it here, then grant further admins via
    #: ``POST /v1/installation/admins``. Matching is case-insensitive.
    #: Keep this list empty in a steady-state deployment once the DB rows exist.
    INSTALL_ADMIN_EMAILS: list[str] = Field(default_factory=list)

    # --- misc --------------------------------------------------------------
    API_KEY_CACHE_TTL_SECONDS: int = 60
    TRUSTED_HOSTS: list[str] = Field(default_factory=lambda: ["*"])

    # --- scheduler (FR-028, D-03) ------------------------------------------
    SCHEDULER_ENABLED: bool = False

    # --- agent execution (D-05) --------------------------------------------
    #: When false the API refuses to fabricate agent output (see
    #: ``services/text_to_sql_service.py``). The agent worker owns this switch.
    AGENT_EXECUTION_ENABLED: bool = False
    #: Mirrors ``apps/agents/app/config.py::AGENT_MAX_CONCURRENT_PER_ORG`` (no
    #: cross-app import per ADR-001). Used by ``POST /v1/agents/ask`` which
    #: creates its own ``agent_runs`` row and therefore must apply the same cap.
    AGENT_MAX_CONCURRENT_PER_ORG: int = 3

    # --- text-to-sql limits (FR-033) ---------------------------------------
    SQL_MAX_ROWS: int = 1000
    SQL_TIMEOUT_SECONDS: int = 10

    # --- duckdb read-only query service (PRD 9.3) --------------------------
    DUCKDB_PATH: str = "/data/duckdb"
    DUCKDB_MEMORY_LIMIT: str = "400MB"

    # --- LLM budget defaults (FR-035) --------------------------------------
    # --- LLM provider env fallback (D-05). The installation_settings row
    # takes precedence; these values apply when no row exists yet. Mirror of
    # apps/agents/app/config.py so installation_service can report status
    # without importing the agents app.
    LLM_PROVIDER_NAME: str = "openai_compatible"
    LLM_BASE_URL: str = ""
    LLM_API_KEY: str = ""
    LLM_MODEL_DEFAULT: str = ""
    LLM_MODEL_SMALL: str = ""
    LLM_TIMEOUT_SECONDS: int = 60
    LLM_PRICE_IN_PER_1K: float = 0.0
    LLM_PRICE_OUT_PER_1K: float = 0.0

    DEFAULT_LLM_BUDGET_USD_MONTH: float = 5.0
    DEFAULT_PER_RUN_TOKEN_LIMIT: int = 20_000

    @field_validator("COOKIE_SECURE")
    @classmethod
    def _secure_in_non_dev(cls, v: bool, info) -> bool:  # noqa: ANN001
        env = info.data.get("ENV", "dev")
        return True if env != "dev" else v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton (safe: process-wide immutable config)."""
    return Settings()


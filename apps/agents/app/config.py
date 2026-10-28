"""Agent worker settings (pydantic-settings).

Every value comes from the environment. No secret has a usable default: the
defaults are obvious placeholders so a misconfigured deployment fails loudly
instead of silently using a well-known key (same convention as
``apps/api/app/config.py``).

Names here match ``apps/agents/.env.example`` 1:1.

Nothing in this module connects to anything; importing it is free.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- runtime -----------------------------------------------------------
    ENV: Literal["dev", "staging", "production"] = "dev"
    LOG_LEVEL: str = "INFO"

    # --- datastores --------------------------------------------------------
    #: SQLAlchemy URL used by the agent worker for its own bookkeeping
    #: (agent_runs / agent_events / llm_calls / artifacts / datasets).
    DATABASE_URL: str = "postgresql+asyncpg://dataloom:CHANGE_ME@localhost:5432/dataloom"
    #: DSN handed to ``PostgresSaver``. psycopg 3 does NOT understand the
    #: ``+asyncpg`` suffix, see :meth:`psycopg_dsn`.
    CHECKPOINT_DATABASE_URL: str = "postgresql://dataloom:CHANGE_ME@localhost:5432/dataloom"

    #: DuckDB directory the worker writes gold files into. The Text-to-SQL
    #: graph opens ``{DUCKDB_PATH}/{org}__{project}.duckdb`` READ-ONLY.
    DUCKDB_PATH: str = "/data/duckdb"

    # --- object storage (Bronze/Silver Parquet; locally disabled) ----------
    #: ``"local"`` reads Parquet from a mounted directory, ``"s3"`` uses boto3.
    #: Default is ``local`` and ``ALLOW_OBJECT_STORAGE=false`` so the agent
    #: worker cannot reach object storage unless an operator opts in.
    ARTIFACT_STORAGE: Literal["local", "s3"] = "local"
    ALLOW_OBJECT_STORAGE: bool = False
    #: Root directory the ``local`` backend may read from (SEC-38). Empty means
    #: "absolute paths only, no root confinement" — kept for backwards
    #: compatibility, but a staging deployment SHOULD set it so a ``datasets``
    #: row cannot point the agent at an arbitrary file on the host.
    ARTIFACT_LOCAL_ROOT: str = ""
    S3_ENDPOINT_URL: str = ""
    S3_BUCKET: str = "dataloom-dev"
    S3_REGION: str = "auto"
    S3_ACCESS_KEY_ID: str = "CHANGE_ME"
    S3_SECRET_ACCESS_KEY: str = "CHANGE_ME"

    # --- LLM provider (D-05 / D-16) ---------------------------------------
    #: OpenAI-compatible base URL WITHOUT the trailing ``/chat/completions``.
    LLM_BASE_URL: str = "https://CHANGE_ME.invalid/v1"
    LLM_API_KEY: str = "CHANGE_ME_LLM_API_KEY"
    LLM_MODEL_DEFAULT: str = "CHANGE_ME_MODEL_DEFAULT"
    LLM_MODEL_SMALL: str = "CHANGE_ME_MODEL_SMALL"
    LLM_TIMEOUT_SECONDS: int = 60
    LLM_PROVIDER_NAME: str = "openai_compatible"
    #: ``fake`` swaps in :class:`app.llm.fake.FakeProvider` (CI only).
    LLM_PROVIDER: Literal["openai_compatible", "fake"] = "openai_compatible"

    # --- LLM prices (FR-036). PLACEHOLDERS: verify against the provider's
    # current price sheet before trusting any cost number. A price of 0.0
    # means "unknown", not "free".
    LLM_PRICE_IN_PER_1K: float = 0.0
    LLM_PRICE_OUT_PER_1K: float = 0.0

    # --- agent execution policy (PRD 9.9) ----------------------------------
    AGENT_MAX_RETRIES: int = 2
    AGENT_NODE_TIMEOUT_SECONDS: int = 45
    AGENT_RUN_TIMEOUT_SECONDS: int = 300
    AGENT_MAX_CONCURRENT_PER_ORG: int = 3

    # --- deterministic tool limits (PRD A1) --------------------------------
    #: PRD: "Sampel acak <=1.000 baris".
    SAMPLE_ROWS_MAX: int = 1000
    #: PRD A1 tool ``sample_rows``: "maks 50".
    PROMPT_SAMPLE_ROWS: int = 50
    #: Hard ceiling for any single prompt payload, in characters.
    PROMPT_MAX_CHARS: int = 24_000

    # --- text-to-sql -------------------------------------------------------
    SQL_MAX_ROWS: int = 1000
    SQL_TIMEOUT_SECONDS: int = 10

    # --- checkpoint / setup -----------------------------------------------
    #: ``PostgresSaver.setup()`` creates the checkpoint tables. It must run
    #: once per database; the runner calls it lazily and this flag documents
    #: whether an operator already did it out of band.
    CHECKPOINTER_AUTOSETUP: bool = True

    # --- dev safety switch -------------------------------------------------
    #: When false (default), ``run_agent`` refuses to call a model. Set true
    #: only in an environment where the agent worker is allowed to execute.
    AGENT_EXECUTION_ENABLED: bool = False

    # --- per-run defaults used only if the ORG row is missing --------------
    DEFAULT_LLM_BUDGET_USD_MONTH: float = 5.0
    DEFAULT_PER_RUN_TOKEN_LIMIT: int = 20_000

    # --- misc --------------------------------------------------------------
    #: Directory an operator may mount READ-ONLY with one ``<run_id>.log`` per
    #: failed run. Empty (default) means "no log evidence available"; the
    #: self-healing agent then reports insufficient evidence instead of
    #: guessing (PRD A4).
    AGENT_LOG_DIR: str = ""
    #: Log lines fed to the self-healing graph, after redaction.
    LOG_EXCERPT_MAX_LINES: int = 200
    LOG_EXCERPT_MAX_CHARS: int = 12_000

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def psycopg_dsn(self) -> str:
        """Return a libpq DSN for psycopg 3.

        SQLAlchemy-style suffixes (``+asyncpg``, ``+psycopg``) are stripped
        because psycopg's own parser rejects them. The checkpoint tables live
        in the same database as the application tables, so the default derives
        from ``DATABASE_URL`` when ``CHECKPOINT_DATABASE_URL`` is unset.
        """
        dsn = self.CHECKPOINT_DATABASE_URL or self.DATABASE_URL
        if "+" in dsn.split("://", 1)[0]:
            scheme, rest = dsn.split("://", 1)
            dsn = f"{scheme.split('+', 1)[0]}://{rest}"
        return dsn

    def llm_price_configured(self) -> bool:
        """True only when both prices were explicitly provided.

        ``cost_usd`` recorded as 0.0 is then a real "we do not know" marker
        rather than a fabricated zero cost.
        """
        return self.LLM_PRICE_IN_PER_1K > 0.0 or self.LLM_PRICE_OUT_PER_1K > 0.0


#: Every env var this app reads. Keep the root ``.env.example`` in sync.
ENV_VARS: tuple[str, ...] = tuple(
    sorted(
        {
            "ENV",
            "LOG_LEVEL",
            "DATABASE_URL",
            "CHECKPOINT_DATABASE_URL",
            "DUCKDB_PATH",
            "ARTIFACT_STORAGE",
            "ALLOW_OBJECT_STORAGE",
            "ARTIFACT_LOCAL_ROOT",
            "S3_ENDPOINT_URL",
            "S3_BUCKET",
            "S3_REGION",
            "S3_ACCESS_KEY_ID",
            "S3_SECRET_ACCESS_KEY",
            "LLM_BASE_URL",
            "LLM_API_KEY",
            "LLM_MODEL_DEFAULT",
            "LLM_MODEL_SMALL",
            "LLM_TIMEOUT_SECONDS",
            "LLM_PROVIDER_NAME",
            "LLM_PROVIDER",
            "LLM_PRICE_IN_PER_1K",
            "LLM_PRICE_OUT_PER_1K",
            "AGENT_MAX_RETRIES",
            "AGENT_NODE_TIMEOUT_SECONDS",
            "AGENT_RUN_TIMEOUT_SECONDS",
            "AGENT_MAX_CONCURRENT_PER_ORG",
            "SAMPLE_ROWS_MAX",
            "PROMPT_SAMPLE_ROWS",
            "PROMPT_MAX_CHARS",
            "SQL_MAX_ROWS",
            "SQL_TIMEOUT_SECONDS",
            "CHECKPOINTER_AUTOSETUP",
            "AGENT_EXECUTION_ENABLED",
            "DEFAULT_LLM_BUDGET_USD_MONTH",
            "DEFAULT_PER_RUN_TOKEN_LIMIT",
            "AGENT_LOG_DIR",
            "LOG_EXCERPT_MAX_LINES",
            "LOG_EXCERPT_MAX_CHARS",
        }
    )
)


@lru_cache(maxsize=1)
def get_settings() -> AgentSettings:
    """Cached settings singleton (process-wide immutable configuration)."""
    return AgentSettings()


def reset_settings_cache() -> None:
    """Test helper: drop the cached settings so a new env is picked up."""
    get_settings.cache_clear()


__all__ = ["ENV_VARS", "AgentSettings", "get_settings", "reset_settings_cache"]

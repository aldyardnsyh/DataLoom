"""Agent worker entrypoint.

The agent graphs are invoked by the API (``POST /v1/agents/*``) in the MVP, so the
"worker" process only needs to:

1. prove it can reach Postgres (so a bad DSN fails fast, not on first request), and
2. keep the process alive so the checkpoint pool stays warm.

A queue-driven mode is intentionally NOT implemented: adding Redis/Broker
infrastructure without a concrete PRD requirement violates ADR-001. When the
API calls ``apps.agents.app.runner.start_profiler`` the graph runs inside the
agent process pool; if it is not running, the API returns the documented
``EXECUTION_DISABLED`` / ``AGENT_UNAVAILABLE`` refusal instead of guessing.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from app.config import get_settings
from app.db.session import dispose_engine, session_scope

logger = logging.getLogger("dataloom.agents")

_STOP = asyncio.Event()


async def _healthcheck() -> bool:
    """One round-trip to Postgres. Returns False instead of raising."""
    from sqlalchemy import text

    try:
        async with session_scope() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 - startup diagnostics, not a request path
        logger.exception("Agent worker tidak dapat terhubung ke Postgres")
        return False


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.LOG_LEVEL)

    logger.info(
        "DataLoom agent worker mulai (execution_enabled=%s, provider=%s)",
        settings.AGENT_EXECUTION_ENABLED,
        settings.LLM_PROVIDER,
    )
    if not settings.AGENT_EXECUTION_ENABLED:
        logger.warning(
            "AGENT_EXECUTION_ENABLED=false: tidak ada model yang akan dipanggil. "
            "Aktifkan hanya di lingkungan yang diizinkan."
        )

    if not await _healthcheck():
        logger.error("Keluar: koneksi database gagal.")
        raise SystemExit(1)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _STOP.set)
        except NotImplementedError:  # pragma: no cover - Windows
            pass

    await _STOP.wait()
    await dispose_engine()
    logger.info("DataLoom agent worker berhenti")


if __name__ == "__main__":
    asyncio.run(main())

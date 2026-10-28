"""Read-only Postgres source reader (FR-015).

Enforced restrictions:
- dedicated connection with ``default_transaction_read_only=on``
- only a single ``SELECT`` generated from identifiers (no user SQL)
- statement timeout and a hard row cap
- credentials are read from the encrypted config and never logged
"""

from __future__ import annotations

import json
import logging
from typing import Any

import psycopg
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core_shared import decrypt_source_config
from app.db import Source

logger = logging.getLogger("dataloom.worker.pollers.postgres")

_SAFE_IDENT = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def _ident(value: str) -> str:
    """Whitelist identifier characters then quote. Never interpolate raw input."""
    if not value or any(c not in _SAFE_IDENT for c in value):
        raise ValueError(f"Identifier tidak aman: {value!r}")
    return '"' + value + '"'


def build_select(config: dict[str, Any], limit: int) -> str:
    schema = _ident(config.get("schema_name", "public"))
    table = _ident(config["table"])
    return f"SELECT * FROM {schema}.{table} LIMIT {int(limit)}"


def dsn_from_config(config: dict[str, Any]) -> str:
    return (
        f"host={config['host']} port={int(config['port'])} dbname={config['database']} "
        f"user={config['username']} password={config['password']} "
        f"sslmode={config.get('sslmode', 'require')} connect_timeout=10"
    )


def read_rows(config: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    """Open a read-only session and fetch at most ``limit`` rows."""
    sql = build_select(config, limit)
    with psycopg.connect(dsn_from_config(config)) as conn:
        conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        conn.execute("SET statement_timeout = '30s'")
        with conn.cursor() as cursor:
            cursor.execute(sql)
            columns = [d[0] for d in cursor.description]
            rows = cursor.fetchall()
    return [dict(zip(columns, row, strict=False)) for row in rows]


async def poll_postgres_source(
    session: AsyncSession, source: Source, redis: aioredis.Redis
) -> int:
    settings = get_settings()
    config = decrypt_source_config(source.config_enc)
    rows = read_rows(config, settings.MAX_ROWS_PER_BATCH)
    if not rows:
        return 0

    stream = f"ingest:{source.org_id}:{source.id}"
    await redis.xadd(
        stream,
        {
            "org_id": str(source.org_id),
            "project_id": str(source.project_id),
            "source_id": str(source.id),
            "batch_id": "postgres-poller",
            "rows": json.dumps(rows, separators=(",", ":"), default=str),
            "received_at": "",
        },
        maxlen=settings.READ_COUNT,
        approximate=True,
    )
    return len(rows)

"""Redis client + token-bucket rate limiting.

Two different failure policies on purpose:
- Ingest backpressure checks FAIL CLOSED (503): accepting data we cannot queue
  would break the durability promise of a 202 response (NFR-03).
- Generic API/brute-force rate limits FAIL OPEN: a Redis blip must not take
  down authentication for everyone.
"""

from __future__ import annotations

from collections.abc import Awaitable

import redis.asyncio as aioredis

from app.config import get_settings

_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    """Shared async Redis client (connection pool created lazily)."""
    global _redis
    if _redis is None:
        settings = get_settings()
        _redis = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            max_connections=64,
        )
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


# Lua token bucket: refill at `rate` tokens/second up to `burst`.
# Returns 1 when allowed, 0 when the caller must be rejected.
_TOKEN_BUCKET_LUA = """
local key = KEYS[1]
local rate = tonumber(ARGV[1])
local burst = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])

local data = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts = tonumber(data[2])
if tokens == nil then tokens = burst; ts = now end

local elapsed = math.max(0, now - ts)
tokens = math.min(burst, tokens + elapsed * rate)

local allowed = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
end

redis.call('HSET', key, 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', key, ttl)
return allowed
"""


async def token_bucket_allow(
    key: str, *, rate: float, burst: int, fail_open: bool = True
) -> bool:
    """Consume one token from bucket ``key``; ``True`` when allowed."""
    import time

    client = get_redis()
    try:
        result = await client.eval(
            _TOKEN_BUCKET_LUA, 1, key, rate, burst, time.time(), 120
        )
        return int(result) == 1
    except Exception:  # noqa: BLE001 - policy below is the point
        return bool(fail_open)


async def ingest_rate_limit_ok(api_key_prefix: str) -> bool:
    settings = get_settings()
    rate = float(settings.RATE_LIMIT_INGEST_PER_SEC)
    return await token_bucket_allow(
        f"rl:ingest:{api_key_prefix}",
        rate=rate,
        burst=int(rate),
        fail_open=True,
    )


async def api_rate_limit_ok(identity: str) -> bool:
    settings = get_settings()
    per_min = settings.RATE_LIMIT_API_PER_MIN
    return await token_bucket_allow(
        f"rl:api:{identity}",
        rate=per_min / 60.0,
        burst=per_min,
        fail_open=True,
    )


async def login_rate_limit_ok(identity: str) -> bool:
    settings = get_settings()
    per_min = settings.RATE_LIMIT_LOGIN_PER_MIN
    return await token_bucket_allow(
        f"rl:login:{identity}",
        rate=per_min / 60.0,
        burst=per_min,
        fail_open=True,
    )


async def agent_rate_limit_ok(identity: str) -> bool:
    """Per-user ceiling for LLM-backed endpoints (``/v1/agents/*``).

    Cost abuse is a denial-of-service: each call can spend real money. The
    monthly USD budget (FR-035) is a *ledger* check that happens after the call
    chain starts and is 0 USD until prices are configured, so it cannot be the
    only door. This bucket is deliberately much tighter than
    ``RATE_LIMIT_API_PER_MIN`` (which is a general anti-flood, not a cost cap).
    Fails open for the same reason as SEC-22: an unavailable Redis must not
    disable the product; the org budget remains the hard stop.
    """
    settings = get_settings()
    per_min = max(1, int(settings.RATE_LIMIT_AGENT_PER_MIN))
    return await token_bucket_allow(
        f"rl:agent:{identity}",
        rate=per_min / 60.0,
        burst=per_min,
        fail_open=True,
    )


def stream_key(org_id: str, source_id: str) -> str:
    """Redis Stream name from PRD section 9.4: ``ingest:{org_id}:{source_id}``."""
    return f"ingest:{org_id}:{source_id}"


async def guard_backpressure(stream: str) -> int:
    """Raise ``TooManyRequests`` when the stream exceeds its threshold.

    Fails closed: an unavailable Redis means we must NOT accept data.
    """
    from app.config import get_settings as _gs
    from app.core.errors import ServiceUnavailable, TooManyRequests

    settings = _gs()
    try:
        length = int(await get_redis().xlen(stream))
    except Exception as exc:  # noqa: BLE001
        raise ServiceUnavailable(
            "Antrian ingest tidak tersedia saat ini. Coba lagi nanti."
        ) from exc

    if length >= settings.INGEST_BACKPRESSURE_THRESHOLD:
        raise TooManyRequests(
            "Antrian sedang penuh. Kirim ulang setelah beberapa saat.",
            retry_after=1,
        )
    return length


async def lookup_with_cache(
    key: str, ttl: int, loader: "Awaitable[str | None] | None" = None
) -> str | None:  # pragma: no cover - placeholder for typed helper use
    raise NotImplementedError

"""Scheduled REST API polling with SSRF protection (FR-014).

Disabled by default (``ENABLE_POLLERS``). When enabled, every outbound request
passes :func:`assert_public_target` which resolves DNS and rejects private,
loopback, link-local and cloud-metadata addresses. The URL is resolved at
request time, not only at source creation, to defeat DNS rebinding.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import socket
from typing import Any
from urllib.parse import urlparse

import httpx
import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core_shared import decrypt_source_config
from app.db import Source

logger = logging.getLogger("dataloom.worker.pollers.rest")

BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local incl. cloud metadata
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),  # unique local
    ipaddress.ip_network("fe80::/10"),  # link-local v6
    # NAT64: ::ffff:a.b.c.d in disguise (64:ff9b::/96 maps every IPv4 range,
    # including 169.254.169.254, into what looks like a public IPv6 address).
    ipaddress.ip_network("64:ff9b::/96"),
    # 6to4 / Teredo embed an IPv4 address in the v6 address.
    ipaddress.ip_network("2002::/16"),
    ipaddress.ip_network("2001::/32"),
]

#: Without an allowlist the poller is a request-forgery primitive against the
#: *internal service mesh*: API (http://api:8000) and Redis/Metabase style
#: hostnames are not IP literals, so ``assert_public_target`` cannot see them.
ALLOWED_PORTS: frozenset[int] = frozenset({80, 443})
MAX_REDIRECTS = 3


class SSRFBlocked(Exception):
    """Raised when an outbound target resolves to a forbidden address."""


def _is_blocked_ip(address: str) -> bool:
    """True when ``address`` (any v4/v6 form) is not a public unicast target."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return True  # unparsable -> treat as forbidden
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped  # ::ffff:169.254.169.254 -> 169.254.169.254
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        return True
    return any(ip in network for network in BLOCKED_NETWORKS)


def assert_public_target(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SSRFBlocked("Skema harus http/https.")
    if parsed.username or parsed.password:
        raise SSRFBlocked("URL tidak boleh memuat kredensial.")
    host = parsed.hostname
    if not host:
        raise SSRFBlocked("Host tidak ada.")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port not in ALLOWED_PORTS:
        raise SSRFBlocked(f"Port {port} tidak diizinkan (hanya 80/443).")

    # Resolve ALL addresses; a single private answer blocks the request.
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise SSRFBlocked("DNS gagal untuk host tujuan.") from exc

    for info in infos:
        address = info[4][0]
        if _is_blocked_ip(address):
            raise SSRFBlocked(f"Alamat {address} diblokir (privat/internal).")


def extract_records(payload: Any, json_path: str | None) -> list[dict[str, Any]]:
    """Navigate a dotted path (``data.items``) to the record array."""
    node = payload
    if json_path:
        for part in json_path.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                raise ValueError(f"Path '{json_path}' tidak ditemukan pada respons.")
    if isinstance(node, dict):
        return [node]
    if isinstance(node, list):
        return [item for item in node if isinstance(item, dict)]
    raise ValueError("Respons bukan object/array JSON.")


async def _fetch_validated(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    max_bytes: int,
) -> httpx.Response:
    """GET/POST ``url`` while re-checking SSRF on every redirect hop.

    ``follow_redirects`` stays off at the client level: we follow manually so
    that hop N is validated against the same policy as hop 0 (a 302 to
    ``http://api:8000/...`` or ``http://169.254.169.254/`` must die here).
    """
    target = url
    for _hop in range(MAX_REDIRECTS + 1):
        assert_public_target(target)
        response = await client.request(method, target, headers=headers)
        if response.is_redirect and response.headers.get("location"):
            target = str(response.url.join(response.headers["location"]))
            method = "GET"  # 301/302/303 semantics; never re-POST a body we don't have
            headers = {}  # do not forward credentials to a third-party origin
            continue
        response.raise_for_status()
        if len(response.content) > max_bytes:
            raise ValueError("Respons melebihi batas ukuran.")
        return response
    raise SSRFBlocked("Terlalu banyak redirect.")


async def poll_source(
    session: AsyncSession,
    source: Source,
    redis: aioredis.Redis,
    *,
    client: httpx.AsyncClient | None = None,
) -> int:
    """Fetch one REST source and enqueue its rows into the ingest stream."""
    settings = get_settings()
    config = decrypt_source_config(source.config_enc)
    url = config["url"]
    assert_public_target(url)

    headers = dict(config.get("headers") or {})
    timeout = float(config.get("timeout_seconds", 30))
    max_bytes = int(config.get("max_response_bytes", 10_485_760))

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=timeout, follow_redirects=False)
    try:
        response = await _fetch_validated(
            client,
            config.get("method", "GET"),
            url,
            headers=headers,
            max_bytes=max_bytes,
        )
        records = extract_records(response.json(), config.get("json_path"))
    finally:
        if owns_client:
            await client.aclose()

    if not records:
        return 0

    stream = f"ingest:{source.org_id}:{source.id}"
    with redis.pipeline(transaction=False) as pipe:
        for record in records[: settings.MAX_ROWS_PER_BATCH]:
            pipe.xadd(
                stream,
                {
                    "org_id": str(source.org_id),
                    "project_id": str(source.project_id),
                    "source_id": str(source.id),
                    "batch_id": "poller",
                    "rows": json.dumps([record], separators=(",", ":"), default=str),
                    "received_at": "",
                },
                maxlen=settings.READ_COUNT,
                approximate=True,
            )
        await pipe.execute()

    logger.info("Poller %s enqueue %d baris", source.id, len(records))
    return len(records)


async def poller_loop(
    session_factory, redis: aioredis.Redis  # noqa: ANN001
) -> None:
    """Poll each due API source at its own interval."""
    while True:
        try:
            async with session_factory() as session:
                sources = (
                    await session.execute(
                        select(Source).where(Source.type == "api", Source.status != "paused")
                    )
                ).scalars().all()
                async with httpx.AsyncClient(follow_redirects=False) as client:
                    for source in sources:
                        try:
                            await poll_source(session, source, redis, client=client)
                        except SSRFBlocked as exc:
                            logger.warning("Poller SSRF diblokir untuk %s: %s", source.id, exc)
                        except Exception:  # noqa: BLE001
                            logger.exception("Poller gagal untuk sumber %s", source.id)
                await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("Loop poller gagal")
        await asyncio.sleep(get_settings().POLLER_TICK_SECONDS)

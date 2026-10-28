"""Server-Sent Events primitives (FR-040, PRD section 12.5).

The stream is intentionally dumb: we serialise contract events and add
heartbeats so proxies keep the connection open. Reconnection logic lives in
the browser (EventSource with Last-Event-ID).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi.responses import StreamingResponse

HEARTBEAT_SECONDS = 15


def format_event(event: str, data: dict[str, Any], event_id: str | None = None) -> str:
    """Encode one SSE frame. ``data`` is JSON on a single line."""
    lines: list[str] = []
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(data, separators=(',', ':'), default=str)}")
    lines.append("")
    lines.append("")
    return "\n".join(lines)


def format_heartbeat() -> str:
    """SSE comment frame; ignored by EventSource but keeps the socket warm."""
    return ": ping\n\n"


async def with_heartbeat(
    source: AsyncIterator[str], interval: int = HEARTBEAT_SECONDS
) -> AsyncIterator[str]:
    """Yield from ``source`` but emit a comment if it is idle for ``interval``."""
    iterator = source.__aiter__()
    while True:
        try:
            chunk = await asyncio.wait_for(iterator.__anext__(), timeout=interval)
        except asyncio.TimeoutError:
            yield format_heartbeat()
            continue
        except StopAsyncIteration:
            break
        yield chunk


def sse_response(source: AsyncIterator[str]) -> StreamingResponse:
    """Wrap an async generator of encoded frames into a StreamingResponse."""
    return StreamingResponse(
        with_heartbeat(source),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

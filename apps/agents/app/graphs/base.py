"""Shared graph infrastructure (PRD 9.9, ADR-003).

Everything a graph node needs that is NOT business logic:

* ``checkpointer()``      - PostgresSaver (never in-memory).
* ``call_llm``           - budget guard + one ``llm_calls`` row per call.
* ``record_event``       - one ``agent_events`` row per graph step.
* ``with_timeout``       - per-node timeout (default 45 s).
* ``parse_json_object``  - strict JSON extraction from model output.
* ``retry_policy``       - bounded retry (max 2) for idempotent work only.

Design rules:
- Nothing here may log prompt content, model output or API keys.
- A node that returns an invalid contract MUST raise ``ContractViolation`` so
  the router can retry or escalate; it must never persist partial output.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import AgentEvent, LlmCall
from app.llm.budget import BudgetExceeded, BudgetGuard, cost_usd
from app.llm.provider import ChatMessage, LLMError, LLMProvider, LLMResponse

logger = logging.getLogger("dataloom.agents.graph")

T = TypeVar("T")


class ContractViolation(Exception):
    """Agent output did not satisfy the Pydantic contract (PRD 10.1 rule 4)."""

    def __init__(self, message: str, issues: list[str] | None = None) -> None:
        super().__init__(message)
        self.issues = issues or []


class NodeTimeout(Exception):
    """A graph node exceeded ``AGENT_NODE_TIMEOUT_SECONDS``."""


class NeedsHuman(Exception):
    """Retry budget exhausted or evidence insufficient: escalate, don't guess."""

    def __init__(self, reason: str, status: str = "insufficient_evidence") -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status


# ---------------------------------------------------------------------------
# Timeouts
# ---------------------------------------------------------------------------
async def with_timeout(coro: Awaitable[T], seconds: int | None = None) -> T:
    """Await ``coro`` with the node timeout applied."""
    limit = seconds or get_settings().AGENT_NODE_TIMEOUT_SECONDS
    try:
        return await asyncio.wait_for(coro, timeout=limit)
    except asyncio.TimeoutError as exc:
        raise NodeTimeout(f"Node melebihi batas {limit} detik.") from exc


# ---------------------------------------------------------------------------
# LLM call with budget + logging
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class LlmCallResult:
    response: LLMResponse
    attempt: int


async def call_llm(
    *,
    session: AsyncSession,
    provider: LLMProvider,
    guard: BudgetGuard,
    org_id: uuid.UUID,
    agent_run_id: uuid.UUID | None,
    node: str,
    messages: Sequence[ChatMessage],
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 1500,
    json_mode: bool = True,
    attempt: int = 0,
) -> LlmCallResult:
    """Guard the budget, call the model, and persist exactly one ``llm_calls``.

    The BudgetGuard check happens BEFORE the call and raises ``BudgetExceeded``
    which the caller maps to ``AgentRunStatus.BUDGET_EXCEEDED``.
    """
    settings = get_settings()
    chosen_model = model or settings.LLM_MODEL_DEFAULT

    # 1. Budget guard (raises before any network I/O).
    await guard.ensure_can_spend(max(1, sum(len(m.content) for m in messages) // 4), max_tokens_out=max_tokens)

    started = time.monotonic()
    error_text: str | None = None
    response: LLMResponse | None = None
    try:
        response = await provider.complete(
            messages,
            model=chosen_model,
            temperature=temperature,
            max_tokens=max_tokens,
            response_schema={"type": "json_object"} if json_mode else None,
        )
    except LLMError as exc:
        error_text = exc.safe_message
        latency_ms = int((time.monotonic() - started) * 1000)
        session.add(
            LlmCall(
                org_id=org_id,
                agent_run_id=agent_run_id,
                node=node,
                provider=provider.name,
                model=chosen_model,
                tokens_in=0,
                tokens_out=0,
                latency_ms=latency_ms,
                cost_usd=0.0,
                error=error_text[:500],
            )
        )
        await session.flush()
        raise

    latency_ms = response.latency_ms or int((time.monotonic() - started) * 1000)
    cost = cost_usd(tokens_in=response.tokens_in, tokens_out=response.tokens_out)

    session.add(
        LlmCall(
            org_id=org_id,
            agent_run_id=agent_run_id,
            node=node,
            provider=response.provider,
            model=response.model or chosen_model,
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
            latency_ms=latency_ms,
            cost_usd=cost,
            error=None,
        )
    )
    await session.flush()
    return LlmCallResult(response=response, attempt=attempt)


# ---------------------------------------------------------------------------
# Structured output parsing
# ---------------------------------------------------------------------------
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def parse_json_object(content: str) -> dict[str, Any]:
    """Extract the first JSON object from model output.

    Models sometimes wrap JSON in prose or fences; we take the fenced block when
    present, otherwise the outermost ``{...}``. Anything else is a violation.
    """
    text = (content or "").strip()
    if not text:
        raise ContractViolation("Model mengembalikan respons kosong.")

    fenced = _FENCE_RE.search(text)
    if fenced:
        text = fenced.group(1).strip()

    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ContractViolation("Respons model tidak mengandung objek JSON.")
        text = text[start : end + 1]

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ContractViolation(f"JSON tidak valid: {exc.msg}") from exc

    if not isinstance(payload, dict):
        raise ContractViolation("JSON harus berupa objek.")
    return payload


# ---------------------------------------------------------------------------
# Event tracing
# ---------------------------------------------------------------------------
async def record_event(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    agent_run_id: uuid.UUID,
    step: str,
    node_name: str,
    status: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Append one ``agent_events`` row (PRD 9.9 "Trace")."""
    session.add(
        AgentEvent(
            org_id=org_id,
            agent_run_id=agent_run_id,
            step=step,
            node_name=node_name,
            status=status,
            payload_json=payload or {},
        )
    )
    await session.flush()


# ---------------------------------------------------------------------------
# Retry helper
# ---------------------------------------------------------------------------
async def retry_until(
    fn: Callable[[int], Awaitable[T]],
    *,
    max_attempts: int | None = None,
    errors: tuple[type[BaseException], ...] = (ContractViolation,),
    on_retry: Callable[[int, BaseException], None] | None = None,
) -> T:
    """Run ``fn(attempt)`` up to ``max_attempts`` times (default 2 retries).

    Only errors listed in ``errors`` are retried; anything else propagates.
    After the budget is exhausted the LAST error is re-raised so the router can
    map it to ``needs_human`` / ``insufficient_evidence``.
    """
    settings = get_settings()
    attempts = (max_attempts or settings.AGENT_MAX_RETRIES) + 1
    last: BaseException | None = None
    for attempt in range(attempts):
        try:
            return await fn(attempt)
        except errors as exc:  # type: ignore[misc]
            last = exc
            if on_retry is not None:
                on_retry(attempt, exc)
            if attempt + 1 >= attempts:
                break
            await asyncio.sleep(min(2**attempt, 4))
    assert last is not None
    raise last


# ---------------------------------------------------------------------------
# Checkpointer
# ---------------------------------------------------------------------------
_checkpointer: Any = None


def checkpointer() -> Any:
    """Return a process-wide ``PostgresSaver``.

    ``PostgresSaver`` is synchronous (psycopg 3); LangGraph accepts it for
    ``graph.compile(checkpointer=...)``. ``setup()`` creates the checkpoint
    tables and is idempotent, guarded by ``CHECKPOINTER_AUTOSETUP``.
    """
    global _checkpointer
    if _checkpointer is not None:
        return _checkpointer

    from langgraph.checkpoint.postgres import PostgresSaver

    settings = get_settings()
    saver_cm = PostgresSaver.from_conn_string(settings.psycopg_dsn())
    saver = saver_cm.__enter__()  # keep the context manager alive for the process
    if settings.CHECKPOINTER_AUTOSETUP:
        saver.setup()
    _checkpointer = saver
    return saver


def reset_checkpointer_for_tests() -> None:
    """Drop the cached saver (tests only; never called in production paths)."""
    global _checkpointer
    _checkpointer = None


__all__ = [
    "BudgetExceeded",
    "ContractViolation",
    "LlmCallResult",
    "NeedsHuman",
    "NodeTimeout",
    "call_llm",
    "checkpointer",
    "parse_json_object",
    "record_event",
    "reset_checkpointer_for_tests",
    "retry_until",
    "with_timeout",
]



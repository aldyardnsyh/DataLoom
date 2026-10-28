"""LLM provider abstraction (D-05, refined by D-16).

``LLMProvider`` is the ONLY way a graph may talk to a model. Implementations:

- :class:`app.llm.openai_compatible.OpenAICompatibleProvider` - real HTTP.
- :class:`app.llm.fake.FakeProvider` - queued canned responses, no network.

Contract notes
--------------
``complete`` returns an :class:`LLMResponse` for *transport-level* success.
A provider may still return ``raw_error`` set together with partial content
(e.g. a 200 with an empty body); callers must check it.

Anything that means "the call did not happen" raises :class:`LLMError`.
:class:`BudgetExceeded` lives in :mod:`app.llm.budget` and is raised *before*
the call, never by a provider.

Security invariants enforced here and in every implementation:
- The API key is never put into ``safe_message``, ``raw_error`` or logs.
- Prompt/message contents are never logged. Only token counts and latency.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

Role = Literal["system", "user", "assistant", "tool"]


@dataclass(slots=True, frozen=True)
class ChatMessage:
    """One chat turn. Kept deliberately tiny: no tool-calling surface."""

    role: Role
    content: str

    def as_dict(self) -> dict[str, str]:
        # ``repr`` of the content is never logged by this package.
        return {"role": self.role, "content": self.content}


@dataclass(slots=True)
class LLMResponse:
    """Result of one provider call.

    ``content`` is the model's raw text. It is UNTRUSTED until parsed against a
    ``dataloom_contracts.agents`` model or otherwise validated.
    """

    content: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    model: str
    provider: str
    #: Sanitised, human-readable transport problem (never a prompt or a key).
    raw_error: str | None = None
    #: Free-form provider metadata worth keeping for debugging (never secrets).
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.raw_error is None


class LLMError(Exception):
    """A provider call failed at transport level.

    ``status`` is the HTTP status when one exists (``None`` for timeouts and
    connection errors). ``safe_message`` is written to ``llm_calls.error`` and
    therefore must never contain the API key or the prompt: it is built from
    the status code and a fixed vocabulary only.
    """

    def __init__(self, status: int | None, safe_message: str) -> None:
        super().__init__(f"LLM call failed (status={status}): {safe_message}")
        self.status = status
        self.safe_message = safe_message


@runtime_checkable
class LLMProvider(Protocol):
    """Minimal async chat-completion surface used by every graph node."""

    name: str

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Run one completion.

        ``response_schema`` is a JSON-schema object. When given, the provider
        asks the model for JSON output. Providers that cannot honour it must
        still accept the argument (and document that in their docstring).
        """
        ...

    async def aclose(self) -> None:
        """Release network resources. Idempotent."""
        ...


def estimate_tokens(text: str) -> int:
    """Rough pre-flight token estimate for the budget guard.

    This is a *heuristic* (``chars / 4`` rounded up, a common English
    approximation) and is used ONLY to decide whether to start a call. The
    authoritative numbers are the provider's usage report stored in
    ``llm_calls``. It is not a measurement and must not be reported as one.
    """
    return max(1, (len(text) + 3) // 4)


def estimate_messages_tokens(messages: Sequence[ChatMessage]) -> int:
    """Estimate the prompt size of a whole chat request."""
    total = sum(estimate_tokens(m.content) + 4 for m in messages)
    return max(1, total)


class Timer:
    """Wall-clock helper so every provider reports latency the same way."""

    __slots__ = ("_start",)

    def __init__(self) -> None:
        self._start = time.monotonic()

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self._start) * 1000)


__all__ = [
    "ChatMessage",
    "LLMError",
    "LLMProvider",
    "LLMResponse",
    "Role",
    "Timer",
    "estimate_messages_tokens",
    "estimate_tokens",
]

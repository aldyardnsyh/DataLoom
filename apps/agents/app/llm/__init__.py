"""LLM adapter package (D-05).

Import the concrete provider from its module, or use :func:`build_provider`
which honours ``LLM_PROVIDER`` so tests can swap in the fake without touching
graph code.
"""

from __future__ import annotations

from app.llm.budget import BudgetExceeded, BudgetGuard, BudgetState, cost_usd
from app.llm.fake import FakeProvider
from app.llm.openai_compatible import OpenAICompatibleProvider
from app.llm.provider import (
    ChatMessage,
    LLMError,
    LLMProvider,
    LLMResponse,
    estimate_messages_tokens,
    estimate_tokens,
)


def build_provider(**overrides: object) -> LLMProvider:
    """Return the provider named by ``LLM_PROVIDER`` (default: real HTTP).

    ``overrides`` are forwarded to the concrete constructor (used by tests to
    pass ``responses=[...]`` to :class:`FakeProvider`).

    A caller owns the returned object and must await ``aclose()`` on it.
    """
    from app.config import get_settings

    settings = get_settings()
    if settings.LLM_PROVIDER == "fake":
        return FakeProvider(**overrides)  # type: ignore[arg-type]
    return OpenAICompatibleProvider(**overrides)  # type: ignore[arg-type]


__all__ = [
    "BudgetExceeded",
    "BudgetGuard",
    "BudgetState",
    "ChatMessage",
    "FakeProvider",
    "LLMError",
    "LLMProvider",
    "LLMResponse",
    "OpenAICompatibleProvider",
    "build_provider",
    "cost_usd",
    "estimate_messages_tokens",
    "estimate_tokens",
]

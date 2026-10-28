"""Deterministic fake provider for tests and offline development (D-05).

No network access, no sleeping, no randomness: the same queue always produces
the same sequence, so a graph test is reproducible.

Two ways to queue answers:

1. Pre-queue raw text (``FakeProvider(responses=[...])``) - what a graph sees
   from a real model, including invalid JSON, so the retry path is testable.
2. Queue exceptions to simulate transport failures:

       fake.queue_raise(LLMError(429, "kuota/rate limit penyedia (429)"))

Every call is recorded in :attr:`FakeProvider.calls` (messages are stored so a
test can assert that data was wrapped in the untrusted-data delimiters).
The queue is closed by default: an extra call raises ``AssertionError`` so a
test cannot silently depend on a call it forgot to stub.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from app.llm.provider import (
    ChatMessage,
    LLMError,
    LLMResponse,
    estimate_messages_tokens,
)


class FakeProvider:
    """Queue-backed provider. Thread-safe enough for single-thread tests."""

    name = "fake"

    def __init__(
        self,
        responses: Sequence[str | dict[str, Any] | Exception] | None = None,
        *,
        model: str = "fake-model",
        tokens_in: int = 100,
        tokens_out: int = 50,
        latency_ms: int = 1,
    ) -> None:
        self._queue: list[str | dict[str, Any] | Exception] = []
        self.calls: list[dict[str, Any]] = []
        self.closed = False
        self._model = model
        self._tokens_in = tokens_in
        self._tokens_out = tokens_out
        self._latency_ms = latency_ms
        for item in responses or []:
            self.queue(item)

    # ------------------------------------------------------------------
    # queue management
    # ------------------------------------------------------------------
    def queue(self, item: str | dict[str, Any] | Exception) -> None:
        """Append one canned answer (text, JSON-able dict, or exception)."""
        self._queue.append(item)

    def queue_raise(self, error: Exception) -> None:
        self._queue.append(error)

    def queue_json(self, payload: dict[str, Any]) -> None:
        self._queue.append(json.dumps(payload, ensure_ascii=False))

    @property
    def remaining(self) -> int:
        return len(self._queue)

    # ------------------------------------------------------------------
    # provider protocol
    # ------------------------------------------------------------------
    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        # Record BEFORE popping so a failing call is still observable.
        self.calls.append(
            {
                "messages": [m.as_dict() for m in messages],
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "response_schema": response_schema,
            }
        )
        if not self._queue:
            raise AssertionError(
                "FakeProvider kehabisan jawaban; tambahkan queue() sebelum memanggil graph."
            )
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            if isinstance(item, LLMError):
                raise item
            raise LLMError(None, "provider palsu gagal (simulasi)") from item

        content = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
        return LLMResponse(
            content=content,
            tokens_in=self._tokens_in,
            tokens_out=self._tokens_out,
            latency_ms=self._latency_ms,
            model=model or self._model,
            provider=self.name,
            raw_error=None,
            meta={"usage_estimated": False, "fake": True},
        )

    async def aclose(self) -> None:
        self.closed = True

    # ------------------------------------------------------------------
    # assertions helpers used by tests
    # ------------------------------------------------------------------
    def last_messages(self) -> list[dict[str, str]]:
        if not self.calls:
            raise AssertionError("FakeProvider belum dipanggil.")
        return self.calls[-1]["messages"]

    def estimated_tokens(self) -> int:
        if not self.calls:
            return 0
        messages = [ChatMessage(**m) for m in self.calls[-1]["messages"]]
        return estimate_messages_tokens(messages)


__all__ = ["FakeProvider"]

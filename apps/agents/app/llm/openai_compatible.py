"""OpenAI-compatible HTTP provider (D-05).

Works against any endpoint implementing ``POST {base_url}/chat/completions``
(OpenAI, Groq, DeepSeek, OpenRouter, vLLM, ...). No vendor SDK is imported so
switching providers is a configuration change, not a code change.

Security invariants
-------------------
1. The API key lives only in the ``Authorization`` header. It is never placed
   in the URL, in an exception message, in ``raw_error`` or in a log record.
2. Prompt and completion contents are NEVER logged here. Callers log token
   counts and latency only.
3. Non-2xx answers raise :class:`LLMError` with a ``safe_message`` built from a
   fixed vocabulary ("HTTP 429 dari penyedia LLM"), never from the response
   body (a provider echo can contain the key prefix or the prompt).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any

import httpx

from app.config import get_settings
from app.llm.provider import (
    ChatMessage,
    LLMError,
    LLMResponse,
    Timer,
    estimate_messages_tokens,
)

logger = logging.getLogger("dataloom.agents.llm")

#: Fixed vocabulary for ``LLMError.safe_message``. Unknown statuses map to a
#: generic string so a provider cannot inject text into our error column.
_STATUS_TEXT: dict[int, str] = {
    400: "permintaan ditolak penyedia (400)",
    401: "kredensial LLM ditolak (401)",
    403: "akses LLM ditolak (403)",
    404: "endpoint atau model tidak ditemukan (404)",
    408: "penyedia LLM timeout (408)",
    413: "prompt melebihi batas penyedia (413)",
    422: "permintaan ditolak penyedia (422)",
    429: "kuota/rate limit penyedia (429)",
    500: "galat internal penyedia (500)",
    502: "gateway penyedia bermasalah (502)",
    503: "penyedia LLM tidak tersedia (503)",
    504: "gateway penyedia timeout (504)",
}


def _safe_status_message(status: int) -> str:
    return _STATUS_TEXT.get(status, f"penyedia LLM membalas HTTP {status}")


class OpenAICompatibleProvider:
    """Async provider over ``httpx.AsyncClient``. One client per instance."""

    name = "openai_compatible"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        settings = get_settings()
        self._base_url = (base_url or settings.LLM_BASE_URL).rstrip("/")
        self._api_key = api_key or settings.LLM_API_KEY
        self._timeout = float(
            timeout_seconds if timeout_seconds is not None else settings.LLM_TIMEOUT_SECONDS
        )
        # Injected client is for tests; a fresh one is created lazily otherwise.
        self._client = client

    # ------------------------------------------------------------------
    # client lifecycle
    # ------------------------------------------------------------------
    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                follow_redirects=False,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    # Some gateways require an explicit UA; no version leakage.
                    "User-Agent": "DataLoom-Agents/0.1",
                },
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # the call
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
        """POST one chat completion.

        ``response_schema`` switches on ``response_format={"type":"json_object"}``.
        We deliberately do NOT use the stricter ``json_schema`` variant: it is
        not implemented by every OpenAI-compatible gateway, and the agent
        layer validates the payload with Pydantic anyway (AGENT_MAX_RETRIES).
        The schema is therefore appended to the prompt by the caller
        (``prompts/*.py``) and only the *switch* travels here.
        """
        payload: dict[str, Any] = {
            "model": model,
            "messages": [m.as_dict() for m in messages],
            "temperature": float(max(0.0, min(temperature, 2.0))),
        }
        if max_tokens is not None:
            payload["max_tokens"] = int(max_tokens)
        if response_schema is not None:
            payload["response_format"] = {"type": "json_object"}

        url = f"{self._base_url}/chat/completions"
        timer = Timer()
        client = self._get_client()

        try:
            response = await client.post(url, json=payload)
        except httpx.TimeoutException as exc:
            # ``str(exc)`` from httpx may include the URL; we only care that the
            # URL never has secrets in it (it does not) but keep the message
            # fixed anyway for a stable error column.
            logger.warning("LLM timeout setelah %s ms", timer.elapsed_ms())
            raise LLMError(None, "permintaan ke penyedia LLM timeout") from exc
        except httpx.HTTPError as exc:
            logger.warning(
                "LLM transport error (%s)", type(exc).__name__
            )
            raise LLMError(None, "gagal menghubungi penyedia LLM") from exc

        latency_ms = timer.elapsed_ms()

        if response.status_code < 200 or response.status_code >= 300:
            safe = _safe_status_message(response.status_code)
            logger.warning(
                "LLM HTTP %s (latency=%sms, model=%s)",
                response.status_code,
                latency_ms,
                model,
            )
            raise LLMError(response.status_code, safe)

        # --- parse -----------------------------------------------------
        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("LLM membalas body non-JSON (latency=%sms)", latency_ms)
            return LLMResponse(
                content="",
                tokens_in=estimate_messages_tokens(messages),
                tokens_out=0,
                latency_ms=latency_ms,
                model=model,
                provider=self.name,
                raw_error="respons penyedia bukan JSON",
            )

        choices = body.get("choices") or []
        if not choices:
            return LLMResponse(
                content="",
                tokens_in=estimate_messages_tokens(messages),
                tokens_out=0,
                latency_ms=latency_ms,
                model=model,
                provider=self.name,
                raw_error="respons penyedia tidak memuat choices",
            )

        message = choices[0].get("message") or {}
        content = message.get("content")
        if content is None:
            # Some gateways answer with a ``refusal`` field instead of content.
            content = ""
        if not isinstance(content, str):
            # Structured content (list of parts) is flattened defensively.
            try:
                content = json.dumps(content, ensure_ascii=False)
            except (TypeError, ValueError):
                content = str(content)

        usage = body.get("usage") or {}
        tokens_in = usage.get("prompt_tokens")
        tokens_out = usage.get("completion_tokens")
        # When the gateway omits usage we fall back to the local estimate and
        # mark it, so nobody mistakes an estimate for a measured value.
        estimated = tokens_in is None or tokens_out is None
        if tokens_in is None:
            tokens_in = estimate_messages_tokens(messages)
        if tokens_out is None:
            tokens_out = max(1, (len(content) + 3) // 4)

        return LLMResponse(
            content=content,
            tokens_in=int(tokens_in),
            tokens_out=int(tokens_out),
            latency_ms=latency_ms,
            model=str(body.get("model") or model),
            provider=self.name,
            raw_error=None if content else "respons penyedia kosong",
            meta={
                "finish_reason": choices[0].get("finish_reason"),
                "usage_estimated": estimated,
            },
        )


__all__ = ["OpenAICompatibleProvider"]

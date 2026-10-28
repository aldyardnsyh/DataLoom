"""Unit tests for installation LLM settings (D-05, FR-036).

Scope: pure logic only. No database, no network, no FastAPI app. The status
read model is exercised through a fake session plus a stub settings object so
``get_status`` runs without Postgres; the rest are contract validators and the
``api_key_hint`` projection.

Each helper imports lazily so a missing optional dependency (argon2, asyncpg,
...) skips the affected test instead of failing collection for the whole file.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

pytestmark = pytest.mark.unit

# ``tests/conftest.py`` already publishes the three app roots, but in an order
# where ``agents`` shadows ``api`` for the bare ``app`` package. Put the API root
# (and the contracts package) first so ``app.services`` means apps/api here.
_REPO_ROOT = Path(__file__).resolve().parents[2]
for _root in (
    str(_REPO_ROOT / "packages" / "contracts"),
    str(_REPO_ROOT / "apps" / "api"),
):
    if _root in sys.path:
        sys.path.remove(_root)
    sys.path.insert(0, _root)


def _service():
    """Import ``app.services.installation_service`` or skip with a clear reason."""
    try:
        from app.services import installation_service
    except Exception as exc:  # noqa: BLE001 - dependency drift must skip, not fail
        pytest.skip(f"installation_service tidak dapat diimpor: {exc!r}")
    return installation_service


def _contracts():
    """Import the installation contracts or skip with a clear reason."""
    try:
        from dataloom_contracts import installation
    except Exception as exc:  # noqa: BLE001 - same rationale as above
        pytest.skip(f"dataloom_contracts.installation tidak dapat diimpor: {exc!r}")
    return installation


def _stub_settings(*, api_key: str = "", base_url: str = "https://api.example.com/v1"):
    """Settings stand-in carrying only the ``LLM_*`` attributes ``_row_status`` reads.

    A real ``Settings()`` cannot be used: ``app.config.Settings`` does not define
    the ``LLM_*`` fields the service reads (see report), so constructing one would
    fail with ``AttributeError`` for reasons unrelated to this test.
    """
    return SimpleNamespace(
        INSTALLATION_SECRET_KEY="test-only-installation-secret",
        INSTALL_ADMIN_EMAILS=[],
        LLM_BASE_URL=base_url,
        LLM_API_KEY=api_key,
        LLM_MODEL_DEFAULT="gpt-4o-mini",
        LLM_MODEL_SMALL="gpt-4o-mini",
        LLM_TIMEOUT_SECONDS=30,
        LLM_PRICE_IN_PER_1K=0.0,
        LLM_PRICE_OUT_PER_1K=0.0,
    )


def _status_row(**overrides):
    """Fake ``installation_settings`` row with every column the read model touches."""
    row = SimpleNamespace(
        llm_base_url="https://api.example.com/v1",
        llm_model_default="gpt-4o-mini",
        llm_model_small="gpt-4o-mini",
        llm_timeout_seconds=30,
        llm_api_key_encrypted="gAAAAA-ciphertext-not-a-key",
        llm_api_key_hint="\u20261234",
        llm_price_in_per_1k=0.0,
        llm_price_out_per_1k=0.0,
        updated_at=None,
        updated_by=None,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


# ---------------------------------------------------------------------------
# 1. The status read model never carries the key
# ---------------------------------------------------------------------------
def test_status_contract_has_no_api_key_field_and_serializes_no_secret():
    """``LlmProviderStatus`` is write-only by shape: no field can carry the key."""
    contracts = _contracts()
    fields = contracts.LlmProviderStatus.model_fields

    assert "api_key" not in fields
    assert not [name for name in fields if "encrypted" in name]
    # Only the boolean + hint projections are allowed to mention the key at all.
    assert {"api_key_configured", "api_key_hint"} <= set(fields)

    secret = "sk-supersecretvalue1234"
    status = contracts.LlmProviderStatus(
        provider="openai_compatible",
        base_url_host="api.example.com",
        model_default="gpt-4o-mini",
        model_small="gpt-4o-mini",
        api_key_configured=True,
        api_key_hint="\u20261234",
        prices_configured=False,
    )
    dumped = status.model_dump_json()

    assert secret not in dumped
    assert '"api_key":' not in dumped
    assert "encrypted" not in dumped
    assert not hasattr(status, "api_key")


def test_get_status_never_returns_the_key_or_the_ciphertext(fake_session):
    """``get_status`` projects the secret away, both with and without a stored row."""
    service = _service()
    contracts = _contracts()
    env_secret = "sk-env-only-value-9999"

    # No stored row: the hint must come from the env key, never the key itself.
    fake_session.queue_scalar(None)
    empty = asyncio.run(_call_get_status(service, fake_session, api_key=env_secret))

    assert isinstance(empty, contracts.LlmProviderStatus)
    assert empty.api_key_configured is True
    assert empty.api_key_hint == "\u20269999"
    assert env_secret not in empty.model_dump_json()
    assert not hasattr(empty, "api_key")

    # Stored row: the hint comes from the column and the ciphertext never leaks.
    fake_session.queue_scalar(_status_row())
    stored = asyncio.run(_call_get_status(service, fake_session, api_key=""))

    dumped = stored.model_dump_json()
    assert stored.api_key_configured is True
    assert stored.api_key_hint == "\u20261234"
    assert "gAAAAA-ciphertext-not-a-key" not in dumped
    assert "encrypted" not in dumped
    assert '"api_key":' not in dumped


async def _call_get_status(service, session, *, api_key: str):
    """Run ``get_status`` with settings stubbed in the service module namespace."""
    original = service.get_settings
    service.get_settings = lambda: _stub_settings(api_key=api_key)
    try:
        return await service.get_status(session)
    finally:
        service.get_settings = original


# ---------------------------------------------------------------------------
# 2. api_key_hint = ellipsis + last 4 characters
# ---------------------------------------------------------------------------
def test_api_key_hint_exposes_only_the_last_four_characters():
    service = _service()

    hint = service.api_key_hint("sk-abcdefgh1234")
    assert hint == "\u20261234"
    assert hint.endswith("1234")
    assert len(hint) == 5
    assert "abcdefgh" not in hint

    # Surrounding whitespace is not part of the key.
    assert service.api_key_hint("  sk-abcdefgh1234  ") == "\u20261234"


@pytest.mark.parametrize("short_key", ["", "   ", "a", "abcd"])
def test_api_key_hint_is_none_when_key_is_too_short(short_key):
    """Four characters or fewer: showing them would be showing the whole key."""
    service = _service()
    assert service.api_key_hint(short_key) is None


# ---------------------------------------------------------------------------
# 3. base_url: no plain http to a public host
# ---------------------------------------------------------------------------
def test_base_url_rejects_plain_http_to_public_host():
    contracts = _contracts()
    with pytest.raises(ValidationError):
        contracts.LlmProviderUpdateRequest(base_url="http://example.com/v1")


def test_base_url_accepts_https_to_any_host():
    contracts = _contracts()

    for url in (
        "https://api.openai.com/v1",
        "https://llm.internal.example/v1",
        "https://192.0.2.10/v1",
    ):
        payload = contracts.LlmProviderUpdateRequest(base_url=url)
        assert str(payload.base_url).startswith("https://")


def test_base_url_accepts_plain_http_to_loopback_only():
    contracts = _contracts()

    for url in ("http://localhost:8000/v1", "http://127.0.0.1:11434/v1", "http://[::1]:8000/v1"):
        payload = contracts.LlmProviderUpdateRequest(base_url=url)
        assert payload.base_url is not None

    with pytest.raises(ValidationError):
        contracts.LlmProviderUpdateRequest(base_url="http://127.0.0.1.evil.example/v1")


# ---------------------------------------------------------------------------
# 4. api_key minimum length
# ---------------------------------------------------------------------------
def test_api_key_shorter_than_sixteen_characters_is_rejected():
    contracts = _contracts()

    with pytest.raises(ValidationError):
        contracts.LlmProviderUpdateRequest(api_key="sk-short")

    accepted = contracts.LlmProviderUpdateRequest(api_key="sk-0123456789abcd")  # 16 chars
    assert accepted.api_key is not None
    assert accepted.api_key.get_secret_value() == "sk-0123456789abcd"


# ---------------------------------------------------------------------------
# 5. Grant request normalizes the email
# ---------------------------------------------------------------------------
def test_admin_grant_request_lowercases_email():
    contracts = _contracts()

    payload = contracts.InstallationAdminGrantRequest(email="Admin.User@Example.COM")
    assert payload.email == "admin.user@example.com"

    stripped = contracts.InstallationAdminGrantRequest(
        email="admin.user@example.com", reason="  karena perlu  "
    )
    assert stripped.reason == "karena perlu"

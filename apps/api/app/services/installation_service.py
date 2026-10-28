"""Installation-wide LLM provider configuration (D-05, FR-036).

This module owns the ONE place where the deployment's LLM provider is stored.
It is deliberately separate from ``source_secrets``: the LLM key belongs to the
installation, not to a tenant, and it is encrypted with its own key
(``INSTALLATION_SECRET_KEY``) so rotating one key never invalidates the other.

Rules enforced here
-------------------
1. **The API key never leaves this module in plaintext.** ``get_status`` reads
   the stored hint column and a boolean; it does not even need to decrypt.
   Only ``test_connection`` decrypts, and only to build one HTTP request.
2. **Every mutating action writes an audit row.** The row is anchored to the
   installation admin's own organization because ``audit_logs`` is org-scoped
   by design (append-only + RLS); no new audit mechanism is introduced.
3. **No secret reaches a log sink.** Log lines carry the host, the status code
   and the latency, never the key, the ciphertext or the request headers.
4. **The stored value falls back to the environment.** D-05 says the provider
   is configured by env; the database row is an override an operator can edit
   without redeploying. ``_effective_*`` helpers express that precedence once.

Precedence for every field: ``installation_settings`` row -> ``Settings.LLM_*``
environment value -> empty (reported as "not configured", never as a fake).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.audit import record_audit
from app.core.errors import Conflict, NotFound, ValidationProblem
from app.db.models import InstallAdmin, InstallationSettings, User
from dataloom_contracts.installation import (
    InstallAdminOut,
    InstallationAdminGrantRequest,
    LlmProviderStatus,
    LlmProviderTestResult,
    LlmProviderUpdateRequest,
)

logger = logging.getLogger("dataloom.installation")

#: The provider is fixed by D-05 (one OpenAI-compatible adapter). Kept as a
#: constant so the status payload has a single source of truth.
LLM_PROVIDER = "openai_compatible"

#: Singleton row id, mirrored by the ``installation_settings_singleton`` CHECK.
_SETTINGS_ID = 1

#: Any value starting with this prefix is an unedited placeholder, i.e. "not
#: configured". Reporting it as configured would be a lie the UI cannot detect.
_PLACEHOLDER_PREFIX = "CHANGE_ME"

#: Hard ceiling for the connection test: a broken endpoint must not hold an API
#: request open for the full configured provider timeout.
_TEST_TIMEOUT_CEILING_SECONDS = 10.0

#: Hint length. The specification allows at most the last 4 characters.
_HINT_TAIL_CHARS = 4


# ---------------------------------------------------------------------------
# Actor
# ---------------------------------------------------------------------------
@dataclass(slots=True, frozen=True)
class InstallationActor:
    """Who performed an installation action, and where the audit row goes.

    ``org_id`` is not optional: ``audit_logs.org_id`` is NOT NULL and RLS-scoped,
    so every audited installation action must be anchored to the actor's own
    organization. Building this from ``AuthContext`` keeps FastAPI out of this
    module (services import no web framework).
    """

    org_id: uuid.UUID
    user_id: uuid.UUID | None = None
    email: str | None = None
    ip: str | None = None

    @classmethod
    def from_auth_context(cls, ctx: object, *, ip: str | None = None) -> "InstallationActor":
        """Adapt ``app.deps.AuthContext`` without importing it (avoids a cycle)."""
        user = getattr(ctx, "user", None)
        return cls(
            org_id=getattr(ctx, "org_id"),
            user_id=getattr(ctx, "user_id", None),
            email=getattr(user, "email", None),
            ip=ip,
        )


# ---------------------------------------------------------------------------
# Encryption
# ---------------------------------------------------------------------------
def _fernet() -> Fernet:
    """Derive the installation Fernet key from ``INSTALLATION_SECRET_KEY``.

    SHA-256 of a namespaced string gives exactly the 32 bytes Fernet wants, and
    the namespace guarantees that a key derived here can never decrypt a source
    config (and vice versa) even if the same env value is reused by mistake.
    """
    settings = get_settings()
    digest = hashlib.sha256(
        f"dataloom-installation-secret:{settings.INSTALLATION_SECRET_KEY}".encode("utf-8")
    ).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_api_key(plaintext: str) -> str:
    """Encrypt the LLM API key. Returns ASCII ciphertext for a TEXT column."""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_api_key(ciphertext: str) -> str:
    """Decrypt a value produced by :func:`encrypt_api_key`.

    Raises ``ValidationProblem`` (422) instead of leaking the exception text:
    the ciphertext itself must never appear in an error message.
    """
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeDecodeError) as exc:
        raise ValidationProblem(
            "API key LLM tersimpan tidak dapat didekripsi. "
            "Kunci INSTALLATION_SECRET_KEY mungkin berubah; simpan ulang API key."
        ) from exc


def api_key_hint(plaintext: str) -> str | None:
    """Return the ellipsis plus the last 4 characters, or ``None`` when too short.

    ``None`` for a key of 4 characters or fewer: showing the whole key as a
    "hint" would defeat the purpose of having a hint at all.
    """
    cleaned = (plaintext or "").strip()
    if len(cleaned) <= _HINT_TAIL_CHARS:
        return None
    return "\u2026" + cleaned[-_HINT_TAIL_CHARS:]


# ---------------------------------------------------------------------------
# Value resolution (row -> env -> empty)
# ---------------------------------------------------------------------------
def _is_configured(value: str | None) -> bool:
    """True when a value is a real setting, not blank and not a placeholder."""
    if value is None:
        return False
    cleaned = value.strip()
    return bool(cleaned) and not cleaned.startswith(_PLACEHOLDER_PREFIX)


def _env_fallback(settings: Settings) -> dict[str, object]:
    """Environment values that D-05 documents as the baseline configuration."""
    return {
        "base_url": settings.LLM_BASE_URL,
        "api_key": settings.LLM_API_KEY,
        "model_default": settings.LLM_MODEL_DEFAULT,
        "model_small": settings.LLM_MODEL_SMALL,
        "timeout_seconds": settings.LLM_TIMEOUT_SECONDS,
        "price_in_per_1k": settings.LLM_PRICE_IN_PER_1K,
        "price_out_per_1k": settings.LLM_PRICE_OUT_PER_1K,
    }


def _pick(row_value: object, env_value: object) -> object:
    """Row wins when it holds a usable value, otherwise the env value."""
    if isinstance(row_value, str):
        return row_value if _is_configured(row_value) else env_value
    if row_value is None:
        return env_value
    return row_value


def base_url_host(base_url: str | None) -> str:
    """Host only: no scheme, no path, no query, no credentials.

    The status payload must never turn a stored URL into a credential leak, so
    this is the ONLY projection of the URL that leaves the service.
    """
    if not base_url:
        return ""
    try:
        return (urlparse(base_url).hostname or "").strip().lower()
    except ValueError:  # pragma: no cover - urlparse is very permissive
        return ""


# ---------------------------------------------------------------------------
# Row access
# ---------------------------------------------------------------------------
async def _load_row(session: AsyncSession) -> InstallationSettings | None:
    return (
        await session.execute(
            select(InstallationSettings).where(InstallationSettings.id == _SETTINGS_ID)
        )
    ).scalar_one_or_none()


def _row_status(row: InstallationSettings | None, settings: Settings) -> LlmProviderStatus:
    """Build the read model from a row plus the environment fallback."""
    env = _env_fallback(settings)

    base_url = str(_pick(row.llm_base_url if row else None, env["base_url"]) or "")
    model_default = str(
        _pick(row.llm_model_default if row else None, env["model_default"]) or ""
    )
    model_small = str(_pick(row.llm_model_small if row else None, env["model_small"]) or "")

    # The hint comes from the stored column, so reading the status never
    # decrypts anything. The env key is only used when no row value exists.
    row_hint = row.llm_api_key_hint if row else None
    row_key_configured = bool(row and row.llm_api_key_encrypted)
    env_key = str(env["api_key"] or "")

    if row_key_configured:
        key_configured = True
        hint = row_hint
    elif _is_configured(env_key):
        key_configured = True
        hint = api_key_hint(env_key)
    else:
        key_configured = False
        hint = None

    price_in = row.llm_price_in_per_1k if row else None
    price_out = row.llm_price_out_per_1k if row else None
    if price_in is None:
        price_in = env["price_in_per_1k"]
    if price_out is None:
        price_out = env["price_out_per_1k"]
    prices_configured = float(price_in or 0.0) > 0.0 or float(price_out or 0.0) > 0.0

    return LlmProviderStatus(
        provider=LLM_PROVIDER,
        base_url_host=base_url_host(base_url),
        model_default=model_default,
        model_small=model_small,
        api_key_configured=key_configured,
        api_key_hint=hint,
        prices_configured=prices_configured,
        updated_at=row.updated_at if row else None,
        updated_by=row.updated_by if row else None,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
async def get_status(session: AsyncSession) -> LlmProviderStatus:
    """Current provider configuration with every secret projected away."""
    settings = get_settings()
    return _row_status(await _load_row(session), settings)


async def update_status(
    session: AsyncSession,
    payload: LlmProviderUpdateRequest,
    actor: InstallationActor,
) -> LlmProviderStatus:
    """Apply a partial update (PATCH semantics) and audit it.

    Omitted fields keep their stored value. An omitted ``api_key`` therefore
    never erases the existing key: replacing it requires sending a new one.
    The audit metadata lists which fields changed and the resulting host, never
    the key, its hint or the ciphertext.
    """
    row = await _load_row(session)
    if row is None:
        row = InstallationSettings(id=_SETTINGS_ID)
        session.add(row)

    changed: list[str] = []

    if payload.base_url is not None:
        row.llm_base_url = str(payload.base_url).rstrip("/")
        changed.append("base_url")
    if payload.model_default is not None:
        row.llm_model_default = payload.model_default
        changed.append("model_default")
    if payload.model_small is not None:
        row.llm_model_small = payload.model_small
        changed.append("model_small")
    if payload.timeout_seconds is not None:
        row.llm_timeout_seconds = payload.timeout_seconds
        changed.append("timeout_seconds")
    if payload.price_in_per_1k is not None:
        row.llm_price_in_per_1k = payload.price_in_per_1k
        changed.append("price_in_per_1k")
    if payload.price_out_per_1k is not None:
        row.llm_price_out_per_1k = payload.price_out_per_1k
        changed.append("price_out_per_1k")
    if payload.api_key is not None:
        # get_secret_value() is the only place the plaintext is read, and it is
        # immediately encrypted. It is never assigned to a variable that could
        # be logged or returned.
        row.llm_api_key_encrypted = encrypt_api_key(payload.api_key.get_secret_value())
        row.llm_api_key_hint = api_key_hint(payload.api_key.get_secret_value())
        changed.append("api_key")

    row.updated_at = datetime.now(tz=timezone.utc)
    row.updated_by = actor.email
    await session.flush()

    await record_audit(
        session,
        org_id=actor.org_id,
        action="installation.llm_update",
        target_type="installation_settings",
        actor_id=actor.user_id,
        actor_email=actor.email,
        target_id=str(_SETTINGS_ID),
        metadata={
            "changed_fields": changed,
            "base_url_host": base_url_host(row.llm_base_url),
            "model_default": row.llm_model_default,
            "model_small": row.llm_model_small,
            "api_key_replaced": "api_key" in changed,
        },
        ip=actor.ip,
    )
    return _row_status(row, get_settings())


async def _resolve_connection_target(
    session: AsyncSession,
) -> tuple[str, str, int]:
    """Return ``(base_url, api_key, timeout_seconds)`` for a live probe.

    Raises ``Conflict`` when the deployment has no usable configuration: a test
    against nothing would produce a misleading "unreachable" error.
    """
    settings = get_settings()
    env = _env_fallback(settings)
    row = await _load_row(session)

    base_url = str(_pick(row.llm_base_url if row else None, env["base_url"]) or "")
    if not base_url:
        raise Conflict("Base URL LLM belum dikonfigurasi.")

    if row is not None and row.llm_api_key_encrypted:
        api_key = decrypt_api_key(row.llm_api_key_encrypted)
    else:
        env_key = str(env["api_key"] or "")
        api_key = env_key if _is_configured(env_key) else ""

    timeout_raw = _pick(
        row.llm_timeout_seconds if row else None, env["timeout_seconds"]
    )
    timeout_seconds = int(timeout_raw or 30)
    return base_url, api_key, timeout_seconds


async def test_connection(session: AsyncSession, actor: InstallationActor) -> LlmProviderTestResult:
    """Probe ``GET {base_url}/models`` with a short timeout and audit the result.

    A provider failure is a normal answer (``ok=false`` with a structured
    code), not an exception: the UI must be able to show "key rejected" without
    the request looking like an API bug. The API key travels only in the
    ``Authorization`` header and is never logged; ``error_message`` is built
    from a fixed vocabulary so a provider body cannot inject text.
    """
    try:
        base_url, api_key, configured_timeout = await _resolve_connection_target(session)
    except ValidationProblem as exc:
        # Decryption failed: report it as a structured result, not a 422, so the
        # operator sees the same "test failed" surface as any other failure.
        result = LlmProviderTestResult(
            ok=False,
            latency_ms=None,
            error_code="decrypt_failed",
            error_message=str(exc.message),
        )
        await _audit_test(session, actor, result)
        return result

    timeout = min(float(configured_timeout), _TEST_TIMEOUT_CEILING_SECONDS)
    url = f"{base_url.rstrip('/')}/models"
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout), follow_redirects=False) as client:
            response = await client.get(url, headers=headers)
    except httpx.TimeoutException:
        result = LlmProviderTestResult(
            ok=False,
            latency_ms=_elapsed_ms(started),
            error_code="timeout",
            error_message="Penyedia LLM tidak merespons dalam batas waktu.",
        )
    except httpx.HTTPError as exc:
        # Only the exception CLASS is logged: a transport error message can
        # contain the full URL and, on some proxies, part of the headers.
        logger.warning("LLM test transport error (%s)", type(exc).__name__)
        result = LlmProviderTestResult(
            ok=False,
            latency_ms=_elapsed_ms(started),
            error_code="unreachable",
            error_message="Tidak dapat menghubungi penyedia LLM. Periksa base URL dan jaringan.",
        )
    else:
        latency = _elapsed_ms(started)
        if 200 <= response.status_code < 300:
            result = LlmProviderTestResult(
                ok=True, latency_ms=latency, error_code=None, error_message=None
            )
        else:
            code, message = _status_to_error(response.status_code)
            logger.warning(
                "LLM test failed status=%s host=%s latency_ms=%s",
                response.status_code,
                base_url_host(base_url),
                latency,
            )
            result = LlmProviderTestResult(
                ok=False, latency_ms=latency, error_code=code, error_message=message
            )

    await _audit_test(session, actor, result)
    return result


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _status_to_error(status_code: int) -> tuple[str, str]:
    """Map an HTTP status to ``(error_code, Indonesian message)``.

    The provider's body is deliberately ignored: it can echo the key prefix or
    the prompt back at us.
    """
    if status_code in (401, 403):
        return "unauthorized", "API key LLM ditolak oleh penyedia (401/403)."
    if status_code == 404:
        return "not_found", "Endpoint /models tidak ditemukan pada base URL ini (404)."
    if status_code == 429:
        return "rate_limited", "Kuota atau rate limit penyedia LLM tercapai (429)."
    if 500 <= status_code < 600:
        return "provider_error", f"Penyedia LLM mengalami galat internal ({status_code})."
    return "http_error", f"Penyedia LLM membalas HTTP {status_code}."


async def _audit_test(
    session: AsyncSession, actor: InstallationActor, result: LlmProviderTestResult
) -> None:
    """Audit one connection test. Never stores the key, the hint or the body."""
    await record_audit(
        session,
        org_id=actor.org_id,
        action="installation.llm_test",
        target_type="installation_settings",
        actor_id=actor.user_id,
        actor_email=actor.email,
        target_id=str(_SETTINGS_ID),
        metadata={
            "ok": result.ok,
            "latency_ms": result.latency_ms,
            "error_code": result.error_code,
        },
        ip=actor.ip,
    )


async def grant_install_admin(
    session: AsyncSession,
    payload: InstallationAdminGrantRequest,
    actor: InstallationActor,
) -> InstallAdminOut:
    """Grant the deployment-wide installation admin flag to an existing user.

    The user must already exist (this grants a flag, it does not invite people).
    Granting twice is a 409 rather than a silent no-op so an operator notices a
    stale assumption instead of believing a change happened.
    """
    email = payload.email.strip().lower()
    user = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if user is None:
        raise NotFound("Pengguna dengan email tersebut tidak ditemukan.")

    existing = (
        await session.execute(select(InstallAdmin).where(InstallAdmin.user_id == user.id))
    ).scalar_one_or_none()
    if existing is not None:
        raise Conflict("Pengguna tersebut sudah menjadi installation admin.")

    grant = InstallAdmin(
        user_id=user.id,
        granted_by=actor.email,
        reason=payload.reason,
    )
    session.add(grant)
    await session.flush()

    await record_audit(
        session,
        org_id=actor.org_id,
        action="installation.admin_grant",
        target_type="user",
        actor_id=actor.user_id,
        actor_email=actor.email,
        target_id=str(user.id),
        metadata={"granted_email": user.email, "reason": payload.reason},
        ip=actor.ip,
    )
    return InstallAdminOut(
        user_id=str(user.id),
        email=user.email,
        granted_by=grant.granted_by,
        reason=grant.reason,
        created_at=grant.created_at,
    )


async def is_install_admin(session: AsyncSession, user: User) -> bool:
    """True when ``user`` is an installation admin.

    Two independent sources, checked in this order:
    1. ``INSTALL_ADMIN_EMAILS`` env list (bootstrap; no DB row needed).
    2. An ``install_admins`` row for the user.

    The env list exists so the FIRST installation admin can be created without
    a chicken-and-egg problem: granting an admin already requires being one.
    """
    settings = get_settings()
    email = (user.email or "").strip().lower()
    bootstrap = {entry.strip().lower() for entry in settings.INSTALL_ADMIN_EMAILS}
    if email and email in bootstrap:
        return True
    row = (
        await session.execute(select(InstallAdmin).where(InstallAdmin.user_id == user.id))
    ).scalar_one_or_none()
    return row is not None


__all__ = [
    "LLM_PROVIDER",
    "InstallationActor",
    "api_key_hint",
    "base_url_host",
    "decrypt_api_key",
    "encrypt_api_key",
    "get_status",
    "grant_install_admin",
    "is_install_admin",
    "test_connection",
    "update_status",
]

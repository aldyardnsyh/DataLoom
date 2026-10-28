"""Installation-wide (deployment) LLM provider contracts (D-05, FR-036).

These models describe the configuration that an **installation admin** manages
for the whole deployment. They are deliberately separate from the
organization-scoped contracts: an organization role (viewer/editor/admin/owner)
never grants access to these endpoints, and an installation admin is not
necessarily a member of the organization whose UI reads the status.

Security invariants baked into this module
------------------------------------------
1. The LLM API key is **write-only**. No model here has a field that can carry
   the plaintext key or its ciphertext back to a client. ``LlmProviderStatus``
   exposes only ``api_key_configured`` plus a hint of at most four trailing
   characters.
2. ``LlmProviderStatus.base_url_host`` is the host only: no path, no query and
   no credentials. A misconfigured URL must not turn a status badge into a
   credential leak.
3. ``LlmProviderUpdateRequest.base_url`` refuses plain ``http`` to a public
   host, so the key is never sent over an unencrypted connection by accident.
"""

from __future__ import annotations

import ipaddress
from datetime import datetime
from urllib.parse import urlparse

from pydantic import EmailStr, Field, HttpUrl, SecretStr, field_validator

from ._base import ContractModel, ReadModel

#: Hostnames that always mean "this machine" (RFC 6761 reserves ``.localhost``).
_LOOPBACK_HOSTNAMES: frozenset[str] = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
    }
)


def _is_loopback_host(host: str) -> bool:
    """True when ``host`` is this machine (name or loopback address literal)."""
    normalized = host.strip().strip("[]").lower()
    if not normalized:
        return False
    if normalized in _LOOPBACK_HOSTNAMES or normalized.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback


class LlmProviderStatus(ReadModel):
    """Read-only view of the deployment LLM configuration.

    Contains no secret: ``api_key_configured`` is a boolean and
    ``api_key_hint`` is the ellipsis plus the last four characters of the key
    (``None`` when no key is stored). ``provider`` is fixed by the adapter
    decision (D-05) and is therefore informational, not editable.
    """

    provider: str = Field(
        description="Adapter name; fixed to 'openai_compatible' by D-05.",
        examples=["openai_compatible"],
    )
    base_url_host: str = Field(
        description=(
            "Host of the configured base URL only (no path, no query, no "
            "credentials). Empty string when nothing is configured."
        ),
        examples=["api.openai.com"],
    )
    model_default: str = Field(description="Model used for the heavy agents.")
    model_small: str = Field(description="Model used for cheap, short calls.")
    api_key_configured: bool = Field(
        description="True when an encrypted API key is stored. Never the key itself."
    )
    api_key_hint: str | None = Field(
        default=None,
        description="Ellipsis plus the last 4 characters, or null.",
        examples=["\u2026abcd"],
    )
    prices_configured: bool = Field(
        description=(
            "True when at least one price per 1K tokens is above 0.0. A 0.0 "
            "price means 'unknown', not 'free' (UV-25)."
        )
    )
    updated_at: datetime | None = Field(
        default=None, description="Last write to the settings row, null when never saved."
    )
    updated_by: str | None = Field(
        default=None, description="Email of the installation admin who saved last."
    )


class LlmProviderUpdateRequest(ContractModel):
    """PATCH-style update of the deployment LLM configuration.

    Every field is optional: omitted fields keep their stored value. Sending
    ``null`` explicitly is the same as omitting it, because there is no
    meaningful way to "unset" a provider setting other than replacing it.

    ``base_url`` validation
    -----------------------
    ``https`` to any host is accepted. Plain ``http`` is accepted **only** for a
    loopback host (``localhost``, ``127.0.0.1``, ``::1``, ``*.localhost``), which
    is how a local model server (vLLM, Ollama, LM Studio) is reached.

    Why: the API key travels in the ``Authorization`` header on every call. Over
    plain ``http`` to a public host that header is readable by any on-path
    observer, and the same URL is reused for the connection test, so a typo in
    the scheme would silently publish the credential. A loopback hop never
    leaves the machine, so ``http`` is safe there and refusing it would force
    every local deployment to fake a TLS certificate.

    Credentials inside the URL (``https://user:pass@host``) are rejected: the
    key belongs in ``api_key``, and a URL with userinfo would leak into logs and
    into ``base_url_host`` handling.
    """

    base_url: HttpUrl | None = Field(
        default=None,
        description="Provider base URL without '/chat/completions'.",
        examples=["https://api.openai.com/v1"],
    )
    api_key: SecretStr | None = Field(
        default=None,
        min_length=16,
        description="Write-only. Encrypted at rest and never returned.",
    )
    model_default: str | None = Field(default=None, min_length=1, max_length=128)
    model_small: str | None = Field(default=None, min_length=1, max_length=128)
    timeout_seconds: int | None = Field(default=None, ge=1, le=300)
    price_in_per_1k: float | None = Field(default=None, ge=0.0)
    price_out_per_1k: float | None = Field(default=None, ge=0.0)

    @field_validator("base_url")
    @classmethod
    def _check_base_url(cls, v: HttpUrl | None) -> HttpUrl | None:
        if v is None:
            return None
        parsed = urlparse(str(v))
        if parsed.username or parsed.password:
            raise ValueError(
                "URL tidak boleh memuat kredensial (user:pass@host). "
                "Isi API key pada field api_key."
            )
        host = (parsed.hostname or "").strip().lower()
        if not host:
            raise ValueError("URL harus memiliki host.")
        if parsed.scheme == "http" and not _is_loopback_host(host):
            raise ValueError(
                "http hanya diizinkan untuk host lokal (localhost, 127.0.0.1, ::1). "
                "Untuk host lain gunakan https agar API key tidak terkirim tanpa enkripsi."
            )
        return v

    @field_validator("model_default", "model_small")
    @classmethod
    def _strip_model(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Nama model tidak boleh kosong.")
        return cleaned


class LlmProviderTestResult(ReadModel):
    """Outcome of ``POST /v1/installation/llm/test``.

    A failed test is a normal 200 answer with ``ok=false``: the UI needs the
    structured reason, and a provider outage must not look like an API bug.
    ``error_message`` is built from a fixed vocabulary and never echoes the
    provider's response body (it can contain the key prefix or the prompt).
    """

    ok: bool
    latency_ms: int | None = Field(default=None, ge=0)
    error_code: str | None = Field(
        default=None,
        description=(
            "not_configured | decrypt_failed | timeout | unreachable | "
            "unauthorized | not_found | rate_limited | provider_error | http_error"
        ),
    )
    error_message: str | None = None


class InstallationAdminGrantRequest(ContractModel):
    """Grant the deployment-wide installation admin flag to an existing user.

    The user must already exist: this endpoint grants a flag, it does not invite
    people. ``reason`` is optional but recommended, because the grant is written
    to the audit trail.
    """

    email: EmailStr = Field(description="Email of an existing DataLoom user.")
    reason: str | None = Field(
        default=None,
        max_length=500,
        description="Why this user needs installation admin. Stored in the audit log.",
    )

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("reason")
    @classmethod
    def _strip_reason(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        return cleaned or None


class InstallAdminOut(ReadModel):
    """The granted flag, as returned by ``POST /v1/installation/admins``.

    Carries no secret: only the identity of the grantee and of the granter.
    """

    user_id: str = Field(description="Id of the user who now holds the flag.")
    email: str = Field(description="Email of the user who now holds the flag.")
    granted_by: str | None = Field(default=None, description="Email of the granter.")
    reason: str | None = Field(default=None, description="Why the flag was granted.")
    created_at: datetime | None = Field(default=None, description="When the flag was granted.")


__all__ = [
    "InstallAdminOut",
    "InstallationAdminGrantRequest",
    "LlmProviderStatus",
    "LlmProviderTestResult",
    "LlmProviderUpdateRequest",
]

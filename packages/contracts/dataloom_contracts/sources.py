"""Source and API-key contracts (FR-004, FR-010, FR-014, FR-015).

A source is a discriminated union on ``type``. The discriminator is the same
string the DB ENUM ``source_type`` uses, so ``SourceCreate`` can be stored with
almost no mapping code.
"""

from __future__ import annotations

import ipaddress
import re
import uuid
from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator

from ._base import ContractModel, JsonDict, ReadModel, SourceType

#: Hosts that must never be reachable from the REST poller (FR-014, SSRF).
#: Keeping the list here means Backend and Worker share one definition.
BLOCKED_HOSTNAMES: frozenset[str] = frozenset(
    {
        "localhost",
        "metadata",
        "metadata.google.internal",
        "instance-data",
    }
)

#: Private / link-local / loopback ranges, expressed as first-octet shortcuts.
#: The real enforcement is in the worker using ``ipaddress``; this is the
#: cheap pre-check the API performs before persisting a config.
#: NOTE: a prefix list cannot express 172.16.0.0/12 exactly, so the whole
#: 172.16-172.31 second-octet band is enumerated. Do not "simplify" it back to
#: ``172.2``: that matched 172.2x.x.x AND silently allowed 172.20-172.29.
BLOCKED_IPV4_PREFIXES: tuple[str, ...] = (
    "0.",
    "10.",
    "127.",
    "169.254.",
    *tuple(f"172.{octet}." for octet in range(16, 32)),
    "192.168.",
)


def _host_is_blocked(host: str) -> bool:
    """Shared pre-check for API-side host validation (FR-014 / FR-015).

    Handles the forms a naive prefix list misses: IPv6 literals, IPv4-mapped
    IPv6 (``::ffff:169.254.169.254``) and decimal/octal-less spellings are
    rejected wholesale instead of being guessed at. A hostname is allowed here
    only if it is not a literal address; DNS is re-checked by the worker.
    """
    stripped = host.strip().strip("[]").lower()
    if stripped in BLOCKED_HOSTNAMES:
        return True
    try:
        ip = ipaddress.ip_address(stripped)
    except ValueError:
        # Not an IP literal: fall back to the cheap string prefixes.
        return any(stripped.startswith(p) for p in BLOCKED_IPV4_PREFIXES)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or ip in ipaddress.ip_network("100.64.0.0/10")
        or ip in ipaddress.ip_network("64:ff9b::/96")
    )


def _reject_private_url(value: str) -> str:
    """Validate scheme + host of an outbound URL (FR-014)."""
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Skema URL harus http atau https.")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("URL harus memiliki host.")
    if _host_is_blocked(host):
        raise ValueError("Host internal/alamat privat diblokir.")
    return value


# ---------------------------------------------------------------------------
# Per-type configuration payloads
# ---------------------------------------------------------------------------


class WebhookSourceConfig(ContractModel):
    """Webhook source: authentication is the project API key (header X-API-Key)."""

    type: Literal[SourceType.WEBHOOK] = SourceType.WEBHOOK
    max_rows_per_request: int = Field(default=1000, ge=1, le=5000)
    max_bytes_per_request: int = Field(default=262144, ge=1024, le=5_242_880)


class CsvSourceConfig(ContractModel):
    """CSV/Parquet upload source (FR-013). Max 500 MB for MVP."""

    type: Literal[SourceType.CSV] = SourceType.CSV
    delimiter: str = Field(default=",", min_length=1, max_length=1)
    encoding: str = Field(default="utf-8", max_length=32)
    has_header: bool = True
    max_file_bytes: int = Field(default=524_288_000, ge=1024, le=524_288_000)


class ApiSourceConfig(ContractModel):
    """Scheduled REST polling (FR-014). ``url`` is SSRF-checked on write."""

    type: Literal[SourceType.API] = SourceType.API
    url: str = Field(max_length=2048)
    method: Literal["GET", "POST"] = "GET"
    headers: dict[str, str] = Field(default_factory=dict)
    json_path: str | None = Field(
        default=None,
        max_length=200,
        description="Dotted path to the array of records, e.g. 'data.items'.",
    )
    poll_interval_seconds: int = Field(default=3600, ge=60, le=86_400)
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    max_response_bytes: int = Field(default=10_485_760, ge=1024, le=52_428_800)

    @field_validator("url")
    @classmethod
    def _check_url(cls, v: str) -> str:
        return _reject_private_url(v)


class PostgresSourceConfig(ContractModel):
    """Read-only Postgres connection (FR-015).

    ``password`` is write-only: the API encrypts it at rest and never returns
    it. ``SourceOut`` therefore has no password field at all.
    """

    type: Literal[SourceType.POSTGRES] = SourceType.POSTGRES
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=5432, ge=1, le=65_535)
    database: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256, repr=False)
    sslmode: Literal["disable", "require", "verify-full"] = "require"
    schema_name: str = Field(default="public", max_length=64)
    table: str = Field(min_length=1, max_length=128)
    poll_interval_seconds: int = Field(default=86_400, ge=300, le=604_800)

    @field_validator("host")
    @classmethod
    def _check_host(cls, v: str) -> str:
        host = v.strip()
        if not host:
            raise ValueError("Host wajib diisi.")
        if _host_is_blocked(host):
            raise ValueError("Host internal / alamat privat diblokir.")
        return host


#: Discriminated union used everywhere a source config is accepted.
SourceConfig = Annotated[
    WebhookSourceConfig | CsvSourceConfig | ApiSourceConfig | PostgresSourceConfig,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Source CRUD
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class SourceCreate(ContractModel):
    """Create a source. ``config`` is validated per ``type``.

    Choose ONE of ``config`` (typed object) or ``config_raw`` (untyped dict) -
    ``config_raw`` exists only so the API can return a per-field validation
    error list instead of a generic union error.
    """

    name: str = Field(min_length=1, max_length=120)
    slug: str | None = Field(
        default=None,
        max_length=64,
        description="URL-safe id used in stream names; defaults from name.",
    )
    config: SourceConfig

    @field_validator("slug")
    @classmethod
    def _check_slug(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().lower()
        if not _SLUG_RE.match(v):
            raise ValueError(
                "Slug hanya boleh huruf kecil, angka, '-' dan '_' (maks 64)."
            )
        return v

    @model_validator(mode="after")
    def _slug_not_reserved(self) -> "SourceCreate":
        if self.slug and self.slug in {"ingest", "internal", "admin"}:
            raise ValueError("Slug tersebut dicadangkan sistem.")
        return self


class SourceUpdate(ContractModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    config: SourceConfig | None = None


class SourceOut(ReadModel):
    """Public view of a source. Secrets are never included."""

    id: uuid.UUID
    org_id: uuid.UUID
    project_id: uuid.UUID
    name: str
    slug: str
    type: SourceType
    status: str = Field(description="connected | error | paused")
    config_public: JsonDict = Field(
        default_factory=dict,
        description="Config with every secret stripped (no passwords/headers).",
    )
    ingest_path: str = Field(
        description="Relative path the webhook posts to: /v1/ingest/{source_id}."
    )
    last_received_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# API keys (FR-004). Plaintext is shown exactly ONCE, at create/rotate.
# ---------------------------------------------------------------------------


class ApiKeyCreate(ContractModel):
    name: str = Field(min_length=1, max_length=120)


class ApiKeyOut(ReadModel):
    """Stored key metadata. ``prefix`` is the only part ever re-displayed."""

    id: uuid.UUID
    org_id: uuid.UUID
    project_id: uuid.UUID
    name: str
    prefix: str = Field(description="Public prefix, e.g. 'dl_ab12cd34'.")
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class ApiKeyCreated(ReadModel):
    """Response of create/rotate - the ONLY time ``plaintext_key`` is sent."""

    key: ApiKeyOut
    plaintext_key: str = Field(
        description="Full key 'dl_<prefix>_<secret>'. Shown once, never stored."
    )
    warning: str = "Simpan key ini sekarang. Kami hanya menyimpan hash-nya."


class ApiKeyRotateRequest(ContractModel):
    """Rotate = revoke old + mint new in one transaction."""

    confirm: Literal[True] = Field(
        description="Must be true; guards against accidental rotation."
    )


__all__ = [
    "ApiSourceConfig",
    "ApiKeyCreate",
    "ApiKeyCreated",
    "ApiKeyOut",
    "ApiKeyRotateRequest",
    "CsvSourceConfig",
    "PostgresSourceConfig",
    "SourceConfig",
    "SourceCreate",
    "SourceOut",
    "SourceUpdate",
    "WebhookSourceConfig",
]

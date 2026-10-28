"""Envelope models shared by every endpoint.

Error contract is fixed by PRD section 9.7: ``{code, message, request_id}``.
Every list endpoint returns ``Paginated[T]`` so the UI has ONE pagination shape.
"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import Field

from ._base import ContractModel, ReadModel

T = TypeVar("T")


class ErrorResponse(ContractModel):
    """Uniform error body. Returned as-is for every 4xx/5xx.

    ``code`` is a stable machine string in SCREAMING_SNAKE_CASE
    (e.g. ``RATE_LIMITED``); the UI maps it to an i18n message (D-04), so
    ``message`` must stay human readable and short.
    """

    code: str = Field(
        description="Stable machine-readable error code, e.g. RATE_LIMITED.",
        examples=["VALIDATION_ERROR"],
    )
    message: str = Field(description="Human-readable Indonesian/English message.")
    request_id: str = Field(
        description="Correlates with logs and the X-Request-ID response header."
    )


class Paginated(ContractModel, Generic[T]):
    """One pagination shape for every list endpoint.

    Use ``next_cursor`` (opaque) rather than offset for large tables; ``total``
    is optional because counting gold tables can be expensive.
    """

    items: list[T]
    next_cursor: str | None = Field(
        default=None, description="Opaque cursor for the next page; null = last page."
    )
    has_more: bool = Field(default=False)
    total: int | None = Field(default=None, ge=0)


class OkResponse(ReadModel):
    """Minimal body for ``DELETE``-style endpoints that return no entity."""

    ok: bool = True


class ValidationIssue(ContractModel):
    """One human-readable validation problem.

    Used by the DAG validator, source-config validator and the gate checklist.
    ``field`` is a dotted path such as ``nodes[2].params.column`` so the UI can
    highlight the offending canvas node.
    """

    field: str = Field(description="Dotted path inside the submitted payload.")
    message: str = Field(description="Message written for a human, Indonesian.")

    model_config = ContractModel.model_config | {"extra": "forbid"}


class ValidationErrorResponse(ErrorResponse):
    """``ErrorResponse`` plus structured issues, used for 422 responses."""

    issues: list[ValidationIssue] = Field(default_factory=list)


__all__ = [
    "ErrorResponse",
    "OkResponse",
    "Paginated",
    "ValidationErrorResponse",
    "ValidationIssue",
]

"""Auth, user, org and membership contracts (FR-001, FR-002, FR-003)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field, field_validator

from ._base import ContractModel, ReadModel, Role


class RegisterRequest(ContractModel):
    """Create the first user AND (per D-01) their single organization."""

    email: str = Field(max_length=254, description="Lowercased + trimmed by validator.")
    password: str = Field(min_length=10, max_length=256)
    name: str = Field(min_length=1, max_length=120)
    org_name: str = Field(
        min_length=1,
        max_length=120,
        description="Display name of the auto-created organization.",
    )

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v or v.startswith("@") or v.endswith("@"):
            raise ValueError("Format email tidak valid.")
        return v


class LoginRequest(ContractModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=256)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, v: str) -> str:
        return v.strip().lower()


class UserOut(ReadModel):
    id: uuid.UUID
    email: str
    name: str
    created_at: datetime


class OrgOut(ReadModel):
    """Organization. ``llm_budget_usd_month`` is the cap enforced by FR-035."""

    id: uuid.UUID
    name: str
    plan: str
    llm_budget_usd_month: float = Field(ge=0.0)
    created_at: datetime


class MembershipOut(ReadModel):
    """Membership row joined with the user, ready for the Team settings page."""

    id: uuid.UUID
    org_id: uuid.UUID
    user_id: uuid.UUID
    role: Role
    user_email: str
    user_name: str
    created_at: datetime


class SessionOut(ReadModel):
    """Current-session bootstrap payload returned by register/login/me.

    ``csrf_token`` is the double-submit value the SPA echoes in the
    ``X-CSRF-Token`` header for mutations (D-07).
    """

    user: UserOut
    org: OrgOut
    membership: MembershipOut
    csrf_token: str
    expires_at: datetime
    is_install_admin: bool = Field(
        default=False,
        description=(
            "True when the user holds the deployment-wide installation admin flag. "
            "This is NOT an organization role: it gates /v1/installation/* mutations "
            "and the installation screens in the UI. Defaults to False so an older "
            "client never assumes elevated access."
        ),
    )


class RegisterResponse(SessionOut):
    """Same shape as ``SessionOut``; aliased for OpenAPI clarity."""


class LoginResponse(SessionOut):
    """Same shape as ``SessionOut``; aliased for OpenAPI clarity."""


class ChangePasswordRequest(ContractModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=10, max_length=256)


class UpdateMembershipRoleRequest(ContractModel):
    role: Role


__all__ = [
    "ChangePasswordRequest",
    "LoginRequest",
    "LoginResponse",
    "MembershipOut",
    "OrgOut",
    "RegisterRequest",
    "RegisterResponse",
    "SessionOut",
    "UpdateMembershipRoleRequest",
    "UserOut",
]

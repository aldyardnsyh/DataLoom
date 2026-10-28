"""FastAPI dependencies: auth, tenant context, RBAC, CSRF, project scoping.

Invariants enforced here (ADR-004):
1. ``org_id`` comes ONLY from a verified session -> membership lookup.
2. A resource belonging to another org raises **404**, not 403 (FR-003).
3. Every unsafe HTTP method requires a matching CSRF token.
4. Viewers are rejected with 403 on any mutation.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Cookie, Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.errors import Forbidden, NotFound, Unauthorized
from app.core.rbac import has_at_least
from app.core.security import constant_time_eq, sha256_hex
from app.config import get_settings as _get_settings
from app.db.models import Membership, Project, Session, User
from app.db.rls import set_tenant_context
from app.db.session import get_session
from dataloom_contracts._base import Role

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass(slots=True)
class AuthContext:
    """Everything a handler needs to authorise itself. No client input here."""

    user: User
    session: Session
    org_id: uuid.UUID
    org_name: str
    role: str

    @property
    def user_id(self) -> uuid.UUID:
        return self.user.id


async def get_current_session(
    request: Request,
    dl_session: str | None = Cookie(default=None, alias="dl_session"),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Session:
    """Resolve the opaque cookie to a live ``sessions`` row."""
    if not dl_session:
        raise Unauthorized("Anda belum masuk.")
    token_hash = sha256_hex(dl_session)
    row = (
        await session.execute(
            select(Session).where(Session.token_hash == token_hash)
        )
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        raise Unauthorized("Sesi tidak valid. Silakan masuk kembali.")
    if row.expires_at <= datetime.now(tz=timezone.utc):
        raise Unauthorized("Sesi sudah kedaluwarsa. Silakan masuk kembali.")
    return row


async def get_current_user(
    auth_session: Session = Depends(get_current_session),
    session: AsyncSession = Depends(get_session),
) -> User:
    user = (
        await session.execute(select(User).where(User.id == auth_session.user_id))
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        raise Unauthorized("Akun tidak aktif.")
    return user


async def _resolve_membership(
    session: AsyncSession, *, user_id: uuid.UUID, requested_org: uuid.UUID | None
) -> Membership:
    stmt = select(Membership).where(Membership.user_id == user_id)
    if requested_org is not None:
        stmt = stmt.where(Membership.org_id == requested_org)
    row = (await session.execute(stmt.limit(1))).scalar_one_or_none()
    if row is None:
        # D-01: one org per user, so "no membership" == "no access".
        raise Forbidden("Anda tidak memiliki akses ke organisasi ini.")
    return row


async def get_org_context(
    request: Request,
    auth_session: Session = Depends(get_current_session),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AuthContext:
    """Build the tenant context and set the RLS session variable."""
    membership = await _resolve_membership(
        session,
        user_id=user.id,
        requested_org=auth_session.org_id,
    )
    # RLS layer two: every subsequent query in this transaction is scoped.
    await set_tenant_context(session, membership.org_id)

    from app.db.models import Organization  # local import avoids cycle

    org = (
        await session.execute(
            select(Organization).where(Organization.id == membership.org_id)
        )
    ).scalar_one_or_none()
    if org is None:
        raise Forbidden("Organisasi tidak ditemukan.")

    return AuthContext(
        user=user,
        session=auth_session,
        org_id=membership.org_id,
        org_name=org.name,
        role=membership.role,
    )


def require_role(minimum: Role):
    """Dependency factory: caller must hold at least ``minimum``."""

    async def _dep(ctx: AuthContext = Depends(get_org_context)) -> AuthContext:
        if not has_at_least(ctx.role, minimum):
            raise Forbidden(
                f"Peran Anda ({ctx.role}) tidak diizinkan melakukan aksi ini."
            )
        return ctx

    return _dep


async def require_mutation(ctx: AuthContext = Depends(get_org_context)) -> AuthContext:
    """Viewer is read-only (FR-002). Applied to every unsafe method."""
    if not has_at_least(ctx.role, Role.EDITOR):
        raise Forbidden("Anda hanya memiliki akses lihat (viewer).")
    return ctx


async def require_install_admin(
    ctx: AuthContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_session),
) -> AuthContext:
    """Caller must hold the deployment-wide installation admin flag.

    Installation admin is NOT an organization role. An ``owner`` of an
    organization is a tenant-level role and never implies this flag: the LLM
    provider configured here is shared by every organization in the
    installation, so granting it must be an explicit, audited act.

    Two sources are accepted (see ``installation_service.is_install_admin``):
    1. The user's email appears in ``INSTALL_ADMIN_EMAILS`` (bootstrap, no DB
       row required). This is how the FIRST installation admin exists, because
       granting an admin already requires being one.
    2. An ``install_admins`` row exists for the user.

    A missing flag is 403 with the standard error envelope, not 404: the
    endpoint's existence is public knowledge (it is in the OpenAPI document),
    so hiding it would only make the failure harder to diagnose.
    """
    from app.services.installation_service import is_install_admin  # local: avoids cycle

    if not await is_install_admin(session, ctx.user):
        raise Forbidden(
            "Aksi ini memerlukan hak installation admin, bukan peran organisasi."
        )
    return ctx


async def require_org_admin_or_install_admin(
    ctx: AuthContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_session),
) -> AuthContext:
    """Read access to installation status: org admin/owner OR install admin.

    The user-facing settings page needs to render a read-only provider card, so
    an organization ``admin``/``owner`` may read the status. An installation
    admin may also read it even when their own organization role is lower
    (installation admin is orthogonal to the org role, so the two must be OR-ed
    rather than ranked against each other).
    """
    from app.services.installation_service import is_install_admin  # local: avoids cycle

    if has_at_least(ctx.role, Role.ADMIN):
        return ctx
    if await is_install_admin(session, ctx.user):
        return ctx
    raise Forbidden(
        "Hanya admin/owner organisasi atau installation admin yang dapat membaca status ini."
    )


async def verify_csrf(
    request: Request,
    dl_session: str | None = Cookie(default=None, alias="dl_session"),
    dl_csrf: str | None = Cookie(default=None, alias="dl_csrf"),
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    auth_session: Session = Depends(get_current_session),
) -> None:
    """Double-submit CSRF check for unsafe methods."""
    if request.method in _SAFE_METHODS:
        return
    if not dl_csrf or not x_csrf_token:
        raise Forbidden("Token CSRF tidak ada.")
    if not constant_time_eq(dl_csrf, x_csrf_token):
        raise Forbidden("Token CSRF tidak cocok.")
    if not constant_time_eq(sha256_hex(x_csrf_token), auth_session.csrf_token_hash):
        raise Forbidden("Token CSRF tidak valid untuk sesi ini.")


async def get_project_or_404(
    project_id: uuid.UUID,
    ctx: AuthContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_session),
) -> Project:
    """Fetch a project **scoped to the caller's org**; 404 otherwise (FR-003)."""
    project = (
        await session.execute(
            select(Project).where(
                Project.id == project_id, Project.org_id == ctx.org_id
            )
        )
    ).scalar_one_or_none()
    if project is None:
        raise NotFound("Proyek tidak ditemukan.")
    return project


def client_ip(request: Request) -> str | None:
    """Best-effort client IP for audit rows (proxy header only when trusted)."""
    settings = _get_settings()
    if "*" not in settings.TRUSTED_HOSTS:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()[:64]
    if request.client:
        return request.client.host[:64]
    return None

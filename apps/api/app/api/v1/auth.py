"""Auth routes (FR-001)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.audit import record_audit
from app.core.errors import Forbidden, Unauthorized
from app.core.ratelimit import login_rate_limit_ok
from app.core.security import constant_time_eq, sha256_hex
from app.db.models import Membership, Organization
from app.db.session import get_session
from app.deps import AuthContext, client_ip, get_org_context
from app.services import auth_service, installation_service
from dataloom_contracts.auth import (
    LoginRequest,
    LoginResponse,
    MembershipOut,
    OrgOut,
    RegisterRequest,
    RegisterResponse,
    SessionOut,
    UserOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _membership_out(m: Membership, user_email: str, user_name: str) -> MembershipOut:
    return MembershipOut(
        id=m.id,
        org_id=m.org_id,
        user_id=m.user_id,
        role=m.role,
        user_email=user_email,
        user_name=user_name,
        created_at=m.created_at,
    )


def _org_out(org: Organization) -> OrgOut:
    return OrgOut(
        id=org.id,
        name=org.name,
        plan=org.plan,
        llm_budget_usd_month=float(org.llm_budget_usd_month),
        created_at=org.created_at,
    )


def _user_out(user) -> UserOut:  # noqa: ANN001
    return UserOut(
        id=user.id, email=user.email, name=user.name, created_at=user.created_at
    )


def _set_cookies(
    response: Response, session_token: str, csrf_token: str, max_age: int
) -> None:
    """HttpOnly session cookie + JS-readable CSRF cookie (double submit)."""
    settings = get_settings()
    response.set_cookie(
        settings.SESSION_COOKIE_NAME,
        session_token,
        max_age=max_age,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        settings.CSRF_COOKIE_NAME,
        csrf_token,
        max_age=max_age,
        httponly=False,  # must be readable by the SPA (double-submit)
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


def _clear_cookies(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(settings.SESSION_COOKIE_NAME, path="/")
    response.delete_cookie(settings.CSRF_COOKIE_NAME, path="/")


@router.post("/register", response_model=RegisterResponse, status_code=201)
async def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> RegisterResponse:
    user, org, membership = await auth_service.register_user(
        session,
        email=payload.email,
        password=payload.password,
        name=payload.name,
        org_name=payload.org_name,
    )
    row, plaintext, csrf = await auth_service.create_session(
        session,
        user=user,
        org_id=org.id,
        ip=client_ip(request),
        ua=request.headers.get("user-agent"),
    )
    await record_audit(
        session,
        org_id=org.id,
        action="auth.register",
        target_type="user",
        actor_id=user.id,
        actor_email=user.email,
        target_id=str(user.id),
        ip=client_ip(request),
    )
    _set_cookies(response, plaintext, csrf, auth_service.session_ttl_seconds())
    return RegisterResponse(
        user=_user_out(user),
        org=_org_out(org),
        membership=_membership_out(membership, user.email, user.name),
        csrf_token=csrf,
        expires_at=row.expires_at,
        # A brand-new user is normally not an installation admin, but the
        # bootstrap env list may name them: ask the single source of truth
        # instead of hardcoding False and disagreeing with /v1/installation.
        is_install_admin=await installation_service.is_install_admin(session, user),
    )


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> LoginResponse:
    ip = client_ip(request) or "unknown"
    if not await login_rate_limit_ok(f"{ip}:{payload.email.lower()}"):
        raise Unauthorized("Terlalu banyak percobaan masuk. Coba lagi sebentar lagi.")

    user, org, membership = await auth_service.authenticate(
        session, email=payload.email, password=payload.password
    )
    row, plaintext, csrf = await auth_service.create_session(
        session,
        user=user,
        org_id=org.id,
        ip=client_ip(request),
        ua=request.headers.get("user-agent"),
    )
    await record_audit(
        session,
        org_id=org.id,
        action="auth.login",
        target_type="user",
        actor_id=user.id,
        actor_email=user.email,
        target_id=str(user.id),
        ip=client_ip(request),
    )
    _set_cookies(response, plaintext, csrf, auth_service.session_ttl_seconds())
    return LoginResponse(
        user=_user_out(user),
        org=_org_out(org),
        membership=_membership_out(membership, user.email, user.name),
        csrf_token=csrf,
        expires_at=row.expires_at,
        is_install_admin=await installation_service.is_install_admin(session, user),
    )


@router.post("/logout", status_code=204, response_class=Response)
async def logout(
    request: Request,
    response: Response,
    ctx: AuthContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await auth_service.revoke_session(session, ctx.session)
    await record_audit(
        session,
        org_id=ctx.org_id,
        action="auth.logout",
        target_type="session",
        actor_id=ctx.user_id,
        actor_email=ctx.user.email,
        target_id=str(ctx.session.id),
        ip=client_ip(request),
    )
    _clear_cookies(response)
    return Response(status_code=204)


@router.get("/me", response_model=SessionOut)
async def me(
    ctx: AuthContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_session),
) -> SessionOut:
    """Bootstrap payload for the SPA.

    ``csrf_token`` is returned only when the caller proves it already holds it
    (echoing the cookie), otherwise it is empty and the SPA must re-login.
    The CSRF cookie is non-HttpOnly, so the SPA can read it directly anyway;
    we therefore never re-issue the secret here.
    """
    membership = (
        await session.execute(
            select(Membership).where(
                Membership.user_id == ctx.user_id, Membership.org_id == ctx.org_id
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise Forbidden("Keanggotaan tidak ditemukan.")
    org = (
        await session.execute(
            select(Organization).where(Organization.id == ctx.org_id)
        )
    ).scalar_one()

    return SessionOut(
        user=_user_out(ctx.user),
        org=_org_out(org),
        membership=_membership_out(membership, ctx.user.email, ctx.user.name),
        csrf_token="",
        expires_at=ctx.session.expires_at,
        # The SPA needs this to decide whether the installation screens are
        # usable; it is a boolean, never the flag's source of truth (the API
        # re-checks it on every mutation).
        is_install_admin=await installation_service.is_install_admin(session, ctx.user),
    )


@router.get("/csrf", status_code=204, response_class=Response)
async def csrf_status(
    ctx: AuthContext = Depends(get_org_context),
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> Response:
    """Verify (without changing) that the client holds a matching CSRF token."""
    if not x_csrf_token or not constant_time_eq(
        sha256_hex(x_csrf_token), ctx.session.csrf_token_hash
    ):
        raise Forbidden("Token CSRF tidak valid.")
    return Response(status_code=204)

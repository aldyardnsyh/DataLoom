"""Installation administration: the deployment-wide LLM provider (D-05, FR-036).

This router is intentionally NOT org-scoped in the RBAC sense. Reading the
status is allowed for an organization ``admin``/``owner`` so the product UI can
show a read-only "LLM provider" card, but every mutation requires the
installation admin flag (``deps.require_install_admin``), which no organization
role grants.

No endpoint here returns the API key. The response models do not even have a
field that could carry it: ``LlmProviderStatus`` exposes ``api_key_configured``
plus a four-character hint, and nothing else.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.deps import (
    AuthContext,
    client_ip,
    require_install_admin,
    require_org_admin_or_install_admin,
    verify_csrf,
)
from app.services import installation_service
from dataloom_contracts.installation import (
    InstallAdminOut,
    InstallationAdminGrantRequest,
    LlmProviderStatus,
    LlmProviderTestResult,
    LlmProviderUpdateRequest,
)

router = APIRouter(prefix="/installation", tags=["installation"])


def _actor(ctx: AuthContext, request: Request) -> installation_service.InstallationActor:
    """Adapt the auth context for the service layer (which imports no FastAPI)."""
    return installation_service.InstallationActor.from_auth_context(
        ctx, ip=client_ip(request)
    )


@router.get("/llm", response_model=LlmProviderStatus)
async def get_llm_provider(
    ctx: AuthContext = Depends(require_org_admin_or_install_admin),
    session: AsyncSession = Depends(get_session),
) -> LlmProviderStatus:
    """Read the provider status. Org admin/owner or install admin; no secret."""
    return await installation_service.get_status(session)


@router.put(
    "/llm",
    response_model=LlmProviderStatus,
    dependencies=[Depends(verify_csrf)],
)
async def update_llm_provider(
    payload: LlmProviderUpdateRequest,
    request: Request,
    ctx: AuthContext = Depends(require_install_admin),
    session: AsyncSession = Depends(get_session),
) -> LlmProviderStatus:
    """Replace part of the provider configuration (PATCH semantics)."""
    return await installation_service.update_status(session, payload, _actor(ctx, request))


@router.post(
    "/llm/test",
    response_model=LlmProviderTestResult,
    dependencies=[Depends(verify_csrf)],
)
async def test_llm_provider(
    request: Request,
    ctx: AuthContext = Depends(require_install_admin),
    session: AsyncSession = Depends(get_session),
) -> LlmProviderTestResult:
    """Probe the configured provider. A failure is a 200 with ``ok=false``.

    Returning 200 for a provider outage keeps the UI logic simple: the body
    carries ``error_code`` and a human message, while a 4xx/5xx would mean "the
    API call itself was wrong".
    """
    return await installation_service.test_connection(session, _actor(ctx, request))


@router.post(
    "/admins",
    response_model=InstallAdminOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_csrf)],
)
async def grant_installation_admin(
    payload: InstallationAdminGrantRequest,
    request: Request,
    ctx: AuthContext = Depends(require_install_admin),
    session: AsyncSession = Depends(get_session),
) -> InstallAdminOut:
    """Grant the installation admin flag to an existing user.

    The first installation admin cannot be created here (that would be
    self-bootstrapping): set ``INSTALL_ADMIN_EMAILS`` for the first one, then
    use this endpoint for every admin after that.
    """
    return await installation_service.grant_install_admin(
        session, payload, _actor(ctx, request)
    )

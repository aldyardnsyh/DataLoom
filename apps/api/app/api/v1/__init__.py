"""Router aggregation for ``/v1``."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    agents,
    api_keys,
    auth,
    explore,
    ingest,
    installation,
    pipelines,
    projects,
    runs,
    settings,
    sources,
    text_to_sql,
    uploads,
    usage,
)

api_router = APIRouter(prefix="/v1")
api_router.include_router(auth.router)
api_router.include_router(projects.router)
api_router.include_router(sources.router)
api_router.include_router(api_keys.router)
api_router.include_router(uploads.router)
api_router.include_router(pipelines.router)
api_router.include_router(runs.router)
api_router.include_router(agents.router)
api_router.include_router(explore.router)
api_router.include_router(text_to_sql.router)
api_router.include_router(usage.router)
api_router.include_router(settings.router)
api_router.include_router(installation.router)
api_router.include_router(ingest.router)

__all__ = ["api_router"]

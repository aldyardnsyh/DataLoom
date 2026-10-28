"""Idempotency cleanup + run enqueue helpers.

Kept separate from ``ingest_service`` so the hot path module stays small.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.errors import Conflict, NotFound, ValidationProblem
from app.db.models import IngestIdempotency, Pipeline, PipelineVersion, Project, Run
from dataloom_contracts._base import RunStatus

#: Statuses a user may request cancellation from. Mirrors the ``CANCELLED`` row
#: of ``ALLOWED`` in ``apps/worker/app/transition.py`` (cross-app import is
#: forbidden by ADR-001, so the subset is restated and must be kept in sync).
ALLOWED_CANCEL_FROM: frozenset[str] = frozenset(
    {
        RunStatus.QUEUED.value,
        RunStatus.RUNNING.value,
        RunStatus.RETRYING.value,
        RunStatus.NEEDS_ATTENTION.value,
    }
)


async def purge_expired_idempotency(session: AsyncSession) -> int:
    """Delete idempotency rows older than the 24 h window (FR-016)."""
    settings = get_settings()
    cutoff = datetime.now(tz=timezone.utc) - timedelta(
        hours=settings.INGEST_IDEMPOTENCY_TTL_HOURS
    )
    result = await session.execute(
        delete(IngestIdempotency).where(IngestIdempotency.created_at < cutoff)
    )
    return int(result.rowcount or 0)


async def enqueue_run(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    pipeline_id: uuid.UUID,
    user_id: uuid.UUID,
    trigger: str = "manual",
    idempotency_key: str | None = None,
) -> Run:
    """Create a queued run pinned to the pipeline's current version (FR-024)."""
    if idempotency_key:
        existing = (
            await session.execute(
                select(Run).where(
                    Run.org_id == org_id, Run.idempotency_key == idempotency_key
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

    pipeline = (
        await session.execute(
            select(Pipeline).where(
                Pipeline.id == pipeline_id, Pipeline.org_id == org_id
            )
        )
    ).scalar_one_or_none()
    if pipeline is None:
        raise NotFound("Pipeline tidak ditemukan.")
    if pipeline.current_version_id is None:
        raise Conflict("Pipeline belum memiliki versi yang valid.")

    version = (
        await session.execute(
            select(PipelineVersion).where(
                PipelineVersion.id == pipeline.current_version_id,
                PipelineVersion.org_id == org_id,
            )
        )
    ).scalar_one_or_none()
    if version is None:
        raise NotFound("Versi pipeline tidak ditemukan.")
    if version.validation_status != "valid":
        raise ValidationProblem(
            "Versi pipeline belum valid. Jalankan validasi terlebih dahulu."
        )

    project = (
        await session.execute(
            select(Project).where(
                Project.id == pipeline.project_id, Project.org_id == org_id
            )
        )
    ).scalar_one_or_none()
    if project is None:
        raise NotFound("Proyek tidak ditemukan.")

    # Concurrency cap (FR-028): count active runs for this project.
    settings_json = project.settings_json or {}
    max_concurrent = int(settings_json.get("max_concurrent_runs", 1))
    active = (
        await session.execute(
            select(Run.id).where(
                Run.project_id == project.id,
                Run.org_id == org_id,
                Run.status.in_(["queued", "running", "retrying"]),
            )
        )
    ).all()
    if len(active) >= max_concurrent:
        raise Conflict(
            f"Batas {max_concurrent} run konkuren untuk proyek ini sudah tercapai."
        )

    now = datetime.now(tz=timezone.utc)
    run = Run(
        org_id=org_id,
        project_id=pipeline.project_id,
        pipeline_id=pipeline.id,
        pipeline_version_id=version.id,
        version=version.version,
        trigger=trigger,
        status=RunStatus.QUEUED.value,
        triggered_by_user_id=user_id,
        queued_at=now,
        idempotency_key=idempotency_key,
    )
    session.add(run)
    await session.flush()
    return run


async def cancel_run(session: AsyncSession, *, org_id: uuid.UUID, run_id: uuid.UUID) -> Run:
    """Request cancellation. A running node finishes, then the loop stops.

    SEC-01 (was OPEN, now closed): this used to ``UPDATE`` ``status`` directly,
    bypassing ``transition()`` and its ``ALLOWED`` table. The review of Phases
    2–6 made it reachable from ``DELETE /v1/runs/{id}``, so the write is now
    guarded three ways:

    1. ``SELECT ... FOR UPDATE`` — the worker's own ``transition()`` takes the
       same row lock, so a cancel cannot interleave with e.g. ``SUCCEEDED``.
    2. The transition is checked against ``ALLOWED_CANCEL_FROM`` (same shape as
       ``apps/worker/app/transition.py``).
    3. ``finished_at`` is only set for ``queued``/``retrying`` — for a ``running``
       run the worker finishes the in-flight node and performs the terminal
       transition itself (documented behaviour: "a running node finishes").
    """
    run = (
        await session.execute(
            select(Run).where(Run.id == run_id, Run.org_id == org_id).with_for_update()
        )
    ).scalar_one_or_none()
    if run is None:
        raise NotFound("Run tidak ditemukan.")
    if run.status not in ALLOWED_CANCEL_FROM:
        raise Conflict(f"Run berstatus '{run.status}' tidak dapat dibatalkan.")
    values: dict[str, object] = {"status": RunStatus.CANCELLED.value}
    if run.status in (RunStatus.QUEUED.value, RunStatus.RETRYING.value):
        # Nothing is executing yet: the run is terminal right now.
        values["finished_at"] = datetime.now(tz=timezone.utc)
    await session.execute(
        update(Run).where(Run.id == run.id, Run.org_id == org_id).values(**values)
    )
    await session.refresh(run)
    return run

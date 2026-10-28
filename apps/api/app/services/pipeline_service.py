"""Pipeline CRUD, DAG validation and immutable versions (FR-020, FR-024, FR-027).

Versions are append-only: editing a pipeline creates version N+1 and the old
row is never mutated, so an old run always resolves to the DAG it ran.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, ValidationProblem
from app.db.models import Pipeline, PipelineVersion, Run
from dataloom_contracts._base import ActorKind, ValidationStatus
from dataloom_contracts.pipelines import PipelineDAG


def format_dag_errors(exc: ValidationError) -> list[dict[str, Any]]:
    """Turn a Pydantic error into UI-friendly Indonesian node issues."""
    issues: list[dict[str, Any]] = []
    for error in exc.errors():
        location = list(error.get("loc", ()))
        node_id = None
        # loc looks like ('nodes', 3, 'params', ...) -> capture node index.
        if len(location) >= 2 and location[0] == "nodes" and isinstance(location[1], int):
            node_id = str(location[1])
        issues.append(
            {
                "loc": location,
                "node_ref": node_id,
                "msg": error.get("msg", "Tidak valid"),
            }
        )
    return issues


def validate_dag_payload(payload: dict[str, Any]) -> tuple[PipelineDAG | None, list[dict[str, Any]]]:
    """Run the deterministic validator. Returns ``(dag, issues)``; never raises."""
    try:
        dag = PipelineDAG.model_validate(payload)
    except ValidationError as exc:
        return None, format_dag_errors(exc)
    return dag, []


async def get_pipeline_scoped(
    session: AsyncSession, *, org_id: uuid.UUID, pipeline_id: uuid.UUID
) -> Pipeline:
    pipeline = (
        await session.execute(
            select(Pipeline).where(
                Pipeline.id == pipeline_id, Pipeline.org_id == org_id
            )
        )
    ).scalar_one_or_none()
    if pipeline is None:
        raise NotFound("Pipeline tidak ditemukan.")
    return pipeline


async def next_version_number(session: AsyncSession, pipeline_id: uuid.UUID) -> int:
    current = (
        await session.execute(
            select(func.max(PipelineVersion.version)).where(
                PipelineVersion.pipeline_id == pipeline_id
            )
        )
    ).scalar()
    return int(current or 0) + 1


async def create_version(
    session: AsyncSession,
    *,
    pipeline: Pipeline,
    dag: PipelineDAG,
    created_by_kind: ActorKind,
    created_by_user_id: uuid.UUID | None = None,
    created_by_agent_run_id: uuid.UUID | None = None,
    change_note: str | None = None,
) -> PipelineVersion:
    """Append a new immutable version. Caller has already validated ``dag``."""
    version_number = await next_version_number(session, pipeline.id)
    parent_id = pipeline.current_version_id

    version = PipelineVersion(
        org_id=pipeline.org_id,
        pipeline_id=pipeline.id,
        version=version_number,
        dag_json=dag.model_dump(mode="json", by_alias=True),
        validation_status=ValidationStatus.VALID.value,
        validation_errors=[],
        created_by_kind=created_by_kind.value,
        created_by_user_id=created_by_user_id,
        created_by_agent_run_id=created_by_agent_run_id,
        parent_version_id=parent_id,
        change_note=(change_note or "")[:500] or None,
    )
    session.add(version)
    await session.flush()
    pipeline.current_version_id = version.id
    await session.flush()
    return version


async def create_pipeline(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    name: str,
    description: str | None,
    dag_payload: dict[str, Any] | None,
    user_id: uuid.UUID,
) -> Pipeline:
    """Create a pipeline; when a DAG is supplied it becomes version 1."""
    dag, issues = (None, [])
    if dag_payload is not None:
        dag, issues = validate_dag_payload(dag_payload)
        if dag is None:
            raise ValidationProblem("DAG pipeline tidak valid.", details=issues)

    pipeline = Pipeline(
        org_id=org_id,
        project_id=project_id,
        name=name.strip(),
        description=description,
        is_active=True,
    )
    session.add(pipeline)
    await session.flush()

    if dag is not None:
        await create_version(
            session,
            pipeline=pipeline,
            dag=dag,
            created_by_kind=ActorKind.USER,
            created_by_user_id=user_id,
            change_note="Versi awal",
        )
    return pipeline


async def replace_dag(
    session: AsyncSession,
    *,
    pipeline: Pipeline,
    dag_payload: dict[str, Any],
    user_id: uuid.UUID,
    change_note: str | None = None,
) -> PipelineVersion:
    """Validate then append. An invalid DAG never becomes a version."""
    dag, issues = validate_dag_payload(dag_payload)
    if dag is None:
        raise ValidationProblem("DAG pipeline tidak valid.", details=issues)
    return await create_version(
        session,
        pipeline=pipeline,
        dag=dag,
        created_by_kind=ActorKind.USER,
        created_by_user_id=user_id,
        change_note=change_note,
    )


async def list_versions(
    session: AsyncSession, *, org_id: uuid.UUID, pipeline_id: uuid.UUID
) -> list[PipelineVersion]:
    return list(
        (
            await session.execute(
                select(PipelineVersion)
                .where(
                    PipelineVersion.org_id == org_id,
                    PipelineVersion.pipeline_id == pipeline_id,
                )
                .order_by(PipelineVersion.version.desc())
            )
        ).scalars()
    )


async def get_version(
    session: AsyncSession, *, org_id: uuid.UUID, version_id: uuid.UUID
) -> PipelineVersion:
    version = (
        await session.execute(
            select(PipelineVersion).where(
                PipelineVersion.id == version_id, PipelineVersion.org_id == org_id
            )
        )
    ).scalar_one_or_none()
    if version is None:
        raise NotFound("Versi pipeline tidak ditemukan.")
    return version


async def lineage_for_project(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> dict[str, Any]:
    """Dataset-level lineage: source -> bronze -> silver -> gold (FR-027).

    Built from real rows only: a layer missing from the DB simply produces no
    edges, so the UI shows an honest partial graph.
    """
    from app.db.models import Dataset, Source

    sources = (
        await session.execute(
            select(Source).where(
                Source.org_id == org_id, Source.project_id == project_id
            )
        )
    ).scalars().all()
    datasets = (
        await session.execute(
            select(Dataset).where(
                Dataset.org_id == org_id, Dataset.project_id == project_id
            )
        )
    ).scalars().all()

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for source in sources:
        nodes.append(
            {
                "id": f"source:{source.id}",
                "label": source.name,
                "kind": "source",
                "layer": None,
            }
        )
    layer_order = ["bronze", "silver", "gold", "quarantine"]
    for dataset in datasets:
        nodes.append(
            {
                "id": f"dataset:{dataset.id}",
                "label": dataset.name,
                "kind": "dataset",
                "layer": dataset.layer,
            }
        )
        if dataset.source_id:
            edges.append(
                {
                    "source": f"source:{dataset.source_id}",
                    "target": f"dataset:{dataset.id}",
                }
            )

    # Chain datasets of the same name across layers (bronze_x -> silver_x -> gold_x).
    by_stem: dict[str, dict[str, str]] = {}
    for dataset in datasets:
        stem = dataset.name.split("_", 1)[-1] if "_" in dataset.name else dataset.name
        by_stem.setdefault(stem, {})[dataset.layer] = f"dataset:{dataset.id}"
    for stem, layers in by_stem.items():
        ordered = [layers.get(layer) for layer in layer_order if layers.get(layer)]
        for left, right in zip(ordered, ordered[1:], strict=False):
            edges.append({"source": left, "target": right})

    return {"nodes": nodes, "edges": edges}


async def recent_run_status(
    session: AsyncSession, *, org_id: uuid.UUID, pipeline_id: uuid.UUID
) -> tuple[str | None, datetime | None]:
    row = (
        await session.execute(
            select(Run.status, Run.created_at)
            .where(Run.org_id == org_id, Run.pipeline_id == pipeline_id)
            .order_by(Run.created_at.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return None, None
    return row[0], row[1]


def now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def ensure_single_active_version(pipeline: Pipeline) -> None:
    """Guard: a pipeline must always point at an existing version."""
    if pipeline.current_version_id is None:
        raise Conflict("Pipeline belum memiliki versi aktif.")

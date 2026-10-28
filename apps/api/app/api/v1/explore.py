"""Data Explorer API (FR-041): datasets, preview, quality, lineage (FR-027).

Preview is capped at ``MAX_PREVIEW_ROWS`` = 1.000 by contract. The response marks
``truncated`` so the UI can show the banner without guessing.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound, ValidationProblem
from app.db.models import Dataset, Project
from app.db.session import get_session
from app.deps import AuthContext, get_org_context, get_project_or_404
from app.services import query_service
from dataloom_contracts.datasets import MAX_PREVIEW_ROWS, DatasetColumn, DatasetPreview, DatasetOut
from dataloom_contracts._base import Layer

router = APIRouter(tags=["datasets"])


def _to_out(dataset: Dataset) -> DatasetOut:
    return DatasetOut(
        id=dataset.id,
        org_id=dataset.org_id,
        project_id=dataset.project_id,
        layer=dataset.layer,
        name=dataset.name,
        row_count=dataset.row_count,
        schema_json=dataset.schema_json or {},
        storage_uri=dataset.storage_uri,
        updated_at=dataset.updated_at,
        created_at=dataset.created_at,
    )


@router.get("/projects/{project_id}/datasets", response_model=list[DatasetOut])
async def list_datasets(
    project: Project = Depends(get_project_or_404),
    session: AsyncSession = Depends(get_session),
) -> list[DatasetOut]:
    rows = (
        await session.execute(
            select(Dataset)
            .where(Dataset.project_id == project.id, Dataset.org_id == project.org_id)
            .order_by(Dataset.layer.asc(), Dataset.updated_at.desc())
        )
    ).scalars().all()
    return [_to_out(row) for row in rows]


@router.get(
    "/projects/{project_id}/datasets/{dataset_id}/preview",
    response_model=DatasetPreview,
)
async def preview_dataset(
    dataset_id: uuid.UUID,
    project: Project = Depends(get_project_or_404),
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=MAX_PREVIEW_ROWS, ge=1, le=MAX_PREVIEW_ROWS),
) -> DatasetPreview:
    """Bounded preview of one dataset (bronze/silver parquet or gold table)."""
    dataset = await query_service.get_dataset(
        session, org_id=project.org_id, dataset_id=dataset_id, project_id=project.id
    )

    schema_entries = (dataset.schema_json or {}).get("columns") if isinstance(dataset.schema_json, dict) else None
    columns = [
        DatasetColumn(name=str(entry.get("name")), dtype=str(entry.get("dtype", "unknown")))
        for entry in (schema_entries or [])
        if isinstance(entry, dict) and entry.get("name")
    ]

    # Only gold datasets live in the DuckDB warehouse; parquet layers cannot be
    # queried without object storage access from the API, so we return schema +
    # counts and an empty row list instead of pretending to have data.
    if dataset.layer != Layer.GOLD.value:
        return DatasetPreview(
            dataset_id=dataset.id,
            columns=columns,
            rows=[],
            total_rows=int(dataset.row_count or 0),
            truncated=False,
        )

    try:
        result = await query_service.preview_rows(
            org_id=project.org_id,
            project_id=project.id,
            table=dataset.name,
            limit=limit,
        )
    except FileNotFoundError as exc:
        raise NotFound("Warehouse untuk proyek ini belum tersedia.") from exc

    return DatasetPreview(
        dataset_id=dataset.id,
        columns=[DatasetColumn(name=name, dtype="unknown") for name in result.columns],
        rows=result.rows,
        total_rows=int(dataset.row_count or result.row_count),
        truncated=result.truncated,
    )


@router.get("/projects/{project_id}/datasets/{dataset_id}/quality")
async def dataset_quality(
    dataset_id: uuid.UUID,
    project: Project = Depends(get_project_or_404),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Per-column null ratio / distinct count from the stored Bronze schema.

    Numbers come from the worker's last profile of the dataset. If the worker
    has not profiled it yet the arrays are empty - we never fabricate stats.
    """
    dataset = await query_service.get_dataset(
        session, org_id=project.org_id, dataset_id=dataset_id, project_id=project.id
    )
    stats = (dataset.schema_json or {}).get("stats") if isinstance(dataset.schema_json, dict) else None
    columns = []
    for entry in stats or []:
        if not isinstance(entry, dict):
            continue
        columns.append(
            {
                "name": str(entry.get("name", "")),
                "dtype": str(entry.get("dtype", "unknown")),
                "null_ratio": float(entry.get("null_ratio") or 0.0),
                "distinct_count": int(entry.get("distinct_count") or 0),
                "histogram": entry.get("histogram") or [],
            }
        )
    return {"columns": columns, "reasons": (dataset.schema_json or {}).get("reasons", {})}


@router.get("/datasets/{dataset_id}", response_model=DatasetOut)
async def get_dataset(
    dataset_id: uuid.UUID,
    ctx: AuthContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_session),
) -> DatasetOut:
    dataset = await query_service.get_dataset(
        session, org_id=ctx.org_id, dataset_id=dataset_id
    )
    return _to_out(dataset)


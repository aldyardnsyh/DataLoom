"""Org/project-scoped dataset allowlist (PRD A2/A5 tool ``list_datasets``).

Everything the agents may look at is derived from the ``datasets`` table,
filtered by BOTH ``org_id`` and ``project_id``. There is no path in this module
that accepts a table name from a model or a user and returns data for it
without it being in the allowlist first.

The allowlist is the join point between this module and
``app.tools.sql_guard.validate_readonly_sql``: Text-to-SQL may only reference
names returned by :func:`allowed_tables`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Dataset
from dataloom_contracts._base import Layer

#: Datasets in these layers are queryable by the agents.
QUERYABLE_LAYERS: frozenset[str] = frozenset({Layer.SILVER.value, Layer.GOLD.value})


@dataclass(slots=True)
class DatasetRef:
    """A dataset row reduced to what an agent needs to know about it."""

    id: uuid.UUID
    name: str
    layer: str
    storage_uri: str
    row_count: int | None = None
    schema: list[dict[str, Any]] = field(default_factory=list)

    @property
    def columns(self) -> list[str]:
        names: list[str] = []
        for entry in self.schema:
            if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                names.append(entry["name"])
        return names

    def as_prompt_dict(self) -> dict[str, Any]:
        """Shape shown to the model. Column names are untrusted text (wrapped by the caller)."""
        return {
            "name": self.name,
            "layer": self.layer,
            "row_count": self.row_count,
            "columns": [
                {"name": entry.get("name"), "dtype": entry.get("dtype")}
                for entry in self.schema
                if isinstance(entry, dict)
            ],
        }


def _schema_entries(schema_json: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Normalise ``datasets.schema_json`` into ``[{"name", "dtype"}]``.

    The column is schema-loose: Bronze stores ``{"columns": [...]}`` while
    Silver writes ``{"schema": [...]}`` in places; accept both, plus a bare
    list, so a future writer cannot silently empty the allowlist.
    """
    if not schema_json:
        return []
    raw: Any = None
    if isinstance(schema_json, dict):
        raw = schema_json.get("columns") or schema_json.get("schema") or schema_json.get("fields")
    elif isinstance(schema_json, list):
        raw = schema_json
    entries: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                entries.append({"name": item["name"], "dtype": str(item.get("dtype", "unknown"))})
            elif isinstance(item, str):
                entries.append({"name": item, "dtype": "unknown"})
    return entries


async def list_datasets(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    layers: frozenset[str] | None = None,
) -> list[DatasetRef]:
    """Return the project's datasets, scoped by org AND project."""
    wanted = layers if layers is not None else QUERYABLE_LAYERS
    rows = (
        await session.execute(
            select(Dataset)
            .where(
                Dataset.org_id == org_id,
                Dataset.project_id == project_id,
                Dataset.layer.in_(sorted(wanted)),
            )
            .order_by(Dataset.layer.asc(), Dataset.name.asc())
        )
    ).scalars().all()
    return [
        DatasetRef(
            id=row.id,
            name=row.name,
            layer=row.layer,
            storage_uri=row.storage_uri,
            row_count=row.row_count,
            schema=_schema_entries(row.schema_json),
        )
        for row in rows
    ]


async def get_dataset(
    session: AsyncSession, *, org_id: uuid.UUID, dataset_id: uuid.UUID
) -> DatasetRef | None:
    row = (
        await session.execute(
            select(Dataset).where(Dataset.id == dataset_id, Dataset.org_id == org_id)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return DatasetRef(
        id=row.id,
        name=row.name,
        layer=row.layer,
        storage_uri=row.storage_uri,
        row_count=row.row_count,
        schema=_schema_entries(row.schema_json),
    )


async def get_schema(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID, dataset_name: str
) -> DatasetRef | None:
    """Exact-name lookup inside the project only (case-insensitive)."""
    datasets = await list_datasets(
        session, org_id=org_id, project_id=project_id, layers=None
    )
    lowered = dataset_name.strip().lower()
    for ref in datasets:
        if ref.name.lower() == lowered:
            return ref
    return None


async def allowed_tables(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> set[str]:
    """The ONLY table names Text-to-SQL may reference, lower-cased."""
    refs = await list_datasets(session, org_id=org_id, project_id=project_id)
    return {ref.name.lower() for ref in refs}


async def allowed_columns(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> dict[str, list[str]]:
    """``{table: [column, ...]}`` for prompt building and column checks."""
    refs = await list_datasets(session, org_id=org_id, project_id=project_id)
    return {ref.name.lower(): ref.columns for ref in refs}


__all__ = [
    "DatasetRef",
    "QUERYABLE_LAYERS",
    "allowed_columns",
    "allowed_tables",
    "get_dataset",
    "get_schema",
    "list_datasets",
]

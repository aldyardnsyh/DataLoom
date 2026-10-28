"""Dataset / Data Explorer contracts (FR-041, FR-027).

Preview is hard-capped at 1,000 rows in the contract, so the UI never has to
trust the server and the server never has to trust the UI.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from ._base import JsonDict, Layer, ReadModel
from .common import ContractModel

#: Hard cap mandated by FR-041.
MAX_PREVIEW_ROWS = 1000


class DatasetOut(ReadModel):
    id: uuid.UUID
    org_id: uuid.UUID
    project_id: uuid.UUID
    layer: Layer
    name: str
    row_count: int | None = Field(default=None, ge=0)
    schema_json: JsonDict = Field(default_factory=dict)
    storage_uri: str
    updated_at: datetime
    created_at: datetime


class DatasetColumn(ContractModel):
    """Schema entry for the Data Explorer 'Schema' tab."""

    name: str
    dtype: str
    nullable: bool = True
    null_ratio: float | None = Field(default=None, ge=0.0, le=1.0)


class DatasetPreviewRequest(ContractModel):
    """Pagination + optional column projection for the preview table."""

    limit: int = Field(default=100, ge=1, le=MAX_PREVIEW_ROWS)
    offset: int = Field(default=0, ge=0)
    columns: list[str] = Field(default_factory=list)


class DatasetPreview(ReadModel):
    dataset_id: uuid.UUID
    layer: Layer
    columns: list[DatasetColumn]
    rows: list[list[str | int | float | bool | None]] = Field(
        max_length=MAX_PREVIEW_ROWS
    )
    total_rows: int | None = Field(default=None, ge=0)
    truncated: bool = False


class LineageNode(ContractModel):
    """One node of the dataset lineage graph (FR-027)."""

    dataset_id: uuid.UUID
    name: str
    layer: Layer
    storage_uri: str


class LineageEdge(ContractModel):
    from_dataset_id: uuid.UUID
    to_dataset_id: uuid.UUID
    run_id: uuid.UUID | None = None


class LineageGraph(ReadModel):
    nodes: list[LineageNode] = Field(default_factory=list)
    edges: list[LineageEdge] = Field(default_factory=list)


__all__ = [
    "DatasetColumn",
    "DatasetOut",
    "DatasetPreview",
    "DatasetPreviewRequest",
    "LineageEdge",
    "LineageGraph",
    "LineageNode",
    "MAX_PREVIEW_ROWS",
]

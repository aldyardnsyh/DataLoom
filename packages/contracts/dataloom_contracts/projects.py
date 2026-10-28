"""Project contracts (FR-003, FR-024). Every project belongs to exactly one org."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from ._base import ContractModel, ReadModel, RunStatus


class ProjectSettings(ContractModel):
    """Typed project settings; stored verbatim in ``projects.settings_json``.

    Defaults are the PRD defaults: 5% quarantine ratio (A0 gate rule b) and the
    knobs the Run Monitor / Settings pages read.
    """

    max_quarantine_ratio: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description="A0 gate (b): fail gold promotion above this ratio. 0.05 = 5%.",
    )
    require_approval_for_destructive: bool = Field(
        default=True, description="A0 gate: destructive cleaning needs approval."
    )
    freshness_threshold_minutes: int = Field(default=1440, ge=1)
    batch_size_rows: int = Field(default=5000, ge=1, le=50000)
    node_timeout_seconds: int = Field(default=45, ge=1, le=600)
    run_timeout_seconds: int = Field(default=300, ge=1, le=3600)
    max_retries_per_node: int = Field(default=2, ge=0, le=5)
    max_concurrent_runs: int = Field(default=1, ge=1, le=10)
    schedule_cron: str | None = Field(
        default=None,
        description="Optional 5-field cron (FR-028). null = manual only.",
    )


class ProjectCreate(ContractModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    settings: ProjectSettings = Field(default_factory=ProjectSettings)


class ProjectUpdate(ContractModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    settings: ProjectSettings | None = None


class ProjectOut(ReadModel):
    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    description: str | None = None
    settings: ProjectSettings = Field(default_factory=ProjectSettings)
    created_at: datetime
    updated_at: datetime


class ProjectSummary(ReadModel):
    """Card view for the Projects page: counts precomputed for the UI."""

    id: uuid.UUID
    name: str
    description: str | None = None
    source_count: int = Field(default=0, ge=0)
    pipeline_count: int = Field(default=0, ge=0)
    last_run_status: RunStatus | None = None
    updated_at: datetime


__all__ = [
    "ProjectCreate",
    "ProjectOut",
    "ProjectSettings",
    "ProjectSummary",
    "ProjectUpdate",
]

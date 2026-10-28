"""Postgres models for the agent worker.

Modular monolith (ADR-001): the agent worker shares the API's *schema* but not
its Python package. Following the precedent set by
``apps/worker/app/db.py``, this module re-declares ONLY the tables the agents
touch, with identical names and column types to
``apps/api/app/db/models.py``.

A schema change must be made in three places: the Alembic migration, the API
models, and here (plus ``apps/worker/app/db.py`` if the worker cares).

Tables declared
---------------
organizations, agent_runs, agent_proposals, agent_events, llm_calls,
artifacts, datasets, pipeline_versions, pipelines, run_nodes, runs, projects.

Enum-ish columns are plain ``String``/``Enum`` mirrors of the native Postgres
ENUMs; values always come from ``dataloom_contracts._base`` so Python and the
DB cannot drift.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Enum,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base local to the agent worker."""


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


def _uuid_fk(index: bool = True) -> Mapped[uuid.UUID]:
    """FK-shaped column without a ForeignKey (kept table-order independent).

    The real constraints exist and are managed by Alembic; re-declaring them
    here would only create a second source of truth.
    """
    return mapped_column(PgUUID(as_uuid=True), nullable=False, index=index)


# ---------------------------------------------------------------------------
# Native enum mirrors (names MUST match alembic/versions/0001_initial_schema.py)
# ---------------------------------------------------------------------------
agent_type_enum = Enum(
    "supervisor",
    "schema_profiler",
    "pipeline_builder",
    "data_quality_guardian",
    "self_healing",
    "text_to_sql",
    name="agent_type",
    create_type=False,
)
agent_run_status_enum = Enum(
    "pending",
    "running",
    "waiting_approval",
    "succeeded",
    "insufficient_evidence",
    "failed",
    "cancelled",
    "budget_exceeded",
    name="agent_run_status",
    create_type=False,
)
proposal_status_enum = Enum(
    "pending",
    "approved",
    "rejected",
    "superseded",
    "expired",
    name="proposal_status",
    create_type=False,
)
proposal_kind_enum = Enum(
    "cleaning_rules",
    "pipeline_draft",
    "fix_proposal",
    name="proposal_kind",
    create_type=False,
)
risk_level_enum = Enum(
    "low", "medium", "destructive", name="risk_level", create_type=False
)
validation_status_enum = Enum(
    "draft", "valid", "invalid", name="validation_status", create_type=False
)
layer_enum = Enum(
    "bronze", "silver", "gold", "quarantine", name="layer", create_type=False
)
run_status_enum = Enum(
    "draft",
    "validated",
    "queued",
    "running",
    "succeeded",
    "failed",
    "retrying",
    "needs_attention",
    "cancelled",
    name="run_status",
    create_type=False,
)


# ---------------------------------------------------------------------------
# Tenant root: holds the budget limits the guard reads.
# ---------------------------------------------------------------------------
class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    plan: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'free'"))
    llm_budget_usd_month: Mapped[float] = mapped_column(
        Numeric(12, 6), nullable=False, server_default=text("5.0")
    )
    per_run_token_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("20000")
    )


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    settings_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class Source(Base):
    """Declared so ``dag_tools`` can check that a DAG only uses this project's sources."""

    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'connected'"))


# ---------------------------------------------------------------------------
# Datasets: the schema allowlist for tools and for Text-to-SQL.
# ---------------------------------------------------------------------------
class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    layer: Mapped[str] = mapped_column(layer_enum, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    storage_uri: Mapped[str] = mapped_column(Text, nullable=False)
    row_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ---------------------------------------------------------------------------
# Pipeline versions (read-only for agents; written by the API on approval).
# ---------------------------------------------------------------------------
class Pipeline(Base):
    __tablename__ = "pipelines"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))


class PipelineVersion(Base):
    __tablename__ = "pipeline_versions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    pipeline_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    dag_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    validation_status: Mapped[str] = mapped_column(
        validation_status_enum, nullable=False, server_default=text("'draft'")
    )
    validation_errors: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    created_by_kind: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'user'")
    )
    created_by_agent_run_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    change_note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ---------------------------------------------------------------------------
# Runs + run_nodes: self-healing input (READ ONLY for agents).
# ---------------------------------------------------------------------------
class Run(Base):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    pipeline_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    pipeline_version_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    trigger: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'manual'"))
    status: Mapped[str] = mapped_column(
        run_status_enum, nullable=False, server_default=text("'queued'")
    )
    rows_in: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    rows_out: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    rows_quarantined: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RunNode(Base):
    __tablename__ = "run_nodes"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    node_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default=text("'pending'"))
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ---------------------------------------------------------------------------
# Agent bookkeeping
# ---------------------------------------------------------------------------
class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    agent_type: Mapped[str] = mapped_column(agent_type_enum, nullable=False)
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        agent_run_status_enum, nullable=False, server_default=text("'pending'")
    )
    budget_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("20000")
    )
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    cost_usd: Mapped[float] = mapped_column(
        Numeric(12, 6), nullable=False, server_default=text("0")
    )
    target_run_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    target_dataset_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    input_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    current_step: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AgentProposal(Base):
    __tablename__ = "agent_proposals"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    agent_run_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(proposal_kind_enum, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    evidence_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    risk: Mapped[str] = mapped_column(
        risk_level_enum, nullable=False, server_default=text("'low'")
    )
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    status: Mapped[str] = mapped_column(
        proposal_status_enum, nullable=False, server_default=text("'pending'")
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(String(1000))
    applied_pipeline_version_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AgentEvent(Base):
    __tablename__ = "agent_events"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    agent_run_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    step: Mapped[str] = mapped_column(String(64), nullable=False)
    node_name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class LlmCall(Base):
    __tablename__ = "llm_calls"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    project_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    node: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    cost_usd: Mapped[float] = mapped_column(
        Numeric(12, 6), nullable=False, server_default=text("0")
    )
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    producing_agent: Mapped[str | None] = mapped_column(agent_type_enum)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    source_artifact_versions: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    validation_status: Mapped[str] = mapped_column(
        validation_status_enum, nullable=False, server_default=text("'draft'")
    )
    storage_uri: Mapped[str | None] = mapped_column(Text)
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


__all__ = [
    "AgentEvent",
    "AgentProposal",
    "AgentRun",
    "Artifact",
    "Base",
    "Dataset",
    "LlmCall",
    "Organization",
    "Pipeline",
    "PipelineVersion",
    "Project",
    "Run",
    "RunNode",
    "Source",
]

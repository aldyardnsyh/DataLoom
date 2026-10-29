"""Typed SQLAlchemy 2.0 models, 1:1 with ``alembic/versions/0001_initial_schema.py``.

Conventions
-----------
- Table names are plural snake_case; PKs are UUID ``gen_random_uuid()``.
- Every org-scoped table has ``org_id`` (denormalised where needed) and RLS.
- Enums are native Postgres ENUMs created by the migration; the Python side
  uses plain ``str`` columns plus ``Enum(...)`` objects exported here so that
  worker and API compare the SAME values.
- ``checkpoint*`` / ``store*`` tables are created by LangGraph itself at runtime
  (``PostgresSaver.setup()``). They are intentionally NOT modelled here.

See ``docs/ARCHITECTURE.md`` section "Model data" for the relationship map.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, CreatedAtMixin, OrgScopedMixin, TimestampMixin, uuid_fk, uuid_pk

# ---------------------------------------------------------------------------
# Native Postgres ENUMs. ``name=`` MUST match the migration exactly.
# ``create_type=False`` because the migration owns CREATE TYPE.
# ---------------------------------------------------------------------------
role_enum = PgEnum(
    "owner",
    "admin",
    "editor",
    "viewer",
    name="role",
    create_type=False,
)
source_type_enum = PgEnum(
    "webhook", "csv", "api", "postgres", name="source_type", create_type=False
)
layer_enum = PgEnum(
    "bronze", "silver", "gold", "quarantine", name="layer", create_type=False
)
run_status_enum = PgEnum(
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
node_status_enum = PgEnum(
    "pending",
    "running",
    "succeeded",
    "failed",
    "skipped",
    "retrying",
    "cancelled",
    name="node_status",
    create_type=False,
)
proposal_status_enum = PgEnum(
    "pending",
    "approved",
    "rejected",
    "superseded",
    "expired",
    name="proposal_status",
    create_type=False,
)
agent_type_enum = PgEnum(
    "supervisor",
    "schema_profiler",
    "pipeline_builder",
    "data_quality_guardian",
    "self_healing",
    "text_to_sql",
    name="agent_type",
    create_type=False,
)
agent_run_status_enum = PgEnum(
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
validation_status_enum = PgEnum(
    "draft", "valid", "invalid", name="validation_status", create_type=False
)
risk_level_enum = PgEnum(
    "low", "medium", "destructive", name="risk_level", create_type=False
)
actor_kind_enum = PgEnum("user", "agent", "system", name="actor_kind", create_type=False)
run_trigger_enum = PgEnum(
    "manual",
    "schedule",
    "webhook",
    "retry",
    "self_heal",
    name="run_trigger",
    create_type=False,
)
upload_status_enum = PgEnum(
    "initiated",
    "completed",
    "failed",
    "aborted",
    name="upload_status",
    create_type=False,
)
proposal_kind_enum = PgEnum(
    "cleaning_rules",
    "pipeline_draft",
    "fix_proposal",
    name="proposal_kind",
    create_type=False,
)


# ---------------------------------------------------------------------------
# 1. users
# ---------------------------------------------------------------------------
class User(Base, CreatedAtMixin):
    """A person. Global (not org-scoped): the same login joins many orgs."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(254), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("position('@' in email) > 1", name="email_shape"),
    )


# ---------------------------------------------------------------------------
# 2. organizations
# ---------------------------------------------------------------------------
class Organization(Base, TimestampMixin):
    """Tenant root. Product creates one per user (D-01) but the schema is N:N."""

    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    plan: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'free'"))
    llm_budget_usd_month: Mapped[float] = mapped_column(
        Numeric(12, 6), nullable=False, server_default=text("5.0")
    )
    per_run_token_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("20000")
    )


# ---------------------------------------------------------------------------
# 3. memberships (user <-> org, carries the role)
# ---------------------------------------------------------------------------
class Membership(Base, OrgScopedMixin, CreatedAtMixin):
    """RBAC source of truth. ``(org_id, user_id)`` is unique."""

    __tablename__ = "memberships"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = uuid_fk("users.id")
    role: Mapped[str] = mapped_column(role_enum, nullable=False)

    __table_args__ = (UniqueConstraint("org_id", "user_id", name="uq_org_user"),)


# ---------------------------------------------------------------------------
# 4. sessions (D-07: opaque token, hash stored)
# ---------------------------------------------------------------------------
class Session(Base, OrgScopedMixin, CreatedAtMixin):
    """Server-side session. Only the SHA-256 hash of the token is stored."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = uuid_fk("users.id")
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    csrf_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now() + interval '7 days'"),
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    ip: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (Index("ix_sessions_user_active", "user_id", "expires_at"),)


# ---------------------------------------------------------------------------
# 5. projects
# ---------------------------------------------------------------------------
class Project(Base, OrgScopedMixin, TimestampMixin):
    """Container for sources, pipelines, datasets and runs."""

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    settings_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_project_name"),)


# ---------------------------------------------------------------------------
# 6. api_keys (FR-004)
# ---------------------------------------------------------------------------
class ApiKey(Base, OrgScopedMixin, CreatedAtMixin):
    """Hashed ingest key. ``prefix`` is the only part ever shown again."""

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = uuid_fk("projects.id")
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    prefix: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_api_keys_project", "project_id"),)


# ---------------------------------------------------------------------------
# 7. sources
# ---------------------------------------------------------------------------
class Source(Base, OrgScopedMixin, TimestampMixin):
    """A configured input. ``config_enc`` is encrypted JSON (AES-GCM, D-02/§9.8)."""

    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = uuid_fk("projects.id")
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[str] = mapped_column(source_type_enum, nullable=False)
    config_enc: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'connected'")
    )
    last_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("project_id", "slug", name="uq_source_slug"),
        CheckConstraint(
            "status in ('connected','error','paused')", name="source_status_values"
        ),
    )


# ---------------------------------------------------------------------------
# 8. datasets (bronze / silver / gold / quarantine pointers)
# ---------------------------------------------------------------------------
class Dataset(Base, OrgScopedMixin, TimestampMixin):
    """A physical dataset (usually one Parquet prefix or DuckDB table)."""

    __tablename__ = "datasets"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = uuid_fk("projects.id")
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("sources.id", ondelete="SET NULL")
    )
    layer: Mapped[str] = mapped_column(layer_enum, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    storage_uri: Mapped[str] = mapped_column(Text, nullable=False)
    row_count: Mapped[int | None] = mapped_column(Integer)
    partition_key: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        UniqueConstraint("project_id", "layer", "name", name="uq_dataset_name"),
        CheckConstraint(
            "storage_uri like 's3://%'", name="dataset_storage_uri_scheme"
        ),
    )


# ---------------------------------------------------------------------------
# 9. pipelines + pipeline_versions (FR-024 immutable versions)
# ---------------------------------------------------------------------------
class Pipeline(Base, OrgScopedMixin, TimestampMixin):
    """Logical pipeline. Holds the pointer to its current version."""

    __tablename__ = "pipelines"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = uuid_fk("projects.id")
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("pipeline_versions.id", ondelete="SET NULL", use_alter=True),
    )
    schedule_cron: Mapped[str | None] = mapped_column(String(120))
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_pipeline_name"),)


class PipelineVersion(Base, OrgScopedMixin, CreatedAtMixin):
    """Immutable DAG snapshot. A run always pins one of these."""

    __tablename__ = "pipeline_versions"

    id: Mapped[uuid.UUID] = uuid_pk()
    pipeline_id: Mapped[uuid.UUID] = uuid_fk("pipelines.id")
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
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_by_agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("pipeline_versions.id", ondelete="SET NULL")
    )
    change_note: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (
        UniqueConstraint("pipeline_id", "version", name="uq_pipeline_version"),
        CheckConstraint(
            "created_by_kind in ('user','agent')", name="created_by_kind_values"
        ),
    )


# ---------------------------------------------------------------------------
# 10. runs (FR-025, FR-026)
# ---------------------------------------------------------------------------
class Run(Base, OrgScopedMixin, CreatedAtMixin):
    """One execution of one pipeline version. Status follows PRD §9.10 only."""

    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = uuid_fk("projects.id")
    pipeline_id: Mapped[uuid.UUID] = uuid_fk("pipelines.id")
    pipeline_version_id: Mapped[uuid.UUID] = uuid_fk(
        "pipeline_versions.id", ondelete="RESTRICT"
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    trigger: Mapped[str] = mapped_column(
        run_trigger_enum, nullable=False, server_default=text("'manual'")
    )
    status: Mapped[str] = mapped_column(
        run_status_enum, nullable=False, server_default=text("'queued'")
    )
    triggered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    current_node_id: Mapped[str | None] = mapped_column(String(64))
    queued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    rows_in: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    rows_out: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    rows_quarantined: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    retry_total: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    batch_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))

    __table_args__ = (
        Index("ix_runs_project_created", "project_id", "created_at"),
        Index("ix_runs_status", "status"),
        UniqueConstraint(
            "pipeline_id", "idempotency_key", name="uq_runs_idempotency"
        ),
        CheckConstraint(
            "rows_in >= 0 and rows_out >= 0 and rows_quarantined >= 0",
            name="run_rows_non_negative",
        ),
    )


# ---------------------------------------------------------------------------
# 11. run_nodes (denormalised org_id so RLS is one comparison)
# ---------------------------------------------------------------------------
class RunNode(Base, OrgScopedMixin, CreatedAtMixin):
    """Per-node execution record powering the Run Monitor timeline."""

    __tablename__ = "run_nodes"

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = uuid_fk("runs.id")
    node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    node_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        node_status_enum, nullable=False, server_default=text("'pending'")
    )
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    rows_in: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    rows_out: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    rows_quarantined: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (
        UniqueConstraint("run_id", "node_id", "attempt", name="uq_run_node_attempt"),
        Index("ix_run_nodes_run", "run_id"),
    )


# ---------------------------------------------------------------------------
# 12. agent_runs (FR-037: thread_id ties to LangGraph checkpoints)
# ---------------------------------------------------------------------------
class AgentRun(Base, OrgScopedMixin, CreatedAtMixin):
    """One LangGraph invocation. ``thread_id`` is the checkpoint thread."""

    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = uuid_fk("projects.id")
    agent_type: Mapped[str] = mapped_column(agent_type_enum, nullable=False)
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
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
    target_run_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("runs.id", ondelete="SET NULL")
    )
    target_dataset_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("datasets.id", ondelete="SET NULL")
    )
    input_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    current_step: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_agent_runs_org_status", "org_id", "status"),)


# ---------------------------------------------------------------------------
# 13. agent_proposals (FR-031: nothing applies without an approval row)
# ---------------------------------------------------------------------------
class AgentProposal(Base, OrgScopedMixin, CreatedAtMixin):
    """A pending decision. ``status`` moves pending -> approved|rejected only."""

    __tablename__ = "agent_proposals"

    id: Mapped[uuid.UUID] = uuid_pk()
    agent_run_id: Mapped[uuid.UUID] = uuid_fk("agent_runs.id")
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
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(String(1000))
    applied_pipeline_version_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("pipeline_versions.id", ondelete="SET NULL")
    )

    __table_args__ = (
        Index("ix_agent_proposals_pending", "org_id", "status"),
        CheckConstraint(
            "confidence is null or (confidence >= 0 and confidence <= 1)",
            name="proposal_confidence_range",
        ),
    )


# ---------------------------------------------------------------------------
# 14. agent_events (PRD §9.9 execution trace)
# ---------------------------------------------------------------------------
class AgentEvent(Base, OrgScopedMixin, CreatedAtMixin):
    """Append-only trace: one row per graph node step."""

    __tablename__ = "agent_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    agent_run_id: Mapped[uuid.UUID] = uuid_fk("agent_runs.id")
    step: Mapped[str] = mapped_column(String(64), nullable=False)
    node_name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_agent_events_run", "agent_run_id", "at"),)


# ---------------------------------------------------------------------------
# 15. llm_calls (FR-036)
# ---------------------------------------------------------------------------
class LlmCall(Base, OrgScopedMixin, CreatedAtMixin):
    """Every metered provider call, success or failure."""

    __tablename__ = "llm_calls"

    id: Mapped[uuid.UUID] = uuid_pk()
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE")
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL")
    )
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

    __table_args__ = (
        Index("ix_llm_calls_org_at", "org_id", "created_at"),
        Index("ix_llm_calls_agent_run", "agent_run_id"),
    )


# ---------------------------------------------------------------------------
# 16. artifacts (versioned agent/engine outputs)
# ---------------------------------------------------------------------------
class Artifact(Base, OrgScopedMixin, CreatedAtMixin):
    """Durable output of an agent or engine step, validated before storage."""

    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = uuid_fk("projects.id")
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    producing_agent: Mapped[str | None] = mapped_column(
        agent_type_enum, nullable=True
    )
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
    source_artifact_versions: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    validation_status: Mapped[str] = mapped_column(
        validation_status_enum, nullable=False, server_default=text("'draft'")
    )
    storage_uri: Mapped[str | None] = mapped_column(Text)
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (
        UniqueConstraint(
            "project_id", "type", "version", name="uq_artifact_version"
        ),
    )


# ---------------------------------------------------------------------------
# 17. audit_logs (FR-043)
# ---------------------------------------------------------------------------
class AuditLog(Base, OrgScopedMixin, CreatedAtMixin):
    """Append-only sensitive-action log. Never updated, never deleted."""

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = uuid_pk()
    actor_kind: Mapped[str] = mapped_column(actor_kind_enum, nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    actor_email: Mapped[str | None] = mapped_column(String(254))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(128))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    ip: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_audit_logs_org_at", "org_id", "at"),)


# ---------------------------------------------------------------------------
# 18. ingest_idempotency (FR-016, 24h window)
# ---------------------------------------------------------------------------
class IngestIdempotency(Base, OrgScopedMixin, CreatedAtMixin):
    """Deduplicates webhook retries. ``(source_id, key)`` unique within 24h."""

    __tablename__ = "ingest_idempotency"

    id: Mapped[uuid.UUID] = uuid_pk()
    source_id: Mapped[uuid.UUID] = uuid_fk("sources.id")
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_accepted: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    response_batch_hint: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now() + interval '24 hours'")
    )

    __table_args__ = (
        UniqueConstraint("source_id", "key", name="uq_ingest_idempotency"),
        Index("ix_ingest_idempotency_expires", "expires_at"),
    )


# ---------------------------------------------------------------------------
# 19. uploads (FR-013, presigned upload bookkeeping)
# ---------------------------------------------------------------------------
class Upload(Base, OrgScopedMixin, CreatedAtMixin):
    """Server-side record of a presigned CSV/Parquet upload."""

    __tablename__ = "uploads"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = uuid_fk("projects.id")
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("sources.id", ondelete="SET NULL")
    )
    dataset_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("datasets.id", ondelete="SET NULL")
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        upload_status_enum, nullable=False, server_default=text("'initiated'")
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------------------
# 20. installation_settings (deployment-wide, NOT org-scoped)
#
#     A singleton row: the ``id`` check constraint guarantees at most one
#     settings row exists, because the LLM provider is configured once per
#     installation (D-05), not per tenant. Deliberately NOT in
#     ``ORG_SCOPED_TABLES``: there is no ``org_id`` column, so an RLS policy
#     would compare against a column that does not exist.
# ---------------------------------------------------------------------------
class InstallationSettings(Base):
    """One row holding the deployment LLM provider configuration.

    ``llm_api_key_encrypted`` stores Fernet ciphertext (text), never the
    plaintext key. ``llm_api_key_hint`` holds at most the last four characters
    and is the only derivative ever returned to a client.
    """

    __tablename__ = "installation_settings"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, nullable=False, server_default=text("1")
    )
    llm_base_url: Mapped[str | None] = mapped_column(Text)
    llm_api_key_encrypted: Mapped[str | None] = mapped_column(Text)
    llm_api_key_hint: Mapped[str | None] = mapped_column(String(8))
    llm_model_default: Mapped[str | None] = mapped_column(String(128))
    llm_model_small: Mapped[str | None] = mapped_column(String(128))
    llm_timeout_seconds: Mapped[int | None] = mapped_column(Integer)
    llm_price_in_per_1k: Mapped[float | None] = mapped_column(Numeric(12, 6))
    llm_price_out_per_1k: Mapped[float | None] = mapped_column(Numeric(12, 6))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    updated_by: Mapped[str | None] = mapped_column(String(254))

    __table_args__ = (
        CheckConstraint("id = 1", name="installation_settings_singleton"),
        CheckConstraint(
            "llm_timeout_seconds is null or "
            "(llm_timeout_seconds >= 1 and llm_timeout_seconds <= 300)",
            name="installation_settings_timeout_range",
        ),
        CheckConstraint(
            "llm_price_in_per_1k is null or llm_price_in_per_1k >= 0",
            name="installation_settings_price_in_non_negative",
        ),
        CheckConstraint(
            "llm_price_out_per_1k is null or llm_price_out_per_1k >= 0",
            name="installation_settings_price_out_non_negative",
        ),
    )


# ---------------------------------------------------------------------------
# 21. install_admins (deployment-wide flag, NOT an organization role)
#
#     Installation admin is orthogonal to the membership role: an org owner is
#     NOT an installation admin, and an installation admin does not need to be
#     a member of any organization. A separate table is used instead of a
#     ``users.is_install_admin`` column so that granting and revoking leaves a
#     row-level trail (who granted it, when, why) next to the audit log.
# ---------------------------------------------------------------------------
class InstallAdmin(Base, CreatedAtMixin):
    """Grants one user the deployment-wide installation admin flag."""

    __tablename__ = "install_admins"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    granted_by: Mapped[str | None] = mapped_column(String(254))
    reason: Mapped[str | None] = mapped_column(String(500))


__all__ = [
    "AgentEvent",
    "AgentProposal",
    "AgentRun",
    "ApiKey",
    "Artifact",
    "AuditLog",
    "Dataset",
    "IngestIdempotency",
    "InstallAdmin",
    "InstallationSettings",
    "LlmCall",
    "Membership",
    "Organization",
    "Pipeline",
    "PipelineVersion",
    "Project",
    "Run",
    "RunNode",
    "Session",
    "Source",
    "Upload",
    "User",
    # enum objects reused by worker/API code
    "actor_kind_enum",
    "agent_run_status_enum",
    "agent_type_enum",
    "layer_enum",
    "node_status_enum",
    "proposal_kind_enum",
    "proposal_status_enum",
    "risk_level_enum",
    "role_enum",
    "run_status_enum",
    "run_trigger_enum",
    "source_type_enum",
    "upload_status_enum",
    "validation_status_enum",
]

"""Initial DataLoom schema: PRD section 9.6 tables + everything FR-001..FR-043 needs.

Tenancy and RLS
---------------
Every org-scoped table has ``org_id UUID NOT NULL REFERENCES organizations(id)
ON DELETE CASCADE``. Tables reachable only through a parent (``run_nodes``,
``agent_proposals``, ``agent_events``, ``llm_calls``, ``pipeline_versions``,
``api_keys``, ``uploads``, ``ingest_idempotency``) ALSO store a denormalised
``org_id`` so that the RLS policy is a single comparison:

    USING (org_id = current_setting('app.current_org_id', true)::uuid)

The application sets the tenant once per transaction, before touching data:

    BEGIN;
    SET LOCAL app.current_org_id = '<org uuid>';
    -- queries here are now filtered by RLS as a second layer
    COMMIT;

``SET LOCAL`` is important: it is scoped to the transaction, so a connection
returned to the pool cannot leak one tenant's context into the next request.
PostgreSQL also normalises this into ``pg_settings.name`` as
``app.current_org_id``; the two-part custom name is required for the setting to
be writable at all.

Two ways to connect (see ADR-004):
- ``DATALOOM_DB_ROLE=app`` (recommended): a non-owner role. RLS applies.
- ``DATALOOM_DB_ROLE=owner``: migrations and the worker's administrative jobs.
  Table owners bypass RLS unless ``FORCE ROW LEVEL SECURITY`` is set, so this
  migration deliberately does NOT force RLS on the owner: the worker needs
  cross-tenant scans for scheduled runs. Application queries still filter by
  ``org_id`` in SQL; RLS is the second layer, not the only layer.

LangGraph checkpoint tables
---------------------------
``checkpoints``, ``checkpoint_blobs``, ``checkpoint_writes``,
``checkpoint_migrations`` (and ``store`` / ``store_migrations`` if the store is
used) are created **at runtime** by
``langgraph.checkpoint.postgres.PostgresSaver.setup()``. This migration must NOT
create them: the library owns their DDL and schema version, and duplicating it
would drift on the next library upgrade.

Revision ID: 0001
Revises: None
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


#: Every table that carries ``org_id`` and therefore gets an RLS policy.
ORG_SCOPED_TABLES: tuple[str, ...] = (
    "memberships",
    "sessions",
    "projects",
    "api_keys",
    "sources",
    "datasets",
    "pipelines",
    "pipeline_versions",
    "runs",
    "run_nodes",
    "agent_runs",
    "agent_proposals",
    "agent_events",
    "llm_calls",
    "artifacts",
    "audit_logs",
    "ingest_idempotency",
    "uploads",
)

POLICY_NAME = "org_isolation"


def _uuid_pk() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )


def _org_fk() -> sa.Column:
    return sa.Column(
        "org_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


def _updated_at() -> sa.Column:
    return sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 0. Extension for gen_random_uuid().
    #    Postgres 13+ ships gen_random_uuid() in core; pgcrypto is the
    #    portable fallback for older servers and Supabase/Neon pre-install it.
    #    IF NOT EXISTS keeps this idempotent on managed providers.
    # ------------------------------------------------------------------
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # ------------------------------------------------------------------
    # 1. Native ENUM types. Values mirror dataloom_contracts._base 1:1.
    # ------------------------------------------------------------------
    op.execute("CREATE TYPE role AS ENUM ('owner','admin','editor','viewer')")
    op.execute("CREATE TYPE source_type AS ENUM ('webhook','csv','api','postgres')")
    op.execute("CREATE TYPE layer AS ENUM ('bronze','silver','gold','quarantine')")
    # PRD section 9.10 state machine, verbatim and in order.
    op.execute(
        "CREATE TYPE run_status AS ENUM ("
        "'draft','validated','queued','running','succeeded','failed',"
        "'retrying','needs_attention','cancelled')"
    )
    op.execute(
        "CREATE TYPE node_status AS ENUM ("
        "'pending','running','succeeded','failed','skipped','retrying','cancelled')"
    )
    op.execute(
        "CREATE TYPE proposal_status AS ENUM ("
        "'pending','approved','rejected','superseded','expired')"
    )
    op.execute(
        "CREATE TYPE proposal_kind AS ENUM ("
        "'cleaning_rules','pipeline_draft','fix_proposal')"
    )
    op.execute(
        "CREATE TYPE agent_type AS ENUM ("
        "'supervisor','schema_profiler','pipeline_builder',"
        "'data_quality_guardian','self_healing','text_to_sql')"
    )
    op.execute(
        "CREATE TYPE agent_run_status AS ENUM ("
        "'pending','running','waiting_approval','succeeded',"
        "'insufficient_evidence','failed','cancelled','budget_exceeded')"
    )
    op.execute("CREATE TYPE validation_status AS ENUM ('draft','valid','invalid')")
    op.execute("CREATE TYPE risk_level AS ENUM ('low','medium','destructive')")
    op.execute("CREATE TYPE actor_kind AS ENUM ('user','agent','system')")
    op.execute(
        "CREATE TYPE run_trigger AS ENUM ("
        "'manual','schedule','webhook','retry','self_heal')"
    )
    op.execute(
        "CREATE TYPE upload_status AS ENUM ("
        "'initiated','completed','failed','aborted')"
    )

    # ENUM handles reused across tables (create_type=False: we just created them).
    role_t = postgresql.ENUM(name="role", create_type=False)
    source_type_t = postgresql.ENUM(name="source_type", create_type=False)
    layer_t = postgresql.ENUM(name="layer", create_type=False)
    run_status_t = postgresql.ENUM(name="run_status", create_type=False)
    node_status_t = postgresql.ENUM(name="node_status", create_type=False)
    proposal_status_t = postgresql.ENUM(name="proposal_status", create_type=False)
    proposal_kind_t = postgresql.ENUM(name="proposal_kind", create_type=False)
    agent_type_t = postgresql.ENUM(name="agent_type", create_type=False)
    agent_run_status_t = postgresql.ENUM(name="agent_run_status", create_type=False)
    validation_status_t = postgresql.ENUM(name="validation_status", create_type=False)
    risk_level_t = postgresql.ENUM(name="risk_level", create_type=False)
    actor_kind_t = postgresql.ENUM(name="actor_kind", create_type=False)
    run_trigger_t = postgresql.ENUM(name="run_trigger", create_type=False)
    upload_status_t = postgresql.ENUM(name="upload_status", create_type=False)

    jsonb = postgresql.JSONB(astext_type=sa.Text())
    uuid_t = postgresql.UUID(as_uuid=True)

    # ------------------------------------------------------------------
    # 2. users (global, intentionally NOT org-scoped)
    # ------------------------------------------------------------------
    op.create_table(
        "users",
        _uuid_pk(),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.CheckConstraint("position('@' in email) > 1", name="email_shape"),
    )

    # ------------------------------------------------------------------
    # 3. organizations (tenant root)
    # ------------------------------------------------------------------
    op.create_table(
        "organizations",
        _uuid_pk(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("slug", sa.String(80), nullable=False, unique=True),
        sa.Column(
            "plan", sa.String(32), nullable=False, server_default=sa.text("'free'")
        ),
        sa.Column(
            "llm_budget_usd_month",
            sa.Numeric(12, 6),
            nullable=False,
            server_default=sa.text("5.0"),
        ),
        sa.Column(
            "per_run_token_limit",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("20000"),
        ),
        _created_at(),
        _updated_at(),
    )

    # ------------------------------------------------------------------
    # 4. memberships (RBAC)
    # ------------------------------------------------------------------
    op.create_table(
        "memberships",
        _uuid_pk(),
        _org_fk(),
        sa.Column(
            "user_id",
            uuid_t,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", role_t, nullable=False),
        _created_at(),
        sa.UniqueConstraint("org_id", "user_id", name="org_user"),
    )
    op.create_index("ix_memberships_org_id", "memberships", ["org_id"])
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"])

    # ------------------------------------------------------------------
    # 5. sessions (D-07: opaque token, hash only)
    # ------------------------------------------------------------------
    op.create_table(
        "sessions",
        _uuid_pk(),
        _org_fk(),
        sa.Column(
            "user_id",
            uuid_t,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("csrf_token_hash", sa.String(64), nullable=False),
        # Server default so a row can never be inserted without an expiry
        # (an ASSERT alone would still 500 after the first successful insert).
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now() + interval '7 days'"),
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.String(255), nullable=True),
        sa.Column("ip", sa.String(64), nullable=True),
        _created_at(),
    )
    op.create_index("ix_sessions_org_id", "sessions", ["org_id"])
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_user_active", "sessions", ["user_id", "expires_at"])

    # ------------------------------------------------------------------
    # 6. projects
    # ------------------------------------------------------------------
    op.create_table(
        "projects",
        _uuid_pk(),
        _org_fk(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column(
            "settings_json", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        _created_at(),
        _updated_at(),
        sa.UniqueConstraint("org_id", "name", name="org_name"),
    )
    op.create_index("ix_projects_org_id", "projects", ["org_id"])

    # ------------------------------------------------------------------
    # 7. api_keys (FR-004: hash stored, plaintext shown once)
    # ------------------------------------------------------------------
    op.create_table(
        "api_keys",
        _uuid_pk(),
        _org_fk(),
        sa.Column(
            "project_id",
            uuid_t,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("prefix", sa.String(32), nullable=False, unique=True),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
    )
    op.create_index("ix_api_keys_org_id", "api_keys", ["org_id"])
    op.create_index("ix_api_keys_project", "api_keys", ["project_id"])

    # ------------------------------------------------------------------
    # 8. sources
    # ------------------------------------------------------------------
    op.create_table(
        "sources",
        _uuid_pk(),
        _org_fk(),
        sa.Column(
            "project_id",
            uuid_t,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("type", source_type_t, nullable=False),
        sa.Column(
            "config_enc", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "status", sa.String(32), nullable=False, server_default=sa.text("'connected'")
        ),
        sa.Column("last_received_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        _updated_at(),
        sa.UniqueConstraint("project_id", "slug", name="project_slug"),
        sa.CheckConstraint(
            "status in ('connected','error','paused')", name="source_status_values"
        ),
    )
    op.create_index("ix_sources_org_id", "sources", ["org_id"])
    op.create_index("ix_sources_project_id", "sources", ["project_id"])

    # ------------------------------------------------------------------
    # 9. datasets (bronze/silver/gold/quarantine pointers)
    # ------------------------------------------------------------------
    op.create_table(
        "datasets",
        _uuid_pk(),
        _org_fk(),
        sa.Column(
            "project_id",
            uuid_t,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_id",
            uuid_t,
            sa.ForeignKey("sources.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("layer", layer_t, nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column(
            "schema_json", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("storage_uri", sa.Text(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("partition_key", sa.String(64), nullable=True),
        _created_at(),
        _updated_at(),
        sa.UniqueConstraint("project_id", "layer", "name", name="project_layer_name"),
        sa.CheckConstraint(
            "storage_uri like 's3://%'", name="storage_uri_scheme"
        ),
    )
    op.create_index("ix_datasets_org_id", "datasets", ["org_id"])
    op.create_index("ix_datasets_project_id", "datasets", ["project_id"])
    op.create_index("ix_datasets_layer", "datasets", ["project_id", "layer"])

    # ------------------------------------------------------------------
    # 10. pipelines (current_version_id FK is added after versions exist)
    # ------------------------------------------------------------------
    op.create_table(
        "pipelines",
        _uuid_pk(),
        _org_fk(),
        sa.Column(
            "project_id",
            uuid_t,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("current_version_id", uuid_t, nullable=True),
        sa.Column("schedule_cron", sa.String(120), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        _created_at(),
        _updated_at(),
        sa.UniqueConstraint("project_id", "name", name="project_name"),
    )
    op.create_index("ix_pipelines_org_id", "pipelines", ["org_id"])
    op.create_index("ix_pipelines_project_id", "pipelines", ["project_id"])

    # ------------------------------------------------------------------
    # 11. agent_runs - created BEFORE pipeline_versions because
    #     pipeline_versions.created_by_agent_run_id references it.
    #     (agent_runs.target_run_id -> runs.id is wired later.)
    # ------------------------------------------------------------------
    op.create_table(
        "agent_runs",
        _uuid_pk(),
        _org_fk(),
        sa.Column(
            "project_id",
            uuid_t,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("agent_type", agent_type_t, nullable=False),
        sa.Column("thread_id", sa.String(128), nullable=False, unique=True),
        sa.Column(
            "status",
            agent_run_status_t,
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "budget_tokens", sa.Integer(), nullable=False, server_default=sa.text("20000")
        ),
        sa.Column("tokens_used", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "cost_usd", sa.Numeric(12, 6), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("target_run_id", uuid_t, nullable=True),
        sa.Column(
            "target_dataset_id",
            uuid_t,
            sa.ForeignKey("datasets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "input_json", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "created_by_user_id",
            uuid_t,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("current_step", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        _created_at(),
    )
    op.create_index("ix_agent_runs_org_id", "agent_runs", ["org_id"])
    op.create_index("ix_agent_runs_project_id", "agent_runs", ["project_id"])
    op.create_index("ix_agent_runs_org_status", "agent_runs", ["org_id", "status"])

    # ------------------------------------------------------------------
    # 12. pipeline_versions (immutable; runs pin one of these)
    # ------------------------------------------------------------------
    op.create_table(
        "pipeline_versions",
        _uuid_pk(),
        _org_fk(),
        sa.Column(
            "pipeline_id",
            uuid_t,
            sa.ForeignKey("pipelines.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("dag_json", jsonb, nullable=False),
        sa.Column(
            "validation_status",
            validation_status_t,
            nullable=False,
            server_default=sa.text("'draft'"),
        ),
        sa.Column(
            "validation_errors",
            jsonb,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_by_kind",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'user'"),
        ),
        sa.Column(
            "created_by_user_id",
            uuid_t,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_by_agent_run_id",
            uuid_t,
            sa.ForeignKey("agent_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "parent_version_id",
            uuid_t,
            sa.ForeignKey("pipeline_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("change_note", sa.String(500), nullable=True),
        _created_at(),
        sa.UniqueConstraint("pipeline_id", "version", name="pipeline_version"),
        sa.CheckConstraint(
            "created_by_kind in ('user','agent')", name="created_by_kind_values"
        ),
        sa.CheckConstraint("version >= 1", name="version_positive"),
    )
    op.create_index("ix_pipeline_versions_org_id", "pipeline_versions", ["org_id"])
    op.create_index("ix_pipeline_versions_pipeline_id", "pipeline_versions", ["pipeline_id"])

    # Close the loop: pipelines.current_version_id -> pipeline_versions.id
    op.create_foreign_key(
        "fk_pipelines_current_version_id_pipeline_versions",
        "pipelines",
        "pipeline_versions",
        ["current_version_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # ------------------------------------------------------------------
    # 13. runs (state machine, PRD section 9.10)
    # ------------------------------------------------------------------
    op.create_table(
        "runs",
        _uuid_pk(),
        _org_fk(),
        sa.Column(
            "project_id",
            uuid_t,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "pipeline_id",
            uuid_t,
            sa.ForeignKey("pipelines.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "pipeline_version_id",
            uuid_t,
            sa.ForeignKey("pipeline_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "trigger", run_trigger_t, nullable=False, server_default=sa.text("'manual'")
        ),
        sa.Column(
            "status", run_status_t, nullable=False, server_default=sa.text("'queued'")
        ),
        sa.Column(
            "triggered_by_user_id",
            uuid_t,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("current_node_id", sa.String(64), nullable=True),
        sa.Column(
            "queued_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("now()"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("rows_in", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("rows_out", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "rows_quarantined", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "retry_total", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("batch_id", uuid_t, nullable=True),
        _created_at(),
        sa.UniqueConstraint(
            "pipeline_id", "idempotency_key", name="pipeline_idempotency_key"
        ),
        sa.CheckConstraint(
            "rows_in >= 0 and rows_out >= 0 and rows_quarantined >= 0",
            name="run_rows_non_negative",
        ),
    )
    op.create_index("ix_runs_org_id", "runs", ["org_id"])
    op.create_index("ix_runs_project_id", "runs", ["project_id"])
    op.create_index("ix_runs_project_created", "runs", ["project_id", "created_at"])
    op.create_index("ix_runs_status", "runs", ["status"])
    op.create_index("ix_runs_pipeline_id", "runs", ["pipeline_id"])

    # Wire the deferred agent_runs.target_run_id -> runs.id FK.
    op.create_foreign_key(
        "fk_agent_runs_target_run_id_runs",
        "agent_runs",
        "runs",
        ["target_run_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # ------------------------------------------------------------------
    # 14. run_nodes (denormalised org_id -> simple RLS)
    # ------------------------------------------------------------------
    op.create_table(
        "run_nodes",
        _uuid_pk(),
        _org_fk(),
        sa.Column(
            "run_id",
            uuid_t,
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_id", sa.String(64), nullable=False),
        sa.Column("node_type", sa.String(32), nullable=False),
        sa.Column(
            "status", node_status_t, nullable=False, server_default=sa.text("'pending'")
        ),
        sa.Column(
            "retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("rows_in", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("rows_out", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "rows_quarantined", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_json", jsonb, nullable=True),
        _created_at(),
        sa.UniqueConstraint("run_id", "node_id", "attempt", name="run_node_attempt"),
    )
    op.create_index("ix_run_nodes_org_id", "run_nodes", ["org_id"])
    op.create_index("ix_run_nodes_run", "run_nodes", ["run_id"])

    # ------------------------------------------------------------------
    # 15. agent_proposals (FR-031: approval gate)
    # ------------------------------------------------------------------
    op.create_table(
        "agent_proposals",
        _uuid_pk(),
        _org_fk(),
        sa.Column(
            "agent_run_id",
            uuid_t,
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", proposal_kind_t, nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("payload_json", jsonb, nullable=False),
        sa.Column("evidence_json", jsonb, nullable=True),
        sa.Column("risk", risk_level_t, nullable=False, server_default=sa.text("'low'")),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column(
            "status",
            proposal_status_t,
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "decided_by",
            uuid_t,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.String(1000), nullable=True),
        sa.Column(
            "applied_pipeline_version_id",
            uuid_t,
            sa.ForeignKey("pipeline_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        _created_at(),
        sa.CheckConstraint(
            "confidence is null or (confidence >= 0 and confidence <= 1)",
            name="confidence_range",
        ),
    )
    op.create_index("ix_agent_proposals_org_id", "agent_proposals", ["org_id"])
    op.create_index("ix_agent_proposals_agent_run_id", "agent_proposals", ["agent_run_id"])
    op.create_index("ix_agent_proposals_pending", "agent_proposals", ["org_id", "status"])

    # ------------------------------------------------------------------
    # 16. agent_events (PRD section 9.9 execution trace)
    # ------------------------------------------------------------------
    op.create_table(
        "agent_events",
        _uuid_pk(),
        _org_fk(),
        sa.Column(
            "agent_run_id",
            uuid_t,
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step", sa.String(64), nullable=False),
        sa.Column("node_name", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("payload_json", jsonb, nullable=True),
        sa.Column(
            "at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        _created_at(),
    )
    op.create_index("ix_agent_events_org_id", "agent_events", ["org_id"])
    op.create_index("ix_agent_events_run", "agent_events", ["agent_run_id", "at"])

    # ------------------------------------------------------------------
    # 17. llm_calls (FR-036: model, tokens, latency, cost, node)
    # ------------------------------------------------------------------
    op.create_table(
        "llm_calls",
        _uuid_pk(),
        _org_fk(),
        sa.Column(
            "agent_run_id",
            uuid_t,
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "project_id",
            uuid_t,
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("node", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("tokens_out", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "cost_usd", sa.Numeric(12, 6), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("error", sa.Text(), nullable=True),
        _created_at(),
    )
    op.create_index("ix_llm_calls_org_id", "llm_calls", ["org_id"])
    op.create_index("ix_llm_calls_org_at", "llm_calls", ["org_id", "created_at"])
    op.create_index("ix_llm_calls_agent_run", "llm_calls", ["agent_run_id"])

    # ------------------------------------------------------------------
    # 18. artifacts (versioned outputs, validated before storage)
    # ------------------------------------------------------------------
    op.create_table(
        "artifacts",
        _uuid_pk(),
        _org_fk(),
        sa.Column(
            "project_id",
            uuid_t,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("producing_agent", agent_type_t, nullable=True),
        sa.Column(
            "agent_run_id",
            uuid_t,
            sa.ForeignKey("agent_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "source_artifact_versions",
            jsonb,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "validation_status",
            validation_status_t,
            nullable=False,
            server_default=sa.text("'draft'"),
        ),
        sa.Column("storage_uri", sa.Text(), nullable=True),
        sa.Column("payload_json", jsonb, nullable=True),
        _created_at(),
        sa.UniqueConstraint("project_id", "type", "version", name="artifact_version"),
    )
    op.create_index("ix_artifacts_org_id", "artifacts", ["org_id"])
    op.create_index("ix_artifacts_project_id", "artifacts", ["project_id"])

    # ------------------------------------------------------------------
    # 19. audit_logs (FR-043) - append only
    # ------------------------------------------------------------------
    op.create_table(
        "audit_logs",
        _uuid_pk(),
        _org_fk(),
        sa.Column("actor_kind", actor_kind_t, nullable=False),
        sa.Column("actor_id", uuid_t, nullable=True),
        sa.Column("actor_email", sa.String(254), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(64), nullable=False),
        sa.Column("target_id", sa.String(128), nullable=True),
        sa.Column(
            "metadata_json", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column(
            "at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        _created_at(),
    )
    op.create_index("ix_audit_logs_org_id", "audit_logs", ["org_id"])
    op.create_index("ix_audit_logs_org_at", "audit_logs", ["org_id", "at"])

    # ------------------------------------------------------------------
    # 20. ingest_idempotency (FR-016: 24h dedup window)
    # ------------------------------------------------------------------
    op.create_table(
        "ingest_idempotency",
        _uuid_pk(),
        _org_fk(),
        sa.Column(
            "source_id",
            uuid_t,
            sa.ForeignKey("sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column(
            "response_accepted", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("response_batch_hint", sa.String(64), nullable=True),
        # 24 h dedup window (FR-016). Server default so the column can never be
        # written as NULL, which would leave an un-purgeable idempotency row.
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now() + interval '24 hours'"),
        ),
        _created_at(),
        sa.UniqueConstraint("source_id", "key", name="source_idem_key"),
    )
    op.create_index("ix_ingest_idempotency_org_id", "ingest_idempotency", ["org_id"])
    op.create_index("ix_ingest_idempotency_expires", "ingest_idempotency", ["expires_at"])

    # ------------------------------------------------------------------
    # 21. uploads (FR-013: presigned upload bookkeeping)
    # ------------------------------------------------------------------
    op.create_table(
        "uploads",
        _uuid_pk(),
        _org_fk(),
        sa.Column(
            "project_id",
            uuid_t,
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_id",
            uuid_t,
            sa.ForeignKey("sources.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "dataset_id",
            uuid_t,
            sa.ForeignKey("datasets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column(
            "status",
            upload_status_t,
            nullable=False,
            server_default=sa.text("'initiated'"),
        ),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
    )
    op.create_index("ix_uploads_org_id", "uploads", ["org_id"])
    op.create_index("ix_uploads_project_id", "uploads", ["project_id"])

    # ------------------------------------------------------------------
    # 22. Row Level Security: one policy per org-scoped table.
    #
    #     current_setting('app.current_org_id', true) returns NULL instead of
    #     raising when the app forgot to set the context. The comparison is then
    #     NULL -> zero rows, which is the safe failure mode.
    # ------------------------------------------------------------------
    for table in ORG_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        # FORCE also applies the policy to the table OWNER. Without it the
        # migration role (== application role on the default docker/single-URL
        # setup) would silently bypass every policy below.
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {POLICY_NAME} ON {table} "
            "USING (org_id = current_setting('app.current_org_id', true)::uuid) "
            "WITH CHECK (org_id = current_setting('app.current_org_id', true)::uuid)"
        )

    # Helpful, cheap index for the Dashboard's "last 24h" query.
    op.create_index(
        "ix_runs_org_created",
        "runs",
        ["org_id", "created_at"],
    )


def downgrade() -> None:
    """Drop everything in reverse dependency order.

    RLS policies disappear with their tables, so only tables and ENUM types
    need explicit drops. LangGraph checkpoint tables are left untouched: they
    are not owned by this migration.
    """
    op.drop_index("ix_runs_org_created", table_name="runs")

    for table in (
        "uploads",
        "ingest_idempotency",
        "audit_logs",
        "artifacts",
        "llm_calls",
        "agent_events",
        "agent_proposals",
        "run_nodes",
    ):
        op.drop_table(table)

    # Break the two late circular FKs before dropping their targets.
    op.drop_constraint("fk_agent_runs_target_run_id_runs", "agent_runs", type_="foreignkey")
    op.drop_constraint(
        "fk_pipelines_current_version_id_pipeline_versions",
        "pipelines",
        type_="foreignkey",
    )

    op.drop_table("runs")
    op.drop_table("pipeline_versions")
    op.drop_table("agent_runs")
    op.drop_table("pipelines")
    op.drop_table("datasets")
    op.drop_table("sources")
    op.drop_table("api_keys")
    op.drop_table("projects")
    op.drop_table("sessions")
    op.drop_table("memberships")
    op.drop_table("organizations")
    op.drop_table("users")

    for enum_name in (
        "upload_status",
        "run_trigger",
        "actor_kind",
        "risk_level",
        "validation_status",
        "agent_run_status",
        "agent_type",
        "proposal_kind",
        "proposal_status",
        "node_status",
        "run_status",
        "layer",
        "source_type",
        "role",
    ):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")

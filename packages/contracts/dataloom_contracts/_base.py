"""Shared primitive types used by every other contract module.

Design rules baked into this module:
- All ids are UUID strings on the wire (JSON) but typed ``uuid.UUID`` in Python.
- All timestamps are timezone-aware UTC (``TIMESTAMPTZ`` in Postgres).
- Field names are snake_case. Agents / Backend / UI MUST import from here
  instead of re-declaring these types.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    """Base class for every DataLoom contract.

    ``extra="forbid"`` is deliberate: agent output is machine-generated, so an
    unexpected key is a bug we want surfaced by the Pydantic validator
    (PRD section 10.1 rule 4) instead of being silently dropped.
    """

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        frozen=False,
    )


class ReadModel(ContractModel):
    """Response model that is tolerant of extra keys.

    Used for API responses built from DB rows: adding a column to the DB must
    not break an older client, so read models ignore unknown keys.
    """

    model_config = ConfigDict(extra="ignore", validate_assignment=True, frozen=False)


# ---------------------------------------------------------------------------
# Scalar aliases (single source of truth for OpenAPI generation)
# ---------------------------------------------------------------------------
OrgId = uuid.UUID
ProjectId = uuid.UUID
SourceId = uuid.UUID
PipelineId = uuid.UUID
PipelineVersionId = uuid.UUID
RunId = uuid.UUID
AgentRunId = uuid.UUID
ProposalId = uuid.UUID
DatasetId = uuid.UUID

#: USD amount, NUMERIC(12,6) in Postgres. Never negative.
UsdAmount = Annotated[float, Field(ge=0.0)]


def utcnow() -> datetime:
    """Timezone-aware UTC now. Never use ``datetime.utcnow()`` (naive)."""
    return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Enumerations shared across API, DB and agents.
# These are the ONLY canonical spellings; DB ENUM labels match them 1:1.
# ---------------------------------------------------------------------------


class Role(str, Enum):
    """Membership role (FR-002, PRD section 9.6)."""

    OWNER = "owner"
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"


class SourceType(str, Enum):
    """The four MVP source types (PRD section 4.1)."""

    WEBHOOK = "webhook"
    CSV = "csv"
    API = "api"
    POSTGRES = "postgres"


class Layer(str, Enum):
    """Medallion layers (PRD section 9.5)."""

    BRONZE = "bronze"
    SILVER = "silver"
    GOLD = "gold"
    QUARANTINE = "quarantine"


class RunStatus(str, Enum):
    """Pipeline run state machine values, PRD section 9.10 exactly."""

    DRAFT = "draft"
    VALIDATED = "validated"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRYING = "retrying"
    NEEDS_ATTENTION = "needs_attention"
    CANCELLED = "cancelled"


class RunTrigger(str, Enum):
    """Why a run exists (``runs.trigger``)."""

    MANUAL = "manual"
    SCHEDULE = "schedule"  # FR-028 cron
    WEBHOOK = "webhook"
    RETRY = "retry"
    SELF_HEAL = "self_heal"


class NodeStatus(str, Enum):
    """Per-node run status stored in ``run_nodes``."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    RETRYING = "retrying"
    CANCELLED = "cancelled"


class ProposalStatus(str, Enum):
    """Agent proposal lifecycle (PRD section 9.6)."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"


class ProposalKind(str, Enum):
    """``agent_proposals.kind`` - which artifact the payload carries."""

    CLEANING_RULES = "cleaning_rules"  # A1 Schema Profiler
    PIPELINE_DRAFT = "pipeline_draft"  # A2 Pipeline Builder
    FIX_PROPOSAL = "fix_proposal"  # A4 Self-Healing


class AgentType(str, Enum):
    """Agent catalogue, PRD section 10.2. A0 is deterministic code, not an LLM."""

    SUPERVISOR = "supervisor"
    SCHEMA_PROFILER = "schema_profiler"
    PIPELINE_BUILDER = "pipeline_builder"
    DATA_QUALITY_GUARDIAN = "data_quality_guardian"
    SELF_HEALING = "self_healing"
    TEXT_TO_SQL = "text_to_sql"


class AgentRunStatus(str, Enum):
    """Status of a single agent invocation (``agent_runs.status``).

    ``INSUFFICIENT_EVIDENCE`` is the "I refuse to guess" terminal state
    (PRD section 10.1 rule 2). It is NOT an error: it is a successful refusal.
    """

    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BUDGET_EXCEEDED = "budget_exceeded"


class AgentOutputStatus(str, Enum):
    """``status`` field present on EVERY agent output model (PRD section 10.1).

    - ``ok``                    : output complete and schema-validated.
    - ``insufficient_evidence`` : agent refused to guess; read ``open_questions``.
    - ``needs_clarification``   : one blocking ambiguity needs a user answer.
    - ``rejected``              : agent intentionally declined (e.g. unsafe SQL).
    """

    OK = "ok"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NEEDS_CLARIFICATION = "needs_clarification"
    REJECTED = "rejected"


class ValidationStatus(str, Enum):
    """``pipeline_versions.validation_status`` and ``artifacts.validation_status``."""

    DRAFT = "draft"
    VALID = "valid"
    INVALID = "invalid"


class ArtifactType(str, Enum):
    """``artifacts.type`` values."""

    PROFILE_REPORT = "profile_report"
    PIPELINE_DRAFT = "pipeline_draft"
    QUALITY_REPORT = "quality_report"
    DIAGNOSIS = "diagnosis"
    FIX_PROPOSAL = "fix_proposal"
    SQL_ANSWER = "sql_answer"
    DAG = "dag"


class RiskLevel(str, Enum):
    """Cleaning/fix risk (PRD A1). ``destructive`` needs explicit approval."""

    LOW = "low"
    MEDIUM = "medium"
    DESTRUCTIVE = "destructive"


class ActorKind(str, Enum):
    """Who performed an audited action (``audit_logs.actor_kind``)."""

    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class GateDecision(str, Enum):
    """Gate verdict produced by the deterministic A0 supervisor (PRD A0)."""

    PASS = "pass"
    FAIL = "fail"


class ColumnDtype(str, Enum):
    """Closed set of column types the profiler may infer."""

    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    DATE = "date"
    TIMESTAMP = "timestamp"
    JSON = "json"
    UNKNOWN = "unknown"


class ColumnSemantic(str, Enum):
    """Coarse semantic guess; used for hints only, never for control flow."""

    IDENTIFIER = "identifier"
    EMAIL = "email"
    PHONE = "phone"
    CURRENCY = "currency"
    DATE = "date"
    CATEGORY = "category"
    FREE_TEXT = "free_text"
    OTHER = "other"


class CleaningOp(str, Enum):
    """CLOSED catalog of cleaning operations (PRD A1). Nothing else is allowed."""

    CAST = "cast"
    TRIM = "trim"
    NORMALIZE_NULL = "normalize_null"
    DEDUP = "dedup"
    PARSE_DATE = "parse_date"
    LOWERCASE = "lowercase"
    REGEX_REPLACE = "regex_replace"
    DROP_IF = "drop_if"
    FILL_DEFAULT = "fill_default"


class UploadStatus(str, Enum):
    """CSV/Parquet upload lifecycle (FR-013)."""

    INITIATED = "initiated"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


class DiagnosisCategory(str, Enum):
    """A4 Self-Healing diagnosis buckets, PRD A4 exactly."""

    SCHEMA_DRIFT = "schema_drift"
    DATA_QUALITY = "data_quality"
    TIMEOUT = "timeout"
    RESOURCE = "resource"
    BUG = "bug"


class RunEventType(str, Enum):
    """SSE event names on ``GET /v1/runs/{id}/events`` (FR-040)."""

    NODE_STATUS = "node_status"
    NODE_LOG = "node_log"
    GATE = "gate"
    RUN_STATUS = "run_status"
    HEARTBEAT = "heartbeat"


#: Free-form JSON object alias used in params/config fields.
JsonDict = dict[str, Any]


__all__ = [
    "ActorKind",
    "AgentOutputStatus",
    "AgentRunId",
    "AgentRunStatus",
    "AgentType",
    "ArtifactType",
    "CleaningOp",
    "ColumnDtype",
    "ColumnSemantic",
    "ContractModel",
    "DatasetId",
    "DiagnosisCategory",
    "GateDecision",
    "JsonDict",
    "Layer",
    "NodeStatus",
    "OrgId",
    "PipelineId",
    "PipelineVersionId",
    "ProposalId",
    "ProposalKind",
    "ProposalStatus",
    "ProjectId",
    "ReadModel",
    "RiskLevel",
    "Role",
    "RunEventType",
    "RunId",
    "RunStatus",
    "RunTrigger",
    "SourceId",
    "SourceType",
    "UploadStatus",
    "UsdAmount",
    "ValidationStatus",
    "utcnow",
]

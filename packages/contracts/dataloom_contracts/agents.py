"""Agent output contracts (PRD section 10). SINGLE SOURCE OF TRUTH for agents.

Rules encoded here, not in prompts:
- Every output has ``status``; ``insufficient_evidence`` is a first-class
  successful outcome, never an error (PRD section 10.1 rule 2).
- Cleaning operations come from the CLOSED ``CleaningOp`` catalog. There is no
  free-form operation string anywhere in this module.
- Every claim carries ``evidence`` (PRD section 10.1 rule 3).
- Risk ``destructive`` on a proposal requires human approval (A1 bounds).

The AI Engineer builds LangGraph nodes whose *final* state field must be one of
these models; the Supervisor's ``validate_contract`` step validates against
them before anything reaches the database or the UI.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import Field, model_validator

from ._base import (
    AgentOutputStatus,
    AgentRunStatus,
    AgentType,
    CleaningOp,
    ColumnDtype,
    ColumnSemantic,
    ContractModel,
    DiagnosisCategory,
    JsonDict,
    ProposalKind,
    ProposalStatus,
    ReadModel,
    RiskLevel,
)
from .common import ValidationIssue
from .pipelines import PipelineDAG

#: Task budget defaults from PRD section 9.9. These are policy defaults, not
#: measurements: the PRD states them as configuration, not as observed values.
DEFAULT_NODE_TIMEOUT_SECONDS = 45
DEFAULT_AGENT_RUN_TIMEOUT_SECONDS = 300
DEFAULT_MAX_RETRIES_PER_NODE = 2
DEFAULT_MAX_VALIDATION_REPAIRS = 2


# ---------------------------------------------------------------------------
# A1 - Schema Profiler: ProfileReport
# ---------------------------------------------------------------------------


class ColumnStats(ContractModel):
    """Deterministic statistics computed by code, not by the LLM.

    The LLM receives these numbers; it must not invent them. ``None`` means
    "not applicable for this dtype" (e.g. ``mean`` on a string column).
    """

    row_count: int = Field(ge=0)
    null_count: int = Field(ge=0)
    null_ratio: float = Field(ge=0.0, le=1.0)
    distinct_count: int | None = Field(default=None, ge=0)
    min_value: str | None = None
    max_value: str | None = None
    mean_value: float | None = None
    sample_values: list[str] = Field(
        default_factory=list, max_length=50, description="At most 50 raw samples."
    )
    top_patterns: list[str] = Field(
        default_factory=list, description="Regex patterns that match many values."
    )


class ColumnProfile(ContractModel):
    """One column in the profile report (PRD A1 output shape)."""

    name: str = Field(min_length=1, max_length=128)
    dtype: ColumnDtype
    nullable: bool
    semantic: ColumnSemantic = ColumnSemantic.OTHER
    stats: ColumnStats
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Agent confidence that dtype/semantic are correct.",
    )


class AnomalyKind(str, Enum):
    """Anomaly vocabulary. A closed set keeps the UI badge list exhaustive."""

    NULL_SPIKE = "null_spike"
    TYPE_MIX = "type_mix"
    DUPLICATE_ROWS = "duplicate_rows"
    OUTLIER = "outlier"
    FORMAT_INCONSISTENT = "format_inconsistent"
    NEGATIVE_VALUE = "negative_value"
    CONSTANT_COLUMN = "constant_column"
    DATE_OUT_OF_RANGE = "date_out_of_range"
    ENCODING_ISSUE = "encoding_issue"
    UNEXPECTED_CATEGORY = "unexpected_category"


class Anomaly(ContractModel):
    """A detected anomaly. ``evidence`` is the statistic that proves it."""

    kind: AnomalyKind
    column: str = Field(min_length=1, max_length=128)
    severity: Literal["info", "warning", "error"] = "warning"
    evidence: str = Field(
        min_length=1,
        max_length=500,
        description="Indonesian sentence with the number, e.g. '12.4% nilai kosong'.",
    )
    examples: list[str] = Field(default_factory=list, max_length=10)


class CleaningProposal(ContractModel):
    """One proposed cleaning rule. Executable AS-IS by the worker after approval.

    ``op`` and its required params are validated against the closed catalog, so
    an agent cannot smuggle in an operation that the worker cannot execute.
    """

    proposal_id: str = Field(
        min_length=1,
        max_length=64,
        description="Stable within one agent run; used as React key.",
    )
    op: CleaningOp
    column: str = Field(min_length=1, max_length=128)
    params: JsonDict = Field(
        default_factory=dict,
        description="Op-specific params, e.g. {'format': 'DD/MM/YYYY'} for parse_date.",
    )
    evidence: str = Field(
        min_length=1,
        max_length=500,
        description="Required. A proposal without evidence is invalid (PRD A1 bounds).",
    )
    risk: RiskLevel = RiskLevel.LOW
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str | None = Field(
        default=None, max_length=500, description="One or two sentences, no jargon."
    )
    before_samples: list[str] = Field(default_factory=list, max_length=5)
    after_samples: list[str] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def _op_params(self) -> "CleaningProposal":
        required: dict[CleaningOp, tuple[str, ...]] = {
            CleaningOp.CAST: ("to_type",),
            CleaningOp.PARSE_DATE: ("format",),
            CleaningOp.REGEX_REPLACE: ("pattern", "replacement"),
            CleaningOp.DROP_IF: ("condition",),
            CleaningOp.FILL_DEFAULT: ("value",),
            CleaningOp.DEDUP: ("keys",),
        }
        for key in required.get(self.op, ()):
            if key not in self.params:
                raise ValueError(
                    f"Usulan '{self.op.value}' pada kolom '{self.column}' "
                    f"membutuhkan parameter '{key}'."
                )
        # PRD A1: regex must be bounded to avoid pathological patterns.
        if self.op is CleaningOp.REGEX_REPLACE:
            pattern = str(self.params.get("pattern", ""))
            if len(pattern) > 256:
                raise ValueError(
                    "Pola regex terlalu panjang (maks 256 karakter) untuk keamanan."
                )
            for forbidden in ("(?<=", "(?<!", "(?>", "(?P="):
                if forbidden in pattern:
                    raise ValueError(
                        f"Pola regex memakai fitur terlarang: {forbidden!r}."
                    )
        return self


class ProfileReport(ContractModel):
    """A1 output. ``status != ok`` still returns a valid, useful report."""

    status: AgentOutputStatus = AgentOutputStatus.OK
    dataset_id: uuid.UUID
    sampled_rows: int = Field(ge=0, le=1000)
    total_rows: int | None = Field(default=None, ge=0)
    columns: list[ColumnProfile] = Field(default_factory=list)
    anomalies: list[Anomaly] = Field(default_factory=list)
    cleaning_proposals: list[CleaningProposal] = Field(default_factory=list)
    open_questions: list[str] = Field(
        default_factory=list,
        description="Required when status is insufficient_evidence/needs_clarification.",
    )
    summary: str | None = Field(
        default=None, max_length=1000, description="Short Indonesian summary."
    )
    generated_at: datetime | None = None

    @model_validator(mode="after")
    def _refusal_needs_questions(self) -> "ProfileReport":
        if self.status is not AgentOutputStatus.OK and not self.open_questions:
            raise ValueError(
                "Saat status bukan 'ok', 'open_questions' wajib berisi minimal "
                "satu pertanyaan untuk pengguna."
            )
        ids = [p.proposal_id for p in self.cleaning_proposals]
        if len(ids) != len(set(ids)):
            raise ValueError("proposal_id harus unik dalam satu ProfileReport.")
        return self


# ---------------------------------------------------------------------------
# A2 - Pipeline Builder: PipelineDraft
# ---------------------------------------------------------------------------


class Assumption(ContractModel):
    """An explicit assumption the draft relies on. Shown to the user verbatim."""

    text: str = Field(min_length=1, max_length=500)
    impact: Literal["low", "medium", "high"] = "low"


class PipelineDraft(ContractModel):
    """A2 output: a DAG that ALREADY passed the deterministic validator (FR-032).

    ``validation_issues`` is populated only when the agent could not repair the
    DAG within the retry budget (PRD A2: invalid after 2 repairs -> escalate).
    """

    status: AgentOutputStatus = AgentOutputStatus.OK
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    dag: PipelineDAG
    assumptions: list[Assumption] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    validation_issues: list[ValidationIssue] = Field(default_factory=list)
    repair_attempts: int = Field(default=0, ge=0, le=2)

    @model_validator(mode="after")
    def _status_matches_issues(self) -> "PipelineDraft":
        if self.validation_issues and self.status is AgentOutputStatus.OK:
            raise ValueError(
                "PipelineDraft dengan validation_issues tidak boleh berstatus 'ok'."
            )
        if self.status is not AgentOutputStatus.OK and not self.open_questions:
            raise ValueError(
                "Saat status bukan 'ok', 'open_questions' wajib berisi pertanyaan."
            )
        return self


# ---------------------------------------------------------------------------
# A3 - Data Quality Guardian: QualityReport (deterministic checks + optional text)
# ---------------------------------------------------------------------------


class QualityRuleResult(ContractModel):
    """Deterministic pass/fail for one rule. The LLM never sets ``passed``."""

    rule_id: str = Field(min_length=1, max_length=64)
    kind: str
    column: str | None = None
    passed: bool
    rows_failed: int = Field(ge=0)
    quarantine_code: str | None = Field(
        default=None, max_length=64, description="Reason code written per bad row."
    )
    sample_values: list[str] = Field(default_factory=list, max_length=5)


class QualityReport(ContractModel):
    """A3 output. ``narrative`` is the only LLM-written field."""

    status: AgentOutputStatus = AgentOutputStatus.OK
    run_id: uuid.UUID | None = None
    dataset_id: uuid.UUID
    rows_checked: int = Field(ge=0)
    rows_passed: int = Field(ge=0)
    rows_quarantined: int = Field(ge=0)
    quarantine_ratio: float = Field(ge=0.0, le=1.0)
    results: list[QualityRuleResult] = Field(default_factory=list)
    narrative: str | None = Field(
        default=None,
        max_length=1500,
        description="Optional LLM explanation of the failure pattern.",
    )

    @model_validator(mode="after")
    def _counts_add_up(self) -> "QualityReport":
        if self.rows_passed + self.rows_quarantined > self.rows_checked:
            raise ValueError(
                "rows_passed + rows_quarantined tidak boleh melebihi rows_checked."
            )
        return self


# ---------------------------------------------------------------------------
# A4 - Self-Healing: Diagnosis + FixProposal
# ---------------------------------------------------------------------------


class Evidence(ContractModel):
    """One piece of evidence: where it came from and what it says."""

    source: Literal["run_node_error", "log", "schema_diff", "stat", "user_report"]
    locator: str = Field(
        max_length=300, description="Node id, log line range, or column name."
    )
    excerpt: str = Field(
        max_length=2000, description="Redacted excerpt (secrets already removed)."
    )


class Diagnosis(ContractModel):
    """A4 first output. ``status=insufficient_evidence`` -> hand back to human."""

    status: AgentOutputStatus = AgentOutputStatus.OK
    run_id: uuid.UUID
    category: DiagnosisCategory
    summary: str = Field(min_length=1, max_length=1000)
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    open_questions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _evidence_required(self) -> "Diagnosis":
        if self.status is AgentOutputStatus.OK and not self.evidence:
            raise ValueError(
                "Diagnosis berstatus 'ok' wajib membawa minimal satu bukti."
            )
        if self.status is not AgentOutputStatus.OK and not self.open_questions:
            raise ValueError(
                "Saat status bukan 'ok', 'open_questions' wajib berisi pertanyaan."
            )
        return self


class FixProposal(ContractModel):
    """A4 second output: a DAG diff that becomes a NEW pipeline version.

    ``diff`` is a JSON-patch style list of operations so the UI can render a
    precise diff and the backend can apply it deterministically.
    """

    status: AgentOutputStatus = AgentOutputStatus.OK
    run_id: uuid.UUID
    diagnosis_category: DiagnosisCategory
    risk: RiskLevel
    description: str = Field(min_length=1, max_length=1000)
    dag: PipelineDAG = Field(description="The complete proposed DAG after the fix.")
    diff: JsonDict = Field(
        default_factory=dict,
        description="Structured diff: {'added_nodes': [...], 'removed_nodes': [...], "
        "'changed_nodes': [...]}.",
    )
    evidence: list[Evidence] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


    @model_validator(mode="after")
    def _refusal_needs_questions(self) -> "FixProposal":
        if self.status is not AgentOutputStatus.OK and not self.open_questions:
            raise ValueError(
                "Saat status bukan 'ok', 'open_questions' wajib berisi pertanyaan."
            )
        return self


# ---------------------------------------------------------------------------
# A5 - Text-to-SQL: SqlAnswer
# ---------------------------------------------------------------------------


class ResultColumn(ContractModel):
    """A column in the query result set."""

    name: str = Field(min_length=1, max_length=128)
    dtype: str = Field(
        default="string",
        description="Display type: string | integer | float | boolean | date | timestamp.",
    )


class SqlAnswer(ContractModel):
    """A5 output. ``sql`` is ALWAYS validated by sqlglot before this is built.

    ``status=rejected`` carries ``rejection_code`` (e.g. ``NOT_SELECT``) so the
    Chat-to-SQL page can show the exact blocked-reason variant.
    """

    status: AgentOutputStatus = AgentOutputStatus.OK
    question: str = Field(min_length=1, max_length=2000)
    sql: str | None = Field(
        default=None,
        max_length=20000,
        description="Single SELECT statement. null when status != ok.",
    )
    columns: list[ResultColumn] = Field(default_factory=list)
    rows: list[list[str | int | float | bool | None]] = Field(
        default_factory=list,
        max_length=1000,
        description="At most 1,000 rows; LIMIT is forced by the query service.",
    )
    row_count: int = Field(default=0, ge=0)
    truncated: bool = Field(
        default=False, description="True when LIMIT cut the result set."
    )
    explanation: str | None = Field(default=None, max_length=2000)
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    rejection_code: str | None = Field(
        default=None,
        description="NOT_SELECT | MULTI_STATEMENT | TABLE_NOT_ALLOWED | "
        "DISALLOWED_FUNCTION | TIMEOUT",
    )
    executed_ms: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _status_consistency(self) -> "SqlAnswer":
        if self.status is AgentOutputStatus.OK and not self.sql:
            raise ValueError("SqlAnswer berstatus 'ok' wajib memiliki SQL.")
        if self.status is AgentOutputStatus.REJECTED and not self.rejection_code:
            raise ValueError("SqlAnswer berstatus 'rejected' wajib punya rejection_code.")
        if (
            self.status is not AgentOutputStatus.OK
            and self.status is not AgentOutputStatus.REJECTED
            and not self.open_questions
        ):
            raise ValueError(
                "Status ini wajib menyertakan 'open_questions' sebagai pertanyaan "
                "klarifikasi ke pengguna."
            )
        if len(self.rows) > 1000:
            raise ValueError("Hasil query dibatasi 1.000 baris.")
        return self


# ---------------------------------------------------------------------------
# Agent run / proposal persistence contracts
# ---------------------------------------------------------------------------


class AgentRunCreateRequest(ContractModel):
    """Body of POST /v1/agents/{profile|build-pipeline|ask|diagnose}."""

    project_id: uuid.UUID
    dataset_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = Field(
        default=None, description="Target pipeline run for self-healing."
    )
    prompt: str | None = Field(
        default=None,
        max_length=4000,
        description="Natural-language input (build-pipeline, ask, diagnose note).",
    )
    locale: Literal["id", "en"] = Field(
        default="id", description="Agent speaks the user's language (PRD 10.1 rule 9)."
    )


class AgentRunOut(ReadModel):
    id: uuid.UUID
    org_id: uuid.UUID
    project_id: uuid.UUID
    agent_type: AgentType
    thread_id: str = Field(description="LangGraph checkpoint thread_id (FR-037).")
    status: AgentRunStatus
    budget_tokens: int = Field(ge=0)
    tokens_used: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0.0)
    current_step: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_code: str | None = None
    created_at: datetime


class AgentProposalOut(ReadModel):
    """A persisted proposal card in the Agent Workspace (FR-031)."""

    id: uuid.UUID
    org_id: uuid.UUID
    agent_run_id: uuid.UUID
    kind: ProposalKind
    title: str
    payload: JsonDict = Field(description="Typed payload matching ``kind``.")
    evidence: JsonDict | None = None
    risk: RiskLevel = RiskLevel.LOW
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    status: ProposalStatus
    decided_by: uuid.UUID | None = None
    decided_at: datetime | None = None
    decision_note: str | None = None
    created_at: datetime


class ProposalDecisionRequest(ContractModel):
    """POST /v1/proposals/{id}/decision - the ONLY way a proposal takes effect."""

    decision: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=1000)
    apply_as_new_version: bool = Field(
        default=True,
        description="For cleaning/pipeline proposals: create a new immutable version.",
    )


class ProposalDecisionResponse(ReadModel):
    proposal: AgentProposalOut
    applied: bool
    new_pipeline_version_id: uuid.UUID | None = None
    audit_log_id: uuid.UUID | None = None


class AgentEventOut(ReadModel):
    """One row of ``agent_events``: the visible execution trace (PRD 9.9)."""

    id: uuid.UUID
    agent_run_id: uuid.UUID
    step: str
    node_name: str
    status: str
    payload: JsonDict | None = None
    at: datetime


class AgentRunDetail(AgentRunOut):
    proposals: list[AgentProposalOut] = Field(default_factory=list)
    events: list[AgentEventOut] = Field(default_factory=list)


class ProfileRunResponse(ReadModel):
    """Response of POST /v1/agents/profile: report + pending proposals."""

    agent_run: AgentRunOut
    report: ProfileReport
    proposals: list[AgentProposalOut] = Field(default_factory=list)


class PipelineDraftResponse(ReadModel):
    agent_run: AgentRunOut
    draft: PipelineDraft
    proposals: list[AgentProposalOut] = Field(default_factory=list)


class SqlAnswerResponse(ReadModel):
    agent_run: AgentRunOut
    answer: SqlAnswer


class DiagnosisResponse(ReadModel):
    agent_run: AgentRunOut
    diagnosis: Diagnosis
    fix_proposal: FixProposal | None = None


# Re-exported so agents import the DAG type from one place.
ContractDAG = PipelineDAG

__all__ = [
    "AgentEventOut",
    "AgentProposalOut",
    "AgentRunCreateRequest",
    "AgentRunDetail",
    "AgentRunOut",
    "Anomaly",
    "AnomalyKind",
    "Assumption",
    "CleaningProposal",
    "ColumnProfile",
    "ColumnStats",
    "ContractDAG",
    "DEFAULT_AGENT_RUN_TIMEOUT_SECONDS",
    "DEFAULT_MAX_RETRIES_PER_NODE",
    "DEFAULT_MAX_VALIDATION_REPAIRS",
    "DEFAULT_NODE_TIMEOUT_SECONDS",
    "Diagnosis",
    "DiagnosisResponse",
    "Evidence",
    "FixProposal",
    "PipelineDraft",
    "PipelineDraftResponse",
    "ProfileReport",
    "ProfileRunResponse",
    "ProposalDecisionRequest",
    "ProposalDecisionResponse",
    "QualityReport",
    "QualityRuleResult",
    "ResultColumn",
    "SqlAnswer",
    "SqlAnswerResponse",
]

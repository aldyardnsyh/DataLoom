"""Run, run-node and SSE event contracts (FR-025, FR-026, FR-040).

The SSE stream ``GET /v1/runs/{id}/events`` emits ONE JSON payload per event,
tagged by ``event``, matching the ``RunEvent`` union below. The UI switches on
``event`` to update the Run Monitor; nothing else is needed on the wire.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import Field

from ._base import (
    ContractModel,
    GateDecision,
    JsonDict,
    NodeStatus,
    ReadModel,
    RunEventType,
    RunStatus,
    RunTrigger,
)
from .common import ValidationIssue


# ---------------------------------------------------------------------------
# Persisted rows
# ---------------------------------------------------------------------------


class RunNodeOut(ReadModel):
    """One row of ``run_nodes`` - a single node's execution record."""

    id: uuid.UUID
    run_id: uuid.UUID
    node_id: str
    node_type: str
    status: NodeStatus
    retry_count: int = Field(default=0, ge=0)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    rows_in: int = Field(default=0, ge=0)
    rows_out: int = Field(default=0, ge=0)
    rows_quarantined: int = Field(default=0, ge=0)
    error_code: str | None = None
    error_message: str | None = None
    error: JsonDict | None = Field(
        default=None, description="Full error_json (redacted) for the error panel."
    )


class RunOut(ReadModel):
    """Run header. ``rows_*`` are the real counters the Dashboard sums."""

    id: uuid.UUID
    org_id: uuid.UUID
    project_id: uuid.UUID
    pipeline_id: uuid.UUID
    pipeline_version_id: uuid.UUID
    version: int = Field(ge=1)
    trigger: RunTrigger
    status: RunStatus
    current_node_id: str | None = None
    queued_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    rows_in: int = Field(default=0, ge=0)
    rows_out: int = Field(default=0, ge=0)
    rows_quarantined: int = Field(default=0, ge=0)
    quarantine_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    retry_total: int = Field(default=0, ge=0)
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime


class RunDetail(RunOut):
    """Run + its nodes, for the Run Monitor page in a single request."""

    nodes: list[RunNodeOut] = Field(default_factory=list)
    gate: "GateChecklist | None" = None


class RunCreateRequest(ContractModel):
    """Trigger a run. ``pipeline_version_id`` pinning is optional (FR-024)."""

    pipeline_version_id: uuid.UUID | None = Field(
        default=None, description="Omit to run the current version."
    )
    trigger: RunTrigger = RunTrigger.MANUAL
    idempotency_key: str | None = Field(
        default=None, max_length=128, description="Repeat-safe manual trigger."
    )


class RunCreateResponse(ReadModel):
    run: RunOut
    stream_url: str = Field(description="Relative SSE path: /v1/runs/{id}/events")


class CancelRunResponse(ReadModel):
    run_id: uuid.UUID
    status: RunStatus
    cancelled_at: datetime


# ---------------------------------------------------------------------------
# Gate checklist (PRD A0 default gate rules)
# ---------------------------------------------------------------------------


class GateRule(str, Enum):
    """The four built-in A0 gate rules (PRD section 10, A0)."""

    SCHEMA_VALID = "schema_valid"
    QUARANTINE_RATIO = "quarantine_ratio"
    RECONCILIATION = "reconciliation"
    PENDING_PROPOSALS = "pending_proposals"


class GateCheckItem(ContractModel):
    """One gate rule with its deterministic evidence.

    ``evidence`` must be a number or structured fact, never prose from an LLM
    (PRD A0: gate decisions are Python rules over structured data).
    """

    rule: str = Field(
        description="schema_valid | quarantine_ratio | reconciliation | pending_proposals"
    )
    decision: GateDecision
    threshold: float | None = None
    observed: float | None = None
    evidence: str = Field(
        description="Short factual Indonesian sentence with the numbers."
    )


class GateChecklist(ContractModel):
    """All four A0 rules for one run; ``promotable`` only if every rule passes."""

    run_id: uuid.UUID
    items: list[GateCheckItem] = Field(min_length=1)
    promotable: bool
    evaluated_at: datetime

    @classmethod
    def build(
        cls,
        *,
        run_id: uuid.UUID,
        evaluated_at: datetime,
        schema_valid: bool,
        quarantine_ratio: float,
        max_quarantine_ratio: float,
        reconciliation_ok: bool | None,
        pending_required_proposals: int,
    ) -> "GateChecklist":
        """Pure constructor implementing A0 rules (a)-(d) with no I/O."""
        items = [
            GateCheckItem(
                rule=GateRule.SCHEMA_VALID,
                decision=GateDecision.PASS if schema_valid else GateDecision.FAIL,
                evidence=(
                    "Skema silver lolos validasi."
                    if schema_valid
                    else "Skema silver gagal validasi."
                ),
            ),
            GateCheckItem(
                rule=GateRule.QUARANTINE_RATIO,
                decision=(
                    GateDecision.PASS
                    if quarantine_ratio <= max_quarantine_ratio
                    else GateDecision.FAIL
                ),
                threshold=max_quarantine_ratio,
                observed=quarantine_ratio,
                evidence=(
                    f"Rasio quarantine {quarantine_ratio:.2%} "
                    f"(ambang {max_quarantine_ratio:.2%})."
                ),
            ),
            GateCheckItem(
                rule=GateRule.RECONCILIATION,
                decision=(
                    GateDecision.PASS
                    if reconciliation_ok is True
                    else GateDecision.FAIL
                ),
                evidence=(
                    "Rekonsiliasi baris silver dan gold cocok."
                    if reconciliation_ok is True
                    else "Rekonsiliasi silver dan gold belum cocok atau belum tersedia."
                ),
            ),
            GateCheckItem(
                rule=GateRule.PENDING_PROPOSALS,
                decision=(
                    GateDecision.PASS
                    if pending_required_proposals == 0
                    else GateDecision.FAIL
                ),
                observed=float(pending_required_proposals),
                evidence=(
                    "Tidak ada usulan agent yang wajib diputuskan."
                    if pending_required_proposals == 0
                    else f"{pending_required_proposals} usulan agent masih pending."
                ),
            ),
        ]
        return cls(
            run_id=run_id,
            items=items,
            promotable=all(i.decision is GateDecision.PASS for i in items),
            evaluated_at=evaluated_at,
        )


# ---------------------------------------------------------------------------
# SSE event union - switch on ``event``
# ---------------------------------------------------------------------------


class _RunEventBase(ContractModel):
    """Every SSE payload carries the run id and a monotonic sequence number."""

    run_id: uuid.UUID
    seq: int = Field(ge=0, description="Monotonic per-run sequence for ordering.")
    emitted_at: datetime


class NodeStatusEvent(_RunEventBase):
    """A node changed status. Drives the Run Monitor timeline."""

    event: Literal[RunEventType.NODE_STATUS] = RunEventType.NODE_STATUS
    node_id: str
    node_type: str
    status: NodeStatus
    retry_count: int = Field(default=0, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    rows_in: int | None = Field(default=None, ge=0)
    rows_out: int | None = Field(default=None, ge=0)
    rows_quarantined: int | None = Field(default=None, ge=0)
    error_code: str | None = None
    error_message: str | None = None


class NodeLogEvent(_RunEventBase):
    """A redacted log line from a node. Already scrubbed of secrets."""

    event: Literal[RunEventType.NODE_LOG] = RunEventType.NODE_LOG
    node_id: str
    level: Literal["debug", "info", "warning", "error"] = "info"
    message: str = Field(max_length=4000)


class GateEvent(_RunEventBase):
    """Gate checklist (re)computed for the run."""

    event: Literal[RunEventType.GATE] = RunEventType.GATE
    checklist: GateChecklist


class RunStatusEvent(_RunEventBase):
    """The run's own status changed (state machine transition)."""

    event: Literal[RunEventType.RUN_STATUS] = RunEventType.RUN_STATUS
    status: RunStatus
    previous_status: RunStatus | None = None
    rows_in: int | None = Field(default=None, ge=0)
    rows_out: int | None = Field(default=None, ge=0)
    rows_quarantined: int | None = Field(default=None, ge=0)
    error_code: str | None = None
    error_message: str | None = None


class HeartbeatEvent(_RunEventBase):
    """Keepalive so the UI can distinguish 'idle' from 'disconnected'."""

    event: Literal[RunEventType.HEARTBEAT] = RunEventType.HEARTBEAT


RunEvent = Annotated[
    NodeStatusEvent | NodeLogEvent | GateEvent | RunStatusEvent | HeartbeatEvent,
    Field(discriminator="event"),
]


class RunEventEnvelope(ContractModel):
    """Typed wrapper for a single parsed SSE message (tests + client use)."""

    event: RunEvent


class RunStatusTransitionRequest(ContractModel):
    """Internal endpoint used by the A0 supervisor's ``transition()`` function.

    Kept as a contract so the transition table has exactly one caller shape.
    """

    run_id: uuid.UUID
    event: str = Field(
        description="'start' | 'node_ok' | 'node_failed' | 'retry' | 'cancel' | "
        "'exhausted' | 'resume'"
    )
    reason: str | None = Field(default=None, max_length=500)


class RunStatusTransitionResponse(ReadModel):
    run_id: uuid.UUID
    previous_status: RunStatus
    status: RunStatus
    allowed: bool
    reason: str | None = None


__all__ = [
    "CancelRunResponse",
    "GateCheckItem",
    "GateChecklist",
    "GateEvent",
    "GateRule",
    "HeartbeatEvent",
    "NodeLogEvent",
    "NodeStatusEvent",
    "RunCreateRequest",
    "RunCreateResponse",
    "RunDetail",
    "RunEvent",
    "RunEventEnvelope",
    "RunNodeOut",
    "RunOut",
    "RunStatusEvent",
    "RunStatusTransitionRequest",
    "RunStatusTransitionResponse",
    "ValidationIssue",
]

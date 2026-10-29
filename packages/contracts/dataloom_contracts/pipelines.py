"""THE TYPED DAG CONTRACT (FR-020, FR-024, FR-032).

Every consumer codes against these types:
- Backend: validates ``PipelineDAG`` on create/update, stores it in
  ``pipeline_versions.dag_json`` and runs the SAME validator the UI runs.
- Worker: walks the validated DAG to execute nodes.
- AI Engineer (Pipeline Builder): the LLM drafts a ``PipelineDAG``; the
  deterministic validator below is the gate before anything is shown.
- UI/UX: node/edge shapes map 1:1 to React Flow nodes/edges.

Node params are typed per node type (``SourceParams``, ``CleanParams``, ...)
so a malformed param fails at the contract boundary, not mid-run.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from ._base import (
    CleaningOp,
    ContractModel,
    JsonDict,
    ReadModel,
    RiskLevel,
    ValidationStatus,
)
from .common import ValidationIssue


class NodeType(str, Enum):
    """The six node types from PRD section 4.1 point 4."""

    SOURCE = "source"
    VALIDATE = "validate"
    CLEAN = "clean"
    TRANSFORM = "transform"
    AGGREGATE = "aggregate"
    SINK = "sink"


#: Node types that may be the single entry point of a DAG.
ENTRY_NODE_TYPES: frozenset[NodeType] = frozenset({NodeType.SOURCE})


# ---------------------------------------------------------------------------
# Column references and expressions (shared by transform/aggregate/clean)
# ---------------------------------------------------------------------------


class ColumnRef(ContractModel):
    """A column reference. ``table`` is optional and only used in joins."""

    column: str = Field(min_length=1, max_length=128)
    table: str | None = Field(default=None, max_length=128)


class AggFunction(str, Enum):
    """Aggregations the worker knows how to translate to DuckDB."""

    SUM = "sum"
    COUNT = "count"
    COUNT_DISTINCT = "count_distinct"
    AVG = "avg"
    MIN = "min"
    MAX = "max"
    MEDIAN = "median"


class AggregateSpec(ContractModel):
    """One output metric of an aggregate node."""

    alias: str = Field(min_length=1, max_length=128)
    function: AggFunction
    column: ColumnRef | None = Field(
        default=None, description="null is allowed only for count(*)."
    )

    @model_validator(mode="after")
    def _column_required(self) -> "AggregateSpec":
        if self.column is None and self.function is not AggFunction.COUNT:
            raise ValueError(f"Fungsi '{self.function.value}' membutuhkan kolom.")
        return self


class FilterOp(str, Enum):
    """Comparison operators available to ``drop_if`` / generic filters."""

    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IS_NULL = "is_null"
    NOT_NULL = "not_null"
    IN = "in"
    NOT_IN = "not_in"


class Condition(ContractModel):
    """A single deterministic predicate. Values are literals only - no code."""

    column: str = Field(min_length=1, max_length=128)
    op: FilterOp
    value: str | int | float | bool | None = None
    values: list[str | int | float | bool] | None = Field(
        default=None, description="Required for in/not_in."
    )

    @model_validator(mode="after")
    def _value_for_op(self) -> "Condition":
        if self.op in (FilterOp.IN, FilterOp.NOT_IN):
            if not self.values:
                raise ValueError(f"Operator '{self.op.value}' membutuhkan 'values'.")
        elif self.op in (FilterOp.IS_NULL, FilterOp.NOT_NULL):
            if self.value is not None or self.values is not None:
                raise ValueError(f"Operator '{self.op.value}' tidak memakai nilai.")
        elif self.value is None:
            raise ValueError(f"Operator '{self.op.value}' membutuhkan 'value'.")
        return self


class JoinType(str, Enum):
    INNER = "inner"
    LEFT = "left"
    RIGHT = "right"
    OUTER = "outer"


# ---------------------------------------------------------------------------
# Per-type params
# ---------------------------------------------------------------------------


class SourceParams(ContractModel):
    kind: Literal["source"] = "source"
    source_id: uuid.UUID
    dataset_id: uuid.UUID | None = Field(
        default=None,
        description="Read an existing dataset instead of a live source (replays).",
    )
    batch_size_rows: int = Field(default=5000, ge=1, le=50000)


class ValidationRuleKind(str, Enum):
    """Deterministic validation rules (FR-022) enforced by A3 code, not LLM."""

    NOT_NULL = "not_null"
    UNIQUE = "unique"
    TYPE = "type"
    RANGE = "range"
    REGEX = "regex"
    ENUM = "enum"
    REFERENTIAL = "referential"
    FRESHNESS = "freshness"
    VOLUME_ZSCORE = "volume_zscore"


class ValidationRule(ContractModel):
    """One quality rule. Matching rows go to quarantine, never silently dropped."""

    rule_id: str = Field(min_length=1, max_length=64)
    kind: ValidationRuleKind
    column: str = Field(min_length=1, max_length=128)
    params: JsonDict = Field(default_factory=dict)
    severity: Literal["error", "warn"] = "error"


class ValidateParams(ContractModel):
    kind: Literal["validate"] = "validate"
    rules: list[ValidationRule] = Field(default_factory=list)


class CleanOpSpec(ContractModel):
    """One cleaning operation, mirroring ``CleaningProposal`` but executable.

    ``op`` is constrained by the CLOSED ``CleaningOp`` catalog (PRD A1).
    """

    op: CleaningOp
    column: str = Field(min_length=1, max_length=128)
    params: JsonDict = Field(default_factory=dict)
    risk: RiskLevel = RiskLevel.LOW

    @model_validator(mode="after")
    def _required_params(self) -> "CleanOpSpec":
        required: dict[CleaningOp, tuple[str, ...]] = {
            CleaningOp.CAST: ("to_type",),
            CleaningOp.PARSE_DATE: ("format",),
            CleaningOp.REGEX_REPLACE: ("pattern", "replacement"),
            CleaningOp.DROP_IF: ("condition",),
            CleaningOp.FILL_DEFAULT: ("value",),
        }
        for key in required.get(self.op, ()):
            if key not in self.params:
                raise ValueError(
                    f"Operasi '{self.op.value}' membutuhkan parameter '{key}'."
                )
        return self


class CleanParams(ContractModel):
    kind: Literal["clean"] = "clean"
    operations: list[CleanOpSpec] = Field(min_length=1)


class ColumnMapping(ContractModel):
    """Rename / type-coerce a column."""

    source_column: str = Field(min_length=1, max_length=128)
    target_column: str = Field(min_length=1, max_length=128)
    cast_to: str | None = Field(
        default=None, description="Target type name, e.g. 'int64' or 'datetime'."
    )


class DerivedColumn(ContractModel):
    """A new column computed by a restricted SQL expression (sqlglot-checked)."""

    alias: str = Field(min_length=1, max_length=128)
    expression: str = Field(
        max_length=1000,
        description="Restricted SQL expression. Validated with sqlglot, no subqueries.",
    )


class TransformParams(ContractModel):
    kind: Literal["transform"] = "transform"
    select_columns: list[str] = Field(default_factory=list)
    mappings: list[ColumnMapping] = Field(default_factory=list)
    derived: list[DerivedColumn] = Field(default_factory=list)
    filter: Condition | None = None
    join_type: JoinType | None = None
    join_key_left: str | None = Field(default=None, max_length=128)
    join_key_right: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _join_needs_keys(self) -> "TransformParams":
        if self.join_type is not None and not (
            self.join_key_left and self.join_key_right
        ):
            raise ValueError("Join membutuhkan join_key_left dan join_key_right.")
        return self


class AggregateParams(ContractModel):
    kind: Literal["aggregate"] = "aggregate"
    group_by: list[str] = Field(default_factory=list)
    metrics: list[AggregateSpec] = Field(min_length=1)


class SinkParams(ContractModel):
    kind: Literal["sink"] = "sink"
    layer: Literal["gold"] = "gold"
    dataset_name: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9_]+$")
    write_mode: Literal["append", "overwrite"] = "overwrite"


#: Discriminated union of every param shape, keyed by ``kind``.
NodeParams = Annotated[
    SourceParams | ValidateParams | CleanParams | TransformParams | AggregateParams | SinkParams,
    Field(discriminator="kind"),
]

#: ``NodeType`` -> ``kind`` literal expected in params. Used by the validator.
NODE_TYPE_TO_KIND: dict[NodeType, str] = {
    NodeType.SOURCE: "source",
    NodeType.VALIDATE: "validate",
    NodeType.CLEAN: "clean",
    NodeType.TRANSFORM: "transform",
    NodeType.AGGREGATE: "aggregate",
    NodeType.SINK: "sink",
}

#: ``kind`` literal -> ``NodeType`` (reverse map, single source of truth).
KIND_TO_NODE_TYPE: dict[str, NodeType] = {v: k for k, v in NODE_TYPE_TO_KIND.items()}


# ---------------------------------------------------------------------------
# Graph elements
# ---------------------------------------------------------------------------


class Position(ContractModel):
    """Canvas coordinates; ignored by the worker, kept for round-tripping."""

    x: float = 0.0
    y: float = 0.0


class PipelineNode(ContractModel):
    """One DAG node.

    ``id`` is a short stable string (``n_source``, ``n_clean_1``) because it is
    also the React Flow node id and the key of ``run_nodes.node_id``.
    """

    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    type: NodeType
    label: str | None = Field(default=None, max_length=120)
    params: NodeParams
    position: Position | None = None

    @model_validator(mode="after")
    def _params_match_type(self) -> "PipelineNode":
        expected = NODE_TYPE_TO_KIND[self.type]
        actual = getattr(self.params, "kind", None)
        if actual != expected:
            raise ValueError(
                f"Node '{self.id}' bertipe '{self.type.value}' tetapi params "
                f"bertipe '{actual}'. Gunakan params '{expected}'."
            )
        return self


class PipelineEdge(ContractModel):
    """Directed edge ``from -> to``. Field names stay Python-safe."""

    id: str | None = Field(default=None, max_length=128)
    from_node: str = Field(alias="from", min_length=1, max_length=64)
    to_node: str = Field(alias="to", min_length=1, max_length=64)

    model_config = ContractModel.model_config | {"populate_by_name": True}


# ---------------------------------------------------------------------------
# The DAG + its validator
# ---------------------------------------------------------------------------

MAX_NODES = 100
MAX_EDGES = 200


class PipelineDAG(ContractModel):
    """A validated, acyclic pipeline definition.

    Validation order (each step stops at the first fatal problem but returns
    ALL issues it can find in that step, so the UI can highlight many nodes):

    1. ``nodes`` non-empty and within size bounds.
    2. Node ids unique.
    3. Edges reference existing nodes.
    4. At least one SOURCE node; exactly one entry node that has no incoming
       edge, and that entry must be a SOURCE.
    5. Acyclic (Kahn topological sort).
    6. No orphan nodes (every node reachable from the entry).
    7. At least one SINK node receives the terminal output.
    """

    schema_version: Literal[1] = 1
    nodes: list[PipelineNode] = Field(min_length=1, max_length=MAX_NODES)
    edges: list[PipelineEdge] = Field(default_factory=list, max_length=MAX_EDGES)
    metadata: JsonDict = Field(default_factory=dict)

    # ---- helpers ---------------------------------------------------------
    def node_map(self) -> dict[str, PipelineNode]:
        return {n.id: n for n in self.nodes}

    def adjacency(self) -> dict[str, list[str]]:
        adj: dict[str, list[str]] = {n.id: [] for n in self.nodes}
        for e in self.edges:
            adj[e.from_node].append(e.to_node)
        return adj

    def entry_node(self) -> PipelineNode:
        """The single node with no incoming edge (guaranteed by validation)."""
        incoming = {e.to_node for e in self.edges}
        entries = [n for n in self.nodes if n.id not in incoming]
        return entries[0]

    def topological_order(self) -> list[str]:
        """Kahn sort. Assumes validation already passed."""
        adj = self.adjacency()
        indegree = {n.id: 0 for n in self.nodes}
        for e in self.edges:
            indegree[e.to_node] += 1
        ready = [nid for nid, deg in indegree.items() if deg == 0]
        order: list[str] = []
        while ready:
            # Keep declaration order stable for deterministic runs.
            ready.sort(key=lambda nid: [n.id for n in self.nodes].index(nid))
            current = ready.pop(0)
            order.append(current)
            for nxt in adj[current]:
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    ready.append(nxt)
        return order

    # ---- the validator ---------------------------------------------------
    @model_validator(mode="after")
    def _validate_dag(self) -> "PipelineDAG":
        issues: list[ValidationIssue] = []

        # (2) unique ids
        seen: set[str] = set()
        for idx, node in enumerate(self.nodes):
            if node.id in seen:
                issues.append(
                    ValidationIssue(
                        field=f"nodes[{idx}].id",
                        message=f"Id node '{node.id}' duplikat. Setiap node harus unik.",
                    )
                )
            seen.add(node.id)

        if issues:
            # Downstream checks need unique ids to be meaningful.
            raise ValueError(_format_issues(issues))

        # (3) edges point at real nodes
        for idx, edge in enumerate(self.edges):
            if edge.from_node not in seen:
                issues.append(
                    ValidationIssue(
                        field=f"edges[{idx}].from",
                        message=(
                            f"Edge menunjuk node '{edge.from_node}' yang tidak ada "
                            "di daftar node."
                        ),
                    )
                )
            if edge.to_node not in seen:
                issues.append(
                    ValidationIssue(
                        field=f"edges[{idx}].to",
                        message=(
                            f"Edge menunjuk node '{edge.to_node}' yang tidak ada "
                            "di daftar node."
                        ),
                    )
                )
            if edge.from_node == edge.to_node:
                issues.append(
                    ValidationIssue(
                        field=f"edges[{idx}]",
                        message="Edge tidak boleh menghubungkan node ke dirinya sendiri.",
                    )
                )

        # A dangling edge makes every later graph walk unsafe. Stop here with the
        # concrete missing-node message instead of failing on a KeyError in Kahn.
        if issues:
            raise ValueError(_format_issues(issues))

        # (4) exactly one entry, and it must be a SOURCE
        incoming = {e.to_node for e in self.edges}
        entries = [n for n in self.nodes if n.id not in incoming]
        source_nodes = [n for n in self.nodes if n.type is NodeType.SOURCE]

        if not source_nodes:
            issues.append(
                ValidationIssue(
                    field="nodes",
                    message=(
                        "Pipeline harus punya minimal satu node bertipe 'source' "
                        "sebagai titik masuk."
                    ),
                )
            )
        if len(entries) == 0:
            issues.append(
                ValidationIssue(
                    field="edges",
                    message=(
                        "Semua node punya edge masuk sehingga tidak ada titik awal. "
                        "Kemungkinan ada siklus."
                    ),
                )
            )
        elif len(entries) > 1:
            names = ", ".join(sorted(n.id for n in entries))
            non_sources = [n.id for n in entries if n.type is not NodeType.SOURCE]
            orphan_note = (
                f" Node non-source tanpa edge masuk tidak akan pernah dijalankan: "
                f"{', '.join(sorted(non_sources))}."
                if non_sources
                else ""
            )
            issues.append(
                ValidationIssue(
                    field="edges",
                    message=(
                        "Hanya boleh ada satu titik masuk, tetapi ditemukan "
                        f"{len(entries)} node tanpa edge masuk: {names}."
                        f"{orphan_note}"
                    ),
                )
            )
        elif entries[0].type is not NodeType.SOURCE:
            issues.append(
                ValidationIssue(
                    field=f"nodes.{entries[0].id}",
                    message=(
                        f"Titik masuk harus node 'source', bukan "
                        f"'{entries[0].type.value}'."
                    ),
                )
            )

        # (5) acyclic (Kahn)
        adj = self.adjacency()
        indegree = {n.id: 0 for n in self.nodes}
        for e in self.edges:
            indegree[e.to_node] += 1
        ready = [nid for nid, deg in indegree.items() if deg == 0]
        visited = 0
        while ready:
            current = ready.pop()
            visited += 1
            for nxt in adj[current]:
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    ready.append(nxt)
        if visited != len(self.nodes):
            cyclic = sorted(nid for nid, deg in indegree.items() if deg > 0)
            issues.append(
                ValidationIssue(
                    field="edges",
                    message=(
                        "DAG mengandung siklus (edge membentuk putaran). "
                        f"Node yang terlibat: {', '.join(cyclic)}. "
                        "Hapus minimal satu edge agar graf menjadi asiklik."
                    ),
                )
            )

        # (6) orphans - only meaningful when there is a single entry
        if len(entries) == 1:
            reachable = set()
            stack = [entries[0].id]
            while stack:
                cur = stack.pop()
                if cur in reachable:
                    continue
                reachable.add(cur)
                stack.extend(adj[cur])
            for node in self.nodes:
                if node.id not in reachable:
                    issues.append(
                        ValidationIssue(
                            field=f"nodes.{node.id}",
                            message=(
                                f"Node '{node.id}' tidak terhubung ke titik masuk "
                                "dan tidak akan pernah dijalankan."
                            ),
                        )
                    )

        # (7) needs a terminal sink
        sinks = [n for n in self.nodes if n.type is NodeType.SINK]
        if not sinks:
            issues.append(
                ValidationIssue(
                    field="nodes",
                    message="Pipeline harus punya minimal satu node 'sink' (output).",
                )
            )
        else:
            for sink in sinks:
                # A sink must be REACHED BY an edge, i.e. appear as an edge
                # target (`incoming`). The previous check tested `outgoing`
                # (edge sources), which a terminal sink never is -- so every
                # valid pipeline was rejected with "tidak punya input".
                if sink.id not in incoming and len(self.nodes) > 1:
                    issues.append(
                        ValidationIssue(
                            field=f"nodes.{sink.id}",
                            message=(
                                f"Node sink '{sink.id}' tidak punya input. "
                                "Hubungkan dari node sebelumnya."
                            ),
                        )
                    )

        if issues:
            raise ValueError(_format_issues(issues))
        return self


def _format_issues(issues: list[ValidationIssue]) -> str:
    """Join issues into one human-readable Indonesian message.

    Pydantic surfaces this string as the validation error message, so the API
    can forward it unchanged and the UI can also read the structured copy from
    ``validate_dag()`` below.
    """
    lines = [f"- {i.field}: {i.message}" for i in issues]
    return "DAG tidak valid:\n" + "\n".join(lines)


def validate_dag(dag: PipelineDAG) -> list[ValidationIssue]:
    """Deterministic validator returning issues instead of raising.

    The AI Pipeline Builder calls this BEFORE showing a draft (FR-032); the
    Backend calls it on create/update (FR-020). Re-validating an already-built
    object re-runs the model validator, so this is a fresh check.
    """
    try:
        PipelineDAG.model_validate(dag.model_dump(by_alias=True))
        return []
    except Exception as exc:  # pydantic.ValidationError
        errors = getattr(exc, "errors", lambda: [])()
        issues: list[ValidationIssue] = []
        for err in errors:
            loc = ".".join(str(p) for p in err.get("loc", ()))
            issues.append(ValidationIssue(field=loc or "dag", message=str(err.get("msg"))))
        if not issues:
            issues.append(ValidationIssue(field="dag", message=str(exc)))
        return issues


# ---------------------------------------------------------------------------
# CRUD / versioning contracts
# ---------------------------------------------------------------------------


class PipelineCreate(ContractModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    dag: PipelineDAG


class PipelineUpdate(ContractModel):
    """Edits always create a NEW version (FR-024); nothing is mutated in place."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    dag: PipelineDAG | None = Field(
        default=None, description="If present, a new pipeline version is created."
    )
    change_note: str | None = Field(default=None, max_length=500)


class PipelineOut(ReadModel):
    id: uuid.UUID
    org_id: uuid.UUID
    project_id: uuid.UUID
    name: str
    description: str | None = None
    current_version_id: uuid.UUID | None = None
    current_version: int | None = None
    schedule_cron: str | None = None
    created_by: str = Field(default="user", description="'user' | 'agent'")
    created_at: datetime
    updated_at: datetime


class PipelineVersionOut(ReadModel):
    """Immutable snapshot. Runs always reference one of these."""

    id: uuid.UUID
    org_id: uuid.UUID
    pipeline_id: uuid.UUID
    version: int = Field(ge=1)
    dag: PipelineDAG
    validation_status: ValidationStatus
    validation_errors: list[ValidationIssue] = Field(default_factory=list)
    created_by_kind: str = Field(description="'user' | 'agent'")
    created_by_user_id: uuid.UUID | None = None
    created_by_agent_run_id: uuid.UUID | None = None
    parent_version_id: uuid.UUID | None = None
    change_note: str | None = None
    created_at: datetime


class PipelineValidateRequest(ContractModel):
    """Dry-run validation for the canvas 'Validate' button."""

    dag: PipelineDAG


class PipelineValidateResponse(ReadModel):
    valid: bool
    issues: list[ValidationIssue] = Field(default_factory=list)
    topological_order: list[str] = Field(default_factory=list)


__all__ = [
    "AggFunction",
    "AggregateParams",
    "AggregateSpec",
    "CleanOpSpec",
    "CleanParams",
    "ColumnMapping",
    "ColumnRef",
    "Condition",
    "DerivedColumn",
    "ENTRY_NODE_TYPES",
    "FilterOp",
    "JoinType",
    "KIND_TO_NODE_TYPE",
    "NODE_TYPE_TO_KIND",
    "NodeParams",
    "NodeType",
    "PipelineCreate",
    "PipelineDAG",
    "PipelineEdge",
    "PipelineNode",
    "PipelineOut",
    "PipelineUpdate",
    "PipelineValidateRequest",
    "PipelineValidateResponse",
    "PipelineVersionOut",
    "Position",
    "SinkParams",
    "SourceParams",
    "TransformParams",
    "ValidationRule",
    "ValidationRuleKind",
    "ValidateParams",
    "validate_dag",
]

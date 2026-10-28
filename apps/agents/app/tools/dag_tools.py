"""Deterministic DAG validation tool (PRD A2 tool ``validate_dag``).

Reuses the ONE validator the API and the UI use:
``dataloom_contracts.pipelines.PipelineDAG`` (its model validator) plus
``validate_dag()`` from the same module. Nothing here re-implements DAG rules:
two validators would eventually disagree, and the disagreement would be silent.

On top of the contract validator this tool adds project-scope checks that the
contract cannot know about:

- every ``source`` node must reference a ``source_id`` / ``dataset_id`` that
  exists in THIS project (org + project scoped lookup);
- a ``sink`` node's ``dataset_name`` must satisfy the same naming rule the
  contract enforces (it does) - no extra rule needed.

Returns ``(ok, issues)`` where ``issues`` are plain dicts
(``{"field", "message"}``) so the graph state stays JSON-serialisable for the
LangGraph checkpointer.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Dataset, Project
from dataloom_contracts.pipelines import NodeType, PipelineDAG, validate_dag


def _issue(field: str, message: str) -> dict[str, str]:
    return {"field": field, "message": message}


def validate_dag_syntax(dag_dict: dict[str, Any] | None) -> tuple[PipelineDAG | None, list[dict[str, str]]]:
    """Pure contract validation. No DB, no LLM. Never raises.

    This is the function the repair loop calls after every attempt: the issue
    list is appended to the next prompt so the model can fix a specific field.
    """
    if not dag_dict:
        return None, [_issue("dag", "DAG kosong: tidak ada node yang dikirim.")]
    try:
        dag = PipelineDAG.model_validate(dag_dict)
    except ValidationError as exc:
        issues: list[dict[str, str]] = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error.get("loc", ())) or "dag"
            issues.append(_issue(location, str(error.get("msg", "tidak valid"))))
        # A raise inside the model validator surfaces as one message; split the
        # "DAG tidak valid:" bullet list back into per-field issues so the UI
        # can highlight individual nodes.
        if not issues:
            issues = _split_bulk_message(str(exc))
        return None, issues

    # Re-run the standalone validator as a second, independent check. It
    # re-validates the dumped payload, so a bug in one path does not hide a
    # broken DAG in the other.
    remaining = validate_dag(dag)
    if remaining:
        return dag, [_issue(item.field or "dag", item.message) for item in remaining]
    return dag, []


def _split_bulk_message(message: str) -> list[dict[str, str]]:
    lines = [ln.strip().lstrip("-").strip() for ln in message.splitlines() if ln.strip()]
    issues = []
    for line in lines:
        if line.lower().startswith("dag tidak valid"):
            continue
        field, _, text = line.partition(":")
        issues.append(_issue(field.strip() or "dag", text.strip() or line))
    return issues or [_issue("dag", message[:400])]


async def _project_scope(
    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> tuple[set[uuid.UUID], set[uuid.UUID]]:
    """``(source_ids, dataset_ids)`` that belong to this project."""
    project = (
        await session.execute(
            select(Project).where(Project.id == project_id, Project.org_id == org_id)
        )
    ).scalar_one_or_none()
    if project is None:
        return set(), set()

    from app.db.models import Source  # local import: keeps module import cheap

    sources = (
        await session.execute(
            select(Source.id).where(
                Source.org_id == org_id, Source.project_id == project_id
            )
        )
    ).scalars().all()
    datasets = (
        await session.execute(
            select(Dataset.id).where(
                Dataset.org_id == org_id, Dataset.project_id == project_id
            )
        )
    ).scalars().all()
    return {row for row in sources}, {row for row in datasets}


async def validate_dag_tool(
    dag_dict: dict[str, Any] | None,
    *,
    session: AsyncSession | None = None,
    org_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
) -> tuple[bool, list[dict[str, str]]]:
    """Full deterministic validation: contract rules + project scope.

    ``ok`` is True only when the contract accepts the DAG AND every referenced
    source/dataset belongs to this org+project. Identity checks are skipped
    only when ``session`` is ``None`` (pure syntax mode, used by unit tests);
    the graph always passes a session.
    """
    dag, issues = validate_dag_syntax(dag_dict)
    if dag is None:
        return False, issues

    if session is not None and org_id is not None and project_id is not None:
        source_ids, dataset_ids = await _project_scope(
            session, org_id=org_id, project_id=project_id
        )
        for node in dag.nodes:
            if node.type is not NodeType.SOURCE:
                continue
            params = node.params
            dataset_id = getattr(params, "dataset_id", None)
            source_id = getattr(params, "source_id", None)
            if dataset_id is not None and dataset_id not in dataset_ids:
                issues.append(
                    _issue(
                        f"nodes.{node.id}.params.dataset_id",
                        "Dataset tidak ada di proyek ini.",
                    )
                )
            elif dataset_id is None and source_id not in source_ids:
                issues.append(
                    _issue(
                        f"nodes.{node.id}.params.source_id",
                        "Sumber tidak ada di proyek ini.",
                    )
                )
        if issues:
            return False, issues

    return True, []


__all__ = ["validate_dag_syntax", "validate_dag_tool"]

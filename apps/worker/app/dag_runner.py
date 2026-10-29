"""DAG executor: runs one pipeline version end to end (FR-020..026).

Execution model:
- Nodes are visited in topological order (``PipelineDAG.topological_order``).
- Each node produces an in-memory Polars frame; sinks drain it.
- Per node: timeout, bounded retry (``NODE_MAX_RETRIES``), status persisted to
  ``run_nodes`` before and after execution so the UI sees progress live.
- Nothing here calls an LLM. Branching decisions are data-dependent, not agent
  decisions.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import polars as pl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import bronze, gold, metrics, quarantine, silver
from app.config import get_settings
from app.datasets import register_dataset, safe_dataset_name
from app.db import Dataset, Pipeline, PipelineVersion, Run, RunNode, Source
from app.gate import evaluate_gate

logger = logging.getLogger("dataloom.worker.runner")


@dataclass(slots=True)
class NodeOutcome:
    node_id: str
    status: str
    rows_in: int
    rows_out: int
    rows_quarantined: int
    duration_ms: int
    error_code: str | None = None
    error_message: str | None = None
    retries: int = 0


class NodeTimeout(Exception):
    """Raised when a node exceeds its timeout budget."""


async def _with_timeout(coro, seconds: int):  # noqa: ANN001, ANN202
    try:
        return await asyncio.wait_for(coro, timeout=seconds)
    except asyncio.TimeoutError as exc:
        raise NodeTimeout(f"Node melebihi batas {seconds} detik.") from exc


# ---------------------------------------------------------------------------
# Node implementations (all deterministic)
# ---------------------------------------------------------------------------
async def _run_source(
    session: AsyncSession, params: dict[str, Any], org_id: uuid.UUID
) -> pl.DataFrame:
    """Load the newest Bronze object for the configured source/dataset."""
    dataset_id = params.get("dataset_id")
    if dataset_id:
        dataset = (
            await session.execute(
                select(Dataset).where(
                    Dataset.id == uuid.UUID(str(dataset_id)), Dataset.org_id == org_id
                )
            )
        ).scalar_one_or_none()
    else:
        dataset = (
            await session.execute(
                select(Dataset)
                .where(
                    Dataset.org_id == org_id,
                    Dataset.source_id == uuid.UUID(str(params["source_id"])),
                    Dataset.layer == "bronze",
                )
                .order_by(Dataset.updated_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    if dataset is None or not dataset.storage_uri:
        raise ValueError("Tidak ada data bronze untuk sumber ini.")
    if dataset.org_id != org_id:
        # RLS is not forced for the owner role the worker logs in with, so the
        # org predicate above is the ONLY guard. Fail loudly instead of reading
        # another tenant's object if that ever regresses.
        raise ValueError("Dataset tidak ditemukan untuk organisasi ini.")

    return await asyncio.to_thread(bronze.read_bronze, dataset.storage_uri)


async def _run_validate(params: dict[str, Any], frame: pl.DataFrame):
    rules = params.get("rules") or []
    passing, rejected, counts = silver.apply_rules(frame, rules)
    return passing, rejected, counts


async def _run_clean(params: dict[str, Any], frame: pl.DataFrame) -> pl.DataFrame:
    operations = params.get("operations") or []
    casts: dict[str, str] = {}
    for op in operations:
        if op["op"] == "cast":
            casts[op["column"]] = op["params"]["to_type"]
    if casts:
        frame, missing = silver.cast_columns(frame, casts)
        if missing:
            raise ValueError(f"Kolom cast tidak ada: {missing}")
    return frame


async def _run_transform(params: dict[str, Any], frame: pl.DataFrame) -> pl.DataFrame:
    selected = params.get("select_columns") or []
    if selected:
        present = [c for c in selected if c in frame.columns]
        missing = [c for c in selected if c not in frame.columns]
        if missing:
            raise ValueError(f"Kolom tidak ditemukan: {missing}")
        frame = frame.select(present)

    for mapping in params.get("mappings") or []:
        source_col = mapping["source_column"]
        if source_col not in frame.columns:
            continue
        frame = frame.rename({source_col: mapping["target_column"]})

    condition = params.get("filter")
    if condition:
        frame = _apply_condition(frame, condition)
    return frame


def _apply_condition(frame: pl.DataFrame, condition: dict[str, Any]) -> pl.DataFrame:
    column = condition["column"]
    op = condition["op"]
    if column not in frame.columns:
        raise ValueError(f"Kolom filter tidak ada: {column}")
    col = pl.col(column)
    if op == "eq":
        expression = col == condition.get("value")
    elif op == "ne":
        expression = col != condition.get("value")
    elif op == "gt":
        expression = col > condition.get("value")
    elif op == "gte":
        expression = col >= condition.get("value")
    elif op == "lt":
        expression = col < condition.get("value")
    elif op == "lte":
        expression = col <= condition.get("value")
    elif op == "is_null":
        expression = col.is_null()
    elif op == "not_null":
        expression = col.is_not_null()
    elif op == "in":
        expression = col.is_in(condition.get("values") or [])
    elif op == "not_in":
        expression = col.is_in(condition.get("values") or []).not_()
    else:
        raise ValueError(f"Operator filter tidak dikenal: {op}")
    return frame.filter(expression.fill_null(False))


async def _run_aggregate(params: dict[str, Any], frame: pl.DataFrame) -> pl.DataFrame:
    group_by = params.get("group_by") or []
    metric_defs = params.get("metrics") or []
    metrics_sql = []
    for metric in metric_defs:
        column = metric.get("column")
        metrics_sql.append(
            {
                "alias": metric["alias"],
                "function": metric["function"],
                "column": (column or {}).get("column") if isinstance(column, dict) else column,
            }
        )
    sql = gold.build_aggregate_sql(table="_t", group_by=group_by, metrics=metrics_sql)
    # Execute the aggregate with Polars (same semantics as DuckDB SUM/COUNT)
    # so the hot path does not open a warehouse connection for every batch.
    try:
        result = frame.group_by(group_by).agg(
            [
                _polars_metric(m)
                for m in metrics_sql
            ]
        )
    except Exception as exc:  # noqa: BLE001 - surface the SQL for debugging
        raise ValueError(f"Agregasi gagal untuk SQL: {sql}") from exc
    return result


def _polars_metric(metric: dict[str, Any]) -> pl.Expr:
    function = metric["function"]
    alias = metric["alias"]
    column = metric["column"]
    col = pl.col(column) if column else pl.len()
    if function == "sum":
        expression = col.sum()
    elif function == "count":
        expression = col.count() if column else pl.len()
    elif function == "count_distinct":
        expression = col.n_unique()
    elif function == "avg":
        expression = col.mean()
    elif function == "min":
        expression = col.min()
    elif function == "max":
        expression = col.max()
    elif function == "median":
        expression = col.median()
    else:
        raise ValueError(f"Fungsi agregasi tidak dikenal: {function}")
    return expression.alias(alias)


# ---------------------------------------------------------------------------
# Run-level orchestration
# ---------------------------------------------------------------------------
async def execute_run(session: AsyncSession, run_id: uuid.UUID) -> str:
    """Execute a queued run. Returns the final run status string."""
    from app.transition import TransitionError, transition
    from dataloom_contracts._base import RunStatus
    from dataloom_contracts.pipelines import PipelineDAG

    settings = get_settings()

    run = (
        await session.execute(select(Run).where(Run.id == run_id).with_for_update())
    ).scalar_one_or_none()
    if run is None:
        raise ValueError(f"Run {run_id} tidak ditemukan.")
    if run.status == "cancelled":
        return "cancelled"

    version = (
        await session.execute(
            select(PipelineVersion).where(PipelineVersion.id == run.pipeline_version_id)
        )
    ).scalar_one()
    pipeline = (
        await session.execute(select(Pipeline).where(Pipeline.id == run.pipeline_id))
    ).scalar_one()

    dag = PipelineDAG.model_validate(version.dag_json)
    order = dag.topological_order()
    nodes = dag.node_map()

    try:
        await transition(session, run_id, RunStatus.RUNNING)
    except TransitionError:
        return run.status
    await session.commit()

    outputs: dict[str, pl.DataFrame] = {}
    total_in = 0
    total_out = 0
    total_quarantined = 0
    quarantine_reasons: dict[str, int] = {}
    reconciliation: dict[str, Any] | None = None
    schema_valid = True
    schema_errors: list[str] = []

    for node_id in order:
        node = nodes[node_id]
        started = time.monotonic()
        node_row = RunNode(
            org_id=run.org_id,
            run_id=run.id,
            node_id=node.id,
            node_type=node.type.value,
            status="running",
            started_at=datetime.now(tz=timezone.utc),
        )
        session.add(node_row)
        await session.commit()

        # Gather upstream inputs (all predecessors must have produced output).
        predecessor_ids = [e.from_node for e in dag.edges if e.to_node == node_id]
        inputs = [outputs[p] for p in predecessor_ids if p in outputs]
        frame_in = inputs[0] if inputs else pl.DataFrame()

        attempt = 0
        outcome: NodeOutcome | None = None
        while attempt <= settings.NODE_MAX_RETRIES:
            try:
                frame_out, rejected, reasons, extra = await _with_timeout(
                    _dispatch(
                        session,
                        node.type.value,
                        node.params.model_dump(mode="json"),
                        frame_in,
                        run,
                        pipeline.name,
                        node.id,
                    ),
                    settings.NODE_TIMEOUT_SECONDS,
                )
                if reasons:
                    for code, count in reasons.items():
                        quarantine_reasons[code] = quarantine_reasons.get(code, 0) + count
                if extra.get("reconciliation"):
                    reconciliation = extra["reconciliation"]
                if extra.get("dataset_name"):
                    outputs[f"{node_id}::gold"] = pl.DataFrame({"_gold": [extra["dataset_name"]]})

                duration = int((time.monotonic() - started) * 1000)
                outcome = NodeOutcome(
                    node_id=node.id,
                    status="succeeded",
                    rows_in=frame_in.height,
                    rows_out=frame_out.height,
                    rows_quarantined=len(rejected),
                    duration_ms=duration,
                    retries=attempt,
                )
                if not frame_out.is_empty() or node.type.value in ("sink", "aggregate"):
                    outputs[node_id] = frame_out
                break
            except (NodeTimeout, ValueError, RuntimeError) as exc:
                attempt += 1
                metrics.batches_total.labels(result="node_retry").inc()
                if attempt > settings.NODE_MAX_RETRIES:
                    outcome = NodeOutcome(
                        node_id=node.id,
                        status="failed",
                        rows_in=frame_in.height,
                        rows_out=0,
                        rows_quarantined=0,
                        duration_ms=int((time.monotonic() - started) * 1000),
                        error_code="node_failed",
                        # Never persist raw exception text: a driver/HTTP error
                        # can embed a DSN with a password (FR-015). The code is
                        # stable, the class name is enough to triage.
                        error_message=f"{type(exc).__name__}: node gagal (detail di log server)"[:1000],
                        retries=attempt - 1,
                    )
                    log_id = node_row.id
                    logger.error(
                        "Node %s (%s) gagal setelah %d percobaan (run_node=%s)",
                        node.id,
                        node.type.value,
                        attempt,
                        log_id,
                        exc_info=exc,
                    )
                    break
                await asyncio.sleep(min(2**attempt, 8))

        assert outcome is not None
        node_row.status = outcome.status
        node_row.finished_at = datetime.now(tz=timezone.utc)
        node_row.duration_ms = outcome.duration_ms
        node_row.rows_in = outcome.rows_in
        node_row.rows_out = outcome.rows_out
        node_row.rows_quarantined = outcome.rows_quarantined
        node_row.retry_count = outcome.retries
        node_row.attempt = outcome.retries + 1
        node_row.error_code = outcome.error_code
        node_row.error_message = outcome.error_message
        if outcome.error_code:
            node_row.error_json = {
                "code": outcome.error_code,
                "message": outcome.error_message,
                "node_type": node.type.value,
            }

        total_in = max(total_in, outcome.rows_in)
        total_out = max(total_out, outcome.rows_out)
        total_quarantined += outcome.rows_quarantined

        await session.commit()

        if outcome.status == "failed":
            schema_valid = False
            schema_errors.append(f"{node.id}: {outcome.error_message or ''}")
            await transition(
                session,
                run_id,
                RunStatus.FAILED,
                error_code=outcome.error_code,
                error_message=outcome.error_message,
            )
            # Retry budget already exhausted inside the node loop.
            await transition(session, run_id, RunStatus.NEEDS_ATTENTION)
            run.rows_in = total_in
            run.rows_out = total_out
            run.rows_quarantined = total_quarantined
            await session.commit()
            metrics.run_status_total.labels(status="needs_attention").inc()
            return "needs_attention"

        # Cancellation is checked between nodes: a running node is not killed.
        await session.refresh(run)
        if run.status == "cancelled":
            return "cancelled"

    # Final gate (A0 deterministic rules).
    gate = evaluate_gate(
        schema_valid=schema_valid,
        schema_errors=schema_errors,
        rows_out=total_out,
        rows_quarantined=total_quarantined,
        quarantine_threshold=float(
            (run.__dict__.get("settings_max_quarantine_ratio") or settings.DEFAULT_QUARANTINE_RATIO)
        ),
        reconciliation_passed=(reconciliation or {}).get("passed") if reconciliation else None,
        reconciliation_detail=reconciliation,
        pending_required_proposals=0,
    )

    run.rows_in = total_in
    run.rows_out = total_out
    run.rows_quarantined = total_quarantined

    if gate.passed:
        await transition(session, run_id, RunStatus.SUCCEEDED)
        metrics.run_status_total.labels(status="succeeded").inc()
        final = "succeeded"
    else:
        await transition(
            session,
            run_id,
            RunStatus.NEEDS_ATTENTION,
            error_code="gate_failed",
            error_message="; ".join(
                c.evidence for c in gate.checks if not c.passed
            )[:1000],
        )
        metrics.run_status_total.labels(status="needs_attention").inc()
        final = "needs_attention"

    await session.commit()
    return final


async def _dispatch(
    session: AsyncSession,
    node_type: str,
    params: dict[str, Any],
    frame_in: pl.DataFrame,
    run: Run,
    pipeline_name: str,
    node_id: str,
) -> tuple[pl.DataFrame, list[dict[str, Any]], dict[str, int], dict[str, Any]]:
    """Run a single node. Returns ``(frame_out, rejected, reasons, extra)``."""
    if node_type == "source":
        frame = await _run_source(session, params, run.org_id)
        return frame, [], {}, {}

    if node_type == "validate":
        passing, rejected, counts = await _run_validate(params, frame_in)
        pipeline_slug = safe_dataset_name(pipeline_name, fallback="pipeline")
        node_slug = safe_dataset_name(node_id, fallback="validate")

        silver_write = await asyncio.to_thread(
            silver.write_silver,
            passing,
            org_id=str(run.org_id),
            run_id=str(run.id),
            node_id=node_slug,
        )
        if silver_write:
            silver_uri, silver_rows = silver_write
            await register_dataset(
                session,
                org_id=run.org_id,
                project_id=run.project_id,
                layer="silver",
                name=f"silver_{pipeline_slug}_{node_slug}",
                storage_uri=silver_uri,
                row_count=silver_rows,
                frame=passing,
            )

        if rejected:
            q_frame = quarantine.build_quarantine_frame(
                rejected, reason_code="validation_failed", reason_detail="rule violation"
            )
            q_write = await asyncio.to_thread(
                quarantine.write_quarantine,
                q_frame,
                org_id=str(run.org_id),
                run_id=str(run.id),
                node_id=node_slug,
            )
            if q_write:
                q_uri, q_rows = q_write
                await register_dataset(
                    session,
                    org_id=run.org_id,
                    project_id=run.project_id,
                    layer="quarantine",
                    name=f"quarantine_{pipeline_slug}_{node_slug}",
                    storage_uri=q_uri,
                    row_count=q_rows,
                    frame=q_frame,
                    extra={"reasons": counts},
                )
        return passing, rejected, counts, {}

    if node_type == "clean":
        frame = await _run_clean(params, frame_in)
        return frame, [], {}, {}

    if node_type == "transform":
        frame = await _run_transform(params, frame_in)
        return frame, [], {}, {}

    if node_type == "aggregate":
        frame = await _run_aggregate(params, frame_in)
        return frame, [], {}, {}

    if node_type == "sink":
        group_by = list(params.get("group_by") or [])
        result = await asyncio.to_thread(
            gold.write_gold,
            org_id=str(run.org_id),
            project_id=str(run.project_id),
            run_id=str(run.id),
            silver=frame_in,
            dataset_name=params["dataset_name"],
            group_by=group_by,
            metrics=[],
            write_mode=params.get("write_mode", "overwrite"),
        )
        metrics.worker_rows_total.labels(layer="gold").inc(result.rows_out)

        # The DuckDB table is the query target; the Parquet export is what the
        # Data Explorer reads. A failed export must not pretend the dataset is
        # readable, but it also must not throw away a valid gold table.
        export_uri = ""
        try:
            export_uri, _ = await asyncio.to_thread(
                gold.export_gold_parquet,
                org_id=str(run.org_id),
                project_id=str(run.project_id),
                dataset_name=result.dataset_name,
            )
        except Exception:  # noqa: BLE001 - keep the run result, surface the gap
            logger.exception(
                "Gold %s tertulis di DuckDB tetapi ekspor Parquet gagal (run=%s).",
                result.dataset_name,
                run.id,
            )

        await register_dataset(
            session,
            org_id=run.org_id,
            project_id=run.project_id,
            layer="gold",
            name=result.dataset_name,
            storage_uri=export_uri,
            row_count=result.rows_out,
            columns=result.columns,
        )
        return frame_in, [], {}, {"reconciliation": result.reconciliation, "dataset_name": result.dataset_name}

    raise ValueError(f"Tipe node tidak dikenal: {node_type}")

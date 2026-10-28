"""Text-to-SQL orchestration used by ``POST /v1/agents/ask`` when enabled.

Bridges the deterministic guard in ``api/v1/text_to_sql.py`` with the agent
worker graph in ``apps/agents`` (a separate deployable). Import is deferred and
optional so the API can run without the agent worker installed.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.services import query_service
from dataloom_contracts._base import AgentOutputStatus
from dataloom_contracts.agents import ResultColumn, SqlAnswer

logger = logging.getLogger("dataloom.text_to_sql")


async def run_text_to_sql(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    question: str,
    clarification_answer: str | None,
    agent_run_id: uuid.UUID,
) -> SqlAnswer:
    """Draft, validate, execute and explain one question.

    If the agent package is unavailable we return a refusal, never a fabricated
    query (PRD 10.1 rule 2).
    """
    settings = get_settings()
    try:
        from app_agents_bridge import draft_sql_external  # type: ignore[import-not-found]
    except ImportError:
        draft_sql_external = None

    if draft_sql_external is None:
        return SqlAnswer(
            status=AgentOutputStatus.REJECTED,
            question=question,
            sql=None,
            columns=[],
            rows=[],
            row_count=0,
            truncated=False,
            explanation=(
                "Modul agent tidak tersedia pada proses API ini. "
                "Jalankan agent worker dan hubungkan lewat bridge."
            ),
            assumptions=[],
            open_questions=[],
            rejection_code="AGENT_UNAVAILABLE",
        )

    # The allowlist is computed HERE (from the DB, org+project scoped) and is the
    # enforcement input to ``query_service.run_readonly``. Nothing about it comes
    # from the model's draft, so a draft cannot widen its own allowed tables.
    tables = await query_service.project_tables(
        session, org_id=org_id, project_id=project_id
    )
    draft: dict[str, Any] = await draft_sql_external(
        agent_run_id=str(agent_run_id),
        question=question,
        clarification_answer=clarification_answer,
    )

    status = str(draft.get("status", "ok"))
    if status != "ok":
        return SqlAnswer(
            status=AgentOutputStatus.NEEDS_CLARIFICATION
            if status == "needs_clarification"
            else AgentOutputStatus.REJECTED,
            question=question,
            sql=None,
            columns=[],
            rows=[],
            row_count=0,
            truncated=False,
            explanation=str(draft.get("explanation", ""))[:2000] or None,
            assumptions=[str(item) for item in draft.get("assumptions", [])][:10],
            open_questions=[str(draft.get("clarify_question"))] if draft.get("clarify_question") else [],
            rejection_code=str(draft.get("rejected_reason") or "")[:64] or None,
        )

    sql = str(draft.get("sql", ""))
    try:
        result = await query_service.run_readonly(
            org_id=org_id,
            project_id=project_id,
            sql=sql,
            limit=settings.SQL_MAX_ROWS,
            timeout_seconds=settings.SQL_TIMEOUT_SECONDS,
            # Model output is untrusted: it is re-validated against the AST
            # validator + project allowlist inside run_readonly. Without this the
            # ONLY guard on this path would be ``enable_external_access=false``.
            allowed_tables=tables,
        )
    except query_service.UnsafeQuery as exc:
        # Deterministic rejection: report the stable reason code, never the SQL
        # and never the exception text (it can echo the statement).
        logger.info("SQL ditolak guard: %s", exc.reason)
        return SqlAnswer(
            status=AgentOutputStatus.REJECTED,
            question=question,
            sql=None,
            columns=[],
            rows=[],
            row_count=0,
            truncated=False,
            explanation="Query yang dihasilkan agent tidak lolos validasi baca-saja.",
            assumptions=[],
            open_questions=[],
            rejection_code=exc.reason[:64] or "NOT_SELECT",
        )
    except Exception as exc:  # noqa: BLE001 - surface a safe rejection, not a 500
        # Type name only: a DuckDB/HTTP driver message can embed a path, a DSN or
        # the SQL text itself (FR-015 / SEC-24 discipline).
        logger.info("Query ditolak/gagal: %s", type(exc).__name__)
        return SqlAnswer(
            status=AgentOutputStatus.REJECTED,
            question=question,
            sql=None,
            columns=[],
            rows=[],
            row_count=0,
            truncated=False,
            explanation="Query tidak dapat dijalankan dengan aman.",
            assumptions=[],
            open_questions=[],
            rejection_code="EXECUTION_ERROR",
        )

    rows_as_lists: list[list[Any]] = [
        [row.get(column) for column in result.columns] for row in result.rows
    ]

    return SqlAnswer(
        status=AgentOutputStatus.OK,
        question=question,
        sql=sql,
        columns=[ResultColumn(name=name, dtype="string") for name in result.columns],
        rows=rows_as_lists[: settings.SQL_MAX_ROWS],
        row_count=result.row_count,
        truncated=result.truncated,
        explanation=str(draft.get("explanation", ""))[:2000] or None,
        assumptions=[str(item) for item in draft.get("assumptions", [])][:10],
        open_questions=[],
        rejection_code=None,
    )

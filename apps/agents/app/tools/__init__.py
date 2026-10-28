"""Read-only, allowlisted tools for the agents (ADR-003 point 8).

**There is NO shell, NO filesystem write, NO network and NO raw SQL tool in
this package.** The complete tool surface an agent node may call:

======================  ====================================================
module                  functions exposed
======================  ====================================================
``tools.stats``         ``profile_frame``, ``profile_column``, ``duplicate_row_count``
``tools.sampling``      ``sample_for_analysis``, ``sample_for_prompt``
``tools.schema``        ``list_datasets``, ``get_schema``, ``allowed_tables``
``tools.sql_guard``     ``validate_readonly_sql``, ``enforce_limit``
``tools.logs``          ``collect_run_evidence``, ``read_log_excerpt``
``tools.dag_tools``     ``validate_dag_tool``
======================  ====================================================

Anything not in this list must not be bound into a graph. The allowlist is
enforced socially (code review) and by ``ALLOWED_TOOL_NAMES`` below, which the
README and tests reference.
"""

from __future__ import annotations

from app.tools.dag_tools import validate_dag_syntax, validate_dag_tool
from app.tools.logs import EvidenceItem, RunEvidence, collect_run_evidence, redact
from app.tools.sampling import render_rows_csv, sample_for_analysis, sample_for_prompt
from app.tools.schema import DatasetRef, allowed_columns, allowed_tables, get_schema, list_datasets
from app.tools.sql_guard import UnsafeSql, enforce_limit, validate_readonly_sql
from app.tools.stats import ColumnStatsResult, duplicate_row_count, profile_column, profile_frame

#: The complete tool allowlist. A name here is a Python function the graphs may
#: call; nothing else is exposed to a model (models never call tools directly:
#: tools run in Python, the model only ever sees their rendered output).
ALLOWED_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "validate_dag_syntax",
        "validate_dag_tool",
        "collect_run_evidence",
        "redact",
        "render_rows_csv",
        "sample_for_analysis",
        "sample_for_prompt",
        "allowed_columns",
        "allowed_tables",
        "get_schema",
        "list_datasets",
        "enforce_limit",
        "validate_readonly_sql",
        "duplicate_row_count",
        "profile_column",
        "profile_frame",
    }
)

__all__ = [
    "ALLOWED_TOOL_NAMES",
    "ColumnStatsResult",
    "DatasetRef",
    "EvidenceItem",
    "RunEvidence",
    "UnsafeSql",
    "allowed_columns",
    "allowed_tables",
    "collect_run_evidence",
    "duplicate_row_count",
    "enforce_limit",
    "get_schema",
    "list_datasets",
    "profile_column",
    "profile_frame",
    "redact",
    "render_rows_csv",
    "sample_for_analysis",
    "sample_for_prompt",
    "validate_dag_syntax",
    "validate_dag_tool",
    "validate_readonly_sql",
]

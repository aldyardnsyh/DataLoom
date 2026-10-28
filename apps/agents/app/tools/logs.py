"""Read-only evidence collection for the Self-Healing agent (PRD A4).

Inputs: ``run_nodes.error_json`` for one run, plus (optionally) a log file the
operator points at with ``AGENT_LOG_DIR``.

**Redaction is mandatory.** ``_redact`` below is a deliberate COPY of
``apps/api/app/core/logging_redaction.py`` (same regexes, same replacement
token). It is copied rather than imported because the modular monolith rule
(ADR-001) forbids importing across ``apps/``; if the API patterns change, this
file must change in the same commit. ``tests/`` should assert both lists are
equal.

Only ORG-SCOPED rows are ever returned: every query filters ``org_id``.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Run, RunNode

logger = logging.getLogger("dataloom.agents.logs")

# --- BEGIN copy of apps/api/app/core/logging_redaction.py ------------------
# Extra patterns (review of Phases 2–6, SEC-41) close shapes the Phase-1 list
# missed and that log/error text actually contains:
#   * ``postgresql://user:pass@host/db`` / ``redis://:pass@host`` — a URL with
#     credentials is the most likely secret to appear verbatim in a driver or
#     HTTP exception. It is anchored on ``@`` so it does not depend on the
#     value's charset (the old ``[^\s,;\"'}]+`` classes stopped at ``)``/``"``,
#     so ``password='p@ss(word)'`` leaked the tail).
#   * Explicit stop-at-``@`` for every userinfo class, so the redaction cannot
#     itself be defeated by a ``@`` inside the password.
# The URL rule runs FIRST; the generic rules then see ``user:***@host``.
_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)([^\s:/@]+):([^\s@]+)@")
_SENSITIVE_PATTERNS = [
    _URL_USERINFO,
    # ``password=...`` / ``password: ...`` / ``"password": "..."`` — one group:
    # the replacement drops the whole match, so the value cannot survive.
    re.compile(
        r"(?i)[\"']?\b(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key)"
        r"[\"']?\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|`[^`]*`|[^\s,;)]+)"
    ),
    re.compile(r"(?i)(x-api-key\s*[:=]\s*)([^\s,;\"']+)"),
    re.compile(r"(?i)(authorization\s*[:=]\s*)([^\s,;\"']+)"),
    re.compile(r"(?i)(dl_session=)([^;\s]+)"),
    re.compile(r"(?i)(dl_csrf=)([^;\s]+)"),
    re.compile(r"(dl_[A-Za-z0-9_-]{4,}_)([A-Za-z0-9_-]{8,})"),  # api keys
]
_REDACTED = "***redacted***"


def redact(text: str) -> str:
    """Return ``text`` with known secret shapes replaced.

    Never raises: a pattern failure skips that pattern (the text is then
    *partially* redacted), and the caller still truncates to 600 chars and wraps
    the excerpt as untrusted data before it can reach a prompt.
    """
    current = text
    for pattern in _SENSITIVE_PATTERNS:
        try:
            if pattern is _URL_USERINFO:
                current = pattern.sub(
                    lambda m: f"{m.group(1)}{m.group(2)}:{_REDACTED}@", current
                )
            elif pattern.groups >= 1:
                # Group 1 is the key name (or the whole match when the pattern
                # has no groups); everything it consumed is replaced.
                current = pattern.sub(lambda m: f"{m.group(1)}{_REDACTED}", current)
            else:
                current = pattern.sub(_REDACTED, current)
        except Exception:  # noqa: BLE001 - broken redaction must not raise
            continue
    return current


# --- END copy --------------------------------------------------------------


@dataclass(slots=True)
class EvidenceItem:
    """One piece of evidence in the shape ``dataloom_contracts.agents.Evidence``.

    ``source`` must be one of the contract's literals:
    ``run_node_error | log | schema_diff | stat | user_report``.
    """

    source: str
    locator: str
    excerpt: str

    def to_contract_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "locator": self.locator[:300],
            "excerpt": self.excerpt[:2000],
        }


@dataclass(slots=True)
class RunEvidence:
    run_id: uuid.UUID | None
    run_status: str | None
    error_code: str | None
    error_message: str | None
    nodes: list[EvidenceItem] = field(default_factory=list)
    log_lines: list[EvidenceItem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def items(self) -> list[EvidenceItem]:
        return [*self.nodes, *self.log_lines]

    def has_any(self) -> bool:
        return bool(self.nodes or self.log_lines)


def _summarise_error_json(payload: dict[str, Any] | None) -> str:
    """Render ``error_json`` compactly. Values are redacted before returning."""
    if not payload:
        return ""
    try:
        parts = []
        for key in ("code", "message", "node_type", "exception", "stage"):
            if key in payload:
                parts.append(f"{key}={payload[key]}")
        return redact("; ".join(parts))[:2000]
    except Exception:  # noqa: BLE001 - evidence gathering must never crash a graph
        return "error_json tidak dapat dibaca"


async def collect_run_evidence(
    session: AsyncSession, *, org_id: uuid.UUID, run_id: uuid.UUID
) -> RunEvidence:
    """Gather deterministic evidence for a failed run (org-scoped)."""
    run = (
        await session.execute(
            select(Run).where(Run.id == run_id, Run.org_id == org_id)
        )
    ).scalar_one_or_none()
    if run is None:
        return RunEvidence(
            run_id=None,
            run_status=None,
            error_code=None,
            error_message=None,
            notes=["Run tidak ditemukan pada organisasi ini."],
        )

    evidence = RunEvidence(
        run_id=run.id,
        run_status=run.status,
        error_code=run.error_code,
        error_message=redact(run.error_message or "")[:2000] or None,
    )

    nodes = (
        await session.execute(
            select(RunNode)
            .where(RunNode.run_id == run.id, RunNode.org_id == org_id)
            .order_by(RunNode.created_at.asc())
        )
    ).scalars().all()

    for node in nodes:
        if node.status not in ("failed", "retrying"):
            continue
        excerpt = _summarise_error_json(node.error_json) or redact(node.error_message or "")
        if not excerpt:
            continue
        evidence.nodes.append(
            EvidenceItem(
                source="run_node_error",
                locator=f"run_nodes/{node.node_id}#attempt={node.attempt}",
                excerpt=excerpt,
            )
        )

    if not evidence.nodes and not evidence.error_code:
        evidence.notes.append(
            "Tidak ada node gagal pada run ini; bukti belum cukup untuk diagnosis."
        )
    return evidence


def _log_dir() -> str | None:
    """Optional directory an operator may mount read-only for agent evidence."""
    return get_settings().AGENT_LOG_DIR or None


def read_log_excerpt(run_id: uuid.UUID, *, max_lines: int | None = None) -> list[EvidenceItem]:
    """Read ``{AGENT_LOG_DIR}/{run_id}.log`` if present, redacted.

    Line-level budget (``LOG_EXCERPT_MAX_LINES``) and character budget
    (``LOG_EXCERPT_MAX_CHARS``) are enforced here so a huge log cannot flood
    the prompt. Only the *last* lines are kept: they contain the failure.

    The file is opened read-only and never executed or parsed as anything but
    text. A missing file is not an error (evidence simply stays empty).
    """
    settings = get_settings()
    base = _log_dir()
    if not base:
        return []
    path = os.path.join(base, f"{run_id}.log")
    if not os.path.isfile(path):
        return []

    keep = int(max_lines if max_lines is not None else settings.LOG_EXCERPT_MAX_LINES)
    char_budget = settings.LOG_EXCERPT_MAX_CHARS
    items: list[EvidenceItem] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()[-keep:]
    except OSError:
        logger.warning("Gagal membaca log run %s (path tidak dapat dibuka)", run_id)
        return []

    used = 0
    for offset, raw in enumerate(lines):
        text = redact(raw.rstrip("\n"))[:600]
        if not text:
            continue
        if used + len(text) > char_budget:
            break
        used += len(text)
        items.append(
            EvidenceItem(source="log", locator=f"log:{offset}", excerpt=text)
        )
    return items


async def gather_all(
    session: AsyncSession, *, org_id: uuid.UUID, run_id: uuid.UUID
) -> RunEvidence:
    """``collect_run_evidence`` + optional log excerpt, both redacted."""
    evidence = await collect_run_evidence(session, org_id=org_id, run_id=run_id)
    evidence.log_lines = read_log_excerpt(run_id)
    return evidence


__all__ = [
    "EvidenceItem",
    "RunEvidence",
    "collect_run_evidence",
    "gather_all",
    "read_log_excerpt",
    "redact",
]

"""Deterministic sampling with hard caps (PRD A1).

Two different caps, deliberately separate:

- :func:`sample_for_analysis` - at most ``SAMPLE_ROWS_MAX`` (1,000) rows. This
  is the analysis sample: statistics are computed on it.
- :func:`sample_for_prompt` - at most ``PROMPT_SAMPLE_ROWS`` (50) rows and at
  most ``PROMPT_MAX_CHARS`` characters. This is what may be pasted into a
  prompt payload, because every pasted cell is untrusted text.

Sampling is deterministic: rows are ordered by a hash of their content before
taking the head, so the same frame always yields the same sample and a test can
assert on it. There is no RNG and no clock in this module.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import polars as pl

from app.config import get_settings
from app.tools.stats import business_columns


def _row_digest(row: dict[str, Any]) -> str:
    canonical = json.dumps(row, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def sample_for_analysis(frame: pl.DataFrame, *, max_rows: int | None = None) -> pl.DataFrame:
    """Pseudo-random but deterministic sample of at most ``max_rows`` rows."""
    limit = int(max_rows if max_rows is not None else get_settings().SAMPLE_ROWS_MAX)
    limit = max(1, min(limit, 1000))  # PRD cap: 1.000
    if frame.height <= limit:
        return frame

    columns = business_columns(frame) or frame.columns
    rows = frame.select(columns).to_dicts()
    ranked = sorted(rows, key=_row_digest)[:limit]
    return pl.DataFrame(ranked, strict=False) if ranked else frame.head(0)


def sample_for_prompt(frame: pl.DataFrame, *, max_rows: int | None = None, max_chars: int | None = None) -> pl.DataFrame:
    """At most 50 rows, additionally trimmed so the rendered text fits ``max_chars``.

    The caller renders the frame (CSV/JSON) and must still wrap the result with
    :func:`app.prompts.wrapping.wrap_untrusted_data`.
    """
    settings = get_settings()
    limit = int(max_rows if max_rows is not None else settings.PROMPT_SAMPLE_ROWS)
    limit = max(1, min(limit, 50))  # PRD A1 tool bound: max 50
    budget = int(max_chars if max_chars is not None else settings.PROMPT_MAX_CHARS)

    sampled = sample_for_analysis(frame, max_rows=max(limit * 4, limit)).head(limit * 4)
    if sampled.height == 0:
        return sampled

    rows = sampled.to_dicts()
    accepted: list[dict[str, Any]] = []
    used = 0
    for row in rows:
        size = len(json.dumps(row, default=str, ensure_ascii=False))
        if accepted and used + size > budget:
            break
        accepted.append(row)
        used += size
        if len(accepted) >= limit:
            break
    return pl.DataFrame(accepted, strict=False)


def render_rows_csv(frame: pl.DataFrame, *, max_chars: int | None = None) -> str:
    """Render sampled rows as CSV text for embedding INSIDE a data wrapper.

    Values are size-clamped per cell so one enormous cell cannot push the
    prompt over budget. The output is untrusted text: wrap it before use.
    """
    settings = get_settings()
    budget = int(max_chars if max_chars is not None else settings.PROMPT_MAX_CHARS)
    columns = frame.columns
    lines: list[str] = [",".join(columns)]
    used = len(lines[0])
    for row in frame.to_dicts():
        cells: list[str] = []
        for column in columns:
            value = row.get(column)
            text = "" if value is None else str(value)
            if len(text) > 120:
                text = text[:117] + "..."
            # Quote when the cell could break the CSV layout.
            if any(ch in text for ch in [",", '"', "\n", "\r"]):
                text = '"' + text.replace('"', '""') + '"'
            cells.append(text)
        line = ",".join(cells)
        if used + len(line) + 1 > budget:
            lines.append("# ... dipotong karena batas ukuran prompt")
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


__all__ = ["render_rows_csv", "sample_for_analysis", "sample_for_prompt"]

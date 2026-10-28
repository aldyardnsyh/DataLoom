"""Log redaction: no secret may reach a log sink (NFR-07).

Applied as a logging filter on the root handler so it covers third-party
libraries too.
"""

from __future__ import annotations

import logging
import re

_SENSITIVE_PATTERNS = [
    re.compile(r"(?i)(x-api-key\s*[:=]\s*)([^\s,;\"']+)"),
    re.compile(r"(?i)(authorization\s*[:=]\s*)([^\s,;\"']+)"),
    re.compile(r"(?i)(dl_session=)([^;\s]+)"),
    re.compile(r"(?i)(dl_csrf=)([^;\s]+)"),
    re.compile(r"(?i)(password\s*[\"']?\s*[:=]\s*[\"']?)([^\s,;\"'}]+)"),
    re.compile(r"(?i)(secret\s*[\"']?\s*[:=]\s*[\"']?)([^\s,;\"'}]+)"),
    re.compile(r"(dl_[A-Za-z0-9_-]{4,}_)([A-Za-z0-9_-]{8,})"),  # api keys
]

_REDACTED = "***redacted***"


def redact(text: str) -> str:
    """Return ``text`` with known secret shapes replaced."""
    for pattern in _SENSITIVE_PATTERNS:
        text = pattern.sub(lambda m: m.group(1) + _REDACTED, text)
    return text


class RedactionFilter(logging.Filter):
    """Rewrite the formatted message in place before it is emitted."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        redacted = redact(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def install_log_redaction() -> None:
    """Attach the filter to the root logger's handlers (idempotent)."""
    root = logging.getLogger()
    for handler in root.handlers:
        if not any(isinstance(f, RedactionFilter) for f in handler.filters):
            handler.addFilter(RedactionFilter())

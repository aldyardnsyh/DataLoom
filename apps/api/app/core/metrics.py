"""Prometheus metrics (FR-042).

Every metric exposed here is a real counter/gauge updated by code paths. No
hardcoded values: if nothing happened yet the series is simply absent.
"""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest
from starlette.responses import Response

ingest_requests_total = Counter(
    "dataloom_ingest_requests_total",
    "Ingest HTTP requests by resulting status class.",
    labelnames=("status",),
)

ingest_rows_total = Counter(
    "dataloom_ingest_rows_total",
    "Rows accepted into the ingest stream.",
)

ingest_rejected_total = Counter(
    "dataloom_ingest_rejected_total",
    "Ingest requests rejected before reaching the stream.",
    labelnames=("reason",),
)

worker_rows_total = Counter(
    "dataloom_worker_rows_total",
    "Rows processed by the worker, by layer.",
    labelnames=("layer",),
)

worker_quarantine_total = Counter(
    "dataloom_worker_quarantine_total",
    "Rows quarantined by reason code.",
    labelnames=("reason",),
)

stream_length = Gauge(
    "dataloom_ingest_stream_length",
    "Current XLEN of the watched ingest stream.",
    labelnames=("stream",),
)

run_duration_ms = Gauge(
    "dataloom_run_duration_ms",
    "Duration of the last finished run, in milliseconds.",
)


def metrics_response() -> Response:
    """Handler body for ``GET /metrics``."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

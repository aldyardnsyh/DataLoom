"""Prometheus metrics for the worker."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, start_http_server

worker_rows_total = Counter(
    "dataloom_worker_rows_total",
    "Rows written by layer.",
    labelnames=("layer",),
)

ingest_rejected_total = Counter(
    "dataloom_worker_rejected_total",
    "Rejected rows/entries by reason.",
    labelnames=("reason",),
)

batches_total = Counter(
    "dataloom_worker_batches_total",
    "Batches processed.",
    labelnames=("result",),
)

stream_lag = Gauge(
    "dataloom_worker_stream_lag",
    "Pending entries per stream.",
    labelnames=("stream",),
)

run_status_total = Counter(
    "dataloom_worker_run_status_total",
    "Pipeline runs finished, by final status.",
    labelnames=("status",),
)


def serve(port: int = 9101) -> None:
    """Expose metrics; called from main() only when enabled."""
    start_http_server(port)

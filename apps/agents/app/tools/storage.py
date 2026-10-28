"""Read-only artifact storage for the agent worker.

The agents need to READ Parquet written by the pipeline worker (Bronze/Silver)
to compute statistics. They must never write to those locations (Bronze is
immutable, PRD FR-021).

Two backends, selected by ``ARTIFACT_STORAGE``:

* ``local`` (default): reads a filesystem path. An operator mounts the Parquet
  directory read-only. Anything else is refused.
* ``s3``: reads via boto3, and only when ``ALLOW_OBJECT_STORAGE`` is true.

Refusing by default is deliberate: an agent process that can reach object
storage can exfiltrate every tenant's data if a bug appears. Opt-in only.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

import polars as pl

from app.config import get_settings

logger = logging.getLogger("dataloom.agents.storage")

#: Hard cap on rows read into memory for profiling. The profiler samples
#: downstream anyway; this prevents an accidental full-table read.
MAX_READ_ROWS = 50_000


class StorageAccessDenied(Exception):
    """Raised when the configured backend is not allowed to read."""


def _allowed_root() -> Path | None:
    """Configured read-only root (``ARTIFACT_LOCAL_ROOT``), if any."""
    root = get_settings().ARTIFACT_LOCAL_ROOT
    return Path(root).expanduser().resolve() if root else None


def _local_path(uri: str) -> Path:
    """Map a ``file://`` or plain path URI to a Path, refusing traversal.

    ``".." in path.parts`` alone was not enough (SEC-38): an absolute path such
    as ``/etc/passwd`` or ``/data/duckdb/..//secrets.parquet`` contains no
    ``..`` segment after normalisation and would be read. When
    ``ARTIFACT_LOCAL_ROOT`` is configured (the operator-mounted read-only
    Parquet directory) the *resolved* path must live under it.
    """
    raw = uri[len("file://") :] if uri.startswith("file://") else uri
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise StorageAccessDenied("Hanya path absolut yang diizinkan.")
    if ".." in path.parts:
        raise StorageAccessDenied("Path traversal ditolak.")
    if path.suffix.lower() != ".parquet":
        # The only thing this backend may read is a Parquet artefact; refusing
        # other suffixes removes the whole class "point a dataset at /etc/passwd"
        # even before the root confinement below is configured.
        raise StorageAccessDenied("Hanya berkas .parquet yang diizinkan.")
    root = _allowed_root()
    if root is not None:
        resolved = path.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise StorageAccessDenied(
                "Path berada di luar direktori artefak yang diizinkan."
            ) from exc
    return path


def read_frame(uri: str) -> pl.DataFrame:
    """Read a Parquet object into a Polars frame (bounded by ``MAX_READ_ROWS``).

    Raises ``StorageAccessDenied`` when the backend is disabled, and
    ``FileNotFoundError`` when the object does not exist.
    """
    settings = get_settings()

    if settings.ARTIFACT_STORAGE == "local":
        path = _local_path(uri)
        if not path.is_file():
            raise FileNotFoundError(f"Berkas tidak ditemukan: {path.name}")
        frame = pl.read_parquet(path)
    elif settings.ARTIFACT_STORAGE == "s3":
        if not settings.ALLOW_OBJECT_STORAGE:
            raise StorageAccessDenied(
                "Akses object storage dinonaktifkan (ALLOW_OBJECT_STORAGE=false)."
            )
        frame = _read_s3(uri)
    else:  # pragma: no cover - Literal is validated by pydantic
        raise StorageAccessDenied(f"Backend storage tidak dikenal: {settings.ARTIFACT_STORAGE}")

    if frame.height > MAX_READ_ROWS:
        # Deterministic head keeps memory bounded; the profiler samples after.
        frame = frame.head(MAX_READ_ROWS)
    return frame


def _read_s3(uri: str) -> pl.DataFrame:
    """Fetch one Parquet object from S3/R2 into memory."""
    import boto3

    settings = get_settings()
    kwargs: dict[str, object] = {
        "service_name": "s3",
        "region_name": settings.S3_REGION,
        "aws_access_key_id": settings.S3_ACCESS_KEY_ID,
        "aws_secret_access_key": settings.S3_SECRET_ACCESS_KEY,
    }
    if settings.S3_ENDPOINT_URL:
        kwargs["endpoint_url"] = settings.S3_ENDPOINT_URL
    client = boto3.client(**kwargs)  # type: ignore[arg-type]

    prefix = f"s3://{settings.S3_BUCKET}/"
    if not uri.startswith(prefix):
        raise StorageAccessDenied("URI menunjuk bucket yang tidak diizinkan.")
    key = uri[len(prefix) :]
    body = client.get_object(Bucket=settings.S3_BUCKET, Key=key)["Body"].read()
    return pl.read_parquet(io.BytesIO(body))


__all__ = ["MAX_READ_ROWS", "StorageAccessDenied", "read_frame"]

"""Object storage adapter: S3 API (Cloudflare R2 / AWS S3 / MinIO).

One adapter, endpoint from env (decision D-02). boto3 is synchronous, so calls
are pushed to the thread pool by callers via ``run_in_threadpool``.

A development-only ``local`` backend writes plain files under
``LOCAL_STORAGE_ROOT``. It exists so the whole stack can run on a laptop
without a cloud account; production keeps using ``s3``.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import boto3
from botocore.client import Config

from app.config import get_settings

_client: Any = None


def _get_client() -> Any:
    """Lazily build a boto3 S3 client. Never used at import time."""
    global _client
    if _client is None:
        settings = get_settings()
        kwargs: dict[str, Any] = {
            "service_name": "s3",
            "region_name": settings.S3_REGION,
            "aws_access_key_id": settings.S3_ACCESS_KEY_ID,
            "aws_secret_access_key": settings.S3_SECRET_ACCESS_KEY,
            "config": Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=5,
                read_timeout=20,
            ),
        }
        if settings.S3_ENDPOINT_URL:
            kwargs["endpoint_url"] = settings.S3_ENDPOINT_URL
        _client = boto3.client(**kwargs)
    return _client


def _is_local() -> bool:
    return get_settings().DATASET_STORAGE.lower() == "local"


def _local_root() -> Path:
    return Path(get_settings().LOCAL_STORAGE_ROOT).expanduser().resolve()


def _local_path(key: str) -> Path:
    """Resolve a key under the local root, refusing to escape it."""
    root = _local_root()
    path = (root / key).resolve()
    if root != path and root not in path.parents:
        raise ValueError("Key storage tidak valid.")
    return path


def upload_key(
    *, org_id: uuid.UUID, project_id: uuid.UUID, upload_id: uuid.UUID, filename: str
) -> str:
    """Deterministic object key; org prefix makes cross-tenant leak obvious."""
    safe_name = "".join(c for c in filename if c.isalnum() or c in "._-")[:120]
    return f"org/{org_id}/project/{project_id}/uploads/{upload_id}/{safe_name}"


def presign_put(key: str, content_type: str, expires_in: int | None = None) -> str:
    settings = get_settings()
    if _is_local():
        # Direct browser uploads need a pre-signed S3 URL. The local backend has
        # no equivalent, so the caller is told plainly instead of receiving a
        # broken link.
        from app.core.errors import ValidationProblem

        raise ValidationProblem(
            "Unggahan langsung butuh penyimpanan S3. Gunakan sumber webhook pada mode lokal."
        )
    return _get_client().generate_presigned_url(
        "put_object",
        Params={"Bucket": settings.S3_BUCKET, "Key": key, "ContentType": content_type},
        ExpiresIn=expires_in or settings.S3_PRESIGN_EXPIRES_SECONDS,
    )


def head_object(key: str) -> dict[str, Any] | None:
    """Return ``{size, content_type}`` or ``None`` when the object is missing."""
    from botocore.exceptions import ClientError

    settings = get_settings()
    if _is_local():
        path = _local_path(key)
        if not path.is_file():
            return None
        return {
            "size": path.stat().st_size,
            "content_type": "application/octet-stream",
        }
    try:
        head = _get_client().head_object(Bucket=settings.S3_BUCKET, Key=key)
    except ClientError:
        return None
    return {
        "size": int(head.get("ContentLength", 0)),
        "content_type": head.get("ContentType"),
    }


def get_range(key: str, length: int = 8) -> bytes:
    """Fetch the first ``length`` bytes (magic-byte validation, FR-013)."""
    settings = get_settings()
    if _is_local():
        return _local_path(key).read_bytes()[:length]
    obj = _get_client().get_object(
        Bucket=settings.S3_BUCKET, Key=key, Range=f"bytes=0-{length - 1}"
    )
    return obj["Body"].read(length)


def key_from_uri(uri: str) -> str:
    """Extract the object key and refuse a URI from a different bucket."""
    settings = get_settings()
    for scheme in ("s3", "local"):
        prefix = f"{scheme}://{settings.S3_BUCKET}/"
        if uri.startswith(prefix):
            key = uri[len(prefix) :]
            if not key or key.startswith("/") or ".." in key.split("/"):
                raise ValueError("Key storage tidak valid.")
            return key
    raise ValueError("URI storage tidak dikenal untuk bucket ini.")


def get_bytes(key: str) -> bytes:
    settings = get_settings()
    if _is_local():
        return _local_path(key).read_bytes()
    obj = _get_client().get_object(Bucket=settings.S3_BUCKET, Key=key)
    return obj["Body"].read()


def delete_object(key: str) -> None:
    settings = get_settings()
    if _is_local():
        path = _local_path(key)
        if path.is_file():
            path.unlink()
        return
    _get_client().delete_object(Bucket=settings.S3_BUCKET, Key=key)

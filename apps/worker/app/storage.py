"""Object storage adapter for the worker.

Two backends, one interface:

- ``s3``   : production default (Cloudflare R2, AWS S3, MinIO).
- ``local``: development only. Objects are plain files under
  ``LOCAL_STORAGE_ROOT`` so the whole stack runs without a cloud account.

The URI shape is identical in both modes (``<scheme>://<bucket>/<key>``), so a
dataset row written by a local worker can still be read by a local API, and the
production code path never learns which backend it is talking to.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import boto3
from botocore.client import Config

from app.config import get_settings

_client: Any = None


def _client_get() -> Any:
    global _client
    if _client is None:
        settings = get_settings()
        kwargs: dict[str, Any] = {
            "service_name": "s3",
            "region_name": settings.S3_REGION,
            "aws_access_key_id": settings.S3_ACCESS_KEY_ID,
            "aws_secret_access_key": settings.S3_SECRET_ACCESS_KEY,
            "config": Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        }
        if settings.S3_ENDPOINT_URL:
            kwargs["endpoint_url"] = settings.S3_ENDPOINT_URL
        _client = boto3.client(**kwargs)
    return _client


def _bucket() -> str:
    return get_settings().S3_BUCKET


def _is_local() -> bool:
    return get_settings().DATASET_STORAGE.lower() == "local"


def _scheme() -> str:
    return "local" if _is_local() else "s3"


def _local_root() -> Path:
    return Path(get_settings().LOCAL_STORAGE_ROOT).expanduser().resolve()


def _local_path(key: str) -> Path:
    """Resolve a key under the local root, refusing to escape it."""
    root = _local_root()
    path = (root / key).resolve()
    if root != path and root not in path.parents:
        raise ValueError("Key storage tidak valid.")
    return path


def uri_for(key: str) -> str:
    return f"{_scheme()}://{_bucket()}/{key}"


def key_from_uri(uri: str) -> str:
    """Extract the object key and refuse a URI from a different bucket."""
    settings = get_settings()
    prefix = f"{_scheme()}://{settings.S3_BUCKET}/"
    if not uri.startswith(prefix):
        raise ValueError("URI storage tidak dikenal untuk bucket ini.")
    key = uri[len(prefix) :]
    if not key or key.startswith("/") or ".." in key.split("/"):
        raise ValueError("Key storage tidak valid.")
    return key


def put_bytes(key: str, body: bytes, content_type: str = "application/octet-stream") -> str:
    """Write an object. Returns the storage URI.

    Bronze objects are written with this function ONLY, and never overwritten
    under the same key because the key embeds ``batch_id`` (append-only).
    """
    if _is_local():
        path = _local_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return uri_for(key)

    _client_get().put_object(
        Bucket=_bucket(), Key=key, Body=body, ContentType=content_type
    )
    return uri_for(key)


def get_bytes(uri_or_key: str) -> bytes:
    if uri_or_key.startswith("local://"):
        return _local_path(key_from_uri(uri_or_key)).read_bytes()
    if uri_or_key.startswith("s3://"):
        return _client_get().get_object(
            Bucket=_bucket(), Key=key_from_uri(uri_or_key)
        )["Body"].read()
    if _is_local():
        return _local_path(uri_or_key).read_bytes()
    obj = _client_get().get_object(Bucket=_bucket(), Key=uri_or_key)
    return obj["Body"].read()


def list_keys(prefix: str, limit: int = 10_000) -> list[str]:
    """List object keys under a prefix (paginated)."""
    if _is_local():
        root = _local_root()
        base = _local_path(prefix)
        if not base.exists():
            return []
        keys: list[str] = []
        for path in sorted(base.rglob("*")):
            if path.is_file():
                keys.append(str(path.relative_to(root)).replace("\\", "/"))
                if len(keys) >= limit:
                    break
        return keys

    keys: list[str] = []
    paginator = _client_get().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=_bucket(), Prefix=prefix):
        for item in page.get("Contents", []):
            keys.append(item["Key"])
            if len(keys) >= limit:
                return keys
    return keys


def object_exists(key: str) -> bool:
    from botocore.exceptions import ClientError

    if _is_local():
        try:
            return _local_path(key).is_file()
        except ValueError:
            return False

    try:
        _client_get().head_object(Bucket=_bucket(), Key=key)
        return True
    except ClientError:
        return False

"""Object storage adapter for the worker (S3 API: R2 / S3 / MinIO)."""

from __future__ import annotations

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


def uri_for(key: str) -> str:
    return f"s3://{_bucket()}/{key}"


def key_from_uri(uri: str) -> str:
    prefix = f"s3://{_bucket()}/"
    if not uri.startswith(prefix):
        raise ValueError("URI storage tidak dikenal untuk bucket ini.")
    return uri[len(prefix) :]


def put_bytes(key: str, body: bytes, content_type: str = "application/octet-stream") -> str:
    """Write an object. Returns the ``s3://`` URI.

    Bronze objects are written with this function ONLY, and never overwritten
    under the same key because the key embeds ``batch_id`` (append-only).
    """
    _client_get().put_object(
        Bucket=_bucket(), Key=key, Body=body, ContentType=content_type
    )
    return uri_for(key)


def get_bytes(uri_or_key: str) -> bytes:
    key = uri_or_key[len("s3://") :].split("/", 1)[1] if uri_or_key.startswith("s3://") else uri_or_key
    obj = _client_get().get_object(Bucket=_bucket(), Key=key)
    return obj["Body"].read()


def list_keys(prefix: str, limit: int = 10_000) -> list[str]:
    """List object keys under a prefix (paginated)."""
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

    try:
        _client_get().head_object(Bucket=_bucket(), Key=key)
        return True
    except ClientError:
        return False

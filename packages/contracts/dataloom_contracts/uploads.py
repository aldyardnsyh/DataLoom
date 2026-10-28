"""Presigned CSV/Parquet upload contracts (FR-013, D-02).

Two-step flow, no file bytes ever touch the API process:

1. ``POST /v1/projects/{id}/uploads``  -> ``UploadInitResponse`` (presigned PUT)
2. browser PUTs chunks directly to object storage
3. ``POST /v1/projects/{id}/uploads/{upload_id}/complete`` -> ``UploadCompleteResponse``

Chunking is expressed as a list of parts so the same contract works for
single-PUT (parts length 1) and multipart uploads.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from ._base import ContractModel, ReadModel, UploadStatus


class UploadInitRequest(ContractModel):
    """Ask for presigned URL(s). Size limit mirrors FR-013 (500 MB MVP)."""

    filename: str = Field(min_length=1, max_length=255)
    content_type: Literal[
        "text/csv",
        "application/csv",
        "application/vnd.apache.parquet",
        "application/octet-stream",
    ] = "text/csv"
    size_bytes: int = Field(gt=0, le=524_288_000)
    part_count: int = Field(
        default=1,
        ge=1,
        le=100,
        description="Multipart part count; 1 = single presigned PUT.",
    )
    source_id: uuid.UUID | None = Field(
        default=None, description="Attach this upload to an existing CSV source."
    )

    @model_validator(mode="after")
    def _parquet_needs_octet_stream(self) -> "UploadInitRequest":
        if self.filename.lower().endswith(".parquet") and self.content_type not in (
            "application/vnd.apache.parquet",
            "application/octet-stream",
        ):
            raise ValueError(
                "File .parquet harus memakai content_type application/vnd.apache.parquet."
            )
        if self.filename.lower().endswith(".csv") and self.content_type not in (
            "text/csv",
            "application/csv",
            "application/octet-stream",
        ):
            raise ValueError("File .csv harus memakai content_type text/csv.")
        return self


class UploadPart(ContractModel):
    """One presigned part. ``part_number`` is 1-based (S3 convention)."""

    part_number: int = Field(ge=1, le=100)
    url: str
    headers: dict[str, str] = Field(
        default_factory=dict,
        description="Headers the browser MUST send verbatim with the PUT.",
    )


class UploadInitResponse(ReadModel):
    upload_id: uuid.UUID
    dataset_id: uuid.UUID
    storage_key: str = Field(description="Object-storage key; not a public URL.")
    parts: list[UploadPart]
    expires_at: datetime
    max_bytes: int


class UploadCompleteRequest(ContractModel):
    """Report uploaded parts so the API can finalize the multipart upload."""

    etags: list[str] = Field(
        default_factory=list,
        description="ETag per part, in part order. Empty for single-PUT uploads.",
    )
    checksum_sha256: str | None = Field(default=None, min_length=64, max_length=64)


class UploadCompleteResponse(ReadModel):
    upload_id: uuid.UUID
    dataset_id: uuid.UUID
    status: UploadStatus
    storage_uri: str = Field(description="s3://bucket/key recorded in datasets.")
    size_bytes: int = Field(ge=0)
    ingest_job_id: uuid.UUID | None = Field(
        default=None, description="Worker job that will parse the file into bronze."
    )


class UploadOut(ReadModel):
    upload_id: uuid.UUID
    filename: str
    status: UploadStatus
    size_bytes: int = Field(ge=0)
    created_at: datetime
    completed_at: datetime | None = None
    error_code: str | None = None


__all__ = [
    "UploadCompleteRequest",
    "UploadCompleteResponse",
    "UploadInitRequest",
    "UploadInitResponse",
    "UploadOut",
    "UploadPart",
]

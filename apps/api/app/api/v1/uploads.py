"""Presigned CSV/Parquet uploads (FR-013).

Bytes never pass through the API: the browser PUTs straight to object storage.
The API validates *claims* at init and *facts* (size + magic bytes) at complete.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.audit import record_audit
from app.core.errors import NotFound, PayloadTooLarge, ValidationProblem
from app.db.models import Dataset, Project, Source, Upload
from app.db.session import get_session
from app.deps import (
    AuthContext,
    client_ip,
    get_project_or_404,
    require_mutation,
    verify_csrf,
)
from app.services import storage
from dataloom_contracts._base import Layer, UploadStatus
from dataloom_contracts.uploads import (
    UploadCompleteRequest,
    UploadCompleteResponse,
    UploadInitRequest,
    UploadInitResponse,
    UploadOut,
    UploadPart,
)

router = APIRouter(tags=["uploads"])

_ALLOWED_EXT = {".csv": "csv", ".parquet": "parquet"}


def _extension(filename: str) -> str:
    lowered = filename.lower()
    for ext, kind in _ALLOWED_EXT.items():
        if lowered.endswith(ext):
            return kind
    raise ValidationProblem("Hanya file .csv atau .parquet yang diizinkan.")


def _looks_like_parquet(head: bytes) -> bool:
    """Parquet files start with the magic bytes ``PAR1``."""
    return head[:4] == b"PAR1"


def _looks_like_csv(head: bytes) -> bool:
    """Reject obvious binaries: CSV must be decodable text without NUL bytes."""
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        try:
            head.decode("latin-1")  # RFC 4180 allows legacy encodings
        except UnicodeDecodeError:
            return False
    return True


@router.post(
    "/projects/{project_id}/uploads",
    response_model=UploadInitResponse,
    status_code=201,
    dependencies=[Depends(verify_csrf)],
)
async def init_upload(
    payload: UploadInitRequest,
    request: Request,
    project: Project = Depends(get_project_or_404),
    ctx: AuthContext = Depends(require_mutation),
    session: AsyncSession = Depends(get_session),
) -> UploadInitResponse:
    settings = get_settings()
    kind = _extension(payload.filename)
    if payload.size_bytes > settings.UPLOAD_MAX_BYTES:
        raise PayloadTooLarge(
            f"Ukuran file melebihi batas {settings.UPLOAD_MAX_BYTES} byte (500 MB)."
        )

    if payload.source_id is not None:
        source = (
            await session.execute(
                select(Source).where(
                    Source.id == payload.source_id,
                    Source.project_id == project.id,
                    Source.org_id == project.org_id,
                )
            )
        ).scalar_one_or_none()
        if source is None:
            # 404, not 403/422: a foreign source id must be indistinguishable
            # from a missing one (FR-003).
            raise NotFound("Sumber tidak ditemukan.")
        if source.type != "csv":
            raise ValidationProblem("Upload hanya dapat dikaitkan ke sumber bertipe csv.")

    # Side effect check AFTER every authorisation check: we must never mint a
    # dataset row (and a presigned URL) for a request we are about to reject.
    upload_id = uuid.uuid4()
    key = storage.upload_key(
        org_id=project.org_id,
        project_id=project.id,
        upload_id=upload_id,
        filename=payload.filename,
    )

    # A dataset row is created up-front so lineage has an anchor (bronze).
    dataset = Dataset(
        org_id=project.org_id,
        project_id=project.id,
        source_id=payload.source_id,
        layer=Layer.BRONZE.value,
        name=payload.filename[:128],
        schema_json={},
        storage_uri=f"s3://{settings.S3_BUCKET}/{key}",
    )
    session.add(dataset)
    await session.flush()

    upload = Upload(
        id=upload_id,
        org_id=project.org_id,
        project_id=project.id,
        source_id=payload.source_id,
        dataset_id=dataset.id,
        filename=payload.filename[:255],
        content_type=payload.content_type,
        size_bytes=payload.size_bytes,
        storage_key=key,
        status=UploadStatus.INITIATED.value,
    )
    session.add(upload)
    await session.flush()

    url = await run_in_threadpool(
        storage.presign_put, key, payload.content_type, settings.S3_PRESIGN_EXPIRES_SECONDS
    )
    parts = [
        UploadPart(
            part_number=1,
            url=url,
            headers={"Content-Type": payload.content_type},
        )
    ]

    await record_audit(
        session,
        org_id=project.org_id,
        action="upload.init",
        target_type="upload",
        actor_id=ctx.user_id,
        actor_email=ctx.user.email,
        target_id=str(upload.id),
        metadata={"filename": upload.filename, "kind": kind},
        ip=client_ip(request),
    )

    return UploadInitResponse(
        upload_id=upload.id,
        dataset_id=dataset.id,
        storage_key=key,
        parts=parts,
        expires_at=datetime.now(tz=timezone.utc)
        + timedelta(seconds=settings.S3_PRESIGN_EXPIRES_SECONDS),
        max_bytes=settings.UPLOAD_MAX_BYTES,
    )


@router.post(
    "/projects/{project_id}/uploads/{upload_id}/complete",
    response_model=UploadCompleteResponse,
    dependencies=[Depends(verify_csrf)],
)
async def complete_upload(
    upload_id: uuid.UUID,
    payload: UploadCompleteRequest,
    request: Request,
    project: Project = Depends(get_project_or_404),
    ctx: AuthContext = Depends(require_mutation),
    session: AsyncSession = Depends(get_session),
) -> UploadCompleteResponse:
    upload = (
        await session.execute(
            select(Upload).where(
                Upload.id == upload_id,
                Upload.project_id == project.id,
                Upload.org_id == project.org_id,
            )
        )
    ).scalar_one_or_none()
    if upload is None:
        raise NotFound("Upload tidak ditemukan.")
    if upload.status == UploadStatus.COMPLETED.value:
        raise ValidationProblem("Upload ini sudah diselesaikan.")

    head = await run_in_threadpool(storage.head_object, upload.storage_key)
    if head is None:
        upload.status = UploadStatus.FAILED.value
        upload.error_code = "object_missing"
        await session.flush()
        raise ValidationProblem("File tidak ditemukan di object storage.")

    settings = get_settings()
    if head["size"] <= 0:
        upload.status = UploadStatus.FAILED.value
        upload.error_code = "empty_file"
        await session.flush()
        raise ValidationProblem("File kosong.")
    if head["size"] > settings.UPLOAD_MAX_BYTES:
        upload.status = UploadStatus.FAILED.value
        upload.error_code = "too_large"
        await session.flush()
        raise PayloadTooLarge("Ukuran file melebihi batas 500 MB.")

    head_bytes = await run_in_threadpool(storage.get_range, upload.storage_key, 8)
    is_parquet = upload.filename.lower().endswith(".parquet")
    valid = _looks_like_parquet(head_bytes) if is_parquet else _looks_like_csv(head_bytes)
    if not valid:
        upload.status = UploadStatus.FAILED.value
        upload.error_code = "magic_bytes_mismatch"
        await session.flush()
        await record_audit(
            session,
            org_id=project.org_id,
            action="upload.complete",
            target_type="upload",
            actor_id=ctx.user_id,
            actor_email=ctx.user.email,
            target_id=str(upload.id),
            metadata={"result": "rejected", "reason": "magic_bytes_mismatch"},
            ip=client_ip(request),
        )
        raise ValidationProblem(
            "Isi file tidak sesuai tipe yang diklaim (validasi magic bytes gagal)."
        )

    upload.status = UploadStatus.COMPLETED.value
    upload.size_bytes = head["size"]
    upload.completed_at = datetime.now(tz=timezone.utc)
    if upload.dataset_id is not None:
        dataset = (
            await session.execute(
                select(Dataset).where(
                    Dataset.id == upload.dataset_id, Dataset.org_id == project.org_id
                )
            )
        ).scalar_one_or_none()
        if dataset is not None:
            dataset.storage_uri = (
                f"s3://{settings.S3_BUCKET}/{upload.storage_key}"
            )
    await session.flush()

    await record_audit(
        session,
        org_id=project.org_id,
        action="upload.complete",
        target_type="upload",
        actor_id=ctx.user_id,
        actor_email=ctx.user.email,
        target_id=str(upload.id),
        metadata={"result": "accepted", "size_bytes": upload.size_bytes},
        ip=client_ip(request),
    )

    return UploadCompleteResponse(
        upload_id=upload.id,
        dataset_id=upload.dataset_id or uuid.uuid4(),
        status=UploadStatus.COMPLETED,
        storage_uri=f"s3://{settings.S3_BUCKET}/{upload.storage_key}",
        size_bytes=upload.size_bytes,
        ingest_job_id=None,  # the worker picks completed uploads up by polling
    )


@router.get("/projects/{project_id}/uploads", response_model=list[UploadOut])
async def list_uploads(
    project: Project = Depends(get_project_or_404),
    session: AsyncSession = Depends(get_session),
) -> list[UploadOut]:
    rows = (
        await session.execute(
            select(Upload)
            .where(Upload.project_id == project.id, Upload.org_id == project.org_id)
            .order_by(Upload.created_at.desc())
            .limit(100)
        )
    ).scalars().all()
    return [
        UploadOut(
            upload_id=u.id,
            filename=u.filename,
            status=u.status,
            size_bytes=u.size_bytes,
            created_at=u.created_at,
            completed_at=u.completed_at,
            error_code=u.error_code,
        )
        for u in rows
    ]

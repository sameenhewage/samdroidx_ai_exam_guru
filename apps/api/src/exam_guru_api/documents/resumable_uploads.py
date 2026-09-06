import hashlib
import hmac
import math
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import BinaryIO, cast
from uuid import UUID, uuid4

from anyio import to_thread
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.domain import Permission, Principal, authorize
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.documents.domain import (
    ExtractionStatus,
    UploadValidationError,
    validate_pdf_upload,
)
from exam_guru_api.documents.fidelity_models import SourceReadJobModel
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.documents.page_reading_jobs import queue_source_read
from exam_guru_api.documents.service import (
    SourceCurriculumInactiveError,
    SourceCurriculumNotFoundError,
    SourceDocumentService,
    SourceLearningScopeInactiveError,
    SourceLearningScopeMismatchError,
    SourceLearningScopeNotFoundError,
)
from exam_guru_api.documents.upload_models import SourceUploadChunkModel, SourceUploadSessionModel
from exam_guru_api.documents.upload_schemas import (
    MAX_UPLOAD_INTEGER,
    UPLOAD_CHUNK_BYTES,
    UPLOAD_RECEIPT_PAGE_SIZE,
    SourceUploadChunkPageResponse,
    SourceUploadChunkReceipt,
    SourceUploadCreateRequest,
    SourceUploadResponse,
    UploadStatus,
)
from exam_guru_api.infrastructure.object_storage import (
    ObjectAlreadyExistsError,
    ObjectStorage,
    ObjectStorageOperationError,
    StoredObject,
)
from exam_guru_api.infrastructure.private_artifacts import (
    PrivateArtifactError,
    PrivateUploadArtifacts,
)

_CHECKSUM = re.compile(r"^[0-9a-f]{64}$")
_QUOTA_LOCK = 3_600_001
_RECEIPT_BATCH_SIZE = 64


class ResumableUploadError(RuntimeError):
    def __init__(
        self, code: str, status_code: int = 409, *, next_offset: int | None = None
    ) -> None:
        self.code = code
        self.status_code = status_code
        self.next_offset = next_offset
        super().__init__(code)


class _LeaseLostError(RuntimeError):
    pass


def _check_execution_deadline(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise ResumableUploadError("source_upload_execution_budget_exhausted", 503)


class _DeadlineStream:
    def __init__(self, stream: BinaryIO, deadline: float | None) -> None:
        self._stream = stream
        self._deadline = deadline

    def read(self, size: int = -1) -> bytes:
        _check_execution_deadline(self._deadline)
        return self._stream.read(size)


@dataclass(frozen=True, slots=True)
class UploadLimits:
    max_total_bytes: int
    max_owner_staged_bytes: int
    max_staged_bytes: int
    max_active_sessions_per_owner: int = 8
    lease_seconds: int = 300

    def __post_init__(self) -> None:
        for value in (self.max_total_bytes, self.max_owner_staged_bytes, self.max_staged_bytes):
            if type(value) is not int or not 5 <= value <= MAX_UPLOAD_INTEGER:
                raise ValueError("upload byte budgets must be positive signed 64-bit integers")
        if (
            type(self.max_active_sessions_per_owner) is not int
            or not 1 <= self.max_active_sessions_per_owner <= MAX_UPLOAD_INTEGER
        ):
            raise ValueError("upload concurrency budget must be a positive integer")
        if type(self.lease_seconds) is not int or not 1 <= self.lease_seconds <= 86400:
            raise ValueError("upload lease must be between one second and one day")


@dataclass(frozen=True, slots=True)
class _Claim:
    upload_id: UUID
    owner_id: UUID
    token: UUID
    request: SourceUploadCreateRequest
    execution_deadline: float | None = None


class ResumableUploadService:
    def __init__(
        self,
        session: AsyncSession,
        object_storage: ObjectStorage,
        artifacts: PrivateUploadArtifacts,
        *,
        limits: UploadLimits,
    ) -> None:
        self._session = session
        self._storage = object_storage
        self._artifacts = artifacts
        self._limits = limits
        self._documents = SourceDocumentService(
            session, object_storage, max_upload_bytes=limits.max_total_bytes
        )

    async def create(
        self, request: SourceUploadCreateRequest, *, principal: Principal
    ) -> SourceUploadResponse:
        authorize(principal, Permission.SOURCE_WRITE)
        request = SourceUploadCreateRequest.model_validate(request)
        try:
            if request.request_id is not None:
                await self._session.execute(select(func.pg_advisory_xact_lock(_QUOTA_LOCK)))
                existing = await self._find_by_request(request.request_id, principal.subject_id)
                if existing is not None:
                    if self._creation_request(existing) != request:
                        raise ResumableUploadError("source_upload_request_conflict")
                    return await self._save(existing)
            if request.size_bytes > self._limits.max_total_bytes:
                raise ResumableUploadError("source_upload_too_large", 413)
            await to_thread.run_sync(self._artifacts.ensure_available)
            await self._validate_scope(request)
            await self._reserve_quota(principal.subject_id, request.size_bytes)
            now = datetime.now(UTC)
            upload = SourceUploadSessionModel(
                id=uuid4(),
                owner_id=principal.subject_id,
                request_id=request.request_id,
                filename=request.filename,
                size_bytes=request.size_bytes,
                document_type=request.document_type,
                intake_metadata=request.intake_metadata.model_dump(mode="json"),
                expected_checksum_sha256=request.expected_checksum_sha256,
                curriculum_version_id=request.curriculum_version_id,
                unit_id=request.unit_id,
                lesson_id=request.lesson_id,
                year=request.year,
                paper_code=request.paper_code,
                status=UploadStatus.UPLOADING,
                next_offset=0,
                verified_bytes=0,
                version=0,
                deduplicated=False,
                created_at=now,
                updated_at=now,
            )
            self._session.add(upload)
            self._audit(
                upload,
                "source_upload.created",
                {
                    "request_id": None if request.request_id is None else str(request.request_id),
                    "filename": request.filename,
                    "size_bytes": request.size_bytes,
                    "document_type": request.document_type.value,
                    "intake_metadata": upload.intake_metadata,
                    "expected_checksum_sha256": request.expected_checksum_sha256,
                },
            )
            return await self._save(upload)
        except PrivateArtifactError:
            await self._session.rollback()
            raise ResumableUploadError("source_upload_staging_unavailable", 503) from None
        except Exception:
            await self._session.rollback()
            raise

    async def get_by_request(
        self, request_id: UUID, *, principal: Principal
    ) -> SourceUploadResponse:
        authorize(principal, Permission.SOURCE_WRITE)
        if not isinstance(request_id, UUID):
            raise ResumableUploadError("invalid_upload_request_id", 422)
        try:
            upload = await self._find_by_request(request_id, principal.subject_id)
            if upload is None:
                raise ResumableUploadError("source_upload_not_found", 404)
            return await self._save(upload)
        except Exception:
            await self._session.rollback()
            raise

    async def _find_by_request(
        self, request_id: UUID, owner_id: UUID
    ) -> SourceUploadSessionModel | None:
        upload: SourceUploadSessionModel | None = await self._session.scalar(
            select(SourceUploadSessionModel)
            .where(
                SourceUploadSessionModel.owner_id == owner_id,
                SourceUploadSessionModel.request_id == request_id,
            )
            .execution_options(populate_existing=True)
        )
        return upload

    @staticmethod
    def _creation_request(upload: SourceUploadSessionModel) -> SourceUploadCreateRequest:
        return SourceUploadCreateRequest.model_validate(
            {name: getattr(upload, name) for name in SourceUploadCreateRequest.model_fields}
        )

    async def get(self, upload_id: UUID, *, principal: Principal) -> SourceUploadResponse:
        authorize(principal, Permission.SOURCE_WRITE)
        try:
            return await self._save(await self._load(upload_id, owner_id=principal.subject_id))
        except Exception:
            await self._session.rollback()
            raise

    async def list_chunks(
        self,
        upload_id: UUID,
        *,
        principal: Principal,
        offset: int = 0,
        limit: int = UPLOAD_RECEIPT_PAGE_SIZE,
    ) -> SourceUploadChunkPageResponse:
        authorize(principal, Permission.SOURCE_WRITE)
        if (
            type(offset) is not int
            or not 0 <= offset <= MAX_UPLOAD_INTEGER
            or offset % UPLOAD_CHUNK_BYTES
        ):
            raise ResumableUploadError("invalid_upload_offset", 422)
        if type(limit) is not int or not 1 <= limit <= UPLOAD_RECEIPT_PAGE_SIZE:
            raise ResumableUploadError("invalid_upload_receipt_limit", 422)
        try:
            upload = await self._load(upload_id, owner_id=principal.subject_id)
            rows = tuple(
                await self._session.scalars(
                    select(SourceUploadChunkModel)
                    .where(
                        SourceUploadChunkModel.upload_id == upload_id,
                        SourceUploadChunkModel.offset >= offset,
                        SourceUploadChunkModel.offset < upload.next_offset,
                    )
                    .order_by(SourceUploadChunkModel.offset)
                    .limit(limit + 1)
                )
            )
            receipts = tuple(SourceUploadChunkReceipt.model_validate(row) for row in rows[:limit])
            response = SourceUploadChunkPageResponse(
                upload_id=upload_id,
                next_offset=upload.next_offset,
                receipts=receipts,
                next_receipt_offset=(receipts[-1].offset + receipts[-1].size_bytes)
                if len(rows) > limit
                else None,
            )
            await self._session.commit()
            return response
        except Exception:
            await self._session.rollback()
            raise

    async def append_chunk(
        self,
        upload_id: UUID,
        *,
        principal: Principal,
        offset: int,
        data: bytes,
        checksum_sha256: str | None = None,
    ) -> SourceUploadResponse:
        authorize(principal, Permission.SOURCE_WRITE)
        if type(offset) is not int or not 0 <= offset <= MAX_UPLOAD_INTEGER:
            raise ResumableUploadError("invalid_upload_offset", 422)
        if not isinstance(data, bytes):
            raise ResumableUploadError("invalid_upload_chunk", 422)
        if len(data) > UPLOAD_CHUNK_BYTES:
            raise ResumableUploadError("source_upload_chunk_too_large", 413)
        if checksum_sha256 is not None and not _CHECKSUM.fullmatch(checksum_sha256):
            raise ResumableUploadError("invalid_upload_checksum", 422)
        try:
            upload = await self._load(upload_id, owner_id=principal.subject_id, lock=True)
            if (
                offset > upload.next_offset
                or offset >= upload.size_bytes
                or offset % UPLOAD_CHUNK_BYTES
            ):
                raise ResumableUploadError(
                    "source_upload_offset_conflict", next_offset=upload.next_offset
                )
            expected = min(UPLOAD_CHUNK_BYTES, upload.size_bytes - offset)
            if len(data) != expected:
                raise ResumableUploadError("source_upload_chunk_size_mismatch", 422)
            checksum = (await to_thread.run_sync(hashlib.sha256, data)).hexdigest()
            if checksum_sha256 is not None and not hmac.compare_digest(checksum, checksum_sha256):
                raise ResumableUploadError("source_upload_chunk_checksum_mismatch", 422)
            if offset == 0:
                try:
                    validate_pdf_upload(
                        filename=upload.filename,
                        content_type="application/pdf",
                        data=data[:5],
                        max_bytes=5,
                    )
                except UploadValidationError as error:
                    raise ResumableUploadError(error.violation.value, 422) from None
            if offset < upload.next_offset:
                receipt = await self._session.get(SourceUploadChunkModel, (upload_id, offset))
                if (
                    receipt is None
                    or receipt.size_bytes != len(data)
                    or not hmac.compare_digest(receipt.checksum_sha256, checksum)
                ):
                    raise ResumableUploadError(
                        "source_upload_chunk_conflict", next_offset=upload.next_offset
                    )
                await to_thread.run_sync(
                    partial(
                        self._artifacts.hash_chunk,
                        upload_id,
                        offset,
                        size=len(data),
                        checksum_sha256=checksum,
                    )
                )
                return await self._save(upload)
            if upload.status is not UploadStatus.UPLOADING:
                raise ResumableUploadError("source_upload_state_conflict")
            await to_thread.run_sync(
                partial(
                    self._artifacts.put_chunk, upload_id, offset, data, checksum_sha256=checksum
                )
            )
            self._session.add(
                SourceUploadChunkModel(
                    upload_id=upload_id,
                    offset=offset,
                    size_bytes=len(data),
                    checksum_sha256=checksum,
                )
            )
            await self._session.flush()
            upload.next_offset += len(data)
            self._touch(upload)
            return await self._save(upload)
        except PrivateArtifactError as error:
            await self._session.rollback()
            if error.code == "staged_chunk_mismatch":
                raise ResumableUploadError("source_upload_chunk_conflict") from None
            raise ResumableUploadError("source_upload_staging_unavailable", 503) from None
        except Exception:
            await self._session.rollback()
            raise

    async def request_completion(
        self, upload_id: UUID, *, principal: Principal, expected_version: int | None = None
    ) -> SourceUploadResponse:
        authorize(principal, Permission.SOURCE_WRITE)
        if expected_version is not None and (
            type(expected_version) is not int or not 0 <= expected_version <= MAX_UPLOAD_INTEGER
        ):
            raise ResumableUploadError("invalid_upload_version", 422)
        try:
            upload = await self._load(upload_id, owner_id=principal.subject_id, lock=True)
            if upload.status is not UploadStatus.UPLOADING:
                return await self._save(upload)
            if expected_version is not None and upload.version != expected_version:
                raise ResumableUploadError(
                    "source_upload_version_conflict", next_offset=upload.next_offset
                )
            if upload.next_offset != upload.size_bytes:
                raise ResumableUploadError(
                    "source_upload_incomplete", next_offset=upload.next_offset
                )
            upload.status = UploadStatus.PENDING
            self._touch(upload)
            self._audit(upload, "source_upload.queued", {"size_bytes": upload.size_bytes})
            return await self._save(upload)
        except Exception:
            await self._session.rollback()
            raise

    async def pending_upload_ids(
        self, *, limit: int = 100, outbox_min_age_seconds: int = 0
    ) -> tuple[UUID, ...]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("upload recovery batch must be between 1 and 1000")
        if type(outbox_min_age_seconds) is not int or not 0 <= outbox_min_age_seconds <= 3600:
            raise ValueError("upload recovery minimum age must be between 0 and 3600 seconds")
        now = datetime.now(UTC)
        ids = tuple(
            await self._session.scalars(
                select(SourceUploadSessionModel.id)
                .where(
                    or_(
                        (SourceUploadSessionModel.status == UploadStatus.PENDING)
                        & (
                            SourceUploadSessionModel.updated_at
                            <= now - timedelta(seconds=outbox_min_age_seconds)
                        ),
                        (SourceUploadSessionModel.status == UploadStatus.FINALIZING)
                        & (SourceUploadSessionModel.lease_expires_at <= now),
                    )
                )
                .order_by(SourceUploadSessionModel.updated_at, SourceUploadSessionModel.id)
                .limit(limit)
            )
        )
        await self._session.commit()
        return ids

    async def finalize(
        self, upload_id: UUID, *, execution_deadline: float | None = None
    ) -> SourceUploadResponse:
        if execution_deadline is not None and (
            type(execution_deadline) not in {int, float} or not math.isfinite(execution_deadline)
        ):
            raise ValueError("upload execution deadline must be finite")
        claim, view = await self._claim(upload_id, execution_deadline=execution_deadline)
        if claim is None:
            return view
        try:
            checksum = await self._hash_upload(claim)
            _check_execution_deadline(claim.execution_deadline)
            stored = await to_thread.run_sync(partial(self._publish, claim, checksum))
            _check_execution_deadline(claim.execution_deadline)
            if (
                stored.key != self._object_key(checksum)
                or stored.checksum_sha256 != checksum
                or stored.size != claim.request.size_bytes
            ):
                raise ResumableUploadError("source_upload_storage_mismatch", 503)
            for attempt in range(2):
                try:
                    return await self._finish(claim, checksum)
                except IntegrityError:
                    await self._session.rollback()
                    if attempt or await self._documents._find_by_checksum(checksum) is None:
                        raise
            raise RuntimeError("source upload finalization did not finish")
        except _LeaseLostError:
            await self._session.rollback()
            return await self._save(await self._load(upload_id))
        except PrivateArtifactError as error:
            return await self._record_failure(
                claim, error.code, retryable=error.code == "private_artifact_unavailable"
            )
        except ObjectStorageOperationError:
            code = (
                "source_upload_execution_budget_exhausted"
                if claim.execution_deadline is not None
                and time.monotonic() >= claim.execution_deadline
                else "source_upload_storage_unavailable"
            )
            return await self._record_failure(claim, code, retryable=True)
        except ObjectAlreadyExistsError:
            return await self._record_failure(
                claim, "source_upload_storage_conflict", retryable=False
            )
        except ResumableUploadError as error:
            return await self._record_failure(claim, error.code, retryable=error.status_code >= 500)
        except Exception:
            return await self._record_failure(
                claim, "source_upload_finalize_retryable", retryable=True
            )
        except BaseException:
            await self._session.rollback()
            raise

    async def _claim(
        self, upload_id: UUID, *, execution_deadline: float | None = None
    ) -> tuple[_Claim | None, SourceUploadResponse]:
        try:
            upload = await self._load(upload_id, lock=True)
            now = datetime.now(UTC)
            if upload.status is UploadStatus.UPLOADING:
                raise ResumableUploadError(
                    "source_upload_incomplete", next_offset=upload.next_offset
                )
            if upload.status in {UploadStatus.COMPLETED, UploadStatus.FAILED} or (
                upload.status is UploadStatus.FINALIZING
                and upload.lease_expires_at is not None
                and upload.lease_expires_at > now
            ):
                return None, await self._save(upload)
            token = uuid4()
            request = self._creation_request(upload)
            claim = _Claim(upload.id, upload.owner_id, token, request, execution_deadline)
            upload.status = UploadStatus.FINALIZING
            upload.lease_token = token
            upload.lease_expires_at = now + timedelta(seconds=self._limits.lease_seconds)
            upload.verified_bytes = 0
            upload.checksum_sha256 = None
            upload.failure_code = None
            self._touch(upload)
            return claim, await self._save(upload)
        except Exception:
            await self._session.rollback()
            raise

    async def _hash_upload(self, claim: _Claim) -> str:
        digest = hashlib.sha256()
        verified = 0
        while verified < claim.request.size_bytes:
            _check_execution_deadline(claim.execution_deadline)
            rows = await self._session.scalars(
                select(SourceUploadChunkModel)
                .where(
                    SourceUploadChunkModel.upload_id == claim.upload_id,
                    SourceUploadChunkModel.offset >= verified,
                )
                .order_by(SourceUploadChunkModel.offset)
                .limit(_RECEIPT_BATCH_SIZE)
            )
            receipts = tuple((row.offset, row.size_bytes, row.checksum_sha256) for row in rows)
            await self._session.commit()
            if not receipts:
                raise ResumableUploadError("source_upload_receipt_missing", 422)
            for offset, size, checksum in receipts:
                _check_execution_deadline(claim.execution_deadline)
                if offset != verified or size != min(
                    UPLOAD_CHUNK_BYTES, claim.request.size_bytes - verified
                ):
                    raise ResumableUploadError("source_upload_receipt_mismatch", 422)
                prefix = await to_thread.run_sync(
                    partial(
                        self._artifacts.hash_chunk,
                        claim.upload_id,
                        offset,
                        size=size,
                        checksum_sha256=checksum,
                        digest=digest,
                    )
                )
                if offset == 0:
                    try:
                        validate_pdf_upload(
                            filename=claim.request.filename,
                            content_type="application/pdf",
                            data=prefix,
                            max_bytes=5,
                        )
                    except UploadValidationError as error:
                        raise ResumableUploadError(error.violation.value, 422) from None
                verified += size
                await self._renew(claim, verified)
        checksum = digest.hexdigest()
        if claim.request.expected_checksum_sha256 is not None and not hmac.compare_digest(
            checksum, claim.request.expected_checksum_sha256
        ):
            raise ResumableUploadError("source_upload_checksum_mismatch", 422)
        return checksum

    def _publish(self, claim: _Claim, checksum: str) -> StoredObject:
        with self._artifacts.open_upload(
            claim.upload_id, expected_size=claim.request.size_bytes
        ) as stream:
            return self._storage.put_stream_immutable(
                self._object_key(checksum),
                cast(BinaryIO, _DeadlineStream(stream, claim.execution_deadline)),
                content_type="application/pdf",
                expected_size=claim.request.size_bytes,
            )

    async def _renew(self, claim: _Claim, verified: int) -> None:
        _check_execution_deadline(claim.execution_deadline)
        upload = await self._load_claim(claim)
        upload.verified_bytes = verified
        upload.lease_expires_at = datetime.now(UTC) + timedelta(seconds=self._limits.lease_seconds)
        self._touch(upload)
        await self._save(upload)

    async def _finish(self, claim: _Claim, checksum: str) -> SourceUploadResponse:
        _check_execution_deadline(claim.execution_deadline)
        upload = await self._load_claim(claim)
        document = await self._documents._find_by_checksum(checksum)
        deduplicated = document is not None
        likely_duplicate_id = None
        if document is None:
            await self._validate_scope(claim.request)
            likely = await self._documents._find_likely_metadata_duplicate(
                filename=claim.request.filename,
                curriculum_version_id=claim.request.curriculum_version_id,
                document_type=claim.request.document_type,
                year=claim.request.year,
                paper_code=claim.request.paper_code,
            )
            likely_duplicate_id = None if likely is None else likely.id
            document = self._new_document(claim, checksum, likely_duplicate_id)
            await self._session.flush()
        elif (
            document.size_bytes != claim.request.size_bytes
            or document.object_key != self._object_key(checksum)
            or document.content_type != "application/pdf"
        ):
            raise ResumableUploadError("source_document_integrity_conflict")
        upload.status = UploadStatus.COMPLETED
        upload.document_id = document.id
        upload.checksum_sha256 = checksum
        upload.verified_bytes = upload.size_bytes
        upload.deduplicated = deduplicated
        upload.likely_metadata_duplicate_of_id = likely_duplicate_id
        upload.lease_token = None
        upload.lease_expires_at = None
        upload.failure_code = None
        self._touch(upload)
        self._audit(
            upload,
            "source_upload.completed",
            {
                "document_id": str(document.id),
                "checksum_sha256": checksum,
                "size_bytes": upload.size_bytes,
                "deduplicated": deduplicated,
            },
        )
        _check_execution_deadline(claim.execution_deadline)
        if deduplicated:
            return await self._save(upload)
        await self._session.flush()
        response = SourceUploadResponse.model_validate(upload)
        read_job = await queue_source_read(self._session, document.id, actor_id=claim.owner_id)
        return response.model_copy(update={"source_read_job_id": read_job.id})

    def _new_document(
        self, claim: _Claim, checksum: str, likely_duplicate_id: UUID | None
    ) -> SourceDocumentModel:
        request = claim.request
        intake = request.intake_metadata.model_dump(mode="json")
        document = SourceDocumentModel(
            id=uuid4(),
            checksum_sha256=checksum,
            object_key=self._object_key(checksum),
            original_filename=request.filename,
            content_type="application/pdf",
            size_bytes=request.size_bytes,
            document_type=request.document_type,
            extraction_status=ExtractionStatus.UPLOADED,
            extraction_attempt_count=0,
            curriculum_version_id=request.curriculum_version_id,
            unit_id=request.unit_id,
            lesson_id=request.lesson_id,
            active_for_ai=True,
            removal_reason=None,
            removed_by=None,
            removed_at=None,
            metadata_scope_version=0,
            intake_metadata=intake,
            metadata_review_required=True,
            year=request.year,
            paper_code=request.paper_code,
            created_by=claim.owner_id,
            updated_by=claim.owner_id,
        )
        self._session.add(document)
        self._session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=claim.owner_id,
                action="source_document.uploaded",
                resource_type="source_document",
                resource_id=document.id,
                payload={
                    "checksum_sha256": checksum,
                    "document_type": request.document_type.value,
                    "original_filename": request.filename,
                    "size_bytes": request.size_bytes,
                    "intake_metadata": intake,
                    "metadata_review_required": True,
                    "curriculum_version_id": self._documents._optional_uuid(
                        request.curriculum_version_id
                    ),
                    "unit_id": self._documents._optional_uuid(request.unit_id),
                    "lesson_id": self._documents._optional_uuid(request.lesson_id),
                    "likely_metadata_duplicate_of_id": self._documents._optional_uuid(
                        likely_duplicate_id
                    ),
                },
            )
        )
        return document

    async def _record_failure(
        self, claim: _Claim, code: str, *, retryable: bool
    ) -> SourceUploadResponse:
        await self._session.rollback()
        try:
            upload = await self._load_claim(claim)
        except _LeaseLostError:
            return await self._save(await self._load(claim.upload_id))
        upload.status = UploadStatus.PENDING if retryable else UploadStatus.FAILED
        upload.failure_code = code
        upload.lease_token = None
        upload.lease_expires_at = None
        if retryable:
            upload.verified_bytes = 0
            upload.checksum_sha256 = None
        self._touch(upload)
        self._audit(
            upload,
            "source_upload.retry_pending" if retryable else "source_upload.failed",
            {"failure_code": code},
        )
        return await self._save(upload)

    async def _reserve_quota(self, owner_id: UUID, size: int) -> None:
        await self._session.execute(select(func.pg_advisory_xact_lock(_QUOTA_LOCK)))
        total = int(
            await self._session.scalar(
                select(func.coalesce(func.sum(SourceUploadSessionModel.size_bytes), 0))
            )
            or 0
        )
        owner_total = int(
            await self._session.scalar(
                select(func.coalesce(func.sum(SourceUploadSessionModel.size_bytes), 0)).where(
                    SourceUploadSessionModel.owner_id == owner_id
                )
            )
            or 0
        )
        active = int(
            await self._session.scalar(
                select(func.count())
                .select_from(SourceUploadSessionModel)
                .where(
                    SourceUploadSessionModel.owner_id == owner_id,
                    SourceUploadSessionModel.status.in_(
                        [UploadStatus.UPLOADING, UploadStatus.PENDING, UploadStatus.FINALIZING]
                    ),
                )
            )
            or 0
        )
        if (
            total + size > self._limits.max_staged_bytes
            or owner_total + size > self._limits.max_owner_staged_bytes
            or active >= self._limits.max_active_sessions_per_owner
        ):
            raise ResumableUploadError("source_upload_quota_exceeded", 409)

    async def _validate_scope(self, request: SourceUploadCreateRequest) -> None:
        try:
            await self._documents._validate_learning_scope(
                request.curriculum_version_id, request.unit_id, request.lesson_id
            )
            if request.curriculum_version_id is not None:
                await self._documents._validate_confirmation_scope(request.curriculum_version_id)
        except SourceCurriculumNotFoundError:
            raise ResumableUploadError("curriculum_version_not_found", 404) from None
        except SourceCurriculumInactiveError:
            raise ResumableUploadError("curriculum_version_inactive") from None
        except SourceLearningScopeNotFoundError:
            raise ResumableUploadError("learning_scope_not_found", 404) from None
        except SourceLearningScopeInactiveError:
            raise ResumableUploadError("learning_scope_inactive") from None
        except SourceLearningScopeMismatchError:
            raise ResumableUploadError("learning_scope_mismatch", 422) from None

    async def _load(
        self, upload_id: UUID, *, owner_id: UUID | None = None, lock: bool = False
    ) -> SourceUploadSessionModel:
        query = (
            select(SourceUploadSessionModel)
            .where(SourceUploadSessionModel.id == upload_id)
            .execution_options(populate_existing=True)
        )
        if owner_id is not None:
            query = query.where(SourceUploadSessionModel.owner_id == owner_id)
        if lock:
            query = query.with_for_update()
        upload = await self._session.scalar(query)
        if upload is None:
            raise ResumableUploadError("source_upload_not_found", 404)
        return upload

    async def _load_claim(self, claim: _Claim) -> SourceUploadSessionModel:
        upload = await self._load(claim.upload_id, lock=True)
        if (
            upload.status is not UploadStatus.FINALIZING
            or upload.lease_token != claim.token
            or upload.lease_expires_at is None
            or upload.lease_expires_at <= datetime.now(UTC)
        ):
            raise _LeaseLostError
        return upload

    async def _save(self, upload: SourceUploadSessionModel) -> SourceUploadResponse:
        await self._session.flush()
        response = SourceUploadResponse.model_validate(upload)
        if response.document_id is not None:
            job_id = await self._session.scalar(
                select(SourceReadJobModel.id)
                .where(
                    SourceReadJobModel.document_id == response.document_id,
                    SourceReadJobModel.page_number.is_(None),
                )
                .order_by(SourceReadJobModel.created_at, SourceReadJobModel.id)
                .limit(1)
            )
            response = response.model_copy(update={"source_read_job_id": job_id})
        await self._session.commit()
        return response

    @staticmethod
    def _touch(upload: SourceUploadSessionModel) -> None:
        upload.version += 1
        upload.updated_at = datetime.now(UTC)

    def _audit(
        self, upload: SourceUploadSessionModel, action: str, payload: dict[str, object]
    ) -> None:
        self._session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=upload.owner_id,
                action=action,
                resource_type="source_upload",
                resource_id=upload.id,
                payload=payload,
            )
        )

    @staticmethod
    def _object_key(checksum: str) -> str:
        return f"sources/{checksum[:2]}/{checksum}.pdf"

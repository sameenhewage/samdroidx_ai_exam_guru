import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4, uuid5

from anyio import to_thread
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.documents.domain import ExtractionStatus
from exam_guru_api.documents.extraction import (
    ExtractionError,
    InvalidExtractionTransitionError,
    NativeExtractionResult,
    transition_extraction_status,
)
from exam_guru_api.documents.models import (
    ExtractedBlockModel,
    SourceDocumentModel,
    SourcePageModel,
)
from exam_guru_api.infrastructure.object_storage import ObjectStorage


class NativeDocumentExtractor(Protocol):
    def extract(self, data: bytes) -> NativeExtractionResult: ...


class ExtractionDocumentNotFoundError(LookupError):
    def __init__(self, document_id: UUID) -> None:
        self.document_id = document_id
        super().__init__(str(document_id))


class ExtractionSourceIntegrityError(RuntimeError):
    def __init__(self, document_id: UUID) -> None:
        self.document_id = document_id
        super().__init__(f"source object does not match document {document_id}")


class ReviewNotActiveError(RuntimeError):
    pass


class SourcePageNotFoundError(LookupError):
    pass


class ExtractedBlockNotFoundError(LookupError):
    pass


class ConcurrentReviewVersionError(RuntimeError):
    def __init__(self, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"expected review version {expected}, found {actual}")


@dataclass(frozen=True, slots=True)
class ExtractionPersistenceResult:
    document_id: UUID
    status: ExtractionStatus
    page_count: int
    block_count: int
    deduplicated: bool


@dataclass(frozen=True, slots=True)
class _ExtractionClaim:
    object_key: str
    checksum_sha256: str
    size_bytes: int


_FINAL_EXTRACTION_STATUSES = frozenset(
    {
        ExtractionStatus.EXTRACTED,
        ExtractionStatus.IN_REVIEW,
        ExtractionStatus.TRUSTED,
    }
)


class DocumentExtractionService:
    def __init__(
        self,
        session: AsyncSession,
        object_storage: ObjectStorage,
        extractor: NativeDocumentExtractor,
    ) -> None:
        self._session = session
        self._object_storage = object_storage
        self._extractor = extractor

    async def queue_extraction(
        self,
        document_id: UUID,
        *,
        actor_id: UUID,
    ) -> ExtractionPersistenceResult:
        document = await self._get_locked_document(document_id)
        if document.extraction_status not in {
            ExtractionStatus.UPLOADED,
            ExtractionStatus.FAILED,
        }:
            raise InvalidExtractionTransitionError(
                document.extraction_status,
                ExtractionStatus.EXTRACTION_PENDING,
            )
        document.extraction_status = transition_extraction_status(
            document.extraction_status,
            ExtractionStatus.EXTRACTION_PENDING,
        )
        document.extraction_attempt_count += 1
        document.extraction_started_at = datetime.now(UTC)
        document.extraction_completed_at = None
        document.extraction_failure_code = None
        self._clear_result_metadata(document)
        document.updated_by = actor_id
        await self._session.flush()
        await self._delete_persisted_result(document.id)
        self._audit(
            document,
            actor_id=actor_id,
            action="source_document.extraction_queued",
            payload={"attempt": document.extraction_attempt_count},
        )
        await self._session.commit()
        return self._result_from_document(document, deduplicated=False)

    async def extract_native(
        self,
        document_id: UUID,
        *,
        actor_id: UUID,
        preclaimed: bool = False,
    ) -> ExtractionPersistenceResult:
        claim_or_result = (
            await self._preclaimed(document_id)
            if preclaimed
            else await self._claim(document_id, actor_id=actor_id)
        )
        if isinstance(claim_or_result, ExtractionPersistenceResult):
            return claim_or_result

        try:
            data = await to_thread.run_sync(
                self._object_storage.get_bytes,
                claim_or_result.object_key,
            )
            if (
                len(data) != claim_or_result.size_bytes
                or hashlib.sha256(data).hexdigest() != claim_or_result.checksum_sha256
            ):
                raise ExtractionSourceIntegrityError(document_id)
            extraction = await to_thread.run_sync(self._extractor.extract, data)
            return await self._persist_result(
                document_id,
                extraction,
                actor_id=actor_id,
            )
        except Exception as error:
            await self._session.rollback()
            await self._record_failure(document_id, error, actor_id=actor_id)
            raise

    async def begin_review(
        self,
        document_id: UUID,
        *,
        actor_id: UUID,
    ) -> ExtractionPersistenceResult:
        document = await self._get_locked_document(document_id)
        if document.extraction_status is ExtractionStatus.IN_REVIEW:
            return self._result_from_document(document, deduplicated=True)

        document.extraction_status = transition_extraction_status(
            document.extraction_status,
            ExtractionStatus.IN_REVIEW,
        )
        document.updated_by = actor_id
        self._audit(
            document,
            actor_id=actor_id,
            action="source_document.extraction_review_started",
            payload={"status": ExtractionStatus.IN_REVIEW.value},
        )
        await self._session.commit()
        return self._result_from_document(document, deduplicated=False)

    async def list_pages(self, document_id: UUID) -> Sequence[SourcePageModel]:
        if await self._session.get(SourceDocumentModel, document_id) is None:
            raise ExtractionDocumentNotFoundError(document_id)
        return (
            await self._session.scalars(
                select(SourcePageModel)
                .where(SourcePageModel.source_document_id == document_id)
                .order_by(SourcePageModel.page_number)
            )
        ).all()

    async def list_blocks(
        self,
        document_id: UUID,
        *,
        page_number: int,
    ) -> Sequence[ExtractedBlockModel]:
        if await self._session.get(SourceDocumentModel, document_id) is None:
            raise ExtractionDocumentNotFoundError(document_id)
        return (
            await self._session.scalars(
                select(ExtractedBlockModel)
                .where(
                    ExtractedBlockModel.source_document_id == document_id,
                    ExtractedBlockModel.page_number == page_number,
                )
                .order_by(ExtractedBlockModel.reading_order)
            )
        ).all()

    async def correct_page(
        self,
        document_id: UUID,
        *,
        page_number: int,
        reviewed_text: str,
        expected_version: int,
        actor_id: UUID,
    ) -> SourcePageModel:
        document = await self._get_locked_document(document_id)
        if document.extraction_status is not ExtractionStatus.IN_REVIEW:
            raise ReviewNotActiveError(document_id)
        page = await self._session.scalar(
            select(SourcePageModel)
            .where(
                SourcePageModel.source_document_id == document_id,
                SourcePageModel.page_number == page_number,
            )
            .with_for_update()
        )
        if page is None:
            raise SourcePageNotFoundError(page_number)
        if page.version != expected_version:
            raise ConcurrentReviewVersionError(expected_version, page.version)
        page.reviewed_text = reviewed_text
        page.version += 1
        page.updated_by = actor_id
        self._audit(
            document,
            actor_id=actor_id,
            action="source_document.page_corrected",
            payload={"page_number": page_number, "version": page.version},
        )
        await self._session.commit()
        await self._session.refresh(page)
        return page

    async def correct_block(
        self,
        document_id: UUID,
        *,
        page_number: int,
        reading_order: int,
        reviewed_text: str,
        expected_version: int,
        actor_id: UUID,
    ) -> ExtractedBlockModel:
        document = await self._get_locked_document(document_id)
        if document.extraction_status is not ExtractionStatus.IN_REVIEW:
            raise ReviewNotActiveError(document_id)
        block = await self._session.scalar(
            select(ExtractedBlockModel)
            .where(
                ExtractedBlockModel.source_document_id == document_id,
                ExtractedBlockModel.page_number == page_number,
                ExtractedBlockModel.reading_order == reading_order,
            )
            .with_for_update()
        )
        if block is None:
            raise ExtractedBlockNotFoundError(reading_order)
        if block.version != expected_version:
            raise ConcurrentReviewVersionError(expected_version, block.version)
        block.reviewed_text = reviewed_text
        block.version += 1
        block.updated_by = actor_id
        self._audit(
            document,
            actor_id=actor_id,
            action="source_document.block_corrected",
            payload={
                "page_number": page_number,
                "reading_order": reading_order,
                "version": block.version,
            },
        )
        await self._session.commit()
        await self._session.refresh(block)
        return block

    async def trust_document(
        self,
        document_id: UUID,
        *,
        actor_id: UUID,
    ) -> ExtractionPersistenceResult:
        document = await self._get_locked_document(document_id)
        if document.extraction_status is ExtractionStatus.TRUSTED:
            return self._result_from_document(document, deduplicated=True)

        document.extraction_status = transition_extraction_status(
            document.extraction_status,
            ExtractionStatus.TRUSTED,
        )
        document.updated_by = actor_id
        self._audit(
            document,
            actor_id=actor_id,
            action="source_document.trusted",
            payload={"status": ExtractionStatus.TRUSTED.value},
        )
        await self._session.commit()
        return self._result_from_document(document, deduplicated=False)

    async def _preclaimed(
        self,
        document_id: UUID,
    ) -> _ExtractionClaim | ExtractionPersistenceResult:
        document = await self._get_locked_document(document_id)
        if document.extraction_status in _FINAL_EXTRACTION_STATUSES:
            return self._result_from_document(document, deduplicated=True)
        if document.extraction_status is not ExtractionStatus.EXTRACTION_PENDING:
            raise InvalidExtractionTransitionError(
                document.extraction_status,
                ExtractionStatus.EXTRACTION_PENDING,
            )
        return _ExtractionClaim(
            object_key=document.object_key,
            checksum_sha256=document.checksum_sha256,
            size_bytes=document.size_bytes,
        )

    async def _claim(
        self,
        document_id: UUID,
        *,
        actor_id: UUID,
    ) -> _ExtractionClaim | ExtractionPersistenceResult:
        document = await self._get_locked_document(document_id)
        if document.extraction_status in _FINAL_EXTRACTION_STATUSES:
            return self._result_from_document(document, deduplicated=True)

        document.extraction_status = transition_extraction_status(
            document.extraction_status,
            ExtractionStatus.EXTRACTION_PENDING,
        )
        document.extraction_attempt_count += 1
        document.extraction_started_at = datetime.now(UTC)
        document.extraction_completed_at = None
        document.extraction_failure_code = None
        self._clear_result_metadata(document)
        document.updated_by = actor_id
        await self._session.flush()
        await self._delete_persisted_result(document.id)
        self._audit(
            document,
            actor_id=actor_id,
            action="source_document.extraction_started",
            payload={"attempt": document.extraction_attempt_count},
        )
        await self._session.commit()
        return _ExtractionClaim(
            object_key=document.object_key,
            checksum_sha256=document.checksum_sha256,
            size_bytes=document.size_bytes,
        )

    async def _persist_result(
        self,
        document_id: UUID,
        extraction: NativeExtractionResult,
        *,
        actor_id: UUID,
    ) -> ExtractionPersistenceResult:
        document = await self._get_locked_document(document_id)
        if document.extraction_status in _FINAL_EXTRACTION_STATUSES:
            return self._result_from_document(document, deduplicated=True)
        if document.extraction_status is ExtractionStatus.FAILED:
            document.extraction_status = transition_extraction_status(
                document.extraction_status,
                ExtractionStatus.EXTRACTION_PENDING,
            )
            document.extraction_completed_at = None
            document.extraction_failure_code = None
            document.updated_by = actor_id
            await self._session.flush()

        transition_extraction_status(
            document.extraction_status,
            ExtractionStatus.EXTRACTED,
        )
        await self._delete_persisted_result(document.id)

        pages: list[SourcePageModel] = []
        blocks: list[ExtractedBlockModel] = []
        for extracted_page in extraction.pages:
            page_id = uuid5(document.id, f"source-page:{extracted_page.page_number}")
            pages.append(
                SourcePageModel(
                    id=page_id,
                    source_document_id=document.id,
                    page_number=extracted_page.page_number,
                    extractor=extraction.engine,
                    extractor_version=extraction.engine_version,
                    raw_text=extracted_page.text,
                    reviewed_text=None,
                    character_count=len(extracted_page.text),
                    block_count=len(extracted_page.blocks),
                    created_by=actor_id,
                    updated_by=actor_id,
                )
            )
            blocks.extend(
                ExtractedBlockModel(
                    id=uuid5(
                        document.id,
                        "source-page:"
                        f"{extracted_block.page_number}:block:{extracted_block.reading_order}",
                    ),
                    source_page_id=page_id,
                    source_document_id=document.id,
                    page_number=extracted_block.page_number,
                    reading_order=extracted_block.reading_order,
                    extractor=extraction.engine,
                    extractor_version=extraction.engine_version,
                    bbox_x0=extracted_block.bbox[0],
                    bbox_y0=extracted_block.bbox[1],
                    bbox_x1=extracted_block.bbox[2],
                    bbox_y1=extracted_block.bbox[3],
                    raw_text=extracted_block.text,
                    reviewed_text=None,
                    character_count=len(extracted_block.text),
                    created_by=actor_id,
                    updated_by=actor_id,
                )
                for extracted_block in extracted_page.blocks
            )

        self._session.add_all(pages)
        await self._session.flush()
        self._session.add_all(blocks)
        await self._session.flush()

        document.extractor = extraction.engine
        document.extractor_version = extraction.engine_version
        document.extracted_page_count = extraction.page_count
        document.extracted_block_count = len(blocks)
        document.extracted_character_count = extraction.character_count
        document.native_text_page_ratio = extraction.native_text_page_ratio
        document.needs_ocr = extraction.needs_ocr
        document.extraction_failure_code = None
        document.extraction_completed_at = datetime.now(UTC)
        document.extraction_status = transition_extraction_status(
            document.extraction_status,
            ExtractionStatus.EXTRACTED,
        )
        document.updated_by = actor_id
        self._audit(
            document,
            actor_id=actor_id,
            action="source_document.extracted",
            payload={
                "attempt": document.extraction_attempt_count,
                "block_count": len(blocks),
                "character_count": extraction.character_count,
                "extractor": extraction.engine,
                "extractor_version": extraction.engine_version,
                "needs_ocr": extraction.needs_ocr,
                "native_text_page_ratio": extraction.native_text_page_ratio,
                "page_count": extraction.page_count,
            },
        )
        await self._session.commit()
        return self._result_from_document(document, deduplicated=False)

    async def _record_failure(
        self,
        document_id: UUID,
        error: Exception,
        *,
        actor_id: UUID,
    ) -> None:
        document = await self._get_locked_document(document_id)
        if document.extraction_status is not ExtractionStatus.EXTRACTION_PENDING:
            return

        failure_code = self._failure_code(error)
        self._clear_result_metadata(document)
        document.extraction_status = transition_extraction_status(
            document.extraction_status,
            ExtractionStatus.FAILED,
        )
        document.extraction_failure_code = failure_code
        document.extraction_completed_at = datetime.now(UTC)
        document.updated_by = actor_id
        self._audit(
            document,
            actor_id=actor_id,
            action="source_document.extraction_failed",
            payload={
                "attempt": document.extraction_attempt_count,
                "failure_code": failure_code,
            },
        )
        await self._session.commit()

    async def _get_locked_document(self, document_id: UUID) -> SourceDocumentModel:
        document = await self._session.get(
            SourceDocumentModel,
            document_id,
            with_for_update=True,
        )
        if document is None:
            raise ExtractionDocumentNotFoundError(document_id)
        return document

    async def _delete_persisted_result(self, document_id: UUID) -> None:
        await self._session.execute(
            delete(ExtractedBlockModel).where(ExtractedBlockModel.source_document_id == document_id)
        )
        await self._session.execute(
            delete(SourcePageModel).where(SourcePageModel.source_document_id == document_id)
        )

    @staticmethod
    def _clear_result_metadata(document: SourceDocumentModel) -> None:
        document.extractor = None
        document.extractor_version = None
        document.extracted_page_count = None
        document.extracted_block_count = None
        document.extracted_character_count = None
        document.native_text_page_ratio = None
        document.needs_ocr = None

    @staticmethod
    def _failure_code(error: Exception) -> str:
        if isinstance(error, ExtractionError):
            return error.violation.value
        if isinstance(error, ExtractionSourceIntegrityError):
            return "source_object_integrity"
        return "unexpected_error"

    @staticmethod
    def _result_from_document(
        document: SourceDocumentModel,
        *,
        deduplicated: bool,
    ) -> ExtractionPersistenceResult:
        return ExtractionPersistenceResult(
            document_id=document.id,
            status=document.extraction_status,
            page_count=document.extracted_page_count or 0,
            block_count=document.extracted_block_count or 0,
            deduplicated=deduplicated,
        )

    def _audit(
        self,
        document: SourceDocumentModel,
        *,
        actor_id: UUID,
        action: str,
        payload: dict[str, object],
    ) -> None:
        self._session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=actor_id,
                action=action,
                resource_type="source_document",
                resource_id=document.id,
                payload=payload,
            )
        )

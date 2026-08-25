import asyncio
import hashlib
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from typing import cast
from uuid import UUID

import pymupdf
import pytest
from sqlalchemy import func, select
from sqlalchemy import text as sql_text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.documents.domain import ExtractionStatus, SourceDocumentType
from exam_guru_api.documents.extraction import (
    ExtractedBlock,
    ExtractedPage,
    ExtractionError,
    ExtractionViolation,
    NativeExtractionResult,
    PyMuPdfExtractor,
)
from exam_guru_api.documents.extraction_outbox import (
    ExtractionRecoveryPolicy,
    ExtractionRecoveryService,
)
from exam_guru_api.documents.extraction_service import (
    ConcurrentReviewVersionError,
    DocumentExtractionService,
    ExtractionPersistenceResult,
    OCRPipelineError,
)
from exam_guru_api.documents.models import (
    ExtractedBlockModel,
    SourceDocumentModel,
    SourcePageModel,
)
from exam_guru_api.documents.ocr import OCRBlock, OCRPage, OCRRequest, OCRResult
from exam_guru_api.infrastructure.migrations import (
    assert_database_schema_current,
    upgrade_database,
)
from exam_guru_api.infrastructure.object_storage import (
    ObjectPage,
    ObjectTagMutation,
    StoredObject,
)

PGVECTOR_IMAGE = "pgvector/pgvector:0.8.6-pg18-trixie"
UPLOAD_ACTOR_ID = UUID(int=8_000)
EXTRACTION_ACTOR_ID = UUID(int=8_001)
REVIEW_ACTOR_ID = UUID(int=8_002)


def pdf_bytes(*page_texts: str) -> bytes:
    document = pymupdf.open()
    for text in page_texts:
        page = document.new_page()
        if text:
            page.insert_text((72, 72), text)
    data = cast(bytes, document.tobytes(garbage=4, deflate=True))
    document.close()
    return data


class MemoryObjectStorage:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects
        self.reads = 0

    def put_immutable(self, key: str, data: bytes, *, content_type: str) -> StoredObject:
        raise AssertionError((key, data, content_type))

    def get_bytes(self, key: str) -> bytes:
        self.reads += 1
        return self.objects[key]

    def list_source_objects(
        self,
        *,
        max_keys: int,
        continuation_token: str | None = None,
    ) -> ObjectPage:
        raise AssertionError((max_keys, continuation_token))

    def merge_reconciliation_tags(
        self,
        key: str,
        *,
        candidate_detected_at: datetime | None,
    ) -> ObjectTagMutation:
        raise AssertionError((key, candidate_detected_at))

    def close(self) -> None:
        return None


class CountingExtractor:
    def __init__(self) -> None:
        self.calls = 0
        self._delegate = PyMuPdfExtractor(max_pages=10)

    def extract(self, data: bytes) -> NativeExtractionResult:
        self.calls += 1
        return self._delegate.extract(data)


class FailOnceExtractor(CountingExtractor):
    def extract(self, data: bytes) -> NativeExtractionResult:
        self.calls += 1
        if self.calls == 1:
            raise ExtractionError(ExtractionViolation.MALFORMED_PDF)
        return self._delegate.extract(data)


class BarrierExtractor(CountingExtractor):
    def __init__(self) -> None:
        super().__init__()
        self._barrier = Barrier(2)

    def extract(self, data: bytes) -> NativeExtractionResult:
        self.calls += 1
        self._barrier.wait(timeout=10)
        return self._delegate.extract(data)


class BarrierRecoveryDispatcher:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, UUID]] = []
        self._barrier = Barrier(2)

    def dispatch(self, document_id: UUID, *, actor_id: UUID) -> str:
        self.calls.append((document_id, actor_id))
        self._barrier.wait(timeout=10)
        return f"recovery-{document_id}"


class StaticNativeResultExtractor:
    def __init__(self, result: NativeExtractionResult) -> None:
        self.result = result
        self.calls = 0

    def extract(self, _data: bytes) -> NativeExtractionResult:
        self.calls += 1
        return self.result


class RecordingFakeOCR:
    def __init__(self, result: OCRResult) -> None:
        self.result = result
        self.requests: list[OCRRequest] = []

    def extract(self, request: OCRRequest) -> OCRResult:
        self.requests.append(request)
        return self.result


class FailOnceOCR(RecordingFakeOCR):
    def extract(self, request: OCRRequest) -> OCRResult:
        self.requests.append(request)
        if len(self.requests) == 1:
            raise RuntimeError("provider output and secrets must never be persisted")
        return self.result


class BarrierFakeOCR(RecordingFakeOCR):
    def __init__(self, result: OCRResult) -> None:
        super().__init__(result)
        self._barrier = Barrier(2)

    def extract(self, request: OCRRequest) -> OCRResult:
        self.requests.append(request)
        self._barrier.wait(timeout=10)
        return self.result


def mixed_native_result() -> NativeExtractionResult:
    first_block = ExtractedBlock(
        page_number=1,
        reading_order=0,
        bbox=(1.0, 2.0, 30.0, 40.0),
        text="native first",
    )
    pages = (
        ExtractedPage(page_number=1, text="native first", blocks=(first_block,)),
        ExtractedPage(page_number=2, text="", blocks=()),
    )
    return NativeExtractionResult(
        engine="pymupdf",
        engine_version="1.28.2",
        pages=pages,
        page_count=2,
        character_count=len("native first"),
        native_text_page_ratio=0.5,
        needs_ocr=True,
        config={"max_pages": 10, "sort_blocks": True},
    )


def fake_ocr_result() -> OCRResult:
    return OCRResult(
        engine="fixture-ocr",
        engine_version="2.1",
        config={"dpi": 300, "language": "sin+eng", "output_format": "tsv"},
        pages=(
            OCRPage(
                page_number=2,
                text="OCR second",
                blocks=(
                    OCRBlock(
                        page_number=2,
                        reading_order=0,
                        text="OCR second",
                        bbox=None,
                        confidence=0.91,
                    ),
                ),
                confidence=0.91,
            ),
        ),
    )


@pytest.fixture(scope="module")
def extraction_database_url() -> Iterator[str]:
    with PostgresContainer(
        image=PGVECTOR_IMAGE,
        username="exam_guru",
        password="extraction-only",
        dbname="exam_guru_extraction_test",
        driver="asyncpg",
    ) as postgres:
        database_url = postgres.get_connection_url()
        upgrade_database(database_url)
        assert_database_schema_current(database_url)
        yield database_url


async def add_uploaded_document(
    session: AsyncSession,
    *,
    document_id: UUID,
    filename: str,
    data: bytes,
) -> SourceDocumentModel:
    checksum = hashlib.sha256(data).hexdigest()
    document = SourceDocumentModel(
        id=document_id,
        checksum_sha256=checksum,
        object_key=f"sources/{checksum[:2]}/{checksum}.pdf",
        original_filename=filename,
        content_type="application/pdf",
        size_bytes=len(data),
        document_type=SourceDocumentType.SYLLABUS,
        extraction_status=ExtractionStatus.UPLOADED,
        curriculum_version_id=None,
        year=None,
        paper_code=None,
        created_by=UPLOAD_ACTOR_ID,
        updated_by=UPLOAD_ACTOR_ID,
    )
    session.add(document)
    await session.commit()
    return document


@pytest.mark.integration
def test_native_extraction_persists_ordered_provenance_metrics_and_is_idempotent(
    extraction_database_url: str,
) -> None:
    async def exercise() -> None:
        data = pdf_bytes("Grade 5 first page", "Second page marking guidance")
        document_id = UUID(int=81_001)
        engine = create_async_engine(extraction_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        extractor = CountingExtractor()

        async with sessions() as session:
            document = await add_uploaded_document(
                session,
                document_id=document_id,
                filename="native-source.pdf",
                data=data,
            )
            storage = MemoryObjectStorage({document.object_key: data})
            service = DocumentExtractionService(session, storage, extractor)

            first = await service.extract_native(document_id, actor_id=EXTRACTION_ACTOR_ID)
            retried = await service.extract_native(document_id, actor_id=EXTRACTION_ACTOR_ID)
            review = await service.begin_review(document_id, actor_id=REVIEW_ACTOR_ID)

            page = await session.scalar(
                select(SourcePageModel)
                .where(SourcePageModel.source_document_id == document_id)
                .order_by(SourcePageModel.page_number)
                .limit(1)
            )
            block = await session.scalar(
                select(ExtractedBlockModel)
                .where(ExtractedBlockModel.source_document_id == document_id)
                .order_by(
                    ExtractedBlockModel.page_number,
                    ExtractedBlockModel.reading_order,
                )
                .limit(1)
            )
            assert page is not None
            assert block is not None
            corrected_page = await service.correct_page(
                document_id,
                page_number=1,
                reviewed_text="Reviewed Grade 5 first page",
                expected_version=0,
                actor_id=REVIEW_ACTOR_ID,
            )
            corrected_block = await service.correct_block(
                document_id,
                page_number=1,
                reading_order=0,
                reviewed_text="Reviewed Grade 5 first page",
                expected_version=0,
                actor_id=REVIEW_ACTOR_ID,
            )
            with pytest.raises(ConcurrentReviewVersionError):
                await service.correct_page(
                    document_id,
                    page_number=1,
                    reviewed_text="Stale overwrite",
                    expected_version=0,
                    actor_id=REVIEW_ACTOR_ID,
                )
            trusted = await service.trust_document(document_id, actor_id=REVIEW_ACTOR_ID)

        async with sessions() as session:
            persisted_document = await session.get(SourceDocumentModel, document_id)
            pages = list(
                await session.scalars(
                    select(SourcePageModel)
                    .where(SourcePageModel.source_document_id == document_id)
                    .order_by(SourcePageModel.page_number)
                )
            )
            blocks = list(
                await session.scalars(
                    select(ExtractedBlockModel)
                    .where(ExtractedBlockModel.source_document_id == document_id)
                    .order_by(
                        ExtractedBlockModel.page_number,
                        ExtractedBlockModel.reading_order,
                    )
                )
            )
            actions = list(
                await session.scalars(
                    select(AdminAuditEventModel.action)
                    .where(AdminAuditEventModel.resource_id == document_id)
                    .order_by(AdminAuditEventModel.created_at)
                )
            )

        await engine.dispose()

        assert first.status is ExtractionStatus.EXTRACTED
        assert first.deduplicated is False
        assert retried.status is ExtractionStatus.EXTRACTED
        assert retried.deduplicated is True
        assert review.status is ExtractionStatus.IN_REVIEW
        assert trusted.status is ExtractionStatus.TRUSTED
        assert corrected_page.version == 1
        assert corrected_block.version == 1
        assert extractor.calls == 1
        assert storage.reads == 1

        assert persisted_document is not None
        assert persisted_document.extraction_status is ExtractionStatus.TRUSTED
        assert persisted_document.extraction_attempt_count == 1
        assert persisted_document.extractor == "pymupdf"
        assert persisted_document.extractor_version
        assert persisted_document.extracted_page_count == 2
        assert persisted_document.extracted_block_count == len(blocks) == 2
        assert persisted_document.extracted_character_count == sum(
            len(page.raw_text) for page in pages
        )
        assert persisted_document.native_text_page_ratio == 1.0
        assert persisted_document.needs_ocr is False
        assert persisted_document.ocr_page_count == 0
        assert persisted_document.extraction_config == {
            "mode": "native",
            "native": {
                "config": {"max_pages": 10, "sort_blocks": True},
                "engine": "pymupdf",
                "version": persisted_document.extractor_version,
            },
        }
        assert persisted_document.extraction_failure_code is None
        assert persisted_document.extraction_started_at is not None
        assert persisted_document.extraction_completed_at is not None

        assert [page.page_number for page in pages] == [1, 2]
        assert all(page.source_document_id == document_id for page in pages)
        assert all(page.extractor == "pymupdf" for page in pages)
        assert all(page.extractor_version == persisted_document.extractor_version for page in pages)
        assert all(
            page.extraction_config == {"max_pages": 10, "sort_blocks": True} for page in pages
        )
        assert all(page.confidence is None for page in pages)
        assert all(page.character_count == len(page.raw_text) for page in pages)
        assert all(page.block_count == 1 for page in pages)
        assert [page.version for page in pages] == [1, 0]
        assert pages[0].raw_text == "Grade 5 first page"
        assert pages[0].reviewed_text == "Reviewed Grade 5 first page"

        assert [(block.page_number, block.reading_order) for block in blocks] == [(1, 0), (2, 0)]
        assert all(block.source_document_id == document_id for block in blocks)
        assert all(block.source_page_id == pages[block.page_number - 1].id for block in blocks)
        assert all(block.extractor == "pymupdf" for block in blocks)
        assert all(
            block.extraction_config == {"max_pages": 10, "sort_blocks": True} for block in blocks
        )
        assert all(block.confidence is None for block in blocks)
        assert all(block.character_count == len(block.raw_text) for block in blocks)
        assert [block.version for block in blocks] == [1, 0]
        assert all(block.bbox is not None and len(block.bbox) == 4 for block in blocks)
        assert blocks[0].raw_text == "Grade 5 first page"
        assert blocks[0].reviewed_text == "Reviewed Grade 5 first page"
        assert actions == [
            "source_document.extraction_started",
            "source_document.extracted",
            "source_document.extraction_review_started",
            "source_document.page_corrected",
            "source_document.block_corrected",
            "source_document.trusted",
        ]

    asyncio.run(exercise())


@pytest.mark.integration
def test_concurrent_native_extraction_serializes_to_one_persisted_result(
    extraction_database_url: str,
) -> None:
    async def exercise() -> None:
        data = pdf_bytes("Concurrent extraction")
        document_id = UUID(int=81_005)
        engine = create_async_engine(extraction_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        extractor = BarrierExtractor()

        async with sessions() as session:
            document = await add_uploaded_document(
                session,
                document_id=document_id,
                filename="concurrent-source.pdf",
                data=data,
            )
            object_key = document.object_key

        async def extract_once(actor_id: UUID) -> ExtractionPersistenceResult:
            async with sessions() as session:
                return await DocumentExtractionService(
                    session,
                    MemoryObjectStorage({object_key: data}),
                    extractor,
                ).extract_native(document_id, actor_id=actor_id)

        results = await asyncio.gather(
            extract_once(UUID(int=8_011)),
            extract_once(UUID(int=8_012)),
        )

        async with sessions() as session:
            persisted_document = await session.get(SourceDocumentModel, document_id)
            page_count = await session.scalar(
                select(func.count(SourcePageModel.id)).where(
                    SourcePageModel.source_document_id == document_id
                )
            )
            block_count = await session.scalar(
                select(func.count(ExtractedBlockModel.id)).where(
                    ExtractedBlockModel.source_document_id == document_id
                )
            )
            actions = list(
                await session.scalars(
                    select(AdminAuditEventModel.action).where(
                        AdminAuditEventModel.resource_id == document_id
                    )
                )
            )
        await engine.dispose()

        assert all(result.status is ExtractionStatus.EXTRACTED for result in results)
        assert sorted(result.deduplicated for result in results) == [False, True]
        assert extractor.calls == 2
        assert persisted_document is not None
        assert persisted_document.extraction_attempt_count == 1
        assert page_count == 1
        assert block_count == 1
        assert actions.count("source_document.extraction_started") == 1
        assert actions.count("source_document.extracted") == 1

    asyncio.run(exercise())


@pytest.mark.integration
def test_failed_extraction_is_persisted_and_retry_recovers_without_duplicate_blocks(
    extraction_database_url: str,
) -> None:
    async def exercise() -> None:
        data = pdf_bytes("Recovery page")
        document_id = UUID(int=81_002)
        engine = create_async_engine(extraction_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        extractor = FailOnceExtractor()

        async with sessions() as session:
            document = await add_uploaded_document(
                session,
                document_id=document_id,
                filename="recovery-source.pdf",
                data=data,
            )
            storage = MemoryObjectStorage({document.object_key: data})
            service = DocumentExtractionService(session, storage, extractor)

            with pytest.raises(ExtractionError) as raised:
                await service.extract_native(document_id, actor_id=EXTRACTION_ACTOR_ID)
            assert raised.value.violation is ExtractionViolation.MALFORMED_PDF

        async with sessions() as session:
            failed = await session.get(SourceDocumentModel, document_id)
            failed_page_count = await session.scalar(
                select(func.count(SourcePageModel.id)).where(
                    SourcePageModel.source_document_id == document_id
                )
            )
            assert failed is not None
            assert failed.extraction_status is ExtractionStatus.FAILED
            assert failed.extraction_attempt_count == 1
            assert failed.extraction_failure_code == "malformed_pdf"
            assert failed_page_count == 0

        async with sessions() as session:
            storage = MemoryObjectStorage({failed.object_key: data})
            service = DocumentExtractionService(session, storage, extractor)
            recovered = await service.extract_native(document_id, actor_id=EXTRACTION_ACTOR_ID)
            retried = await service.extract_native(document_id, actor_id=EXTRACTION_ACTOR_ID)

        async with sessions() as session:
            recovered_document = await session.get(SourceDocumentModel, document_id)
            page_count = await session.scalar(
                select(func.count(SourcePageModel.id)).where(
                    SourcePageModel.source_document_id == document_id
                )
            )
            block_count = await session.scalar(
                select(func.count(ExtractedBlockModel.id)).where(
                    ExtractedBlockModel.source_document_id == document_id
                )
            )
            actions = list(
                await session.scalars(
                    select(AdminAuditEventModel.action)
                    .where(AdminAuditEventModel.resource_id == document_id)
                    .order_by(AdminAuditEventModel.created_at)
                )
            )
        await engine.dispose()

        assert recovered.status is ExtractionStatus.EXTRACTED
        assert recovered.deduplicated is False
        assert retried.deduplicated is True
        assert extractor.calls == 2
        assert recovered_document is not None
        assert recovered_document.extraction_status is ExtractionStatus.EXTRACTED
        assert recovered_document.extraction_attempt_count == 2
        assert recovered_document.extraction_failure_code is None
        assert page_count == 1
        assert block_count == 1
        assert actions == [
            "source_document.extraction_started",
            "source_document.extraction_failed",
            "source_document.extraction_started",
            "source_document.extracted",
        ]

    asyncio.run(exercise())


@pytest.mark.integration
def test_pending_retry_replaces_partial_rows_from_an_interrupted_attempt(
    extraction_database_url: str,
) -> None:
    async def exercise() -> None:
        data = pdf_bytes("Fresh extraction")
        document_id = UUID(int=81_004)
        stale_page_id = UUID(int=81_104)
        engine = create_async_engine(extraction_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)

        async with sessions() as session:
            document = await add_uploaded_document(
                session,
                document_id=document_id,
                filename="interrupted-source.pdf",
                data=data,
            )
            document.extraction_status = ExtractionStatus.EXTRACTION_PENDING
            document.extraction_attempt_count = 1
            document.extraction_started_at = datetime.now(UTC)
            document.updated_by = EXTRACTION_ACTOR_ID
            await session.flush()
            session.add(
                SourcePageModel(
                    id=stale_page_id,
                    source_document_id=document_id,
                    page_number=1,
                    extractor="interrupted-extractor",
                    extractor_version="0.1",
                    raw_text="stale text",
                    reviewed_text=None,
                    character_count=len("stale text"),
                    block_count=1,
                    created_by=EXTRACTION_ACTOR_ID,
                    updated_by=EXTRACTION_ACTOR_ID,
                )
            )
            await session.flush()
            session.add(
                ExtractedBlockModel(
                    id=UUID(int=81_204),
                    source_page_id=stale_page_id,
                    source_document_id=document_id,
                    page_number=1,
                    reading_order=0,
                    extractor="interrupted-extractor",
                    extractor_version="0.1",
                    bbox_x0=0.0,
                    bbox_y0=0.0,
                    bbox_x1=1.0,
                    bbox_y1=1.0,
                    raw_text="stale text",
                    reviewed_text=None,
                    character_count=len("stale text"),
                    created_by=EXTRACTION_ACTOR_ID,
                    updated_by=EXTRACTION_ACTOR_ID,
                )
            )
            await session.commit()

        async with sessions() as session:
            storage = MemoryObjectStorage({document.object_key: data})
            result = await DocumentExtractionService(
                session,
                storage,
                CountingExtractor(),
            ).extract_native(document_id, actor_id=EXTRACTION_ACTOR_ID)

        async with sessions() as session:
            persisted_document = await session.get(SourceDocumentModel, document_id)
            pages = list(
                await session.scalars(
                    select(SourcePageModel).where(SourcePageModel.source_document_id == document_id)
                )
            )
            blocks = list(
                await session.scalars(
                    select(ExtractedBlockModel).where(
                        ExtractedBlockModel.source_document_id == document_id
                    )
                )
            )
        await engine.dispose()

        assert result.status is ExtractionStatus.EXTRACTED
        assert persisted_document is not None
        assert persisted_document.extraction_attempt_count == 1
        assert len(pages) == len(blocks) == 1
        assert pages[0].id != stale_page_id
        assert pages[0].raw_text == "Fresh extraction"
        assert blocks[0].raw_text == "Fresh extraction"

    asyncio.run(exercise())


@pytest.mark.integration
def test_database_rejects_noncontiguous_block_reading_order(
    extraction_database_url: str,
) -> None:
    async def exercise() -> None:
        data = pdf_bytes("Reading order")
        document_id = UUID(int=81_006)
        page_id = UUID(int=81_106)
        engine = create_async_engine(extraction_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)

        async with sessions() as session:
            document = await add_uploaded_document(
                session,
                document_id=document_id,
                filename="invalid-order-source.pdf",
                data=data,
            )
            document.extraction_status = ExtractionStatus.EXTRACTION_PENDING
            document.extraction_attempt_count = 1
            document.extraction_started_at = datetime.now(UTC)
            document.updated_by = EXTRACTION_ACTOR_ID
            await session.flush()
            session.add(
                SourcePageModel(
                    id=page_id,
                    source_document_id=document_id,
                    page_number=1,
                    extractor="fixture",
                    extractor_version="1.0",
                    raw_text="text",
                    reviewed_text=None,
                    character_count=4,
                    block_count=1,
                    created_by=EXTRACTION_ACTOR_ID,
                    updated_by=EXTRACTION_ACTOR_ID,
                )
            )
            await session.flush()
            session.add(
                ExtractedBlockModel(
                    id=UUID(int=81_206),
                    source_page_id=page_id,
                    source_document_id=document_id,
                    page_number=1,
                    reading_order=1,
                    extractor="fixture",
                    extractor_version="1.0",
                    bbox_x0=0.0,
                    bbox_y0=0.0,
                    bbox_x1=1.0,
                    bbox_y1=1.0,
                    raw_text="text",
                    reviewed_text=None,
                    character_count=4,
                    created_by=EXTRACTION_ACTOR_ID,
                    updated_by=EXTRACTION_ACTOR_ID,
                )
            )
            await session.flush()
            document.extractor = "fixture"
            document.extractor_version = "1.0"
            document.extracted_page_count = 1
            document.extracted_block_count = 1
            document.extracted_character_count = 4
            document.native_text_page_ratio = 1.0
            document.needs_ocr = False
            document.ocr_page_count = 0
            document.extraction_config = {}
            document.extraction_completed_at = datetime.now(UTC)
            document.extraction_status = ExtractionStatus.EXTRACTED
            with pytest.raises(IntegrityError):
                await session.commit()

        await engine.dispose()

    asyncio.run(exercise())


@pytest.mark.integration
def test_database_rejects_invalid_state_skips_and_provenance_mutation(
    extraction_database_url: str,
) -> None:
    async def exercise() -> None:
        data = pdf_bytes("Immutable provenance")
        document_id = UUID(int=81_003)
        engine = create_async_engine(extraction_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)

        async with sessions() as session:
            document = await add_uploaded_document(
                session,
                document_id=document_id,
                filename="immutable-source.pdf",
                data=data,
            )
            document.extraction_status = ExtractionStatus.IN_REVIEW
            with pytest.raises(IntegrityError):
                await session.commit()

        async with sessions() as session:
            persisted_document = await session.get(SourceDocumentModel, document_id)
            assert persisted_document is not None
            storage = MemoryObjectStorage({persisted_document.object_key: data})
            await DocumentExtractionService(
                session,
                storage,
                CountingExtractor(),
            ).extract_native(document_id, actor_id=EXTRACTION_ACTOR_ID)

        async with sessions() as session:
            page = await session.scalar(
                select(SourcePageModel).where(SourcePageModel.source_document_id == document_id)
            )
            assert page is not None
            page.page_number = 99
            with pytest.raises(IntegrityError):
                await session.commit()

        async with sessions() as session:
            block = await session.scalar(
                select(ExtractedBlockModel).where(
                    ExtractedBlockModel.source_document_id == document_id
                )
            )
            assert block is not None
            block.raw_text = "silently rewritten"
            with pytest.raises(IntegrityError):
                await session.commit()

        await engine.dispose()

    asyncio.run(exercise())


@pytest.mark.integration
def test_hybrid_ocr_pipeline_persists_exact_provenance_audit_and_idempotency(
    extraction_database_url: str,
) -> None:
    async def exercise() -> None:
        data = pdf_bytes("storage identity fixture", "second")
        document_id = UUID(int=81_007)
        engine = create_async_engine(extraction_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        extractor = StaticNativeResultExtractor(mixed_native_result())
        ocr = RecordingFakeOCR(fake_ocr_result())

        async with sessions() as session:
            document = await add_uploaded_document(
                session,
                document_id=document_id,
                filename="hybrid-source.pdf",
                data=data,
            )
            storage = MemoryObjectStorage({document.object_key: data})
            service = DocumentExtractionService(session, storage, extractor, ocr_port=ocr)
            first = await service.extract_native(document_id, actor_id=EXTRACTION_ACTOR_ID)
            duplicate = await service.extract_native(document_id, actor_id=EXTRACTION_ACTOR_ID)

        async with sessions() as session:
            persisted_document = await session.get(SourceDocumentModel, document_id)
            pages = list(
                await session.scalars(
                    select(SourcePageModel)
                    .where(SourcePageModel.source_document_id == document_id)
                    .order_by(SourcePageModel.page_number)
                )
            )
            blocks = list(
                await session.scalars(
                    select(ExtractedBlockModel)
                    .where(ExtractedBlockModel.source_document_id == document_id)
                    .order_by(ExtractedBlockModel.page_number, ExtractedBlockModel.reading_order)
                )
            )
            extracted_event = await session.scalar(
                select(AdminAuditEventModel).where(
                    AdminAuditEventModel.resource_id == document_id,
                    AdminAuditEventModel.action == "source_document.extracted",
                )
            )
        await engine.dispose()

        assert first.deduplicated is False
        assert duplicate.deduplicated is True
        assert extractor.calls == 1
        assert len(ocr.requests) == 1
        assert ocr.requests[0].source_document_id == str(document_id)
        assert ocr.requests[0].source_checksum_sha256 == hashlib.sha256(data).hexdigest()
        assert ocr.requests[0].content == data
        assert ocr.requests[0].page_numbers == (2,)

        assert persisted_document is not None
        assert persisted_document.extractor == "hybrid"
        assert persisted_document.extractor_version == "1"
        assert persisted_document.ocr_page_count == 1
        assert persisted_document.needs_ocr is False
        assert persisted_document.extraction_config == {
            "mode": "hybrid",
            "native": {
                "config": {"max_pages": 10, "sort_blocks": True},
                "engine": "pymupdf",
                "version": "1.28.2",
            },
            "ocr": {
                "config": {"dpi": 300, "language": "sin+eng", "output_format": "tsv"},
                "engine": "fixture-ocr",
                "version": "2.1",
            },
            "ocr_page_numbers": [2],
        }
        assert [(item.page_number, item.extractor) for item in pages] == [
            (1, "pymupdf"),
            (2, "fixture-ocr"),
        ]
        assert pages[0].extraction_config == {"max_pages": 10, "sort_blocks": True}
        assert pages[0].confidence is None
        assert pages[1].extraction_config == {
            "dpi": 300,
            "language": "sin+eng",
            "output_format": "tsv",
        }
        assert pages[1].confidence == pytest.approx(0.91)
        assert [(item.page_number, item.extractor) for item in blocks] == [
            (1, "pymupdf"),
            (2, "fixture-ocr"),
        ]
        assert blocks[1].bbox is None
        assert blocks[1].confidence == pytest.approx(0.91)
        assert blocks[1].extraction_config == pages[1].extraction_config

        assert extracted_event is not None
        assert extracted_event.payload["mode"] == "hybrid"
        assert extracted_event.payload["ocr_page_count"] == 1
        assert extracted_event.payload["ocr_engine"] == "fixture-ocr"
        assert extracted_event.payload["ocr_engine_version"] == "2.1"
        assert "OCR second" not in repr(extracted_event.payload)
        assert "sin+eng" not in repr(extracted_event.payload)

    asyncio.run(exercise())


@pytest.mark.integration
def test_duplicate_preclaimed_workers_converge_but_can_repeat_ocr_provider_work(
    extraction_database_url: str,
) -> None:
    async def exercise() -> None:
        data = pdf_bytes("storage identity fixture", "second")
        document_id = UUID(int=81_010)
        engine = create_async_engine(extraction_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        extractor = StaticNativeResultExtractor(mixed_native_result())
        ocr = BarrierFakeOCR(fake_ocr_result())

        async with sessions() as session:
            document = await add_uploaded_document(
                session,
                document_id=document_id,
                filename="concurrent-hybrid-source.pdf",
                data=data,
            )
            object_key = document.object_key
            await DocumentExtractionService(
                session,
                MemoryObjectStorage({object_key: data}),
                extractor,
                ocr_port=ocr,
            ).queue_extraction(document_id, actor_id=EXTRACTION_ACTOR_ID)

        async def extract_once(actor_id: UUID) -> ExtractionPersistenceResult:
            async with sessions() as session:
                return await DocumentExtractionService(
                    session,
                    MemoryObjectStorage({object_key: data}),
                    extractor,
                    ocr_port=ocr,
                ).extract_native(document_id, actor_id=actor_id, preclaimed=True)

        results = await asyncio.gather(
            extract_once(UUID(int=8_021)),
            extract_once(UUID(int=8_022)),
        )

        async with sessions() as session:
            persisted_document = await session.get(SourceDocumentModel, document_id)
            page_count = await session.scalar(
                select(func.count(SourcePageModel.id)).where(
                    SourcePageModel.source_document_id == document_id
                )
            )
            block_count = await session.scalar(
                select(func.count(ExtractedBlockModel.id)).where(
                    ExtractedBlockModel.source_document_id == document_id
                )
            )
            extracted_event_count = await session.scalar(
                select(func.count(AdminAuditEventModel.id)).where(
                    AdminAuditEventModel.resource_id == document_id,
                    AdminAuditEventModel.action == "source_document.extracted",
                )
            )
        await engine.dispose()

        assert all(result.status is ExtractionStatus.EXTRACTED for result in results)
        assert sorted(result.deduplicated for result in results) == [False, True]
        assert extractor.calls == 2
        assert len(ocr.requests) == 2
        assert persisted_document is not None
        assert persisted_document.extraction_attempt_count == 1
        assert persisted_document.ocr_page_count == 1
        assert page_count == block_count == 2
        assert extracted_event_count == 1

    asyncio.run(exercise())


@pytest.mark.integration
def test_ocr_failure_rolls_back_all_rows_and_manual_retry_recovers(
    extraction_database_url: str,
) -> None:
    async def exercise() -> None:
        data = pdf_bytes("storage identity fixture", "second")
        document_id = UUID(int=81_008)
        engine = create_async_engine(extraction_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        extractor = StaticNativeResultExtractor(mixed_native_result())
        ocr = FailOnceOCR(fake_ocr_result())

        async with sessions() as session:
            document = await add_uploaded_document(
                session,
                document_id=document_id,
                filename="ocr-retry-source.pdf",
                data=data,
            )
            storage = MemoryObjectStorage({document.object_key: data})
            with pytest.raises(OCRPipelineError) as raised:
                await DocumentExtractionService(
                    session,
                    storage,
                    extractor,
                    ocr_port=ocr,
                ).extract_native(document_id, actor_id=EXTRACTION_ACTOR_ID)
            assert raised.value.failure_code == "ocr_process"

        async with sessions() as session:
            failed = await session.get(SourceDocumentModel, document_id)
            page_count = await session.scalar(
                select(func.count(SourcePageModel.id)).where(
                    SourcePageModel.source_document_id == document_id
                )
            )
            block_count = await session.scalar(
                select(func.count(ExtractedBlockModel.id)).where(
                    ExtractedBlockModel.source_document_id == document_id
                )
            )
            assert failed is not None
            assert failed.extraction_status is ExtractionStatus.FAILED
            assert failed.extraction_failure_code == "ocr_process"
            assert failed.extraction_config is None
            assert failed.ocr_page_count is None
            assert page_count == block_count == 0

        async with sessions() as session:
            failed = await session.get(SourceDocumentModel, document_id)
            assert failed is not None
            storage = MemoryObjectStorage({failed.object_key: data})
            recovered = await DocumentExtractionService(
                session,
                storage,
                extractor,
                ocr_port=ocr,
            ).extract_native(document_id, actor_id=EXTRACTION_ACTOR_ID)

        async with sessions() as session:
            recovered_document = await session.get(SourceDocumentModel, document_id)
            page_count = await session.scalar(
                select(func.count(SourcePageModel.id)).where(
                    SourcePageModel.source_document_id == document_id
                )
            )
            block_count = await session.scalar(
                select(func.count(ExtractedBlockModel.id)).where(
                    ExtractedBlockModel.source_document_id == document_id
                )
            )
            failure_event = await session.scalar(
                select(AdminAuditEventModel).where(
                    AdminAuditEventModel.resource_id == document_id,
                    AdminAuditEventModel.action == "source_document.extraction_failed",
                )
            )
        await engine.dispose()

        assert recovered.status is ExtractionStatus.EXTRACTED
        assert len(ocr.requests) == 2
        assert recovered_document is not None
        assert recovered_document.extraction_attempt_count == 2
        assert recovered_document.extraction_failure_code is None
        assert page_count == block_count == 2
        assert failure_event is not None
        assert failure_event.payload == {"attempt": 1, "failure_code": "ocr_process"}
        assert "provider output" not in repr(failure_event.payload)
        assert "secrets" not in repr(failure_event.payload)

    asyncio.run(exercise())


@pytest.mark.integration
@pytest.mark.parametrize(
    ("document_integer", "config", "confidence"),
    [
        (81_011, {"nested": {"not": "scalar"}}, None),
        (81_012, {"oversized": "x" * (64 * 1024)}, None),
        (81_013, {}, 1.01),
        (81_014, {}, float("nan")),
    ],
)
def test_database_directly_rejects_unbounded_or_invalid_page_provenance(
    extraction_database_url: str,
    document_integer: int,
    config: dict[str, object],
    confidence: float | None,
) -> None:
    async def exercise() -> None:
        data = pdf_bytes("constraint fixture")
        document_id = UUID(int=document_integer)
        engine = create_async_engine(extraction_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)

        async with sessions() as session:
            document = await add_uploaded_document(
                session,
                document_id=document_id,
                filename=f"constraint-{document_integer}.pdf",
                data=data,
            )
            document.extraction_status = ExtractionStatus.EXTRACTION_PENDING
            document.extraction_attempt_count = 1
            document.extraction_started_at = datetime.now(UTC)
            document.updated_by = EXTRACTION_ACTOR_ID
            await session.flush()
            session.add(
                SourcePageModel(
                    id=UUID(int=document_integer + 100_000),
                    source_document_id=document_id,
                    page_number=1,
                    extractor="fixture",
                    extractor_version="1",
                    extraction_config=config,
                    confidence=confidence,
                    raw_text="text",
                    reviewed_text=None,
                    character_count=4,
                    block_count=0,
                    created_by=EXTRACTION_ACTOR_ID,
                    updated_by=EXTRACTION_ACTOR_ID,
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()

        await engine.dispose()

    asyncio.run(exercise())


@pytest.mark.integration
def test_extraction_outbox_cas_and_database_identity_constraints(
    extraction_database_url: str,
) -> None:
    async def exercise() -> None:
        data = pdf_bytes("outbox identity constraint fixture 81020")
        document_id = UUID(int=81_020)
        engine = create_async_engine(extraction_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)

        async with sessions() as session:
            document = await add_uploaded_document(
                session,
                document_id=document_id,
                filename="outbox-identity.pdf",
                data=data,
            )
            service = DocumentExtractionService(
                session,
                MemoryObjectStorage({document.object_key: data}),
                CountingExtractor(),
            )
            queued = await service.queue_extraction(document_id, actor_id=EXTRACTION_ACTOR_ID)

            for invalid_message_id in ("invalid message", "x" * 129):
                with pytest.raises(DBAPIError):
                    await session.execute(
                        sql_text(
                            "UPDATE source_documents SET extraction_queue_message_id = :message_id "
                            "WHERE id = :document_id"
                        ),
                        {"document_id": document_id, "message_id": invalid_message_id},
                    )
                await session.rollback()

            attached = await service.attach_queue_message(
                document_id,
                "attempt-one-message",
                actor_id=EXTRACTION_ACTOR_ID,
            )
            duplicate_attach = await service.attach_queue_message(
                document_id,
                "duplicate-send-message",
                actor_id=EXTRACTION_ACTOR_ID,
            )

            assert queued.queue_message_id is None
            assert attached.queue_message_id == "attempt-one-message"
            assert attached.deduplicated is False
            assert duplicate_attach.queue_message_id == "attempt-one-message"
            assert duplicate_attach.deduplicated is True

            with pytest.raises(IntegrityError):
                await session.execute(
                    sql_text(
                        "UPDATE source_documents SET extraction_queue_message_id = NULL "
                        "WHERE id = :document_id"
                    ),
                    {"document_id": document_id},
                )
            await session.rollback()

            await session.execute(
                sql_text(
                    "UPDATE source_documents SET extraction_status = 'failed', "
                    "extraction_failure_code = 'unexpected_error', "
                    "extraction_completed_at = now() WHERE id = :document_id"
                ),
                {"document_id": document_id},
            )
            await session.commit()

            with pytest.raises(IntegrityError):
                await session.execute(
                    sql_text(
                        "UPDATE source_documents SET extraction_status = 'extraction_pending', "
                        "extraction_attempt_count = extraction_attempt_count + 1, "
                        "extraction_completed_at = NULL, extraction_failure_code = NULL "
                        "WHERE id = :document_id"
                    ),
                    {"document_id": document_id},
                )
            await session.rollback()

            retried = await service.queue_extraction(document_id, actor_id=REVIEW_ACTOR_ID)
            reattached = await service.attach_queue_message(
                document_id,
                "attempt-two-message",
                actor_id=REVIEW_ACTOR_ID,
            )

            assert retried.queue_message_id is None
            assert reattached.queue_message_id == "attempt-two-message"

        async with sessions() as session:
            persisted = await session.get(SourceDocumentModel, document_id)
            actions = list(
                await session.scalars(
                    select(AdminAuditEventModel.action)
                    .where(AdminAuditEventModel.resource_id == document_id)
                    .order_by(AdminAuditEventModel.created_at)
                )
            )
        await engine.dispose()

        assert persisted is not None
        assert persisted.extraction_attempt_count == 2
        assert persisted.extraction_queue_message_id == "attempt-two-message"
        assert actions == [
            "source_document.extraction_queued",
            "source_document.extraction_dispatched",
            "source_document.extraction_queued",
            "source_document.extraction_dispatched",
        ]

    asyncio.run(exercise())


@pytest.mark.integration
def test_concurrent_extraction_recoverers_skip_locked_and_dispatch_each_job_once(
    extraction_database_url: str,
) -> None:
    document_ids = (UUID(int=81_021), UUID(int=81_022))

    async def seed() -> None:
        engine = create_async_engine(extraction_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        for document_id in document_ids:
            data = pdf_bytes(f"recovery concurrency {document_id}")
            async with sessions() as session:
                document = await add_uploaded_document(
                    session,
                    document_id=document_id,
                    filename=f"recovery-{document_id}.pdf",
                    data=data,
                )
                await DocumentExtractionService(
                    session,
                    MemoryObjectStorage({document.object_key: data}),
                    CountingExtractor(),
                ).queue_extraction(document_id, actor_id=UUID(int=document_id.int + 100))
        await engine.dispose()

    asyncio.run(seed())
    dispatcher = BarrierRecoveryDispatcher()

    def recover_once() -> object:
        async def recover() -> object:
            engine = create_async_engine(extraction_database_url)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions() as session:
                result = await ExtractionRecoveryService(
                    session,
                    dispatcher,
                    ExtractionRecoveryPolicy(batch_size=1, outbox_min_age_seconds=5),
                ).recover(now=datetime(2027, 1, 1, tzinfo=UTC))
            await engine.dispose()
            return result

        return asyncio.run(recover())

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: recover_once(), range(2)))

    async def inspect() -> tuple[list[str | None], int]:
        engine = create_async_engine(extraction_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            message_ids = list(
                await session.scalars(
                    select(SourceDocumentModel.extraction_queue_message_id)
                    .where(SourceDocumentModel.id.in_(document_ids))
                    .order_by(SourceDocumentModel.id)
                )
            )
            audit_count = await session.scalar(
                select(func.count(AdminAuditEventModel.id)).where(
                    AdminAuditEventModel.resource_id.in_(document_ids),
                    AdminAuditEventModel.action == "source_document.extraction_redispatched",
                )
            )
        await engine.dispose()
        return message_ids, int(audit_count or 0)

    message_ids, audit_count = asyncio.run(inspect())
    assert sorted(result.scanned for result in results) == [1, 1]  # type: ignore[attr-defined]
    assert sorted(result.dispatched for result in results) == [1, 1]  # type: ignore[attr-defined]
    assert {call[0] for call in dispatcher.calls} == set(document_ids)
    assert message_ids == [f"recovery-{document_id}" for document_id in document_ids]
    assert audit_count == 2

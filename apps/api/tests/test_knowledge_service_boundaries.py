import asyncio
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.documents.domain import ExtractionStatus, SourceDocumentType
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.knowledge.domain import (
    ChunkType,
    HistoricalQuestion,
    KnowledgeChunk,
    Provenance,
    QuestionType,
    ReviewState,
)
from exam_guru_api.knowledge.repository import SqlAlchemyKnowledgeRepository
from exam_guru_api.knowledge.service import (
    KnowledgeCurriculumNotFoundError,
    KnowledgePersistenceService,
    KnowledgeRecordNotReadyError,
    KnowledgeSourceCurriculumMismatchError,
    KnowledgeSourceDocumentNotFoundError,
    KnowledgeSourceMetadataMismatchError,
)

CURRICULUM_ID = UUID(int=100)
SOURCE_ID = UUID(int=101)
BLOCK_ID = UUID(int=102)


class LookupSession:
    def __init__(self, *responses: object | None) -> None:
        self.responses = list(responses)

    async def get(self, _model: object, _identity: object) -> object | None:
        return self.responses.pop(0)


def historical_question() -> HistoricalQuestion:
    return HistoricalQuestion(
        id=UUID(int=103),
        curriculum_version_id=CURRICULUM_ID,
        year=2021,
        paper_code="P1",
        question_number="1",
        text="Question",
        question_type=QuestionType.SHORT_ANSWER,
        marks=1,
        provenance=Provenance(SOURCE_ID, 1, BLOCK_ID),
    )


def knowledge_chunk() -> KnowledgeChunk:
    return KnowledgeChunk(
        id=UUID(int=104),
        curriculum_version_id=CURRICULUM_ID,
        chunk_type=ChunkType.EXPLANATION,
        text="Chunk",
        educational_boundary="Unit",
        sequence=0,
        provenance=Provenance(SOURCE_ID, 1, BLOCK_ID),
    )


def source_document(
    *,
    curriculum_version_id: UUID = CURRICULUM_ID,
    document_type: SourceDocumentType = SourceDocumentType.PAST_PAPER,
    year: int | None = 2021,
    paper_code: str | None = "P1",
) -> SourceDocumentModel:
    return SourceDocumentModel(
        id=SOURCE_ID,
        curriculum_version_id=curriculum_version_id,
        document_type=document_type,
        extraction_status=ExtractionStatus.TRUSTED,
        year=year,
        paper_code=paper_code,
    )


def test_service_rejects_missing_curriculum_and_source() -> None:
    async def exercise() -> None:
        missing_curriculum = KnowledgePersistenceService(cast(AsyncSession, LookupSession(None)))
        with pytest.raises(KnowledgeCurriculumNotFoundError):
            await missing_curriculum._ensure_curriculum_exists(CURRICULUM_ID)

        missing_source = KnowledgePersistenceService(cast(AsyncSession, LookupSession(None)))
        with pytest.raises(KnowledgeSourceDocumentNotFoundError):
            await missing_source._validate_source(historical_question())

    asyncio.run(exercise())


def test_service_rejects_cross_curriculum_and_mismatched_past_paper_metadata() -> None:
    async def exercise() -> None:
        cross_curriculum = KnowledgePersistenceService(
            cast(
                AsyncSession,
                LookupSession(source_document(curriculum_version_id=UUID(int=999))),
            )
        )
        with pytest.raises(KnowledgeSourceCurriculumMismatchError):
            await cross_curriculum._validate_source(historical_question())

        mismatched_metadata = KnowledgePersistenceService(
            cast(AsyncSession, LookupSession(source_document(year=2020)))
        )
        with pytest.raises(KnowledgeSourceMetadataMismatchError):
            await mismatched_metadata._validate_source(historical_question())

        wrong_document_type = KnowledgePersistenceService(
            cast(
                AsyncSession,
                LookupSession(
                    source_document(
                        document_type=SourceDocumentType.SYLLABUS,
                        year=None,
                        paper_code=None,
                    )
                ),
            )
        )
        with pytest.raises(KnowledgeSourceMetadataMismatchError):
            await wrong_document_type._validate_source(historical_question())

    asyncio.run(exercise())


def test_chunk_source_metadata_and_review_readiness() -> None:
    async def exercise() -> None:
        chunk_service = KnowledgePersistenceService(
            cast(
                AsyncSession,
                LookupSession(
                    source_document(
                        document_type=SourceDocumentType.SYLLABUS,
                        year=None,
                        paper_code=None,
                    )
                ),
            )
        )
        await chunk_service._validate_source(knowledge_chunk())

    asyncio.run(exercise())

    unclassified = historical_question()
    with pytest.raises(KnowledgeRecordNotReadyError):
        KnowledgePersistenceService._ensure_review_candidate(
            unclassified,
            ReviewState.REVIEWED,
        )


def test_service_lists_chunks_after_curriculum_scope_validation() -> None:
    class RecordingRepository:
        def __init__(self) -> None:
            self.calls: list[tuple[UUID, dict[str, object]]] = []

        async def list_chunks(
            self,
            curriculum_version_id: UUID,
            **filters: object,
        ) -> tuple[KnowledgeChunk, ...]:
            self.calls.append((curriculum_version_id, filters))
            return (knowledge_chunk(),)

    async def exercise() -> None:
        repository = RecordingRepository()
        service = KnowledgePersistenceService(cast(AsyncSession, LookupSession(object())))
        service._repository = cast(SqlAlchemyKnowledgeRepository, repository)

        chunks = await service.list_chunks(
            CURRICULUM_ID,
            review_state=ReviewState.DRAFT,
            source_document_id=SOURCE_ID,
            competency_id=UUID(int=105),
            chunk_type=ChunkType.EXPLANATION,
            limit=12,
            offset=3,
        )

        assert chunks == (knowledge_chunk(),)
        assert repository.calls == [
            (
                CURRICULUM_ID,
                {
                    "review_state": ReviewState.DRAFT,
                    "source_document_id": SOURCE_ID,
                    "competency_id": UUID(int=105),
                    "chunk_type": ChunkType.EXPLANATION,
                    "limit": 12,
                    "offset": 3,
                },
            )
        ]

    asyncio.run(exercise())


def test_service_lists_questions_after_curriculum_scope_validation() -> None:
    class RecordingRepository:
        async def list_questions(
            self,
            curriculum_version_id: UUID,
            **filters: object,
        ) -> tuple[HistoricalQuestion, ...]:
            assert curriculum_version_id == CURRICULUM_ID
            assert filters == {
                "review_state": ReviewState.REVIEWED,
                "source_document_id": SOURCE_ID,
                "competency_id": UUID(int=105),
                "question_type": QuestionType.SHORT_ANSWER,
                "year": 2021,
                "paper_code": "P1",
                "limit": 12,
                "offset": 3,
            }
            return (historical_question(),)

    async def exercise() -> None:
        service = KnowledgePersistenceService(cast(AsyncSession, LookupSession(object())))
        service._repository = cast(SqlAlchemyKnowledgeRepository, RecordingRepository())

        questions = await service.list_questions(
            CURRICULUM_ID,
            review_state=ReviewState.REVIEWED,
            source_document_id=SOURCE_ID,
            competency_id=UUID(int=105),
            question_type=QuestionType.SHORT_ANSWER,
            year=2021,
            paper_code="P1",
            limit=12,
            offset=3,
        )

        assert questions == (historical_question(),)

    asyncio.run(exercise())

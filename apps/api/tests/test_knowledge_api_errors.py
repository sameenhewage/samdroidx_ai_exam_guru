import asyncio
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.api.routes.knowledge import (
    _execute_knowledge_operation,
    _knowledge_http_exception,
    classify_historical_question,
    classify_knowledge_chunk,
    get_historical_question,
    get_knowledge_chunk,
    import_historical_question,
    import_knowledge_chunk,
    list_historical_questions,
    list_knowledge_chunks,
    transition_historical_question_review,
    transition_knowledge_chunk_review,
)
from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.knowledge.domain import (
    ChunkType,
    DifficultyLabel,
    EmbeddingConfigurationMetadata,
    HistoricalQuestion,
    KnowledgeChunk,
    KnowledgeContractError,
    Provenance,
    QuestionType,
    ReviewState,
)
from exam_guru_api.knowledge.repository import (
    ConcurrentKnowledgeVersionError,
    KnowledgeRecordNotFoundError,
    SourceImportConflictError,
)
from exam_guru_api.knowledge.schemas import (
    HistoricalQuestionImportRequest,
    KnowledgeChunkImportRequest,
    KnowledgeClassificationRequest,
    KnowledgeReviewTransitionRequest,
)
from exam_guru_api.knowledge.service import (
    FinalKnowledgeRecordError,
    KnowledgeCurriculumNotFoundError,
    KnowledgePersistenceService,
    KnowledgeRecordNotReadyError,
    KnowledgeSourceCurriculumMismatchError,
    KnowledgeSourceDocumentNotFoundError,
    KnowledgeSourceMetadataMismatchError,
    SourceImportResult,
    TrustedKnowledgeSourceRequiredError,
)

ACTOR_ID = UUID(int=1)
CURRICULUM_ID = UUID(int=2)
SOURCE_ID = UUID(int=3)
BLOCK_ID = UUID(int=4)
QUESTION_ID = UUID(int=5)
CHUNK_ID = UUID(int=6)
TIMESTAMP = datetime(2026, 1, 1, tzinfo=UTC)
PRINCIPAL = Principal(subject_id=ACTOR_ID, roles=frozenset({AdminRole.ADMIN}))


class RollbackSession:
    def __init__(self) -> None:
        self.rolled_back = False

    async def rollback(self) -> None:
        self.rolled_back = True


def question() -> HistoricalQuestion:
    return HistoricalQuestion(
        id=QUESTION_ID,
        curriculum_version_id=CURRICULUM_ID,
        year=2021,
        paper_code="P1",
        question_number="1",
        text="Question text",
        question_type=QuestionType.MULTIPLE_CHOICE,
        marks=2,
        provenance=Provenance(SOURCE_ID, 1, BLOCK_ID),
        media_references=("source://page/1/figure/1",),
        options=("A", "B", "C", "D"),
        answer="B",
        marking_guidance="Award two marks for B.",
        marking_data={"criteria": [{"description": "Selects B.", "marks": 2}]},
        question_archetype="single_best_answer",
        difficulty_label=DifficultyLabel.MEDIUM,
        difficulty_confidence=0.9,
        difficulty_source="reviewer_confirmed",
        competency_id=UUID(int=7),
        version=1,
        created_at=TIMESTAMP,
        updated_at=TIMESTAMP,
        embedding_configurations=(
            EmbeddingConfigurationMetadata(
                id=UUID(int=8),
                provider="fixture",
                model="fixture-model",
                dimension=3,
                version="v1",
                config_fingerprint="fixture-v1",
            ),
        ),
    )


def chunk() -> KnowledgeChunk:
    return KnowledgeChunk(
        id=CHUNK_ID,
        curriculum_version_id=CURRICULUM_ID,
        chunk_type=ChunkType.EXPLANATION,
        text="Chunk text",
        educational_boundary="Unit 1",
        sequence=0,
        provenance=Provenance(SOURCE_ID, 1, BLOCK_ID),
        competency_id=UUID(int=7),
        version=1,
        created_at=TIMESTAMP,
        updated_at=TIMESTAMP,
    )


def test_historical_question_request_rejects_cross_field_metadata_invariants() -> None:
    base: dict[str, object] = {
        "year": 2021,
        "paper_code": "P1",
        "question_number": "1",
        "text": "Question text",
        "question_type": "multiple_choice",
        "marks": 2,
        "source_document_id": SOURCE_ID,
        "page_number": 1,
        "source_block_id": BLOCK_ID,
    }
    invalid_metadata: tuple[dict[str, object], ...] = (
        {"options": ["A", "A"]},
        {"difficulty_label": "easy"},
        {"marking_data": {"score": float("nan")}},
    )

    for metadata in invalid_metadata:
        with pytest.raises(ValidationError):
            HistoricalQuestionImportRequest.model_validate({**base, **metadata})

    constructed = HistoricalQuestionImportRequest.model_validate(
        {
            **base,
            "question_type": "short_answer",
            "options": ["14", "fourteen"],
            "answer": "Any equivalent expression equal to fourteen",
        }
    )
    assert constructed.answer == "Any equivalent expression equal to fourteen"


def test_knowledge_route_wrappers_return_typed_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    question_record = question()
    chunk_record = chunk()
    question_import_calls = 0
    chunk_import_calls = 0

    async def import_question(
        _service: KnowledgePersistenceService,
        imported: HistoricalQuestion,
        *,
        actor_id: UUID,
    ) -> SourceImportResult[HistoricalQuestion]:
        nonlocal question_import_calls
        assert actor_id == ACTOR_ID
        assert imported.media_references == ("source://page/1/figure/1",)
        assert imported.options == ("A", "B", "C", "D")
        assert imported.answer == "B"
        assert imported.marking_guidance == "Award two marks for B."
        assert imported.marking_data is not None
        assert imported.question_archetype == "single_best_answer"
        assert imported.difficulty_label is DifficultyLabel.MEDIUM
        assert imported.difficulty_confidence == 0.9
        assert imported.difficulty_source == "reviewer_confirmed"
        question_import_calls += 1
        return SourceImportResult(
            question_record,
            deduplicated=question_import_calls == 2,
        )

    async def import_chunk(
        _service: KnowledgePersistenceService,
        _chunk: KnowledgeChunk,
        *,
        actor_id: UUID,
    ) -> SourceImportResult[KnowledgeChunk]:
        nonlocal chunk_import_calls
        assert actor_id == ACTOR_ID
        chunk_import_calls += 1
        return SourceImportResult(
            chunk_record,
            deduplicated=chunk_import_calls == 2,
        )

    async def return_questions(
        _service: KnowledgePersistenceService,
        *_args: object,
        **_kwargs: object,
    ) -> tuple[HistoricalQuestion, ...]:
        return (question_record,)

    async def return_question(
        _service: KnowledgePersistenceService,
        *_args: object,
        **_kwargs: object,
    ) -> HistoricalQuestion:
        return question_record

    async def return_chunks(
        _service: KnowledgePersistenceService,
        *_args: object,
        **_kwargs: object,
    ) -> tuple[KnowledgeChunk, ...]:
        return (chunk_record,)

    async def return_chunk(
        _service: KnowledgePersistenceService,
        *_args: object,
        **_kwargs: object,
    ) -> KnowledgeChunk:
        return chunk_record

    monkeypatch.setattr(KnowledgePersistenceService, "import_question", import_question)
    monkeypatch.setattr(KnowledgePersistenceService, "import_chunk", import_chunk)
    monkeypatch.setattr(KnowledgePersistenceService, "list_questions", return_questions)
    monkeypatch.setattr(KnowledgePersistenceService, "get_question", return_question)
    monkeypatch.setattr(KnowledgePersistenceService, "classify_question", return_question)
    monkeypatch.setattr(
        KnowledgePersistenceService,
        "transition_question_review",
        return_question,
    )
    monkeypatch.setattr(KnowledgePersistenceService, "list_chunks", return_chunks)
    monkeypatch.setattr(KnowledgePersistenceService, "get_chunk", return_chunk)
    monkeypatch.setattr(KnowledgePersistenceService, "classify_chunk", return_chunk)
    monkeypatch.setattr(KnowledgePersistenceService, "transition_chunk_review", return_chunk)

    session = cast(AsyncSession, RollbackSession())
    question_request = HistoricalQuestionImportRequest(
        year=2021,
        paper_code="P1",
        question_number="1",
        text="Question text",
        question_type=QuestionType.MULTIPLE_CHOICE,
        marks=2,
        source_document_id=SOURCE_ID,
        page_number=1,
        source_block_id=BLOCK_ID,
        media_references=("source://page/1/figure/1",),
        options=("A", "B", "C", "D"),
        answer="B",
        marking_guidance="Award two marks for B.",
        marking_data={"criteria": [{"description": "Selects B.", "marks": 2}]},
        question_archetype="single_best_answer",
        difficulty_label=DifficultyLabel.MEDIUM,
        difficulty_confidence=0.9,
        difficulty_source="reviewer_confirmed",
    )
    chunk_request = KnowledgeChunkImportRequest(
        chunk_type=ChunkType.EXPLANATION,
        text="Chunk text",
        educational_boundary="Unit 1",
        sequence=0,
        source_document_id=SOURCE_ID,
        page_number=1,
        source_block_id=BLOCK_ID,
    )
    classification = KnowledgeClassificationRequest(
        competency_id=UUID(int=7),
        expected_version=1,
    )
    transition = KnowledgeReviewTransitionRequest(
        target=ReviewState.IN_REVIEW,
        expected_version=1,
    )

    async def exercise() -> None:
        first_question_response = Response()
        second_question_response = Response()
        first_chunk_response = Response()
        second_chunk_response = Response()
        imported_question = await import_historical_question(
            CURRICULUM_ID,
            question_request,
            first_question_response,
            PRINCIPAL,
            session,
        )
        duplicate_question = await import_historical_question(
            CURRICULUM_ID,
            question_request,
            second_question_response,
            PRINCIPAL,
            session,
        )
        imported_chunk = await import_knowledge_chunk(
            CURRICULUM_ID,
            chunk_request,
            first_chunk_response,
            PRINCIPAL,
            session,
        )
        duplicate_chunk = await import_knowledge_chunk(
            CURRICULUM_ID,
            chunk_request,
            second_chunk_response,
            PRINCIPAL,
            session,
        )
        listed_questions = await list_historical_questions(
            CURRICULUM_ID,
            PRINCIPAL,
            session,
        )
        fetched_question = await get_historical_question(
            CURRICULUM_ID,
            QUESTION_ID,
            PRINCIPAL,
            session,
        )
        classified_question = await classify_historical_question(
            CURRICULUM_ID,
            QUESTION_ID,
            classification,
            PRINCIPAL,
            session,
        )
        reviewed_question = await transition_historical_question_review(
            CURRICULUM_ID,
            QUESTION_ID,
            transition,
            PRINCIPAL,
            session,
        )
        listed_chunks = await list_knowledge_chunks(
            CURRICULUM_ID,
            PRINCIPAL,
            session,
        )
        fetched_chunk = await get_knowledge_chunk(
            CURRICULUM_ID,
            CHUNK_ID,
            PRINCIPAL,
            session,
        )
        classified_chunk = await classify_knowledge_chunk(
            CURRICULUM_ID,
            CHUNK_ID,
            classification,
            PRINCIPAL,
            session,
        )
        reviewed_chunk = await transition_knowledge_chunk_review(
            CURRICULUM_ID,
            CHUNK_ID,
            transition,
            PRINCIPAL,
            session,
        )

        assert first_question_response.status_code == 201
        assert second_question_response.status_code == 200
        assert first_chunk_response.status_code == 201
        assert second_chunk_response.status_code == 200
        assert imported_question.embedding_status.value == "embedded"
        assert imported_question.embedding_configurations[0].provider == "fixture"
        assert imported_question.media_references == ["source://page/1/figure/1"]
        assert imported_question.options == ["A", "B", "C", "D"]
        assert imported_question.answer == "B"
        assert imported_question.marking_guidance == "Award two marks for B."
        assert imported_question.marking_data == {
            "criteria": [{"description": "Selects B.", "marks": 2}]
        }
        assert imported_question.question_archetype == "single_best_answer"
        assert imported_question.difficulty_label is DifficultyLabel.MEDIUM
        assert imported_question.difficulty_confidence == 0.9
        assert imported_question.difficulty_source == "reviewer_confirmed"
        assert duplicate_question.deduplicated is True
        assert imported_chunk.id == duplicate_chunk.id == CHUNK_ID
        assert listed_questions == [fetched_question]
        assert classified_question.id == reviewed_question.id == QUESTION_ID
        assert listed_chunks == [fetched_chunk]
        assert classified_chunk.id == reviewed_chunk.id == CHUNK_ID

    asyncio.run(exercise())


def test_import_route_rejects_domain_invalid_whitespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def should_not_run(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("service must not run")

    monkeypatch.setattr(KnowledgePersistenceService, "import_question", should_not_run)
    monkeypatch.setattr(KnowledgePersistenceService, "import_chunk", should_not_run)
    session = cast(AsyncSession, RollbackSession())

    async def exercise() -> tuple[HTTPException, HTTPException]:
        with pytest.raises(HTTPException) as question_error:
            await import_historical_question(
                CURRICULUM_ID,
                HistoricalQuestionImportRequest(
                    year=2021,
                    paper_code="P1",
                    question_number="1",
                    text=" ",
                    question_type=QuestionType.SHORT_ANSWER,
                    marks=1,
                    source_document_id=SOURCE_ID,
                    page_number=1,
                    source_block_id=BLOCK_ID,
                ),
                Response(),
                PRINCIPAL,
                session,
            )
        with pytest.raises(HTTPException) as chunk_error:
            await import_knowledge_chunk(
                CURRICULUM_ID,
                KnowledgeChunkImportRequest(
                    chunk_type=ChunkType.EXPLANATION,
                    text=" ",
                    educational_boundary="Unit",
                    sequence=0,
                    source_document_id=SOURCE_ID,
                    page_number=1,
                    source_block_id=BLOCK_ID,
                ),
                Response(),
                PRINCIPAL,
                session,
            )
        return question_error.value, chunk_error.value

    question_error, chunk_error = asyncio.run(exercise())
    assert question_error.status_code == 422
    assert chunk_error.status_code == 422
    assert cast(dict[str, str], question_error.detail) == {"code": "invalid_knowledge_record"}
    assert cast(dict[str, str], chunk_error.detail) == {"code": "invalid_knowledge_record"}


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (KnowledgeCurriculumNotFoundError(CURRICULUM_ID), 404, "curriculum_version_not_found"),
        (KnowledgeSourceDocumentNotFoundError(SOURCE_ID), 404, "source_document_not_found"),
        (
            KnowledgeRecordNotFoundError("knowledge_chunk", CHUNK_ID),
            404,
            "knowledge_record_not_found",
        ),
        (ConcurrentKnowledgeVersionError(1, 2), 409, "concurrent_knowledge_modification"),
        (
            SourceImportConflictError("knowledge_chunk", SOURCE_ID, "0"),
            409,
            "source_import_conflict",
        ),
        (FinalKnowledgeRecordError(CHUNK_ID, ReviewState.REVIEWED), 409, "final_knowledge_record"),
        (KnowledgeContractError("invalid transition"), 409, "invalid_review_transition"),
        (
            KnowledgeSourceCurriculumMismatchError(SOURCE_ID, CURRICULUM_ID),
            422,
            "source_curriculum_mismatch",
        ),
        (TrustedKnowledgeSourceRequiredError(SOURCE_ID), 422, "trusted_source_required"),
        (KnowledgeSourceMetadataMismatchError(SOURCE_ID), 422, "source_metadata_mismatch"),
        (KnowledgeRecordNotReadyError(CHUNK_ID), 422, "knowledge_record_not_ready"),
    ],
)
def test_knowledge_error_mapping_is_stable(
    error: Exception,
    status_code: int,
    code: str,
) -> None:
    mapped = _knowledge_http_exception(error)

    assert mapped.status_code == status_code
    assert cast(dict[str, object], mapped.detail)["code"] == code
    if isinstance(error, ConcurrentKnowledgeVersionError):
        assert cast(dict[str, object], mapped.detail) == {
            "code": "concurrent_knowledge_modification",
            "expected_version": 1,
            "actual_version": 2,
        }


def test_knowledge_operation_rolls_back_integrity_errors_and_maps_domain_errors() -> None:
    session = RollbackSession()

    async def fail_integrity() -> object:
        raise IntegrityError("UPDATE", {}, RuntimeError("constraint"))

    async def fail_domain() -> object:
        raise KnowledgeCurriculumNotFoundError(CURRICULUM_ID)

    async def exercise() -> tuple[HTTPException, HTTPException]:
        with pytest.raises(HTTPException) as integrity_error:
            await _execute_knowledge_operation(
                cast(AsyncSession, session),
                fail_integrity,
                integrity_code="invalid_taxonomy_classification",
            )
        with pytest.raises(HTTPException) as domain_error:
            await _execute_knowledge_operation(cast(AsyncSession, session), fail_domain)
        return integrity_error.value, domain_error.value

    integrity_error, domain_error = asyncio.run(exercise())
    assert session.rolled_back
    assert integrity_error.status_code == 422
    assert cast(dict[str, str], integrity_error.detail) == {
        "code": "invalid_taxonomy_classification"
    }
    assert domain_error.status_code == 404

from collections.abc import Awaitable, Callable
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.api.dependencies import get_database_session
from exam_guru_api.auth.api import require_permission
from exam_guru_api.auth.domain import Permission, Principal
from exam_guru_api.knowledge.domain import (
    ChunkType,
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
    HistoricalQuestionResponse,
    KnowledgeChunkImportRequest,
    KnowledgeChunkResponse,
    KnowledgeClassificationRequest,
    KnowledgeReviewTransitionRequest,
)
from exam_guru_api.knowledge.service import (
    ActiveKnowledgeSourceRequiredError,
    FinalKnowledgeRecordError,
    KnowledgeCurriculumNotFoundError,
    KnowledgePersistenceService,
    KnowledgeRecordNotReadyError,
    KnowledgeSourceCurriculumMismatchError,
    KnowledgeSourceDocumentNotFoundError,
    KnowledgeSourceMetadataMismatchError,
    TrustedKnowledgeSourceRequiredError,
)

router = APIRouter()
ReadPrincipal = Annotated[Principal, Depends(require_permission(Permission.KNOWLEDGE_READ))]
WritePrincipal = Annotated[Principal, Depends(require_permission(Permission.KNOWLEDGE_WRITE))]
ReviewPrincipal = Annotated[Principal, Depends(require_permission(Permission.CONTENT_REVIEW))]
DatabaseSession = Annotated[AsyncSession, Depends(get_database_session)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=100_000)]


@router.post(
    "/{curriculum_version_id}/knowledge/questions",
    operation_id="import_historical_question",
    response_model=HistoricalQuestionResponse,
    responses={
        status.HTTP_200_OK: {"description": "Existing source question returned idempotently"},
        status.HTTP_201_CREATED: {"description": "Draft historical question imported"},
        status.HTTP_409_CONFLICT: {"description": "Conflicting source import"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Knowledge invariant failed"},
    },
    status_code=status.HTTP_201_CREATED,
    summary="Import a draft historical question",
)
async def import_historical_question(
    curriculum_version_id: UUID,
    request: HistoricalQuestionImportRequest,
    response: Response,
    principal: WritePrincipal,
    session: DatabaseSession,
) -> HistoricalQuestionResponse:
    try:
        question = HistoricalQuestion(
            id=uuid4(),
            curriculum_version_id=curriculum_version_id,
            year=request.year,
            paper_code=request.paper_code,
            question_number=request.question_number,
            text=request.text,
            question_type=request.question_type,
            marks=request.marks,
            provenance=Provenance(
                source_document_id=request.source_document_id,
                page_number=request.page_number,
                source_block_id=request.source_block_id,
            ),
            media_references=request.media_references,
            options=request.options,
            answer=request.answer,
            marking_guidance=request.marking_guidance,
            marking_data=request.marking_data,
            question_archetype=request.question_archetype,
            difficulty_label=request.difficulty_label,
            difficulty_confidence=request.difficulty_confidence,
            difficulty_source=request.difficulty_source,
        )
    except KnowledgeContractError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "invalid_knowledge_record"},
        ) from error
    result = await _execute_knowledge_operation(
        session,
        lambda: KnowledgePersistenceService(session).import_question(
            question,
            actor_id=principal.subject_id,
        ),
        integrity_code="knowledge_invariant_violation",
    )
    response.status_code = status.HTTP_200_OK if result.deduplicated else status.HTTP_201_CREATED
    return HistoricalQuestionResponse.from_domain(
        result.record,
        deduplicated=result.deduplicated,
    )


@router.get(
    "/{curriculum_version_id}/knowledge/questions",
    operation_id="list_historical_questions",
    response_model=list[HistoricalQuestionResponse],
    summary="List curriculum historical questions",
)
async def list_historical_questions(
    curriculum_version_id: UUID,
    principal: ReadPrincipal,
    session: DatabaseSession,
    review_state: ReviewState | None = None,
    source_document_id: UUID | None = None,
    competency_id: UUID | None = None,
    question_type: QuestionType | None = None,
    year: Annotated[int | None, Query(ge=1900, le=2100)] = None,
    paper_code: Annotated[str | None, Query(max_length=64)] = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> list[HistoricalQuestionResponse]:
    del principal
    records = await _execute_knowledge_operation(
        session,
        lambda: KnowledgePersistenceService(session).list_questions(
            curriculum_version_id,
            review_state=review_state,
            source_document_id=source_document_id,
            competency_id=competency_id,
            question_type=question_type,
            year=year,
            paper_code=paper_code,
            limit=limit,
            offset=offset,
        ),
    )
    return [HistoricalQuestionResponse.from_domain(record) for record in records]


@router.get(
    "/{curriculum_version_id}/knowledge/questions/{question_id}",
    operation_id="get_historical_question",
    response_model=HistoricalQuestionResponse,
    summary="Get a curriculum historical question",
)
async def get_historical_question(
    curriculum_version_id: UUID,
    question_id: UUID,
    principal: ReadPrincipal,
    session: DatabaseSession,
) -> HistoricalQuestionResponse:
    del principal
    record = await _execute_knowledge_operation(
        session,
        lambda: KnowledgePersistenceService(session).get_question(
            curriculum_version_id,
            question_id,
        ),
    )
    return HistoricalQuestionResponse.from_domain(record)


@router.patch(
    "/{curriculum_version_id}/knowledge/questions/{question_id}/classification",
    operation_id="classify_historical_question",
    response_model=HistoricalQuestionResponse,
    summary="Correct a historical question classification",
)
async def classify_historical_question(
    curriculum_version_id: UUID,
    question_id: UUID,
    request: KnowledgeClassificationRequest,
    principal: ReviewPrincipal,
    session: DatabaseSession,
) -> HistoricalQuestionResponse:
    record = await _execute_knowledge_operation(
        session,
        lambda: KnowledgePersistenceService(session).classify_question(
            curriculum_version_id,
            question_id,
            competency_id=request.competency_id,
            skill_id=request.skill_id,
            sub_skill_id=request.sub_skill_id,
            learning_concept_id=request.learning_concept_id,
            expected_version=request.expected_version,
            actor_id=principal.subject_id,
        ),
        integrity_code="invalid_taxonomy_classification",
    )
    return HistoricalQuestionResponse.from_domain(record)


@router.post(
    "/{curriculum_version_id}/knowledge/questions/{question_id}/review",
    operation_id="transition_historical_question_review",
    response_model=HistoricalQuestionResponse,
    summary="Advance a historical question review state",
)
async def transition_historical_question_review(
    curriculum_version_id: UUID,
    question_id: UUID,
    request: KnowledgeReviewTransitionRequest,
    principal: ReviewPrincipal,
    session: DatabaseSession,
) -> HistoricalQuestionResponse:
    record = await _execute_knowledge_operation(
        session,
        lambda: KnowledgePersistenceService(session).transition_question_review(
            curriculum_version_id,
            question_id,
            request.target,
            expected_version=request.expected_version,
            actor_id=principal.subject_id,
        ),
        integrity_code="knowledge_record_not_ready",
    )
    return HistoricalQuestionResponse.from_domain(record)


@router.post(
    "/{curriculum_version_id}/knowledge/chunks",
    operation_id="import_knowledge_chunk",
    response_model=KnowledgeChunkResponse,
    responses={
        status.HTTP_200_OK: {"description": "Existing source chunk returned idempotently"},
        status.HTTP_201_CREATED: {"description": "Draft knowledge chunk imported"},
        status.HTTP_409_CONFLICT: {"description": "Conflicting source import"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Knowledge invariant failed"},
    },
    status_code=status.HTTP_201_CREATED,
    summary="Import a draft educational knowledge chunk",
)
async def import_knowledge_chunk(
    curriculum_version_id: UUID,
    request: KnowledgeChunkImportRequest,
    response: Response,
    principal: WritePrincipal,
    session: DatabaseSession,
) -> KnowledgeChunkResponse:
    try:
        chunk = KnowledgeChunk(
            id=uuid4(),
            curriculum_version_id=curriculum_version_id,
            chunk_type=request.chunk_type,
            text=request.text,
            educational_boundary=request.educational_boundary,
            sequence=request.sequence,
            provenance=Provenance(
                source_document_id=request.source_document_id,
                page_number=request.page_number,
                source_block_id=request.source_block_id,
            ),
        )
    except KnowledgeContractError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "invalid_knowledge_record"},
        ) from error
    result = await _execute_knowledge_operation(
        session,
        lambda: KnowledgePersistenceService(session).import_chunk(
            chunk,
            actor_id=principal.subject_id,
        ),
        integrity_code="knowledge_invariant_violation",
    )
    response.status_code = status.HTTP_200_OK if result.deduplicated else status.HTTP_201_CREATED
    return KnowledgeChunkResponse.from_domain(
        result.record,
        deduplicated=result.deduplicated,
    )


@router.get(
    "/{curriculum_version_id}/knowledge/chunks",
    operation_id="list_knowledge_chunks",
    response_model=list[KnowledgeChunkResponse],
    summary="List curriculum knowledge chunks",
)
async def list_knowledge_chunks(
    curriculum_version_id: UUID,
    principal: ReadPrincipal,
    session: DatabaseSession,
    review_state: ReviewState | None = None,
    source_document_id: UUID | None = None,
    competency_id: UUID | None = None,
    chunk_type: ChunkType | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> list[KnowledgeChunkResponse]:
    del principal
    records = await _execute_knowledge_operation(
        session,
        lambda: KnowledgePersistenceService(session).list_chunks(
            curriculum_version_id,
            review_state=review_state,
            source_document_id=source_document_id,
            competency_id=competency_id,
            chunk_type=chunk_type,
            limit=limit,
            offset=offset,
        ),
    )
    return [KnowledgeChunkResponse.from_domain(record) for record in records]


@router.get(
    "/{curriculum_version_id}/knowledge/chunks/{chunk_id}",
    operation_id="get_knowledge_chunk",
    response_model=KnowledgeChunkResponse,
    summary="Get a curriculum knowledge chunk",
)
async def get_knowledge_chunk(
    curriculum_version_id: UUID,
    chunk_id: UUID,
    principal: ReadPrincipal,
    session: DatabaseSession,
) -> KnowledgeChunkResponse:
    del principal
    record = await _execute_knowledge_operation(
        session,
        lambda: KnowledgePersistenceService(session).get_chunk(
            curriculum_version_id,
            chunk_id,
        ),
    )
    return KnowledgeChunkResponse.from_domain(record)


@router.patch(
    "/{curriculum_version_id}/knowledge/chunks/{chunk_id}/classification",
    operation_id="classify_knowledge_chunk",
    response_model=KnowledgeChunkResponse,
    summary="Correct a knowledge chunk classification",
)
async def classify_knowledge_chunk(
    curriculum_version_id: UUID,
    chunk_id: UUID,
    request: KnowledgeClassificationRequest,
    principal: ReviewPrincipal,
    session: DatabaseSession,
) -> KnowledgeChunkResponse:
    record = await _execute_knowledge_operation(
        session,
        lambda: KnowledgePersistenceService(session).classify_chunk(
            curriculum_version_id,
            chunk_id,
            competency_id=request.competency_id,
            skill_id=request.skill_id,
            sub_skill_id=request.sub_skill_id,
            learning_concept_id=request.learning_concept_id,
            expected_version=request.expected_version,
            actor_id=principal.subject_id,
        ),
        integrity_code="invalid_taxonomy_classification",
    )
    return KnowledgeChunkResponse.from_domain(record)


@router.post(
    "/{curriculum_version_id}/knowledge/chunks/{chunk_id}/review",
    operation_id="transition_knowledge_chunk_review",
    response_model=KnowledgeChunkResponse,
    summary="Advance a knowledge chunk review state",
)
async def transition_knowledge_chunk_review(
    curriculum_version_id: UUID,
    chunk_id: UUID,
    request: KnowledgeReviewTransitionRequest,
    principal: ReviewPrincipal,
    session: DatabaseSession,
) -> KnowledgeChunkResponse:
    record = await _execute_knowledge_operation(
        session,
        lambda: KnowledgePersistenceService(session).transition_chunk_review(
            curriculum_version_id,
            chunk_id,
            request.target,
            expected_version=request.expected_version,
            actor_id=principal.subject_id,
        ),
        integrity_code="knowledge_record_not_ready",
    )
    return KnowledgeChunkResponse.from_domain(record)


async def _execute_knowledge_operation[OperationResultT](
    session: AsyncSession,
    operation: Callable[[], Awaitable[OperationResultT]],
    *,
    integrity_code: str = "knowledge_invariant_violation",
) -> OperationResultT:
    try:
        return await operation()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": integrity_code},
        ) from error
    except (
        ActiveKnowledgeSourceRequiredError,
        ConcurrentKnowledgeVersionError,
        FinalKnowledgeRecordError,
        KnowledgeContractError,
        KnowledgeCurriculumNotFoundError,
        KnowledgeRecordNotFoundError,
        KnowledgeRecordNotReadyError,
        KnowledgeSourceCurriculumMismatchError,
        KnowledgeSourceDocumentNotFoundError,
        KnowledgeSourceMetadataMismatchError,
        SourceImportConflictError,
        TrustedKnowledgeSourceRequiredError,
    ) as error:
        raise _knowledge_http_exception(error) from error


def _knowledge_http_exception(error: Exception) -> HTTPException:
    if isinstance(error, KnowledgeCurriculumNotFoundError):
        return HTTPException(status_code=404, detail={"code": "curriculum_version_not_found"})
    if isinstance(error, KnowledgeSourceDocumentNotFoundError):
        return HTTPException(status_code=404, detail={"code": "source_document_not_found"})
    if isinstance(error, KnowledgeRecordNotFoundError):
        return HTTPException(status_code=404, detail={"code": "knowledge_record_not_found"})
    if isinstance(error, ConcurrentKnowledgeVersionError):
        return HTTPException(
            status_code=409,
            detail={
                "code": "concurrent_knowledge_modification",
                "expected_version": error.expected,
                "actual_version": error.actual,
            },
        )
    if isinstance(error, SourceImportConflictError):
        return HTTPException(status_code=409, detail={"code": "source_import_conflict"})
    if isinstance(error, FinalKnowledgeRecordError):
        return HTTPException(status_code=409, detail={"code": "final_knowledge_record"})
    if isinstance(error, KnowledgeContractError):
        return HTTPException(status_code=409, detail={"code": "invalid_review_transition"})
    if isinstance(error, KnowledgeSourceCurriculumMismatchError):
        code = "source_curriculum_mismatch"
    elif isinstance(error, TrustedKnowledgeSourceRequiredError):
        code = "trusted_source_required"
    elif isinstance(error, ActiveKnowledgeSourceRequiredError):
        code = "active_source_required"
    elif isinstance(error, KnowledgeSourceMetadataMismatchError):
        code = "source_metadata_mismatch"
    else:
        code = "knowledge_record_not_ready"
    return HTTPException(status_code=422, detail={"code": code})

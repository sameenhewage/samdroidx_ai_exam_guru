import asyncio
from typing import cast
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.api.routes.embedding_jobs import _execute_embedding_operation
from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.core.config import Settings
from exam_guru_api.knowledge.domain import ReviewState
from exam_guru_api.knowledge.embedding_job_repository import EmbeddingJobNotFoundError
from exam_guru_api.knowledge.embedding_job_schemas import (
    EmbeddingJobCreateRequest,
    EmbeddingJobResponse,
)
from exam_guru_api.knowledge.embedding_job_service import (
    EmbeddingCurriculumNotFoundError,
    EmbeddingIdempotencyConflictError,
    EmbeddingJobCreationResult,
    EmbeddingQueueUnavailableError,
    EmbeddingSourceNotFoundError,
    EmbeddingSourceNotReviewedError,
)
from exam_guru_api.knowledge.embedding_jobs import DeterministicEmbeddingDispatcher
from exam_guru_api.knowledge.embeddings import EmbeddingConfig
from exam_guru_api.knowledge.models import EmbeddingJobModel, EmbeddingJobStatus
from exam_guru_api.knowledge.repository import (
    EmbeddingSourceConflictError,
    EmbeddingSpaceConflictError,
    KnowledgeRecordNotFoundError,
)
from exam_guru_api.knowledge.service import EmbeddingRequiresReviewedRecordError
from exam_guru_api.retrieval.embeddings import (
    ActiveEmbeddingConfigUnavailableError,
    EmbeddingProviderRegistry,
    EmbeddingProviderUnavailableError,
)

CURRICULUM_ID = UUID(int=1_830_001)
RESOURCE_ID = UUID(int=1_830_002)
CONFIG = EmbeddingConfig("deterministic", "fixture", 3, "v1", "fixture-v1")


class RollbackSession:
    def __init__(self) -> None:
        self.rolled_back = False

    async def rollback(self) -> None:
        self.rolled_back = True


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (EmbeddingJobNotFoundError(RESOURCE_ID), 404, "embedding_job_not_found"),
        (EmbeddingCurriculumNotFoundError(), 404, "embedding_curriculum_not_found"),
        (EmbeddingSourceNotFoundError(), 404, "embedding_source_not_found"),
        (
            KnowledgeRecordNotFoundError("knowledge_chunk", RESOURCE_ID),
            404,
            "embedding_source_not_found",
        ),
        (EmbeddingSourceNotReviewedError(), 422, "embedding_source_not_reviewed"),
        (
            EmbeddingRequiresReviewedRecordError(RESOURCE_ID, ReviewState.DRAFT),
            422,
            "embedding_source_not_reviewed",
        ),
        (EmbeddingIdempotencyConflictError(), 409, "embedding_idempotency_conflict"),
        (
            EmbeddingSourceConflictError(RESOURCE_ID, RESOURCE_ID),
            409,
            "embedding_source_conflict",
        ),
        (EmbeddingSpaceConflictError(CONFIG), 409, "embedding_config_conflict"),
        (EmbeddingQueueUnavailableError(), 503, "embedding_queue_unavailable"),
        (
            ActiveEmbeddingConfigUnavailableError(),
            503,
            "embedding_config_unavailable",
        ),
        (EmbeddingProviderUnavailableError(), 503, "embedding_provider_unavailable"),
    ],
)
def test_embedding_job_errors_have_stable_sanitized_http_contracts(
    error: Exception,
    status_code: int,
    code: str,
) -> None:
    async def exercise() -> None:
        async def fail() -> None:
            raise error

        with pytest.raises(HTTPException) as raised:
            await _execute_embedding_operation(cast(AsyncSession, object()), fail)
        assert raised.value.status_code == status_code
        assert cast(dict[str, object], raised.value.detail) == {"code": code}

    asyncio.run(exercise())


def test_embedding_job_integrity_rolls_back_and_success_passes_through() -> None:
    async def exercise() -> None:
        session = RollbackSession()

        async def fail() -> None:
            raise IntegrityError("INSERT", {}, RuntimeError("constraint secret"))

        with pytest.raises(HTTPException) as raised:
            await _execute_embedding_operation(cast(AsyncSession, session), fail)
        assert raised.value.status_code == 409
        assert cast(dict[str, object], raised.value.detail) == {
            "code": "embedding_persistence_conflict"
        }
        assert session.rolled_back

        async def succeed() -> str:
            return "ok"

        assert await _execute_embedding_operation(cast(AsyncSession, session), succeed) == "ok"

    asyncio.run(exercise())


def test_embedding_job_route_functions_serialize_create_list_and_get(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from exam_guru_api.api.routes import embedding_jobs as routes

    async def exercise() -> None:
        job = cast(EmbeddingJobModel, object())
        response = cast(EmbeddingJobResponse, object())
        principal = Principal(RESOURCE_ID, frozenset({AdminRole.ADMIN}))
        calls: list[str] = []

        class FakeCommandService:
            def __init__(self, *args: object) -> None:
                calls.append("command_init")

            async def create(self, *args: object, **kwargs: object) -> EmbeddingJobCreationResult:
                calls.append("create")
                return EmbeddingJobCreationResult(job=job, deduplicated=True)

        class FakeReadService:
            def __init__(self, *args: object) -> None:
                calls.append("read_init")

            async def list(self, *args: object, **kwargs: object) -> tuple[EmbeddingJobModel, ...]:
                calls.append("list")
                return (job,)

            async def get(self, *args: object, **kwargs: object) -> EmbeddingJobModel:
                calls.append("get")
                return job

        def from_model(value: object, *, deduplicated: bool = False) -> EmbeddingJobResponse:
            assert value is job
            if deduplicated:
                calls.append("deduplicated_response")
            return response

        monkeypatch.setattr(routes, "EmbeddingJobService", FakeCommandService)
        monkeypatch.setattr(routes, "EmbeddingJobReadService", FakeReadService)
        monkeypatch.setattr(EmbeddingJobResponse, "from_model", from_model)

        created = await routes.create_embedding_job(
            CURRICULUM_ID,
            EmbeddingJobCreateRequest(historical_question_ids=(RESOURCE_ID,)),
            "route-key",
            principal,
            cast(AsyncSession, object()),
            EmbeddingProviderRegistry({}),
            DeterministicEmbeddingDispatcher(),
            Settings(environment="test"),
        )
        listed = await routes.list_embedding_jobs(
            CURRICULUM_ID,
            principal,
            cast(AsyncSession, object()),
            EmbeddingJobStatus.QUEUED,
            10,
            0,
        )
        fetched = await routes.get_embedding_job(
            CURRICULUM_ID,
            RESOURCE_ID,
            principal,
            cast(AsyncSession, object()),
        )

        assert created is response
        assert listed == [response]
        assert fetched is response
        assert calls == [
            "command_init",
            "create",
            "deduplicated_response",
            "read_init",
            "list",
            "read_init",
            "get",
        ]

    asyncio.run(exercise())

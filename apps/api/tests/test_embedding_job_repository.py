import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.knowledge.domain import ReviewState
from exam_guru_api.knowledge.embedding_job_repository import (
    EmbeddingJobNotFoundError,
    SqlAlchemyEmbeddingJobRepository,
)
from exam_guru_api.knowledge.models import EmbeddingJobModel, EmbeddingJobStatus

JOB_ID = UUID(int=1_831_001)
CURRICULUM_ID = UUID(int=1_831_002)
ACTOR_ID = UUID(int=1_831_003)
QUESTION_ID = UUID(int=1_831_004)
CHUNK_ID = UUID(int=1_831_005)
NOW = datetime.now(UTC)


def _session() -> tuple[AsyncSession, AsyncMock, AsyncMock, AsyncMock]:
    value = SimpleNamespace(
        scalar=AsyncMock(),
        scalars=AsyncMock(),
        get=AsyncMock(),
    )
    return cast(AsyncSession, value), value.scalar, value.scalars, value.get


def _job() -> EmbeddingJobModel:
    return EmbeddingJobModel(id=JOB_ID, curriculum_version_id=CURRICULUM_ID)


def test_repository_source_projection_handles_each_bounded_record_kind() -> None:
    async def exercise() -> None:
        session, _, scalars, _ = _session()
        repository = SqlAlchemyEmbeddingJobRepository(session)
        assert await repository.load_sources((), ()) == ()

        question_model = SimpleNamespace(
            id=QUESTION_ID,
            curriculum_version_id=CURRICULUM_ID,
            review_state=ReviewState.REVIEWED,
            text="question",
            version=2,
        )
        chunk_model = SimpleNamespace(
            id=CHUNK_ID,
            curriculum_version_id=CURRICULUM_ID,
            review_state=ReviewState.REVIEWED,
            text="chunk",
            version=3,
        )
        scalars.side_effect = [
            [question_model],
            [chunk_model],
            [question_model],
            [chunk_model],
        ]
        question = await repository.load_sources((QUESTION_ID,), ())
        chunk = await repository.load_sources((), (CHUNK_ID,))
        combined = await repository.load_sources((QUESTION_ID,), (CHUNK_ID,))

        assert [(item.kind, item.id, item.text) for item in question] == [
            ("historical_question", QUESTION_ID, "question")
        ]
        assert [(item.kind, item.id, item.text) for item in chunk] == [
            ("knowledge_chunk", CHUNK_ID, "chunk")
        ]
        assert [(item.kind, item.id) for item in combined] == [
            ("historical_question", QUESTION_ID),
            ("knowledge_chunk", CHUNK_ID),
        ]

    asyncio.run(exercise())


def test_repository_idempotent_insert_winner_and_impossible_missing_winner() -> None:
    async def exercise() -> None:
        values = {
            "created_by": ACTOR_ID,
            "idempotency_key_hash": "sha256:" + "1" * 64,
        }
        inserted = _job()
        session, scalar, _, _ = _session()
        scalar.return_value = inserted
        result = await SqlAlchemyEmbeddingJobRepository(session).store_job(values)
        assert result.job is inserted
        assert result.created is True

        session, scalar, _, _ = _session()
        scalar.side_effect = [None, inserted]
        existing = await SqlAlchemyEmbeddingJobRepository(session).store_job(values)
        assert existing.job is inserted
        assert existing.created is False

        session, scalar, _, _ = _session()
        scalar.side_effect = [None, None]
        with pytest.raises(RuntimeError, match="winner"):
            await SqlAlchemyEmbeddingJobRepository(session).store_job(values)

    asyncio.run(exercise())


def test_repository_attach_and_get_return_direct_cas_results() -> None:
    async def exercise() -> None:
        job = _job()
        session, scalar, _, _ = _session()
        scalar.side_effect = [job, job]
        repository = SqlAlchemyEmbeddingJobRepository(session)
        assert await repository.attach_queue_message(JOB_ID, "message") is job
        assert await repository.get_job(CURRICULUM_ID, JOB_ID) is job

    asyncio.run(exercise())


def test_repository_fallbacks_not_found_filters_and_recovery_scan() -> None:
    async def exercise() -> None:
        job = _job()
        session, scalar, scalars, get = _session()
        scalar.side_effect = [None, job, object(), None, None, None]
        scalars.side_effect = [[job], [job], [job], [job]]
        get.return_value = job
        repository = SqlAlchemyEmbeddingJobRepository(session)

        assert await repository.attach_queue_message(JOB_ID, "message") is job
        assert (
            await repository.latest_failed_retry(
                curriculum_version_id=CURRICULUM_ID,
                actor_id=ACTOR_ID,
                request_fingerprint="sha256:" + "2" * 64,
            )
            is None
        )
        with pytest.raises(EmbeddingJobNotFoundError):
            await repository.get_job(CURRICULUM_ID, JOB_ID)
        with pytest.raises(EmbeddingJobNotFoundError):
            await repository.attach_queue_message(JOB_ID, "message")
        assert await repository.get_job_unscoped(JOB_ID) is job
        assert await repository.list_jobs(
            CURRICULUM_ID,
            status=None,
            limit=10,
            offset=0,
        ) == (job,)
        assert await repository.list_jobs(
            CURRICULUM_ID,
            status=EmbeddingJobStatus.QUEUED,
            limit=10,
            offset=0,
        ) == (job,)
        assert await repository.lock_recoverable_outbox_jobs(
            created_before=NOW,
            limit=10,
        ) == (job,)
        assert await repository.lock_expired_claims(
            claimed_before=NOW,
            limit=10,
        ) == (job,)

    asyncio.run(exercise())


def test_repository_cas_writes_cover_progress_and_both_terminal_states() -> None:
    async def exercise() -> None:
        job = _job()
        session, scalar, _, _ = _session()
        scalar.side_effect = [JOB_ID, job, job, job, job, job, job, None]
        repository = SqlAlchemyEmbeddingJobRepository(session)

        assert await repository.curriculum_exists(CURRICULUM_ID)
        assert await repository.claim(JOB_ID, claimed_at=NOW) is job
        assert (
            await repository.advance_progress(
                JOB_ID,
                expected_version=1,
                embedded=True,
                updated_at=NOW,
            )
            is job
        )
        assert (
            await repository.advance_progress(
                JOB_ID,
                expected_version=2,
                embedded=False,
                updated_at=NOW,
            )
            is job
        )
        assert (
            await repository.complete(
                JOB_ID,
                expected_version=3,
                succeeded=True,
                failure_code=None,
                completed_at=NOW,
            )
            is job
        )
        assert (
            await repository.complete(
                JOB_ID,
                expected_version=3,
                succeeded=False,
                failure_code="embedding_internal_error",
                completed_at=NOW,
            )
            is job
        )
        assert await repository.expire_claim(job, completed_at=NOW) is job
        assert await repository.expire_claim(job, completed_at=NOW) is None

    asyncio.run(exercise())

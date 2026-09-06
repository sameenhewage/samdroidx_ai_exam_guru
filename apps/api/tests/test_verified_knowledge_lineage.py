import asyncio
from dataclasses import replace
from typing import TypedDict, cast
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.documents.domain import ExtractionStatus
from exam_guru_api.documents.fidelity_models import PageTextCandidateModel
from exam_guru_api.generation.repository import GenerationContextRecord
from exam_guru_api.generation.run_service import (
    GenerationContextSourceUntrustedError,
    GenerationCurriculumInactiveError,
    GenerationIdempotencyConflictError,
    GenerationRunService,
)
from exam_guru_api.knowledge.domain import (
    KnowledgeChunk,
    KnowledgeContractError,
    Provenance,
    ReviewState,
)
from exam_guru_api.knowledge.embedding_job_repository import EmbeddingSourceRecord
from exam_guru_api.knowledge.embedding_job_service import (
    EmbeddingJobService,
    EmbeddingSourceNotReviewedError,
    _source_fingerprint,
)
from exam_guru_api.knowledge.service import (
    KnowledgePersistenceService,
    TrustedKnowledgeSourceRequiredError,
)
from tests.test_embedding_job_service import _record
from tests.test_generation_run_service import (
    CHUNK_ID,
    FakeGenerationRepository,
    build_service,
    context_record,
    create,
    retrieval_filters,
)
from tests.test_knowledge_service_boundaries import CANDIDATE_ID, knowledge_chunk, source_document


class EmbeddingLineageChanges(TypedDict, total=False):
    source_candidate_id: UUID | None
    source_candidate_sha256: str | None
    source_fidelity_current: bool
    metadata_resolved: bool
    catalogue_admitted: bool


class GenerationLineageChanges(EmbeddingLineageChanges, total=False):
    source_active_for_ai: bool


def test_candidate_provenance_requires_typed_identity() -> None:
    with pytest.raises(KnowledgeContractError, match="source_candidate_id must be a UUID"):
        Provenance(UUID(int=1), 1, source_candidate_id=cast(UUID, "not-a-uuid"))


def test_generation_legacy_context_defaults_fail_closed() -> None:
    original = context_record("knowledge_chunk", CHUNK_ID)
    record = GenerationContextRecord(
        record_kind=original.record_kind,
        id=original.id,
        curriculum_version_id=original.curriculum_version_id,
        text=original.text,
        version=original.version,
        review_state=original.review_state,
        competency_id=original.competency_id,
        skill_id=original.skill_id,
        sub_skill_id=None,
        learning_concept_id=None,
        source_document_id=original.source_document_id,
        source_curriculum_version_id=original.source_curriculum_version_id,
        source_checksum_sha256=original.source_checksum_sha256,
        source_status=ExtractionStatus.TRUSTED,
        source_active_for_ai=True,
        page_number=1,
        source_block_id=original.source_block_id,
    )
    with pytest.raises(GenerationContextSourceUntrustedError):
        GenerationRunService._validate_context_records(
            retrieval_filters(), (record.id,), (), (record,)
        )


def test_embedding_legacy_source_defaults_fail_closed() -> None:
    record = EmbeddingSourceRecord(
        kind="knowledge_chunk",
        id=UUID(int=1),
        curriculum_version_id=UUID(int=2),
        review_state=ReviewState.REVIEWED,
        text="Legacy reviewed text is not verified evidence.",
        version=2,
        active_for_ai=True,
    )
    with pytest.raises(EmbeddingSourceNotReviewedError, match="current verified page lineage"):
        EmbeddingJobService._validate_sources(
            record.curriculum_version_id, (), (record.id,), (record,)
        )


def test_document_trust_without_page_evidence_cannot_authorize_import() -> None:
    async def scenario() -> None:
        session = AsyncMock(spec=AsyncSession)
        session.get.return_value = source_document()
        session.scalar.return_value = None
        service = KnowledgePersistenceService(cast(AsyncSession, session))
        with pytest.raises(TrustedKnowledgeSourceRequiredError):
            await service._validate_source(knowledge_chunk())

    asyncio.run(scenario())


def test_missing_provenance_does_not_bypass_embedding_source_validation() -> None:
    async def scenario() -> None:
        service = KnowledgePersistenceService(cast(AsyncSession, AsyncMock(spec=AsyncSession)))
        with pytest.raises((TrustedKnowledgeSourceRequiredError, AttributeError)):
            await service._require_active_source(cast(KnowledgeChunk, None))

    asyncio.run(scenario())


def test_unresolved_metadata_blocks_previously_trusted_source() -> None:
    async def scenario() -> None:
        source = source_document()
        source.metadata_review_required = True
        session = AsyncMock(spec=AsyncSession)
        session.get.return_value = source
        service = KnowledgePersistenceService(cast(AsyncSession, session))
        with pytest.raises(TrustedKnowledgeSourceRequiredError):
            await service._validate_source(replace(knowledge_chunk(), text="Chunk"))

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "changes",
    [
        {"source_active_for_ai": False},
        {"source_candidate_id": None},
        {"source_candidate_sha256": None},
        {"source_fidelity_current": False},
        {"metadata_resolved": False},
        {"catalogue_admitted": False},
    ],
)
def test_generation_rejects_each_missing_server_owned_lineage_fact(
    changes: GenerationLineageChanges,
) -> None:
    record = replace(context_record("knowledge_chunk", CHUNK_ID), **changes)
    with pytest.raises(GenerationContextSourceUntrustedError):
        GenerationRunService._validate_context_records(
            retrieval_filters(), (record.id,), (), (record,)
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"source_candidate_id": None},
        {"source_candidate_sha256": None},
        {"source_fidelity_current": False},
        {"metadata_resolved": False},
        {"catalogue_admitted": False},
    ],
)
def test_embedding_rejects_each_missing_server_owned_lineage_fact(
    changes: EmbeddingLineageChanges,
) -> None:
    record = replace(_record(), **changes)
    with pytest.raises(EmbeddingSourceNotReviewedError, match="verified page lineage"):
        EmbeddingJobService._validate_sources(
            record.curriculum_version_id, (record.id,), (), (record,)
        )


@pytest.mark.parametrize("field", ["source_candidate_id", "source_candidate_sha256"])
def test_embedding_fingerprints_bind_candidate_identity_and_text_hash(field: str) -> None:
    record = _record()
    changed = (
        replace(record, source_candidate_id=UUID(int=999))
        if field == "source_candidate_id"
        else replace(record, source_candidate_sha256="f" * 64)
    )
    assert _source_fingerprint((record,)) != _source_fingerprint((changed,))


@pytest.mark.parametrize("field", ["source_candidate_id", "source_candidate_sha256"])
def test_generation_request_fingerprints_bind_candidate_identity_and_text_hash(field: str) -> None:
    async def scenario() -> None:
        repository = FakeGenerationRepository()
        service, _, _ = build_service(repository)
        original = await create(service, key="lineage-fingerprint")
        repository.records = tuple(
            replace(record, source_candidate_id=UUID(int=999))
            if field == "source_candidate_id"
            else replace(record, source_candidate_sha256="f" * 64)
            for record in repository.records
        )
        with pytest.raises(GenerationIdempotencyConflictError):
            await create(service, key="lineage-fingerprint")
        changed = await create(service, key="new-lineage-fingerprint")
        assert changed.run.request_fingerprint != original.run.request_fingerprint

    asyncio.run(scenario())


def test_generation_rejects_active_but_unadmitted_curriculum() -> None:
    async def scenario() -> None:
        repository = FakeGenerationRepository()
        assert repository.scope is not None
        repository.scope = replace(repository.scope, catalogue_admitted=False)
        service, _, dispatcher = build_service(repository)
        with pytest.raises(GenerationCurriculumInactiveError):
            await create(service)
        assert dispatcher.dispatched == []

    asyncio.run(scenario())


def test_nonimport_operations_never_auto_bind_legacy_artifacts() -> None:
    async def scenario() -> None:
        session = AsyncMock(spec=AsyncSession)
        session.get.return_value = source_document()
        record = knowledge_chunk()
        record = replace(record, provenance=replace(record.provenance, source_candidate_id=None))
        service = KnowledgePersistenceService(cast(AsyncSession, session))
        with pytest.raises(TrustedKnowledgeSourceRequiredError):
            await service._require_active_source(record)
        session.scalar.assert_not_awaited()

    asyncio.run(scenario())


def test_reviewed_record_still_requires_current_taxonomy_and_learning_scope() -> None:
    async def scenario() -> None:
        session = AsyncMock(spec=AsyncSession)
        session.get.return_value = source_document()
        session.scalar.side_effect = [PageTextCandidateModel(id=CANDIDATE_ID), False]
        record = replace(
            knowledge_chunk(), review_state=ReviewState.REVIEWED, competency_id=UUID(int=999)
        )
        service = KnowledgePersistenceService(cast(AsyncSession, session))
        with pytest.raises(TrustedKnowledgeSourceRequiredError):
            await service._require_active_source(record)
        assert session.scalar.await_count == 2

    asyncio.run(scenario())

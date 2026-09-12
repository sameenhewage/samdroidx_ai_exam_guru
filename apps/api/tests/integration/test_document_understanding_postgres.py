import asyncio
import hashlib
from dataclasses import replace
from typing import Any
from uuid import UUID, uuid4

import pymupdf
import pytest
from alembic import command
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from testcontainers.community.postgres import PostgresContainer

from exam_guru_api.auth.domain import AuthorizationError
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.documents.fidelity_service import PageFidelityService
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.documents.page_images import SourceCandidateImageMetadata, image_artifact_id
from exam_guru_api.documents.understanding_contracts import _canonical_bytes, _canonical_json
from exam_guru_api.documents.understanding_models import (
    ObservationCandidateModel,
    PageVerificationDecisionModel,
    PageVerificationReportModel,
    TrustedPageKnowledgeModel,
)
from exam_guru_api.documents.understanding_provider import (
    UnderstandingProviderResult,
    UnderstandingRequest,
)
from exam_guru_api.documents.understanding_service import (
    PageUnderstandingService,
    UnderstandingConflictError,
    UnderstandingSourceError,
)
from exam_guru_api.documents.understanding_verification import (
    ObservationCandidate,
    PageArtifactIdentity,
    TrustedPageKnowledge,
    accept_trusted_page,
)
from exam_guru_api.generation.domain import GenerationAccounting
from exam_guru_api.infrastructure.migrations import (
    _config_for_database,
    assert_database_schema_current,
)
from tests.integration.test_fidelity_workspace_postgres import (
    ADMIN,
    REVIEWER,
    add_source,
    database_session,
)
from tests.integration.test_fidelity_workspace_postgres import (
    workspace_database_url as workspace_database_url,
)
from tests.test_document_understanding_contracts import counting_candidate, parse
from tests.test_document_understanding_provider import request as fixture_request

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("coordinate", [0.1, 0.0000001, -0.0])
def test_structured_source_fingerprints_have_the_same_canonical_bytes_in_postgres(
    workspace_database_url: str, coordinate: float
) -> None:
    async def compare() -> None:
        payload = counting_candidate()
        payload["observation"]["regions"][0]["bounds"]["left"] = coordinate
        candidate = parse(payload)
        async with database_session(workspace_database_url) as session:
            canonical = await session.scalar(
                text("SELECT public.paper_canonical_jsonb(CAST(:payload AS jsonb))"),
                {"payload": candidate.model_dump_json()},
            )
        assert _canonical_bytes(candidate).decode("utf-8") == canonical

    asyncio.run(compare())


async def page_input(
    session: AsyncSession,
) -> tuple[UnderstandingRequest, UnderstandingProviderResult, SourceCandidateImageMetadata]:
    document_id = await add_source(session, total=1)
    document = await session.get(SourceDocumentModel, document_id)
    assert document is not None
    fixture = fixture_request()
    request = UnderstandingRequest.model_validate(
        fixture.model_copy(
            update={
                "source": PageArtifactIdentity(
                    document_id=document_id,
                    source_sha256=document.checksum_sha256,
                    page_number=1,
                    image_sha256=fixture.source.image_sha256,
                )
            }
        )
    )
    result = UnderstandingProviderResult(
        source=request.source,
        profile=request.profile,
        content=parse(counting_candidate()),
        accounting=GenerationAccounting(
            input_tokens=10,
            output_tokens=20,
            total_tokens=30,
            cost_microusd=50,
            latency_ms=10,
        ),
    )
    width, height = request.image_dimensions
    metadata = SourceCandidateImageMetadata.model_validate(
        {
            "document_id": str(document_id),
            "source_checksum_sha256": document.checksum_sha256,
            "source_object_key": document.object_key,
            "source_size_bytes": document.size_bytes,
            "page_number": 1,
            "sha256": request.source.image_sha256,
            "width": width,
            "height": height,
            "dpi": 72,
            "content_type": "image/png",
            "rasterizer": "pymupdf",
            "rasterizer_version": pymupdf.VersionBind,
            "artifact": {
                "id": str(image_artifact_id(request.source.image_sha256)),
                "size_bytes": len(request.image_png),
                "sha256": request.source.image_sha256,
                "chunk_sha256": [request.source.image_sha256],
            },
        }
    )
    return request, result, metadata


async def approve_page(
    service: PageUnderstandingService, document_id: UUID, candidate_id: UUID, version: int
) -> TrustedPageKnowledge:
    return await service.verify(
        principal=ADMIN,
        document_id=document_id,
        page_number=1,
        candidate_id=candidate_id,
        expected_version=version,
        compared_with_original=True,
        reviewed_region_keys=("heading", "sequence", "groups", "answer"),
        accepted_claim_keys=("grouping",),
        resolved_uncertainty_keys=(),
        reason="Synthetic source comparison for isolated persistence testing",
    )


def test_observation_review_and_new_candidates_preserve_history_without_promoting_legacy_data(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            request, result, metadata = await page_input(session)
            service = PageUnderstandingService(session)
            request_id = uuid4()
            candidate = await service.record_result(
                principal=ADMIN,
                request_id=request_id,
                expected_version=0,
                request=request,
                result=result,
                image_metadata=metadata,
            )
            replay = await service.record_result(
                principal=ADMIN,
                request_id=request_id,
                expected_version=0,
                request=request,
                result=result,
                image_metadata=metadata,
            )
            assert replay == candidate
            page = await service.get_page(
                principal=REVIEWER, document_id=request.source.document_id, page_number=1
            )
            assert page.version == 1
            assert page.state == "needs_human_review"
            assert page.candidate == candidate
            assert page.trusted is None
            assert (
                await session.scalar(
                    text(
                        "SELECT count(*) FROM source_understanding_regions WHERE candidate_id=:id"
                    ),
                    {"id": candidate.id},
                )
                == 4
            )
            trusted = await approve_page(
                service, request.source.document_id, candidate.id, page.version
            )
            assert trusted.observation == candidate.content.observation
            assert trusted.revision == 1
            assert (
                await session.scalar(
                    text("SELECT trusted_page_knowledge_is_current(:id)"), {"id": trusted.id}
                )
                is True
            )
            assert await session.scalar(text("SELECT count(*) FROM source_page_ground_truth")) == 0
            assert (
                await session.scalar(text("SELECT count(*) FROM source_evaluation_references")) == 0
            )
            assert await session.scalar(text("SELECT count(*) FROM knowledge_chunks")) == 0
            changed = await service.record_result(
                principal=ADMIN,
                request_id=uuid4(),
                expected_version=2,
                request=request,
                result=result,
                image_metadata=metadata,
            )
            assert changed.id != candidate.id
            assert changed.revision == 2
            assert (
                await session.scalar(
                    text("SELECT trusted_page_knowledge_is_current(:id)"), {"id": trusted.id}
                )
                is False
            )
            assert (
                await session.scalar(
                    text("SELECT count(*) FROM trusted_page_knowledge WHERE document_id=:id"),
                    {"id": request.source.document_id},
                )
                == 1
            )
            current = await service.get_page(
                principal=ADMIN, document_id=request.source.document_id, page_number=1
            )
            assert current.version == 3
            assert current.trusted is None
            assert current.candidate == changed
            assert (
                await session.scalar(
                    text("SELECT count(*) FROM source_page_review_states WHERE document_id=:id"),
                    {"id": request.source.document_id},
                )
                == 0
            )

    asyncio.run(check())


def test_understanding_mutations_require_authority_and_current_versions(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            request, result, metadata = await page_input(session)
            service = PageUnderstandingService(session)
            with pytest.raises(AuthorizationError):
                await service.record_result(
                    principal=REVIEWER,
                    request_id=uuid4(),
                    expected_version=0,
                    request=request,
                    result=result,
                    image_metadata=metadata,
                )
            candidate = await service.record_result(
                principal=ADMIN,
                request_id=uuid4(),
                expected_version=0,
                request=request,
                result=result,
                image_metadata=metadata,
            )
            with pytest.raises(UnderstandingConflictError):
                await approve_page(service, request.source.document_id, candidate.id, 0)
            trusted = await approve_page(service, request.source.document_id, candidate.id, 1)
            with pytest.raises(UnderstandingConflictError):
                await approve_page(service, request.source.document_id, candidate.id, 1)
            page = await service.get_page(
                principal=REVIEWER, document_id=request.source.document_id, page_number=1
            )
            assert page.version == 2
            assert page.trusted == trusted

    asyncio.run(check())


def test_understanding_evidence_and_verified_snapshots_are_append_only_in_postgres(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            request, result, metadata = await page_input(session)
            service = PageUnderstandingService(session)
            candidate = await service.record_result(
                principal=ADMIN,
                request_id=uuid4(),
                expected_version=0,
                request=request,
                result=result,
                image_metadata=metadata,
            )
            trusted = await approve_page(service, request.source.document_id, candidate.id, 1)
            for query, identifier in (
                (
                    "UPDATE source_understanding_candidates SET revision=revision+1 WHERE id=:id",
                    candidate.id,
                ),
                ("DELETE FROM source_understanding_candidates WHERE id=:id", candidate.id),
                ("UPDATE trusted_page_knowledge SET revision=revision+1 WHERE id=:id", trusted.id),
                ("DELETE FROM source_understanding_decisions WHERE id=:id", trusted.decision.id),
            ):
                with pytest.raises(DBAPIError):
                    await session.execute(text(query), {"id": identifier})
                await session.rollback()
            assert (
                await session.scalar(
                    text("SELECT trusted_page_knowledge_is_current(:id)"), {"id": trusted.id}
                )
                is True
            )

    asyncio.run(check())


@pytest.mark.parametrize(
    "case",
    [
        "missing_regions",
        "duplicate_regions",
        "foreign_region",
        "foreign_claim",
        "duplicate_claim",
        "blank_reason",
        "control_reason",
        "unknown_policy",
        "wrong_audit_action",
    ],
)
def test_postgres_rejects_verification_decisions_without_exact_review_attestations(
    workspace_database_url: str, case: str
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            request, result, metadata = await page_input(session)
            service = PageUnderstandingService(session)
            candidate = await service.record_result(
                principal=ADMIN,
                request_id=uuid4(),
                expected_version=0,
                request=request,
                result=result,
                image_metadata=metadata,
            )
            page = await service.get_page(
                principal=ADMIN, document_id=request.source.document_id, page_number=1
            )
            assert page.report is not None
            report_id = await session.scalar(
                select(PageVerificationReportModel.id).where(
                    PageVerificationReportModel.candidate_id == candidate.id
                )
            )
            assert report_id is not None
            trusted = accept_trusted_page(
                candidate,
                page.report,
                principal=ADMIN,
                decision_id=uuid4(),
                revision=1,
                reason="Synthetic review for database invariants",
                compared_with_original=True,
                reviewed_region_keys=("heading", "sequence", "groups", "answer"),
                accepted_claim_keys=("grouping",),
                resolved_uncertainty_keys=(),
            )
            payload = trusted.decision.model_dump(mode="json")
            if case == "missing_regions":
                payload["reviewed_region_keys"] = []
            elif case == "duplicate_regions":
                payload["reviewed_region_keys"] *= 2
            elif case == "foreign_region":
                payload["reviewed_region_keys"] = ["foreign"]
            elif case == "foreign_claim":
                payload["accepted_claim_keys"] = ["foreign"]
            elif case == "duplicate_claim":
                payload["accepted_claim_keys"] *= 2
            elif case == "blank_reason":
                payload["reason"] = " "
            elif case == "control_reason":
                payload["reason"] = "review\x01note"
            elif case == "unknown_policy":
                payload["policy_version"] = "model-self-approval"
            audit = AdminAuditEventModel(
                id=uuid4(),
                actor_id=ADMIN.subject_id,
                resource_type="page_understanding",
                resource_id=request.source.document_id,
                action="page_understanding.observed"
                if case == "wrong_audit_action"
                else "page_understanding.verified",
                payload={
                    "page_number": 1,
                    "version": 2,
                    "candidate_id": str(candidate.id),
                    "report_id": str(report_id),
                    "run_id": str(candidate.run_id),
                    "trusted_knowledge_id": str(trusted.id),
                },
            )
            session.add(audit)
            session.add(
                PageVerificationDecisionModel(
                    id=trusted.id,
                    candidate_id=candidate.id,
                    report_id=report_id,
                    document_id=request.source.document_id,
                    page_number=1,
                    payload=payload,
                    fingerprint=hashlib.sha256(_canonical_json(payload).encode()).hexdigest(),
                    created_by=ADMIN.subject_id,
                    audit_event_id=audit.id,
                )
            )
            with pytest.raises(DBAPIError):
                await session.commit()
            await session.rollback()

    asyncio.run(check())


@pytest.mark.parametrize(
    "case",
    [
        "source",
        "provider",
        "output_budget",
        "cost_budget",
        "price",
        "image_dimensions",
        "source_size",
        "chunk_hash",
    ],
)
def test_invalid_provider_results_never_create_observation_history(
    workspace_database_url: str, case: str
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            request, result, metadata = await page_input(session)
            if case == "source":
                result = result.model_copy(
                    update={"source": result.source.model_copy(update={"page_number": 2})}
                )
            elif case == "provider":
                result = result.model_copy(
                    update={
                        "profile": result.profile.model_copy(
                            update={"model_version": "another-model"}
                        )
                    }
                )
            elif case == "output_budget":
                request = request.model_copy(
                    update={"budget": request.budget.model_copy(update={"max_output_tokens": 1})}
                )
            elif case == "cost_budget":
                request = request.model_copy(
                    update={"budget": request.budget.model_copy(update={"max_cost_microusd": 1})}
                )
            elif case == "price":
                result = result.model_copy(
                    update={"accounting": replace(result.accounting, cost_microusd=51)}
                )
            elif case == "image_dimensions":
                metadata = metadata.model_copy(update={"width": metadata.width + 1})
            elif case == "source_size":
                metadata = metadata.model_copy(
                    update={"source_size_bytes": metadata.source_size_bytes + 1}
                )
            else:
                metadata = metadata.model_copy(
                    update={
                        "artifact": metadata.artifact.model_copy(
                            update={"chunk_sha256": ("0" * 64,)}
                        )
                    }
                )
            with pytest.raises(ValueError, match="understanding"):
                await PageUnderstandingService(session).record_result(
                    principal=ADMIN,
                    request_id=uuid4(),
                    expected_version=0,
                    request=request,
                    result=result,
                    image_metadata=metadata,
                )
            assert (
                await session.scalar(
                    text("SELECT count(*) FROM source_understanding_runs WHERE document_id=:id"),
                    {"id": request.source.document_id},
                )
                == 0
            )

    asyncio.run(check())


def test_unprocessed_missing_and_stale_source_commands_remain_untrusted(
    workspace_database_url: str,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            request, result, metadata = await page_input(session)
            service = PageUnderstandingService(session)
            page = await service.get_page(
                principal=REVIEWER, document_id=request.source.document_id, page_number=1
            )
            assert page.state == "unprocessed"
            assert page.trusted is None
            with pytest.raises(UnderstandingSourceError):
                await service.get_page(principal=ADMIN, document_id=uuid4(), page_number=1)
            with pytest.raises(UnderstandingSourceError):
                await service.get_page(
                    principal=ADMIN, document_id=request.source.document_id, page_number=2
                )
            with pytest.raises(UnderstandingConflictError):
                await approve_page(service, request.source.document_id, uuid4(), 0)
            request_id = uuid4()
            candidate = await service.record_result(
                principal=ADMIN,
                request_id=request_id,
                expected_version=0,
                request=request,
                result=result,
                image_metadata=metadata,
            )
            with pytest.raises(UnderstandingConflictError):
                await approve_page(service, request.source.document_id, uuid4(), 1)
            with pytest.raises(UnderstandingConflictError):
                await service.record_result(
                    principal=ADMIN,
                    request_id=uuid4(),
                    expected_version=0,
                    request=request,
                    result=result,
                    image_metadata=metadata,
                )
            changed_content = counting_candidate()
            changed_content["education"]["claims"][0]["description"] = "Changed interpretation"
            with pytest.raises(UnderstandingConflictError):
                await service.record_result(
                    principal=ADMIN,
                    request_id=request_id,
                    expected_version=1,
                    request=request,
                    result=result.model_copy(update={"content": parse(changed_content)}),
                    image_metadata=metadata,
                )
            current = await service.get_page(
                principal=ADMIN, document_id=request.source.document_id, page_number=1
            )
            assert current.candidate == candidate
            assert current.version == 1

    asyncio.run(check())


@pytest.mark.parametrize("kind", ["candidate", "report", "trusted"])
def test_page_reads_reject_corrupt_snapshot_fingerprints(
    workspace_database_url: str, kind: str
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            request, result, metadata = await page_input(session)
            service = PageUnderstandingService(session)
            candidate = await service.record_result(
                principal=ADMIN,
                request_id=uuid4(),
                expected_version=0,
                request=request,
                result=result,
                image_metadata=metadata,
            )
            trusted = await approve_page(service, request.source.document_id, candidate.id, 1)
            row = (
                await session.get(ObservationCandidateModel, candidate.id)
                if kind == "candidate"
                else await session.scalar(
                    select(PageVerificationReportModel).where(
                        PageVerificationReportModel.candidate_id == candidate.id
                    )
                )
                if kind == "report"
                else await session.get(TrustedPageKnowledgeModel, trusted.id)
            )
            assert row is not None
            with session.no_autoflush:
                row.fingerprint = "0" * 64
                with pytest.raises(ValueError, match="fingerprint"):
                    await service.get_page(
                        principal=ADMIN, document_id=request.source.document_id, page_number=1
                    )
            await session.rollback()

    asyncio.run(check())


def test_concurrent_page_verification_has_one_current_revision(workspace_database_url: str) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            request, result, metadata = await page_input(session)
            candidate = await PageUnderstandingService(session).record_result(
                principal=ADMIN,
                request_id=uuid4(),
                expected_version=0,
                request=request,
                result=result,
                image_metadata=metadata,
            )

        async def verify() -> TrustedPageKnowledge:
            async with database_session(workspace_database_url) as session:
                return await approve_page(
                    PageUnderstandingService(session), request.source.document_id, candidate.id, 1
                )

        outcomes = await asyncio.gather(verify(), verify(), return_exceptions=True)
        assert sum(isinstance(value, TrustedPageKnowledge) for value in outcomes) == 1
        assert sum(isinstance(value, UnderstandingConflictError) for value in outcomes) == 1
        async with database_session(workspace_database_url) as session:
            page = await PageUnderstandingService(session).get_page(
                principal=ADMIN, document_id=request.source.document_id, page_number=1
            )
            assert page.version == 2
            assert page.trusted is not None
            assert page.trusted.revision == 1

    asyncio.run(check())


@pytest.mark.parametrize("same_request", [True, False])
def test_concurrent_observation_delivery_is_idempotent_or_conflicting(
    workspace_database_url: str, same_request: bool
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            request, result, metadata = await page_input(session)
        request_id = uuid4()

        async def deliver(identifier: UUID) -> ObservationCandidate:
            async with database_session(workspace_database_url) as session:
                return await PageUnderstandingService(session).record_result(
                    principal=ADMIN,
                    request_id=identifier,
                    expected_version=0,
                    request=request,
                    result=result,
                    image_metadata=metadata,
                )

        outcomes = await asyncio.gather(
            deliver(request_id),
            deliver(request_id if same_request else uuid4()),
            return_exceptions=True,
        )
        assert sum(isinstance(value, ObservationCandidate) for value in outcomes) == (
            2 if same_request else 1
        )
        if same_request:
            assert outcomes[0] == outcomes[1]
            assert not isinstance(outcomes[0], Exception)
        else:
            assert sum(isinstance(value, UnderstandingConflictError) for value in outcomes) == 1
        async with database_session(workspace_database_url) as session:
            assert (
                await session.scalar(
                    text("SELECT count(*) FROM source_understanding_runs WHERE document_id=:id"),
                    {"id": request.source.document_id},
                )
                == 1
            )
            page = await PageUnderstandingService(session).get_page(
                principal=ADMIN, document_id=request.source.document_id, page_number=1
            )
            assert page.version == 1
            assert page.candidate is not None
            assert page.candidate.revision == 1

    asyncio.run(check())


@pytest.mark.parametrize("missing", [ObservationCandidateModel, PageVerificationReportModel])
def test_missing_verification_dependencies_cannot_create_trusted_knowledge(
    workspace_database_url: str,
    missing: type[ObservationCandidateModel | PageVerificationReportModel],
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            request, result, metadata = await page_input(session)
            service = PageUnderstandingService(session)
            candidate = await service.record_result(
                principal=ADMIN,
                request_id=uuid4(),
                expected_version=0,
                request=request,
                result=result,
                image_metadata=metadata,
            )
            original = session.get

            async def missing_dependency(entity: Any, *args: Any, **kwargs: Any) -> Any:
                return None if entity is missing else await original(entity, *args, **kwargs)

            with pytest.MonkeyPatch.context() as patch:
                patch.setattr(session, "get", missing_dependency)
                with pytest.raises(UnderstandingConflictError):
                    await approve_page(service, request.source.document_id, candidate.id, 1)
            page = await service.get_page(
                principal=ADMIN, document_id=request.source.document_id, page_number=1
            )
            assert page.version == 1
            assert page.trusted is None

    asyncio.run(check())


def test_understanding_schema_matches_the_migrated_metadata(workspace_database_url: str) -> None:
    assert_database_schema_current(workspace_database_url)


def test_migration_preserves_legacy_evidence_and_refuses_history_loss() -> None:
    async def legacy_snapshot(url: str, identifier: UUID) -> object:
        async with database_session(url) as session:
            return await session.scalar(
                text("""
                SELECT jsonb_build_object(
                    'source',(SELECT to_jsonb(d) FROM source_documents d WHERE d.id=:id),
                    'candidates',(SELECT jsonb_agg(to_jsonb(c) ORDER BY c.id)
                        FROM source_page_text_candidates c WHERE c.document_id=:id),
                    'events',(SELECT jsonb_agg(to_jsonb(e) ORDER BY e.version)
                        FROM source_page_review_events e WHERE e.document_id=:id),
                    'state',(SELECT to_jsonb(s) FROM source_page_review_states s
                        WHERE s.document_id=:id))
            """),
                {"id": identifier},
            )

    async def seed_legacy(url: str) -> UUID:
        async with database_session(url) as session:
            identifier = await add_source(session, total=1, extracted_count=1, legacy_trusted=True)
            await PageFidelityService(session).record_candidate(
                identifier,
                1,
                raw_text="Legacy source evidence",
                method="legacy",
                actor_id=ADMIN.subject_id,
                provenance={"source_reprocessing_required": True},
            )
            return identifier

    async def check_empty(url: str) -> None:
        async with database_session(url) as session:
            assert (
                await session.scalar(text("SELECT count(*) FROM source_understanding_candidates"))
                == 0
            )
            assert await session.scalar(text("SELECT count(*) FROM trusted_page_knowledge")) == 0
            assert await session.scalar(text("SELECT count(*) FROM knowledge_embeddings")) == 0

    async def add_new_evidence(url: str) -> UUID:
        async with database_session(url) as session:
            request, result, metadata = await page_input(session)
            candidate = await PageUnderstandingService(session).record_result(
                principal=ADMIN,
                request_id=uuid4(),
                expected_version=0,
                request=request,
                result=result,
                image_metadata=metadata,
            )
            return candidate.id

    with pytest.MonkeyPatch.context() as environment:
        environment.delenv("EXAM_GURU_DATABASE_URL", raising=False)
        with PostgresContainer(
            image="pgvector/pgvector:0.8.6-pg18-trixie",
            username="exam_guru",
            password=uuid4().hex,
            dbname="understanding_migration_test",
            driver="asyncpg",
        ) as postgres:
            url = postgres.get_connection_url()
            config = _config_for_database(url)
            command.upgrade(config, "0042_evaluation_references")
            identifier = asyncio.run(seed_legacy(url))
            before = asyncio.run(legacy_snapshot(url, identifier))
            command.upgrade(config, "head")
            assert asyncio.run(legacy_snapshot(url, identifier)) == before
            asyncio.run(check_empty(url))
            assert_database_schema_current(url)
            command.downgrade(config, "0042_evaluation_references")
            assert asyncio.run(legacy_snapshot(url, identifier)) == before
            command.upgrade(config, "head")
            asyncio.run(add_new_evidence(url))
            with pytest.raises(DBAPIError, match="cannot discard document understanding evidence"):
                command.downgrade(config, "0042_evaluation_references")
            assert asyncio.run(legacy_snapshot(url, identifier)) == before
            assert_database_schema_current(url)

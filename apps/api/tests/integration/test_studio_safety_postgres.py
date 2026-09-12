import asyncio
import hashlib
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.sql import Executable
from testcontainers.community.postgres import PostgresContainer

from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.documents.domain import SourceDocumentType
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.documents.schemas import MaterialStatus
from exam_guru_api.documents.service import (
    ConcurrentMaterialScopeVersionError,
    FixtureProvenanceEvidence,
    FixtureQuarantineResult,
    SourceDocumentNotFoundError,
    SourceDocumentService,
)
from exam_guru_api.infrastructure.migrations import ALEMBIC_CONFIG_PATH
from exam_guru_api.infrastructure.object_storage import ObjectStorage
from exam_guru_api.knowledge.embeddings import EmbeddingResult
from exam_guru_api.knowledge.service import KnowledgePersistenceService
from exam_guru_api.retrieval.repository import PostgresHybridRetrievalRepository
from tests.integration.test_verified_knowledge_lineage_postgres import CONFIG, seed

pytestmark = pytest.mark.integration
ADMIN = Principal(UUID(int=101), frozenset({AdminRole.ADMIN}))
REASON = "Exact synthetic source contents and upload provenance reviewed in a disposable database"


@dataclass(frozen=True)
class SafetyDatabase:
    url: str
    existing: dict[UUID, dict[str, object]]


def migration_config(url: str) -> Config:
    config = Config(str(ALEMBIC_CONFIG_PATH))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


@asynccontextmanager
async def database_session(url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


async def seed_preexisting(url: str) -> dict[UUID, dict[str, object]]:
    async with database_session(url) as session:
        snapshots: dict[UUID, dict[str, object]] = {}
        for filename in ("E2E teacher's real material.pdf", "Synthetic exact fixture.pdf"):
            identifier = uuid4()
            checksum = hashlib.sha256(identifier.bytes).hexdigest()
            await session.execute(
                text(
                    "INSERT INTO source_documents (id, checksum_sha256, object_key, "
                    "original_filename, content_type, size_bytes, document_type, "
                    "original_page_count, created_by, updated_by) VALUES "
                    "(:id, :checksum, :key, :filename, 'application/pdf', 100, "
                    "'teacher_guide', 1, :actor, :actor)"
                ),
                {
                    "id": identifier,
                    "checksum": checksum,
                    "key": f"sources/{checksum[:2]}/{checksum}.pdf",
                    "filename": filename,
                    "actor": ADMIN.subject_id,
                },
            )
            session.add(
                AdminAuditEventModel(
                    id=uuid4(),
                    actor_id=ADMIN.subject_id,
                    resource_type="source_document",
                    resource_id=identifier,
                    action="source_document.uploaded",
                    payload={"checksum_sha256": checksum},
                )
            )
            await session.commit()
            snapshots[identifier] = cast(
                dict[str, object],
                await session.scalar(
                    text("SELECT to_jsonb(d) FROM source_documents d WHERE id=:id"),
                    {"id": identifier},
                ),
            )
        return snapshots


@pytest.fixture(scope="module")
def safety_database() -> Iterator[SafetyDatabase]:
    with pytest.MonkeyPatch.context() as environment:
        environment.delenv("EXAM_GURU_DATABASE_URL", raising=False)
        with PostgresContainer(
            image="pgvector/pgvector:0.8.6-pg18-trixie",
            username="exam_guru",
            password=uuid4().hex,
            dbname="disposable_studio_safety",
            driver="asyncpg",
        ) as postgres:
            url = postgres.get_connection_url()
            config = migration_config(url)
            command.upgrade(config, "0036_resumable_source_uploads")
            existing = asyncio.run(seed_preexisting(url))
            command.upgrade(config, "head")
            yield SafetyDatabase(url, existing)


async def execute_and_commit(session: AsyncSession, statement: Executable | None) -> None:
    if statement is not None:
        await session.execute(statement)
    await session.commit()


def service(session: AsyncSession) -> SourceDocumentService:
    return SourceDocumentService(session, cast(ObjectStorage, object()), max_upload_bytes=1024)


async def upload_evidence(
    session: AsyncSession, document: SourceDocumentModel
) -> FixtureProvenanceEvidence:
    upload = AdminAuditEventModel(
        id=uuid4(),
        actor_id=document.created_by,
        action="source_document.uploaded",
        resource_type="source_document",
        resource_id=document.id,
        payload={
            "checksum_sha256": document.checksum_sha256,
            "intake_metadata": document.intake_metadata,
            "metadata_review_required": bool(document.metadata_review_required),
        },
    )
    session.add(upload)
    await session.commit()
    return FixtureProvenanceEvidence(
        source_document_id=document.id,
        checksum_sha256=document.checksum_sha256,
        upload_audit_event_id=upload.id,
        fixture_reference="tests/integration/test_studio_safety_postgres.py:exact-source-factory",
        observed_evidence=(REASON,),
    )


async def add_source(
    session: AsyncSession, *, removed: bool = False, intake: bool = False
) -> FixtureProvenanceEvidence:
    identifier = uuid4()
    checksum = hashlib.sha256(identifier.bytes).hexdigest()
    document = SourceDocumentModel(
        id=identifier,
        checksum_sha256=checksum,
        object_key=f"sources/{checksum[:2]}/{checksum}.pdf",
        original_filename="Exact synthetic source fixture.pdf",
        content_type="application/pdf",
        size_bytes=100,
        document_type=SourceDocumentType.TEACHER_GUIDE,
        original_page_count=1,
        active_for_ai=not removed,
        removal_reason="Previously removed for incorrect grade" if removed else None,
        removed_by=UUID(int=201) if removed else None,
        removed_at=datetime(2026, 1, 1, tzinfo=UTC) if removed else None,
        intake_metadata={"candidate_grade": 7} if intake else None,
        metadata_review_required=intake,
        created_by=ADMIN.subject_id,
        updated_by=ADMIN.subject_id,
    )
    session.add(document)
    return await upload_evidence(session, document)


async def change(
    session: AsyncSession,
    evidence: FixtureProvenanceEvidence,
    *,
    restore: bool = False,
    version: int = 0,
) -> FixtureQuarantineResult:
    return await service(session).change_fixture_quarantine(
        evidence.source_document_id,
        quarantined=not restore,
        principal=ADMIN,
        expected_version=version,
        confirmation="restore_exact_source_fixture"
        if restore
        else "quarantine_exact_source_fixture",
        reason=REASON,
        provenance_evidence=evidence,
    )


def audit_payload(evidence: FixtureProvenanceEvidence) -> dict[str, object]:
    return {
        "confirmation": "quarantine_exact_source_fixture",
        "reason": REASON,
        "provenance_evidence": evidence.model_dump(mode="json"),
        "previous_version": 0,
        "version": 1,
        "from": {"quarantined_for_teacher_use": False, "active_for_ai": True},
        "to": {"quarantined_for_teacher_use": True, "active_for_ai": False},
    }


def test_forward_migration_preserves_every_preexisting_value_and_never_classifies_labels(
    safety_database: SafetyDatabase,
) -> None:
    async def scenario() -> None:
        async with database_session(safety_database.url) as session:
            for identifier, before in safety_database.existing.items():
                after = await session.scalar(
                    text(
                        "SELECT to_jsonb(d) - 'quarantined_for_teacher_use' "
                        "FROM source_documents d WHERE id=:id"
                    ),
                    {"id": identifier},
                )
                assert after == before
                document = await session.get(SourceDocumentModel, identifier)
                assert document is not None
                assert document.quarantined_for_teacher_use is False
            assert await session.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0043_document_understanding"
            )

    asyncio.run(scenario())


@pytest.mark.parametrize("removed", [False, True])
def test_quarantine_and_restore_are_versioned_and_material_reads_exclude_exact_source(
    safety_database: SafetyDatabase, removed: bool
) -> None:
    async def scenario() -> None:
        async with database_session(safety_database.url) as session:
            evidence = await add_source(session, removed=removed, intake=True)
            before = await service(session).list_materials(grade=7)
            assert evidence.source_document_id in {row.id for row in before}
            summary_before = next(
                row for row in await service(session).grade_summary() if row.grade == 7
            )
            receipt = await change(session, evidence)
            document = receipt.document
            assert document.metadata_scope_version == 1
            assert document.quarantined_for_teacher_use
            assert not document.active_for_ai
            assert document.metadata_review_required
            assert document.intake_metadata == {"candidate_grade": 7}
            assert document.original_page_count == 1
            assert document.extraction_status.value == "uploaded"
            removal = (document.removal_reason, document.removed_by, document.removed_at)
            if removed:
                assert removal == (
                    "Previously removed for incorrect grade",
                    UUID(int=201),
                    datetime(2026, 1, 1, tzinfo=UTC),
                )
            else:
                assert removal[:2] == (REASON, ADMIN.subject_id)
            for material_status in (None, *MaterialStatus):
                assert not await service(session).list_materials(
                    document_id=document.id, status=material_status
                )
            assert (await service(session).list_documents(document_id=document.id))[
                0
            ].id == document.id
            summary_after = next(
                row for row in await service(session).grade_summary() if row.grade == 7
            )
            assert summary_after.material_count == summary_before.material_count - 1
            with pytest.raises(SourceDocumentNotFoundError):
                await service(session).restore_to_ai_use(
                    document.id, expected_version=1, actor_id=ADMIN.subject_id
                )
            restored = (await change(session, evidence, restore=True, version=1)).document
            assert not restored.quarantined_for_teacher_use
            assert not restored.active_for_ai
            assert restored.metadata_scope_version == 2
            assert (restored.removal_reason, restored.removed_by, restored.removed_at) == removal
            assert restored.metadata_review_required
            assert restored.extraction_status.value == "uploaded"
            assert (await service(session).list_materials(document_id=document.id))[
                0
            ].status is MaterialStatus.REMOVED
            events = list(
                await session.scalars(
                    select(AdminAuditEventModel)
                    .where(
                        AdminAuditEventModel.resource_id == document.id,
                        AdminAuditEventModel.action.in_(
                            (
                                "source_document.fixture_quarantined",
                                "source_document.fixture_restored",
                            )
                        ),
                    )
                    .order_by(AdminAuditEventModel.payload["version"].as_integer())
                )
            )
            assert len(events) == 2
            assert events[0].id == receipt.audit_event_id
            assert events[0].payload == audit_payload(evidence) | {
                "from": {"quarantined_for_teacher_use": False, "active_for_ai": not removed}
            }
            assert events[1].payload["version"] == 2

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "problem", ["no_version", "no_audit", "active", "scope_version_only", "extraction", "rename"]
)
def test_direct_sql_cannot_quarantine_without_protected_version_evidence_and_preservation(
    safety_database: SafetyDatabase, problem: str
) -> None:
    async def scenario() -> None:
        async with database_session(safety_database.url) as session:
            evidence = await add_source(session, removed=True)
            values: dict[str, object] = {
                "quarantined_for_teacher_use": True,
                "metadata_scope_version": 1,
            }
            if problem == "no_version":
                values.pop("metadata_scope_version")
            elif problem == "scope_version_only":
                values.pop("quarantined_for_teacher_use")
            elif problem == "active":
                values.update(
                    active_for_ai=True, removal_reason=None, removed_by=None, removed_at=None
                )
            elif problem == "extraction":
                values.update(
                    extraction_status="extraction_pending",
                    extraction_attempt_count=1,
                    extraction_started_at=datetime.now(UTC),
                )
            elif problem == "rename":
                values["original_filename"] = "Reclassified source.pdf"
            with pytest.raises(IntegrityError):
                await execute_and_commit(
                    session,
                    update(SourceDocumentModel)
                    .where(SourceDocumentModel.id == evidence.source_document_id)
                    .values(**values),
                )
            await session.rollback()
            document = await session.get(SourceDocumentModel, evidence.source_document_id)
            assert document is not None
            assert not document.quarantined_for_teacher_use
            assert document.metadata_scope_version == 0

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "problem",
    [
        "confirmation",
        "reason",
        "checksum",
        "upload",
        "reference",
        "observations",
        "source",
        "actor",
        "from",
        "to",
        "previous_version",
        "version",
        "extra",
        "duplicate",
    ],
)
def test_database_rejects_blind_mismatched_or_duplicate_fixture_audit_proof(
    safety_database: SafetyDatabase, problem: str
) -> None:
    async def scenario() -> None:
        async with database_session(safety_database.url) as session:
            evidence = await add_source(session)
            payload = audit_payload(evidence)
            proof = evidence.model_dump(mode="json")
            if problem in {"confirmation", "reason", "from", "to", "previous_version", "version"}:
                payload[problem] = "invalid"
            elif problem == "checksum":
                proof["checksum_sha256"] = "b" * 64
            elif problem == "upload":
                proof["upload_audit_event_id"] = str(uuid4())
            elif problem == "reference":
                proof["fixture_reference"] = " "
            elif problem == "observations":
                proof["observed_evidence"] = []
            elif problem == "source":
                proof["source_document_id"] = str(uuid4())
            elif problem == "extra":
                proof["actor_id"] = str(ADMIN.subject_id)
            payload["provenance_evidence"] = proof
            for _ in range(2 if problem == "duplicate" else 1):
                session.add(
                    AdminAuditEventModel(
                        id=uuid4(),
                        actor_id=UUID(int=901) if problem == "actor" else ADMIN.subject_id,
                        action="source_document.fixture_quarantined",
                        resource_type="source_document",
                        resource_id=evidence.source_document_id,
                        payload=payload,
                    )
                )
            with pytest.raises(IntegrityError):
                await execute_and_commit(
                    session,
                    update(SourceDocumentModel)
                    .where(SourceDocumentModel.id == evidence.source_document_id)
                    .values(
                        quarantined_for_teacher_use=True,
                        active_for_ai=False,
                        metadata_scope_version=1,
                        removal_reason=REASON,
                        removed_by=ADMIN.subject_id,
                        removed_at=datetime.now(UTC),
                        updated_by=ADMIN.subject_id,
                    ),
                )
            await session.rollback()
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(AdminAuditEventModel)
                    .where(
                        AdminAuditEventModel.resource_id == evidence.source_document_id,
                        AdminAuditEventModel.action == "source_document.fixture_quarantined",
                    )
                )
                == 0
            )

    asyncio.run(scenario())


@pytest.mark.parametrize("reactivate", [False, True])
def test_direct_restore_cannot_bypass_audit_or_reactivate_the_source(
    safety_database: SafetyDatabase, reactivate: bool
) -> None:
    async def scenario() -> None:
        async with database_session(safety_database.url) as session:
            evidence = await add_source(session)
            await change(session, evidence)
            values: dict[str, object] = {
                "quarantined_for_teacher_use": False,
                "metadata_scope_version": 2,
            }
            if reactivate:
                values.update(
                    active_for_ai=True, removal_reason=None, removed_by=None, removed_at=None
                )
            with pytest.raises(IntegrityError):
                await execute_and_commit(
                    session,
                    update(SourceDocumentModel)
                    .where(SourceDocumentModel.id == evidence.source_document_id)
                    .values(**values),
                )
            await session.rollback()
            document = await session.get(SourceDocumentModel, evidence.source_document_id)
            assert document is not None
            assert document.quarantined_for_teacher_use
            assert not document.active_for_ai
            assert document.metadata_scope_version == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "mutation", ["update_audit", "delete_audit", "delete_source", "initial_quarantine"]
)
def test_fixture_evidence_cannot_be_rewritten_deleted_or_invented_on_insert(
    safety_database: SafetyDatabase, mutation: str
) -> None:
    async def scenario() -> None:
        async with database_session(safety_database.url) as session:
            evidence = await add_source(session)
            receipt = await change(session, evidence)
            if mutation == "initial_quarantine":
                new_id = uuid4()
                checksum = hashlib.sha256(new_id.bytes).hexdigest()
                document = SourceDocumentModel(
                    id=new_id,
                    checksum_sha256=checksum,
                    object_key=f"sources/{checksum[:2]}/{checksum}.pdf",
                    original_filename="test.pdf",
                    content_type="application/pdf",
                    size_bytes=100,
                    document_type=SourceDocumentType.TEACHER_GUIDE,
                    quarantined_for_teacher_use=True,
                    active_for_ai=False,
                    removal_reason=REASON,
                    removed_by=ADMIN.subject_id,
                    removed_at=datetime.now(UTC),
                    created_by=ADMIN.subject_id,
                    updated_by=ADMIN.subject_id,
                )
                session.add(document)
            statement = (
                update(AdminAuditEventModel)
                .where(AdminAuditEventModel.id == receipt.audit_event_id)
                .values(payload={})
                if mutation == "update_audit"
                else delete(AdminAuditEventModel).where(
                    AdminAuditEventModel.id == receipt.audit_event_id
                )
                if mutation == "delete_audit"
                else delete(SourceDocumentModel).where(
                    SourceDocumentModel.id == evidence.source_document_id
                )
            )
            with pytest.raises(IntegrityError):
                await execute_and_commit(
                    session, statement if mutation != "initial_quarantine" else None
                )
            await session.rollback()

    asyncio.run(scenario())


def test_concurrent_quarantine_has_one_winner_and_one_immutable_event(
    safety_database: SafetyDatabase,
) -> None:
    async def scenario() -> None:
        async with database_session(safety_database.url) as session:
            evidence = await add_source(session)

        async def attempt() -> FixtureQuarantineResult | ConcurrentMaterialScopeVersionError:
            async with database_session(safety_database.url) as session:
                try:
                    return await change(session, evidence)
                except ConcurrentMaterialScopeVersionError as error:
                    return error

        results = await asyncio.gather(attempt(), attempt())
        assert sum(isinstance(result, FixtureQuarantineResult) for result in results) == 1
        assert (
            sum(isinstance(result, ConcurrentMaterialScopeVersionError) for result in results) == 1
        )
        async with database_session(safety_database.url) as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(AdminAuditEventModel)
                    .where(
                        AdminAuditEventModel.resource_id == evidence.source_document_id,
                        AdminAuditEventModel.action == "source_document.fixture_quarantined",
                    )
                )
                == 1
            )

    asyncio.run(scenario())


def test_quarantine_preserves_verified_page_knowledge_and_embedding_history_but_blocks_retrieval(
    safety_database: SafetyDatabase,
) -> None:
    async def scenario() -> None:
        async with database_session(safety_database.url) as session:
            source = await seed(session, trusted=True)
            document = await session.get(SourceDocumentModel, source.document_id)
            assert document is not None
            evidence = await upload_evidence(session, document)
            persistence = KnowledgePersistenceService(session)
            imported = await persistence.import_chunk(source.chunk(), actor_id=ADMIN.subject_id)
            await persistence.store_chunk_embedding(
                imported.record.id,
                EmbeddingResult(config=CONFIG, vector=(1.0, 0.0, 0.0)),
                actor_id=ADMIN.subject_id,
            )
            history_query = text("""
                SELECT jsonb_build_object(
                    'pages', (SELECT jsonb_agg(to_jsonb(p) ORDER BY p.id)
                        FROM source_pages p WHERE source_document_id=:id),
                    'blocks', (SELECT jsonb_agg(to_jsonb(b) ORDER BY b.id)
                        FROM extracted_blocks b WHERE source_document_id=:id),
                    'candidates', (SELECT jsonb_agg(to_jsonb(c) ORDER BY c.id)
                        FROM source_page_text_candidates c WHERE document_id=:id),
                    'reviews', (SELECT jsonb_agg(to_jsonb(r) ORDER BY r.id)
                        FROM source_page_review_events r WHERE document_id=:id),
                    'chunks', (SELECT jsonb_agg(to_jsonb(k) ORDER BY k.id)
                        FROM knowledge_chunks k WHERE source_document_id=:id),
                    'embeddings', (SELECT jsonb_agg(to_jsonb(e) ORDER BY e.id)
                        FROM knowledge_embeddings e WHERE knowledge_chunk_id=:chunk)
                )
            """)
            parameters = {"id": source.document_id, "chunk": imported.record.id}
            history = await session.scalar(history_query, parameters)
            assert isinstance(history, dict)
            assert all(history.values())
            repository = PostgresHybridRetrievalRepository(session, embedding_config=CONFIG)
            before = await repository.retrieve_candidates(
                query="Fractions", query_vector=(1.0, 0.0, 0.0), filters=source.scope
            )
            assert before.lexical_candidates
            assert before.vector_candidates
            await change(session, evidence)
            assert document.extraction_status.value == "trusted"
            assert await session.scalar(history_query, parameters) == history
            for restore in (False, True):
                if restore:
                    await change(session, evidence, restore=True, version=1)
                candidates = await repository.retrieve_candidates(
                    query="Fractions", query_vector=(1.0, 0.0, 0.0), filters=source.scope
                )
                assert not candidates.lexical_candidates
                assert not candidates.vector_candidates
                assert await session.scalar(history_query, parameters) == history

    asyncio.run(scenario())


def test_normal_restore_refreshes_a_source_quarantined_after_it_was_cached(
    safety_database: SafetyDatabase,
) -> None:
    async def scenario() -> None:
        async with database_session(safety_database.url) as stale:
            evidence = await add_source(stale, removed=True)
            cached = await stale.get(SourceDocumentModel, evidence.source_document_id)
            assert cached is not None
            assert not cached.quarantined_for_teacher_use
            async with database_session(safety_database.url) as writer:
                await change(writer, evidence)
            with pytest.raises(SourceDocumentNotFoundError):
                await service(stale).restore_to_ai_use(
                    evidence.source_document_id, expected_version=0, actor_id=ADMIN.subject_id
                )

    asyncio.run(scenario())


async def downgrade_snapshot(url: str) -> dict[str, object]:
    async with database_session(url) as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        snapshot = await session.scalar(
            text("""
            SELECT jsonb_build_object(
                'head', (SELECT version_num FROM alembic_version),
                'sources', (SELECT jsonb_agg(to_jsonb(d) ORDER BY d.id) FROM source_documents d),
                'audit', (SELECT jsonb_agg(to_jsonb(a) ORDER BY a.id) FROM admin_audit_events a),
                'candidates', (SELECT jsonb_agg(to_jsonb(c) ORDER BY c.id)
                    FROM source_page_text_candidates c),
                'reviews', (SELECT jsonb_agg(to_jsonb(e) ORDER BY e.id)
                    FROM source_page_review_events e),
                'states', (SELECT jsonb_agg(to_jsonb(s) ORDER BY s.document_id, s.page_number)
                    FROM source_page_review_states s),
                'ground_truth', (SELECT jsonb_agg(to_jsonb(g) ORDER BY g.id)
                    FROM source_page_ground_truth g)
            )
        """)
        )
        assert isinstance(snapshot, dict)
        return snapshot


def test_clean_downgrade_preserves_0036_sources_and_scope_guards() -> None:
    with pytest.MonkeyPatch.context() as environment:
        environment.delenv("EXAM_GURU_DATABASE_URL", raising=False)
        with PostgresContainer(
            image="pgvector/pgvector:0.8.6-pg18-trixie",
            username="exam_guru",
            password=uuid4().hex,
            dbname="disposable_studio_safety_downgrade",
            driver="asyncpg",
        ) as postgres:
            url = postgres.get_connection_url()
            config = migration_config(url)
            command.upgrade(config, "0036_resumable_source_uploads")
            before = asyncio.run(seed_preexisting(url))
            command.upgrade(config, "head")
            command.downgrade(config, "0036_resumable_source_uploads")

            async def verify() -> None:
                async with database_session(url) as session:
                    for identifier, snapshot in before.items():
                        assert (
                            await session.scalar(
                                text("SELECT to_jsonb(d) FROM source_documents d WHERE id=:id"),
                                {"id": identifier},
                            )
                            == snapshot
                        )
                    guard = await session.scalar(
                        text(
                            "SELECT pg_get_functiondef("
                            "'enforce_source_document_use_and_scope()'::regprocedure)"
                        )
                    )
                    assert isinstance(guard, str)
                    assert "metadata_review_required" in guard
                    assert "quarantined_for_teacher_use" not in guard
                    assert "trusted or imported source scope is immutable" in guard

            asyncio.run(verify())
            command.upgrade(config, "head")
            command.check(config)

            async def quarantine_history() -> None:
                async with database_session(url) as session:
                    evidence = await add_source(session)
                    await change(session, evidence)
                    await change(session, evidence, restore=True, version=1)

            asyncio.run(quarantine_history())
            history = asyncio.run(downgrade_snapshot(url))
            assert history["head"] == "0043_document_understanding"
            assert history["candidates"] is None
            with pytest.raises(
                IntegrityError, match="cannot discard source fixture quarantine history"
            ):
                command.downgrade(config, "0036_resumable_source_uploads")
            assert asyncio.run(downgrade_snapshot(url)) == history
            command.check(config)


def test_downgrade_refuses_to_discard_quarantine_history(safety_database: SafetyDatabase) -> None:
    async def scenario() -> None:
        async with database_session(safety_database.url) as session:
            await seed(session, trusted=True)
            evidence = await add_source(session)
            await change(session, evidence)
            await change(session, evidence, restore=True, version=1)

    asyncio.run(scenario())
    history = asyncio.run(downgrade_snapshot(safety_database.url))
    assert history["head"] == "0043_document_understanding"
    assert history["candidates"]
    with pytest.raises(IntegrityError, match="cannot discard source fidelity v2 protections"):
        command.downgrade(migration_config(safety_database.url), "0036_resumable_source_uploads")
    assert asyncio.run(downgrade_snapshot(safety_database.url)) == history
    command.check(migration_config(safety_database.url))

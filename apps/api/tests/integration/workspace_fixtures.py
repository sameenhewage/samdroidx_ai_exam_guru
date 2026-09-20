"""Shared Postgres integration fixtures for surviving document/knowledge suites.

Extracted from the removed V1 source-fidelity workspace test module so that
non-source-reader integration suites keep their disposable database, seeding
and principal helpers after the D9 legacy cutover.
"""

import hashlib
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.auth.ports import AuthenticationError, AuthenticationFailureCode
from exam_guru_api.curriculum.admission import (
    AdmissionDecisionRequest,
    get_catalogue_admission,
    record_catalogue_admission,
)
from exam_guru_api.curriculum.models import (
    CurriculumVersionModel,
    ExamConfigurationModel,
    MediumModel,
    SubjectModel,
)
from exam_guru_api.documents.domain import ExtractionStatus, SourceDocumentType
from exam_guru_api.documents.fidelity_models import (
    PageGroundTruthModel,
    PageReviewEventModel,
    PageReviewStateModel,
    PageTextCandidateModel,
)
from exam_guru_api.documents.fidelity_service import PageFidelityService
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.infrastructure.migrations import upgrade_database

pytestmark = pytest.mark.integration
ADMIN = Principal(UUID(int=86001), frozenset({AdminRole.ADMIN}))
REVIEWER = Principal(UUID(int=86002), frozenset({AdminRole.REVIEWER}))
ADMIN_HEADERS = {"Authorization": "Bearer admin-token"}
REVIEWER_HEADERS = {"Authorization": "Bearer reviewer-token"}
PREFIX = "/api/v1/admin"


class StaticIdentityProvider:
    async def authenticate(self, access_token: str) -> Principal:
        if access_token == "admin-token":
            return ADMIN
        if access_token == "reviewer-token":
            return REVIEWER
        raise AuthenticationError(AuthenticationFailureCode.INVALID)


@pytest.fixture(scope="module")
def workspace_database_url() -> Iterator[str]:
    with pytest.MonkeyPatch.context() as environment:
        environment.delenv("EXAM_GURU_DATABASE_URL", raising=False)
        with PostgresContainer(
            image="pgvector/pgvector:0.8.6-pg18-trixie",
            username="exam_guru",
            password=uuid4().hex,
            dbname="source_fidelity_workspace_test",
            driver="asyncpg",
        ) as postgres:
            database_url = postgres.get_connection_url()
            upgrade_database(database_url)
            yield database_url


@asynccontextmanager
async def database_session(database_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


async def add_source(
    session: AsyncSession,
    *,
    total: int | None = 3,
    curriculum_id: UUID | None = None,
    extracted_count: int | None = None,
    legacy_trusted: bool = False,
    intake_metadata: dict[str, object] | None = None,
) -> UUID:
    identifier = uuid4()
    checksum = hashlib.sha256(identifier.bytes).hexdigest()
    document = SourceDocumentModel(
        id=identifier,
        checksum_sha256=checksum,
        object_key=f"sources/{checksum[:2]}/{checksum}.pdf",
        original_filename="Private source comparison.pdf",
        content_type="application/pdf",
        size_bytes=100,
        document_type=SourceDocumentType.TEACHER_GUIDE,
        created_by=ADMIN.subject_id,
        updated_by=ADMIN.subject_id,
        original_page_count=total,
        curriculum_version_id=curriculum_id,
        intake_metadata=intake_metadata,
        metadata_review_required=intake_metadata is not None,
    )
    if extracted_count is not None:
        document.extraction_status = ExtractionStatus.EXTRACTED
        document.extraction_attempt_count = 1
        document.extraction_started_at = datetime.now(UTC)
        document.extraction_completed_at = datetime.now(UTC)
        document.extractor = "deterministic-native"
        document.extractor_version = "1"
        document.extracted_page_count = extracted_count
        document.extracted_block_count = 0
        document.extracted_character_count = 0
        document.native_text_page_ratio = 1.0
        document.needs_ocr = False
        document.ocr_page_count = 0
        document.extraction_config = {}
    if legacy_trusted:
        document.extraction_status = ExtractionStatus.TRUSTED
    session.add(document)
    if intake_metadata is not None:
        session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=ADMIN.subject_id,
                resource_type="source_document",
                resource_id=identifier,
                action="source_document.uploaded",
                payload={"intake_metadata": intake_metadata, "metadata_review_required": True},
            )
        )
    await session.commit()
    return identifier


async def add_curriculum(session: AsyncSession, *, medium_name: str = "Sinhala") -> UUID:
    exam = ExamConfigurationModel(
        id=uuid4(),
        code="G7-C" + uuid4().hex[:20].upper(),
        name="School Grade 7",
        grade=7,
        created_by=ADMIN.subject_id,
        updated_by=ADMIN.subject_id,
    )
    medium = MediumModel(
        id=uuid4(),
        code="m" + uuid4().hex[:15],
        name=medium_name,
        created_by=ADMIN.subject_id,
        updated_by=ADMIN.subject_id,
    )
    subject = SubjectModel(
        id=uuid4(),
        code="MATHS-C" + uuid4().hex.upper(),
        name="Mathematics",
        created_by=ADMIN.subject_id,
        updated_by=ADMIN.subject_id,
    )
    session.add_all([exam, medium, subject])
    await session.flush()
    curriculum = CurriculumVersionModel(
        id=uuid4(),
        exam_configuration_id=exam.id,
        medium_id=medium.id,
        subject_id=subject.id,
        code="2026",
        title="Mathematics curriculum 2026",
        created_by=ADMIN.subject_id,
        updated_by=ADMIN.subject_id,
    )
    session.add(curriculum)
    await session.commit()
    return curriculum.id


async def admit_curriculum(session: AsyncSession, curriculum_id: UUID) -> None:
    review = await get_catalogue_admission(session, curriculum_id)
    await record_catalogue_admission(
        session,
        curriculum_id,
        AdmissionDecisionRequest(
            state="approved",
            expected_version=review.version,
            expected_scope_fingerprint=review.scope_fingerprint,
            educational_approval=True,
            reason="Reviewed educational scope in the disposable scenario",
            source_reference="Deterministic local comparison register",
            evidence=("Grade, medium, subject and curriculum were explicitly reviewed",),
        ),
        principal=ADMIN,
    )
    await session.commit()


async def record_page(
    session: AsyncSession,
    document_id: UUID,
    page_number: int,
    *,
    value: str = "ගණිතය 2 + 3 = 5",
    method: str = "native",
    languages: tuple[str, ...] = ("si",),
) -> PageReviewStateModel:
    return await PageFidelityService(session).record_candidate(
        document_id,
        page_number,
        raw_text=value,
        method=method,
        actor_id=ADMIN.subject_id,
        provenance={
            "engine": "deterministic-comparison",
            "version": "1",
            "languages": list(languages),
            "confidence": 1.0,
        },
    )


async def confirm_state(session: AsyncSession, state: PageReviewStateModel) -> None:
    await PageFidelityService(session).confirm_page(
        state.document_id,
        state.page_number,
        candidate_id=state.current_candidate_id,
        expected_version=state.version,
        actor_id=ADMIN.subject_id,
        reason="Compared every line with the original source page",
    )


async def evidence_counts(session: AsyncSession) -> tuple[int, ...]:
    return tuple(
        [
            int(await session.scalar(select(func.count()).select_from(model)) or 0)
            for model in (
                PageReviewStateModel,
                PageTextCandidateModel,
                PageReviewEventModel,
                PageGroundTruthModel,
                AdminAuditEventModel,
            )
        ]
    )

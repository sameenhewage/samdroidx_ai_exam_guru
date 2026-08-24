import asyncio
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from hashlib import sha256
from threading import Barrier
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.auth.ports import AuthenticationError, AuthenticationFailureCode
from exam_guru_api.curriculum.domain import TaxonomyLevel, TaxonomyNode, TaxonomyReviewState
from exam_guru_api.curriculum.models import (
    CurriculumVersionModel,
    ExamConfigurationModel,
    MediumModel,
    TaxonomyNodeModel,
)
from exam_guru_api.documents.domain import ExtractionStatus, SourceDocumentType
from exam_guru_api.documents.models import ExtractedBlockModel, SourceDocumentModel, SourcePageModel
from exam_guru_api.infrastructure.migrations import upgrade_database
from exam_guru_api.main import create_app

PGVECTOR_IMAGE = "pgvector/pgvector:0.8.6-pg18-trixie"
ADMIN_ID = UUID(int=800_000)
REVIEWER_ID = UUID(int=800_001)
CURRICULUM_ID = UUID(int=800_010)
OTHER_CURRICULUM_ID = UUID(int=800_011)
COMPETENCY_ID = UUID(int=800_020)
SKILL_ID = UUID(int=800_021)
OTHER_COMPETENCY_ID = UUID(int=800_022)
TRUSTED_PAPER_ID = UUID(int=800_030)
TRUSTED_PAPER_BLOCK_ID = UUID(int=800_031)
TRUSTED_SYLLABUS_ID = UUID(int=800_032)
TRUSTED_SYLLABUS_BLOCK_ID = UUID(int=800_033)
UNTRUSTED_PAPER_ID = UUID(int=800_034)
UNTRUSTED_PAPER_BLOCK_ID = UUID(int=800_035)
OTHER_PAPER_ID = UUID(int=800_036)
OTHER_PAPER_BLOCK_ID = UUID(int=800_037)

ADMIN_HEADERS = {"Authorization": "Bearer admin-token"}
REVIEWER_HEADERS = {"Authorization": "Bearer reviewer-token"}
QUESTION_PATH = f"/api/v1/admin/curricula/{CURRICULUM_ID}/knowledge/questions"
CHUNK_PATH = f"/api/v1/admin/curricula/{CURRICULUM_ID}/knowledge/chunks"


class StaticIdentityProvider:
    async def authenticate(self, access_token: str) -> Principal:
        if access_token == "admin-token":
            return Principal(subject_id=ADMIN_ID, roles=frozenset({AdminRole.ADMIN}))
        if access_token == "reviewer-token":
            return Principal(subject_id=REVIEWER_ID, roles=frozenset({AdminRole.REVIEWER}))
        raise AuthenticationError(AuthenticationFailureCode.INVALID)


class DatabaseTestResources:
    def __init__(self, database_url: str) -> None:
        self.engine = create_async_engine(database_url)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def check_database(self) -> None:
        return None

    async def check_valkey(self) -> None:
        return None

    async def close(self) -> None:
        await self.engine.dispose()


def api_client(database_url: str) -> TestClient:
    return TestClient(
        create_app(
            identity_provider=StaticIdentityProvider(),
            resource_factory=lambda _: DatabaseTestResources(database_url),
        )
    )


async def _seed_curriculum(
    session: AsyncSession,
    *,
    suffix: int,
    curriculum_id: UUID,
    competency_id: UUID,
) -> None:
    exam_id = UUID(int=801_000 + suffix)
    medium_id = UUID(int=802_000 + suffix)
    session.add_all(
        [
            ExamConfigurationModel(
                id=exam_id,
                code=f"G5KAPI-{suffix}",
                name="Grade 5 Scholarship Examination",
                grade=5,
                active=True,
                created_by=ADMIN_ID,
                updated_by=ADMIN_ID,
            ),
            MediumModel(
                id=medium_id,
                code=f"ka{suffix}",
                name=f"Knowledge API medium {suffix}",
                active=True,
                created_by=ADMIN_ID,
                updated_by=ADMIN_ID,
            ),
        ]
    )
    await session.flush()
    session.add(
        CurriculumVersionModel(
            id=curriculum_id,
            exam_configuration_id=exam_id,
            medium_id=medium_id,
            code=f"KAPI-{suffix}",
            title=f"Knowledge API curriculum {suffix}",
            active=True,
            created_by=ADMIN_ID,
            updated_by=ADMIN_ID,
        )
    )
    await session.flush()
    session.add(
        TaxonomyNodeModel.from_domain(
            TaxonomyNode(
                id=competency_id,
                curriculum_version_id=curriculum_id,
                level=TaxonomyLevel.COMPETENCY,
                code="C1",
                title="Competency 1",
                review_state=TaxonomyReviewState.REVIEWED,
            ),
            ADMIN_ID,
        )
    )
    await session.flush()


async def _seed_skill(session: AsyncSession) -> None:
    session.add(
        TaxonomyNodeModel.from_domain(
            TaxonomyNode(
                id=SKILL_ID,
                curriculum_version_id=CURRICULUM_ID,
                level=TaxonomyLevel.SKILL,
                code="S1",
                title="Skill 1",
                parent_id=COMPETENCY_ID,
                review_state=TaxonomyReviewState.REVIEWED,
            ),
            ADMIN_ID,
        )
    )
    await session.flush()


async def _seed_source(
    session: AsyncSession,
    *,
    document_id: UUID,
    block_id: UUID,
    page_id: UUID,
    curriculum_id: UUID,
    document_type: SourceDocumentType,
    trusted: bool,
    suffix: str,
) -> None:
    text = f"Reviewed knowledge API source {suffix}"
    document = SourceDocumentModel(
        id=document_id,
        checksum_sha256=sha256(f"knowledge-api-{suffix}".encode()).hexdigest(),
        object_key=f"sources/knowledge-api-{suffix}.pdf",
        original_filename=f"knowledge-api-{suffix}.pdf",
        content_type="application/pdf",
        size_bytes=500,
        document_type=document_type,
        extraction_status=ExtractionStatus.EXTRACTION_PENDING,
        curriculum_version_id=curriculum_id,
        year=2021 if document_type is SourceDocumentType.PAST_PAPER else None,
        paper_code="P1" if document_type is SourceDocumentType.PAST_PAPER else None,
        extraction_attempt_count=1,
        extraction_started_at=datetime.now(UTC),
        created_by=ADMIN_ID,
        updated_by=ADMIN_ID,
    )
    session.add(document)
    await session.flush()
    session.add(
        SourcePageModel(
            id=page_id,
            source_document_id=document_id,
            page_number=1,
            extractor="fixture",
            extractor_version="v1",
            raw_text=text,
            reviewed_text=text,
            character_count=len(text),
            block_count=1,
            created_by=ADMIN_ID,
            updated_by=ADMIN_ID,
        )
    )
    await session.flush()
    session.add(
        ExtractedBlockModel(
            id=block_id,
            source_page_id=page_id,
            source_document_id=document_id,
            page_number=1,
            reading_order=0,
            extractor="fixture",
            extractor_version="v1",
            bbox_x0=0.0,
            bbox_y0=0.0,
            bbox_x1=1.0,
            bbox_y1=1.0,
            raw_text=text,
            reviewed_text=text,
            character_count=len(text),
            created_by=ADMIN_ID,
            updated_by=ADMIN_ID,
        )
    )
    await session.flush()
    if trusted:
        document.extraction_status = ExtractionStatus.EXTRACTED
        document.extractor = "fixture"
        document.extractor_version = "v1"
        document.extracted_page_count = 1
        document.extracted_block_count = 1
        document.extracted_character_count = len(text)
        document.native_text_page_ratio = 1.0
        document.needs_ocr = False
        document.extraction_completed_at = datetime.now(UTC)
        await session.flush()
        document.extraction_status = ExtractionStatus.IN_REVIEW
        await session.flush()
        document.extraction_status = ExtractionStatus.TRUSTED
        await session.flush()


@pytest.fixture(scope="module")
def knowledge_api_database_url() -> Iterator[str]:
    credentials = ("exam_guru", "knowledge-api-" + "only")
    with PostgresContainer(
        image=PGVECTOR_IMAGE,
        username=credentials[0],
        password=credentials[1],
        dbname="exam_guru_knowledge_api_test",
        driver="asyncpg",
    ) as postgres:
        database_url = postgres.get_connection_url()
        upgrade_database(database_url)

        async def seed() -> None:
            engine = create_async_engine(database_url)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions() as session:
                await _seed_curriculum(
                    session,
                    suffix=1,
                    curriculum_id=CURRICULUM_ID,
                    competency_id=COMPETENCY_ID,
                )
                await _seed_curriculum(
                    session,
                    suffix=2,
                    curriculum_id=OTHER_CURRICULUM_ID,
                    competency_id=OTHER_COMPETENCY_ID,
                )
                await _seed_skill(session)
                await _seed_source(
                    session,
                    document_id=TRUSTED_PAPER_ID,
                    block_id=TRUSTED_PAPER_BLOCK_ID,
                    page_id=UUID(int=803_001),
                    curriculum_id=CURRICULUM_ID,
                    document_type=SourceDocumentType.PAST_PAPER,
                    trusted=True,
                    suffix="trusted-paper",
                )
                await _seed_source(
                    session,
                    document_id=TRUSTED_SYLLABUS_ID,
                    block_id=TRUSTED_SYLLABUS_BLOCK_ID,
                    page_id=UUID(int=803_002),
                    curriculum_id=CURRICULUM_ID,
                    document_type=SourceDocumentType.SYLLABUS,
                    trusted=True,
                    suffix="trusted-syllabus",
                )
                await _seed_source(
                    session,
                    document_id=UNTRUSTED_PAPER_ID,
                    block_id=UNTRUSTED_PAPER_BLOCK_ID,
                    page_id=UUID(int=803_003),
                    curriculum_id=CURRICULUM_ID,
                    document_type=SourceDocumentType.PAST_PAPER,
                    trusted=False,
                    suffix="untrusted-paper",
                )
                await _seed_source(
                    session,
                    document_id=OTHER_PAPER_ID,
                    block_id=OTHER_PAPER_BLOCK_ID,
                    page_id=UUID(int=803_004),
                    curriculum_id=OTHER_CURRICULUM_ID,
                    document_type=SourceDocumentType.PAST_PAPER,
                    trusted=True,
                    suffix="other-paper",
                )
                await session.commit()
            await engine.dispose()

        asyncio.run(seed())
        yield database_url


def _question_payload(question_number: str) -> dict[str, object]:
    return {
        "year": 2021,
        "paper_code": "P1",
        "question_number": question_number,
        "text": f"Historical question {question_number}",
        "question_type": "multiple_choice",
        "marks": 2,
        "source_document_id": str(TRUSTED_PAPER_ID),
        "page_number": 1,
        "source_block_id": str(TRUSTED_PAPER_BLOCK_ID),
    }


def _chunk_payload(sequence: int) -> dict[str, object]:
    return {
        "chunk_type": "explanation",
        "text": f"Meaningful educational chunk {sequence}",
        "educational_boundary": f"Competency 1 / explanation {sequence}",
        "sequence": sequence,
        "source_document_id": str(TRUSTED_SYLLABUS_ID),
        "page_number": 1,
        "source_block_id": str(TRUSTED_SYLLABUS_BLOCK_ID),
    }


@pytest.mark.integration
def test_question_import_is_server_identified_authorized_scoped_and_idempotent(
    knowledge_api_database_url: str,
) -> None:
    payload = {
        **_question_payload("API-1"),
        "media_references": ["source://page/1/figure/1"],
        "options": ["12", "14", "16", "18"],
        "answer": "16",
        "marking_guidance": "Award two marks for selecting sixteen.",
        "marking_data": {
            "criteria": [{"description": "Selects sixteen.", "marks": 2}],
            "alternative_answers": ["16"],
        },
        "question_archetype": "single_best_answer",
        "difficulty_label": "medium",
        "difficulty_confidence": 0.9,
        "difficulty_source": "reviewer_confirmed",
    }
    with api_client(knowledge_api_database_url) as client:
        unauthenticated = client.post(QUESTION_PATH, json=payload)
        forbidden = client.post(QUESTION_PATH, json=payload, headers=REVIEWER_HEADERS)
        vector_rejected = client.post(
            QUESTION_PATH,
            json={**_question_payload("API-VECTOR"), "embedding": [0.1, 0.2]},
            headers=ADMIN_HEADERS,
        )
        created = client.post(QUESTION_PATH, json=payload, headers=ADMIN_HEADERS)
        duplicate = client.post(QUESTION_PATH, json=payload, headers=ADMIN_HEADERS)
        conflict = client.post(
            QUESTION_PATH,
            json={**payload, "answer": "14"},
            headers=ADMIN_HEADERS,
        )
        duplicate_options = client.post(
            QUESTION_PATH,
            json={**_question_payload("API-DUPLICATE-OPTIONS"), "options": ["A", "A"]},
            headers=ADMIN_HEADERS,
        )
        source_label_answer = client.post(
            QUESTION_PATH,
            json={
                **_question_payload("API-SOURCE-LABEL-ANSWER"),
                "options": ["Twelve", "Fourteen"],
                "answer": "B",
            },
            headers=ADMIN_HEADERS,
        )
        partial_difficulty = client.post(
            QUESTION_PATH,
            json={
                **_question_payload("API-PARTIAL-DIFFICULTY"),
                "difficulty_label": "easy",
            },
            headers=ADMIN_HEADERS,
        )
        unbounded_confidence = client.post(
            QUESTION_PATH,
            json={
                **_question_payload("API-UNBOUNDED-CONFIDENCE"),
                "difficulty_label": "easy",
                "difficulty_confidence": 1.01,
                "difficulty_source": "reviewer_confirmed",
            },
            headers=ADMIN_HEADERS,
        )
        listed = client.get(
            QUESTION_PATH,
            params={
                "review_state": "draft",
                "source_document_id": str(TRUSTED_PAPER_ID),
                "year": 2021,
                "paper_code": "P1",
                "limit": 1,
                "offset": 0,
            },
            headers=REVIEWER_HEADERS,
        )
        fetched = client.get(
            f"{QUESTION_PATH}/{created.json()['id']}",
            headers=REVIEWER_HEADERS,
        )
        cross_curriculum = client.get(
            f"/api/v1/admin/curricula/{OTHER_CURRICULUM_ID}/knowledge/questions/"
            f"{created.json()['id']}",
            headers=REVIEWER_HEADERS,
        )
        cross_curriculum_list = client.get(
            f"/api/v1/admin/curricula/{OTHER_CURRICULUM_ID}/knowledge/questions",
            headers=REVIEWER_HEADERS,
        )
        unknown_curriculum_list = client.get(
            f"/api/v1/admin/curricula/{UUID(int=999_999_998)}/knowledge/questions",
            headers=REVIEWER_HEADERS,
        )
        unbounded = client.get(QUESTION_PATH, params={"limit": 101}, headers=REVIEWER_HEADERS)

    assert unauthenticated.status_code == 401
    assert forbidden.status_code == 403
    assert vector_rejected.status_code == 422
    assert created.status_code == 201
    body = created.json()
    assert UUID(body["id"])
    assert body["curriculum_version_id"] == str(CURRICULUM_ID)
    assert body["review_state"] == "draft"
    assert body["version"] == 0
    assert body["provenance"] == {
        "source_document_id": str(TRUSTED_PAPER_ID),
        "page_number": 1,
        "source_block_id": str(TRUSTED_PAPER_BLOCK_ID),
    }
    assert body["classification"] == {
        "competency_id": None,
        "skill_id": None,
        "sub_skill_id": None,
        "learning_concept_id": None,
    }
    assert body["media_references"] == ["source://page/1/figure/1"]
    assert body["options"] == ["12", "14", "16", "18"]
    assert body["answer"] == "16"
    assert body["marking_guidance"] == "Award two marks for selecting sixteen."
    assert body["marking_data"] == {
        "alternative_answers": ["16"],
        "criteria": [{"description": "Selects sixteen.", "marks": 2}],
    }
    assert body["question_archetype"] == "single_best_answer"
    assert body["difficulty_label"] == "medium"
    assert body["difficulty_confidence"] == 0.9
    assert body["difficulty_source"] == "reviewer_confirmed"
    assert body["created_at"] == body["updated_at"]
    assert body["embedding_status"] == "not_embedded"
    assert body["embedding_configurations"] == []
    assert "embedding" not in body
    assert "vector" not in body
    assert "raw_vector" not in body
    assert body["deduplicated"] is False
    assert duplicate.status_code == 200
    assert duplicate.json()["id"] == body["id"]
    assert duplicate.json()["deduplicated"] is True
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "source_import_conflict"
    assert duplicate_options.status_code == 422
    assert source_label_answer.status_code == 201
    assert source_label_answer.json()["answer"] == "B"
    assert partial_difficulty.status_code == 422
    assert unbounded_confidence.status_code == 422
    assert listed.status_code == 200
    assert listed.json() == [{**body, "deduplicated": False}]
    assert fetched.status_code == 200
    assert fetched.json() == {**body, "deduplicated": False}
    assert cross_curriculum.status_code == 404
    assert cross_curriculum.json() == {"detail": {"code": "knowledge_record_not_found"}}
    assert cross_curriculum_list.status_code == 200
    assert cross_curriculum_list.json() == []
    assert unknown_curriculum_list.status_code == 404
    assert unknown_curriculum_list.json() == {"detail": {"code": "curriculum_version_not_found"}}
    assert unbounded.status_code == 422


@pytest.mark.integration
def test_question_classification_and_review_use_expected_version_and_forward_transitions(
    knowledge_api_database_url: str,
) -> None:
    with api_client(knowledge_api_database_url) as client:
        created = client.post(
            QUESTION_PATH,
            json=_question_payload("API-2"),
            headers=ADMIN_HEADERS,
        )
        item_path = f"{QUESTION_PATH}/{created.json()['id']}"
        classified = client.patch(
            f"{item_path}/classification",
            json={
                "competency_id": str(COMPETENCY_ID),
                "skill_id": str(SKILL_ID),
                "sub_skill_id": None,
                "learning_concept_id": None,
                "expected_version": 0,
            },
            headers=REVIEWER_HEADERS,
        )
        stale = client.patch(
            f"{item_path}/classification",
            json={
                "competency_id": str(COMPETENCY_ID),
                "skill_id": None,
                "expected_version": 0,
            },
            headers=REVIEWER_HEADERS,
        )
        invalid_transition = client.post(
            f"{item_path}/review",
            json={"target": "reviewed", "expected_version": 1},
            headers=REVIEWER_HEADERS,
        )
        cross_curriculum_taxonomy = client.patch(
            f"{item_path}/classification",
            json={
                "competency_id": str(OTHER_COMPETENCY_ID),
                "expected_version": 1,
            },
            headers=REVIEWER_HEADERS,
        )
        in_review = client.post(
            f"{item_path}/review",
            json={"target": "in_review", "expected_version": 1},
            headers=REVIEWER_HEADERS,
        )
        reviewed = client.post(
            f"{item_path}/review",
            json={"target": "reviewed", "expected_version": 2},
            headers=REVIEWER_HEADERS,
        )
        immutable = client.patch(
            f"{item_path}/classification",
            json={
                "competency_id": str(COMPETENCY_ID),
                "skill_id": None,
                "expected_version": 3,
            },
            headers=REVIEWER_HEADERS,
        )

    assert created.status_code == 201
    assert {
        "media_references": None,
        "options": None,
        "answer": None,
        "marking_guidance": None,
        "marking_data": None,
        "question_archetype": None,
        "difficulty_label": None,
        "difficulty_confidence": None,
        "difficulty_source": None,
    }.items() <= created.json().items()
    assert classified.status_code == 200
    assert classified.json()["version"] == 1
    assert classified.json()["classification"]["skill_id"] == str(SKILL_ID)
    assert stale.status_code == 409
    assert stale.json()["detail"] == {
        "code": "concurrent_knowledge_modification",
        "expected_version": 0,
        "actual_version": 1,
    }
    assert invalid_transition.status_code == 409
    assert invalid_transition.json()["detail"]["code"] == "invalid_review_transition"
    assert cross_curriculum_taxonomy.status_code == 422
    assert cross_curriculum_taxonomy.json()["detail"]["code"] == "invalid_taxonomy_classification"
    assert in_review.status_code == 200
    assert in_review.json()["review_state"] == "in_review"
    assert in_review.json()["version"] == 2
    assert reviewed.status_code == 200
    assert reviewed.json()["review_state"] == "reviewed"
    assert reviewed.json()["version"] == 3
    assert immutable.status_code == 409
    assert immutable.json()["detail"]["code"] == "final_knowledge_record"


@pytest.mark.integration
def test_chunk_api_supports_review_workflow_and_atomic_stale_version_race(
    knowledge_api_database_url: str,
) -> None:
    payload = _chunk_payload(10)
    with api_client(knowledge_api_database_url) as client:
        forbidden = client.post(CHUNK_PATH, json=payload, headers=REVIEWER_HEADERS)
        created = client.post(CHUNK_PATH, json=payload, headers=ADMIN_HEADERS)
        duplicate = client.post(CHUNK_PATH, json=payload, headers=ADMIN_HEADERS)
        listed = client.get(
            CHUNK_PATH,
            params={"chunk_type": "explanation", "review_state": "draft", "limit": 100},
            headers=REVIEWER_HEADERS,
        )
        fetched = client.get(
            f"{CHUNK_PATH}/{created.json()['id']}",
            headers=REVIEWER_HEADERS,
        )

    assert forbidden.status_code == 403
    assert created.status_code == 201
    assert created.json()["review_state"] == "draft"
    assert created.json()["version"] == 0
    assert duplicate.status_code == 200
    assert duplicate.json()["deduplicated"] is True
    assert created.json()["id"] in {chunk["id"] for chunk in listed.json()}
    assert fetched.json() == {**created.json(), "deduplicated": False}

    race_payload = _chunk_payload(11)
    with api_client(knowledge_api_database_url) as client:
        raced_chunk = client.post(CHUNK_PATH, json=race_payload, headers=ADMIN_HEADERS)
    item_path = f"{CHUNK_PATH}/{raced_chunk.json()['id']}"
    barrier = Barrier(2)

    def classify(skill_id: UUID | None) -> tuple[int, dict[str, object]]:
        barrier.wait()
        with api_client(knowledge_api_database_url) as race_client:
            response = race_client.patch(
                f"{item_path}/classification",
                json={
                    "competency_id": str(COMPETENCY_ID),
                    "skill_id": str(skill_id) if skill_id else None,
                    "expected_version": 0,
                },
                headers=REVIEWER_HEADERS,
            )
            return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(classify, (None, SKILL_ID)))

    assert sorted(status_code for status_code, _ in results) == [200, 409]
    conflict = next(body for status_code, body in results if status_code == 409)
    assert conflict["detail"] == {
        "code": "concurrent_knowledge_modification",
        "expected_version": 0,
        "actual_version": 1,
    }
    with api_client(knowledge_api_database_url) as client:
        persisted = client.get(item_path, headers=REVIEWER_HEADERS)
        in_review = client.post(
            f"{item_path}/review",
            json={"target": "in_review", "expected_version": 1},
            headers=REVIEWER_HEADERS,
        )
        reviewed = client.post(
            f"{item_path}/review",
            json={"target": "reviewed", "expected_version": 2},
            headers=REVIEWER_HEADERS,
        )
    assert persisted.json()["version"] == 1
    assert in_review.status_code == 200
    assert reviewed.status_code == 200
    assert reviewed.json()["version"] == 3


@pytest.mark.integration
def test_import_rejects_untrusted_cross_curriculum_and_missing_sources(
    knowledge_api_database_url: str,
) -> None:
    untrusted = {
        **_question_payload("API-UNTRUSTED"),
        "source_document_id": str(UNTRUSTED_PAPER_ID),
        "source_block_id": str(UNTRUSTED_PAPER_BLOCK_ID),
    }
    cross_curriculum = {
        **_question_payload("API-CROSS"),
        "source_document_id": str(OTHER_PAPER_ID),
        "source_block_id": str(OTHER_PAPER_BLOCK_ID),
    }
    missing = {
        **_question_payload("API-MISSING"),
        "source_document_id": str(UUID(int=999_999_999)),
        "source_block_id": str(UUID(int=999_999_997)),
    }
    with api_client(knowledge_api_database_url) as client:
        untrusted_response = client.post(QUESTION_PATH, json=untrusted, headers=ADMIN_HEADERS)
        cross_response = client.post(
            QUESTION_PATH,
            json=cross_curriculum,
            headers=ADMIN_HEADERS,
        )
        missing_response = client.post(QUESTION_PATH, json=missing, headers=ADMIN_HEADERS)

    assert untrusted_response.status_code == 422
    assert untrusted_response.json() == {"detail": {"code": "trusted_source_required"}}
    assert cross_response.status_code == 422
    assert cross_response.json() == {"detail": {"code": "source_curriculum_mismatch"}}
    assert missing_response.status_code == 404
    assert missing_response.json() == {"detail": {"code": "source_document_not_found"}}


@pytest.mark.integration
def test_knowledge_api_audits_every_successful_write(knowledge_api_database_url: str) -> None:
    async def read_events() -> list[AdminAuditEventModel]:
        engine = create_async_engine(knowledge_api_database_url)
        sessions = async_sessionmaker(engine)
        async with sessions() as session:
            events = list(
                await session.scalars(
                    select(AdminAuditEventModel)
                    .where(
                        AdminAuditEventModel.resource_type.in_(
                            ["historical_question", "knowledge_chunk"]
                        )
                    )
                    .order_by(AdminAuditEventModel.created_at, AdminAuditEventModel.id)
                )
            )
        await engine.dispose()
        return events

    events = asyncio.run(read_events())
    actions = [event.action for event in events]
    assert actions.count("knowledge.question.imported") == 3
    assert actions.count("knowledge.question.classified") == 1
    assert actions.count("knowledge.question.review_state_changed") == 2
    assert actions.count("knowledge.chunk.imported") == 2
    assert actions.count("knowledge.chunk.classified") == 1
    assert actions.count("knowledge.chunk.review_state_changed") == 2
    assert all("version" in event.payload for event in events)
    rich_import = next(
        event
        for event in events
        if event.action == "knowledge.question.imported"
        and event.payload.get("question_number") == "API-1"
    )
    assert rich_import.payload["historical_metadata"] == {
        "media_reference_count": 1,
        "option_count": 4,
        "answer_available": True,
        "marking_guidance_available": True,
        "marking_data_available": True,
        "question_archetype": "single_best_answer",
        "difficulty_label": "medium",
        "difficulty_confidence": 0.9,
        "difficulty_source": "reviewer_confirmed",
    }

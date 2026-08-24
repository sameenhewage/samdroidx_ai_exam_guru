import asyncio
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from threading import Barrier
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from exam_guru_api.analytics.models import AnalyticsRunModel
from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.auth.ports import AuthenticationError, AuthenticationFailureCode
from exam_guru_api.blueprints.domain import TaxonomyRequirement, TaxonomyTarget
from exam_guru_api.blueprints.models import PaperBlueprintModel
from exam_guru_api.blueprints.serialization import serialize_specification
from exam_guru_api.curriculum.domain import TaxonomyLevel, TaxonomyNode, TaxonomyReviewState
from exam_guru_api.curriculum.models import (
    CurriculumVersionModel,
    ExamConfigurationModel,
    MediumModel,
    TaxonomyNodeModel,
)
from exam_guru_api.infrastructure.migrations import assert_database_schema_current, upgrade_database
from exam_guru_api.main import create_app
from tests.test_blueprint_domain import (
    COMPETENCY_A,
    CURRICULUM_VERSION_ID,
    SKILL_A,
    baseline_priority,
    make_uniform_specification,
)
from tests.test_blueprint_persisted_analytics import analytics_record
from tests.test_blueprint_persistence_service import analytics_specification

PGVECTOR_IMAGE = "pgvector/pgvector:0.8.6-pg18-trixie"
ADMIN_ID = UUID(int=840_001)
REVIEWER_ID = UUID(int=840_002)
BASE_CURRICULUM_ID = CURRICULUM_VERSION_ID
ANALYTICS_CURRICULUM_ID = analytics_record().curriculum_version_id
OTHER_CURRICULUM_ID = UUID(int=840_010)
OTHER_ANALYTICS_RUN_ID = UUID(int=840_011)
DRAFT_SKILL_ID = UUID(int=840_020)
BASE_PATH = f"/api/v1/admin/curricula/{BASE_CURRICULUM_ID}/blueprints"
ANALYTICS_PATH = f"/api/v1/admin/curricula/{ANALYTICS_CURRICULUM_ID}/blueprints"
ADMIN_HEADERS = {"Authorization": "Bearer admin-token"}
REVIEWER_HEADERS = {"Authorization": "Bearer reviewer-token"}


class StaticIdentityProvider:
    async def authenticate(self, access_token: str) -> Principal:
        if access_token == "admin-token":
            return Principal(ADMIN_ID, frozenset({AdminRole.ADMIN}))
        if access_token == "reviewer-token":
            return Principal(REVIEWER_ID, frozenset({AdminRole.REVIEWER}))
        if access_token == "no-role-token":
            return Principal(UUID(int=840_003), frozenset())
        raise AuthenticationError(AuthenticationFailureCode.INVALID)


class DatabaseResources:
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
            resource_factory=lambda _: DatabaseResources(database_url),
        )
    )


def request_payload(
    specification: object,
    *,
    seed: int,
    analytics_run_id: UUID | None = None,
) -> dict[str, Any]:
    snapshot = cast(
        dict[str, Any],
        deepcopy(serialize_specification(specification)),  # type: ignore[arg-type]
    )
    for requirement in snapshot["taxonomy_requirements"]:
        priority = requirement["priority"]
        requirement["priority"] = {
            "baseline_score": priority["baseline_score"],
            "baseline_version": priority["baseline_version"],
            "baseline_evidence_refs": priority["baseline_evidence_refs"],
        }
    return {
        "seed": seed,
        "analytics_run_id": str(analytics_run_id) if analytics_run_id else None,
        "specification": snapshot,
    }


async def seed_curriculum(
    session: AsyncSession,
    *,
    curriculum_id: UUID,
    offset: int,
    medium_code: str,
    nodes: tuple[TaxonomyNode, ...],
) -> None:
    exam_id = UUID(int=841_000 + offset)
    medium_id = UUID(int=842_000 + offset)
    session.add_all(
        [
            ExamConfigurationModel(
                id=exam_id,
                code=f"BP-{offset}",
                name=f"Blueprint exam {offset}",
                grade=5,
                active=True,
                created_by=ADMIN_ID,
                updated_by=ADMIN_ID,
            ),
            MediumModel(
                id=medium_id,
                code=medium_code,
                name=f"Blueprint medium {offset}",
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
            code=f"BP-CUR-{offset}",
            title=f"Blueprint curriculum {offset}",
            active=True,
            created_by=ADMIN_ID,
            updated_by=ADMIN_ID,
        )
    )
    await session.flush()
    for node in nodes:
        session.add(TaxonomyNodeModel.from_domain(node, ADMIN_ID))
        await session.flush()


def analytics_nodes() -> tuple[TaxonomyNode, ...]:
    specification = analytics_specification()
    requirements = specification.taxonomy_requirements
    competency_id = requirements[0].target.competency_id
    return (
        TaxonomyNode(
            id=competency_id,
            curriculum_version_id=ANALYTICS_CURRICULUM_ID,
            level=TaxonomyLevel.COMPETENCY,
            code="C1",
            title="Analytics competency",
            review_state=TaxonomyReviewState.REVIEWED,
        ),
        *(
            TaxonomyNode(
                id=cast(UUID, requirement.target.skill_id),
                curriculum_version_id=ANALYTICS_CURRICULUM_ID,
                level=TaxonomyLevel.SKILL,
                code=f"S{index}",
                title=f"Analytics skill {index}",
                parent_id=competency_id,
                review_state=TaxonomyReviewState.REVIEWED,
            )
            for index, requirement in enumerate(requirements, start=1)
        ),
    )


async def seed_analytics_run(session: AsyncSession) -> None:
    record = analytics_record()
    session.add(
        AnalyticsRunModel(
            id=record.id,
            curriculum_version_id=record.curriculum_version_id,
            run_fingerprint=record.run_fingerprint,
            config_fingerprint=record.config_fingerprint,
            input_fingerprint=record.input_fingerprint,
            source_fingerprint=record.source_fingerprint,
            result_fingerprint=record.result_fingerprint,
            statistics_algorithm_version=record.statistics_algorithm_version,
            practice_priority_algorithm_version=record.practice_priority_algorithm_version,
            baseline_algorithm_version=record.baseline_algorithm_version,
            backtest_algorithm_version=record.backtest_algorithm_version,
            config=record.config,
            input_snapshot=record.input_snapshot,
            source_versions=record.source_versions,
            data_quality=record.data_quality,
            result=record.result,
            compute_duration_ms=record.compute_duration_ms,
            created_by=record.created_by,
        )
    )
    await session.flush()
    session.add(
        AnalyticsRunModel(
            id=OTHER_ANALYTICS_RUN_ID,
            curriculum_version_id=OTHER_CURRICULUM_ID,
            run_fingerprint="sha256:" + "b" * 64,
            config_fingerprint=record.config_fingerprint,
            input_fingerprint="sha256:" + "c" * 64,
            source_fingerprint="sha256:" + "d" * 64,
            result_fingerprint=record.result_fingerprint,
            statistics_algorithm_version=record.statistics_algorithm_version,
            practice_priority_algorithm_version=record.practice_priority_algorithm_version,
            baseline_algorithm_version=record.baseline_algorithm_version,
            backtest_algorithm_version=record.backtest_algorithm_version,
            config=record.config,
            input_snapshot=record.input_snapshot,
            source_versions=record.source_versions,
            data_quality=record.data_quality,
            result=record.result,
            compute_duration_ms=record.compute_duration_ms,
            created_by=record.created_by,
        )
    )


@pytest.fixture(scope="module")
def blueprint_database_url() -> Iterator[str]:
    credentials = ("exam_guru", "blueprints-" + "only")
    with PostgresContainer(
        image=PGVECTOR_IMAGE,
        username=credentials[0],
        password=credentials[1],
        dbname="exam_guru_blueprint_test",
        driver="asyncpg",
    ) as postgres:
        database_url = postgres.get_connection_url()
        upgrade_database(database_url)
        assert_database_schema_current(database_url)

        async def seed() -> None:
            engine = create_async_engine(database_url)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions() as session:
                await seed_curriculum(
                    session,
                    curriculum_id=BASE_CURRICULUM_ID,
                    offset=1,
                    medium_code="en",
                    nodes=(
                        TaxonomyNode(
                            id=COMPETENCY_A,
                            curriculum_version_id=BASE_CURRICULUM_ID,
                            level=TaxonomyLevel.COMPETENCY,
                            code="C1",
                            title="Base competency",
                            review_state=TaxonomyReviewState.REVIEWED,
                        ),
                        TaxonomyNode(
                            id=SKILL_A,
                            curriculum_version_id=BASE_CURRICULUM_ID,
                            level=TaxonomyLevel.SKILL,
                            code="S1",
                            title="Base skill",
                            parent_id=COMPETENCY_A,
                            review_state=TaxonomyReviewState.REVIEWED,
                        ),
                        TaxonomyNode(
                            id=DRAFT_SKILL_ID,
                            curriculum_version_id=BASE_CURRICULUM_ID,
                            level=TaxonomyLevel.SKILL,
                            code="S2",
                            title="Draft skill",
                            parent_id=COMPETENCY_A,
                            review_state=TaxonomyReviewState.DRAFT,
                        ),
                    ),
                )
                await seed_curriculum(
                    session,
                    curriculum_id=ANALYTICS_CURRICULUM_ID,
                    offset=2,
                    medium_code="si",
                    nodes=analytics_nodes(),
                )
                other_competency = UUID(int=843_001)
                await seed_curriculum(
                    session,
                    curriculum_id=OTHER_CURRICULUM_ID,
                    offset=3,
                    medium_code="ta",
                    nodes=(
                        TaxonomyNode(
                            id=other_competency,
                            curriculum_version_id=OTHER_CURRICULUM_ID,
                            level=TaxonomyLevel.COMPETENCY,
                            code="C1",
                            title="Other competency",
                            review_state=TaxonomyReviewState.REVIEWED,
                        ),
                    ),
                )
                await seed_analytics_run(session)
                await session.commit()
            await engine.dispose()

        asyncio.run(seed())
        yield database_url


@pytest.mark.integration
def test_blueprint_api_authorization_idempotency_exact_slots_audit_and_immutability(
    blueprint_database_url: str,
) -> None:
    payload = request_payload(make_uniform_specification((2,), 2), seed=2025)
    with api_client(blueprint_database_url) as client:
        unauthenticated = client.post(BASE_PATH, json=payload)
        reviewer_write = client.post(BASE_PATH, json=payload, headers=REVIEWER_HEADERS)
        created = client.post(BASE_PATH, json=payload, headers=ADMIN_HEADERS)
        duplicate = client.post(BASE_PATH, json=payload, headers=ADMIN_HEADERS)
        listed = client.get(BASE_PATH, headers=REVIEWER_HEADERS)
        fetched = client.get(
            f"{BASE_PATH}/{created.json()['id']}",
            headers=REVIEWER_HEADERS,
        )
        no_role = client.get(
            f"{BASE_PATH}/{created.json()['id']}",
            headers={"Authorization": "Bearer no-role-token"},
        )
        unbounded = client.get(BASE_PATH, params={"limit": 101}, headers=REVIEWER_HEADERS)
        cross_scope = client.get(
            f"{ANALYTICS_PATH}/{created.json()['id']}",
            headers=REVIEWER_HEADERS,
        )
        unknown_scope = client.post(
            f"/api/v1/admin/curricula/{UUID(int=999_999)}/blueprints",
            json=payload,
            headers=ADMIN_HEADERS,
        )

    assert unauthenticated.status_code == 401
    assert reviewer_write.status_code == 403
    assert no_role.status_code == 403
    assert unbounded.status_code == 422
    assert created.status_code == 201
    assert duplicate.status_code == 200
    body = created.json()
    assert duplicate.json() == {**body, "deduplicated": True}
    assert body["deduplicated"] is False
    assert body["curriculum_version_id"] == str(BASE_CURRICULUM_ID)
    assert body["analytics_run_id"] is None
    assert body["seed"] == 2025
    assert body["total_marks"] == 4
    assert body["slot_count"] == 2
    assert len(body["blueprint"]["slots"]) == 2
    assert sum(slot["marks"] for slot in body["blueprint"]["slots"]) == 4
    assert all(
        slot["generation_constraints"]["exact_marks"] == slot["marks"]
        for slot in body["blueprint"]["slots"]
    )
    assert all(
        requirement["priority"]["forecast_score"] is None
        for requirement in body["specification"]["taxonomy_requirements"]
    )
    assert all(node["review_state"] == "reviewed" for node in body["taxonomy_snapshot"])
    assert fetched.status_code == 200
    assert fetched.json() == body
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == body["id"]
    assert listed.json()[0]["slot_count"] == 2
    assert cross_scope.status_code == 404
    assert cross_scope.json() == {"detail": {"code": "paper_blueprint_not_found"}}
    assert unknown_scope.status_code == 404

    async def inspect() -> None:
        engine = create_async_engine(blueprint_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            blueprint_id = UUID(body["id"])
            assert await session.get(PaperBlueprintModel, blueprint_id) is not None
            audits = tuple(
                await session.scalars(
                    select(AdminAuditEventModel).where(
                        AdminAuditEventModel.action == "blueprint.created",
                        AdminAuditEventModel.resource_id == blueprint_id,
                    )
                )
            )
            assert len(audits) == 1
            assert audits[0].actor_id == ADMIN_ID
            assert audits[0].payload["slot_count"] == 2

            async def mutate_blueprint() -> None:
                await session.execute(
                    update(PaperBlueprintModel)
                    .where(PaperBlueprintModel.id == blueprint_id)
                    .values(seed=1)
                )
                await session.flush()

            with pytest.raises(IntegrityError):
                await mutate_blueprint()
            await session.rollback()

            async def delete_blueprint() -> None:
                await session.execute(
                    delete(PaperBlueprintModel).where(PaperBlueprintModel.id == blueprint_id)
                )
                await session.flush()

            with pytest.raises(IntegrityError):
                await delete_blueprint()
            await session.rollback()
        await engine.dispose()

    asyncio.run(inspect())


@pytest.mark.integration
def test_blueprint_api_uses_linked_analytics_and_rejects_spoofing_scope_taxonomy_and_rules(
    blueprint_database_url: str,
) -> None:
    record = analytics_record()
    analytics_spec = analytics_specification()
    payload = request_payload(
        analytics_spec,
        seed=7,
        analytics_run_id=record.id,
    )
    spoofed = deepcopy(payload)
    spoofed["specification"]["taxonomy_requirements"][0]["priority"]["forecast_score"] = 999
    cross_analytics = deepcopy(payload)
    cross_analytics["analytics_run_id"] = str(OTHER_ANALYTICS_RUN_ID)

    base_spec = make_uniform_specification((1,), 1)
    draft_target = TaxonomyTarget(COMPETENCY_A, DRAFT_SKILL_ID)
    draft_spec = replace(
        base_spec,
        taxonomy_requirements=(
            TaxonomyRequirement(
                target=draft_target,
                minimum_slots=1,
                maximum_slots=1,
                priority=baseline_priority("draft"),
                retrieval_query_hints=("draft target",),
                generation_instructions=("generate draft target",),
            ),
        ),
    )
    taxonomy_spoof = request_payload(draft_spec, seed=8)
    hierarchy_spoof = request_payload(base_spec, seed=9)
    hierarchy_spoof["specification"]["taxonomy_requirements"][0]["target"] = {
        "competency_id": str(SKILL_A),
        "skill_id": None,
        "sub_skill_id": None,
        "learning_concept_id": None,
    }
    scope_spoof = request_payload(base_spec, seed=10)
    scope_spoof["specification"]["curriculum_scope"]["medium"] = "ta"
    impossible = request_payload(base_spec, seed=11)
    impossible["specification"]["total_marks"] = 2

    with api_client(blueprint_database_url) as client:
        linked = client.post(ANALYTICS_PATH, json=payload, headers=ADMIN_HEADERS)
        forecast_spoof = client.post(ANALYTICS_PATH, json=spoofed, headers=ADMIN_HEADERS)
        cross = client.post(ANALYTICS_PATH, json=cross_analytics, headers=ADMIN_HEADERS)
        draft = client.post(BASE_PATH, json=taxonomy_spoof, headers=ADMIN_HEADERS)
        hierarchy = client.post(BASE_PATH, json=hierarchy_spoof, headers=ADMIN_HEADERS)
        scope = client.post(BASE_PATH, json=scope_spoof, headers=ADMIN_HEADERS)
        impossible_response = client.post(BASE_PATH, json=impossible, headers=ADMIN_HEADERS)

    assert linked.status_code == 201
    linked_body = linked.json()
    assert linked_body["analytics_run_id"] == str(record.id)
    assert all(
        f"analytics:persisted-run:{record.id}" in requirement["priority"]["forecast_evidence_refs"]
        for requirement in linked_body["specification"]["taxonomy_requirements"]
    )
    assert all(
        requirement["priority"]["baseline_score"] != 99_999
        for requirement in linked_body["specification"]["taxonomy_requirements"]
    )
    assert forecast_spoof.status_code == 422
    assert cross.status_code == 422
    assert cross.json() == {"detail": {"code": "blueprint_analytics_cross_curriculum"}}
    assert draft.status_code == 422
    assert draft.json()["detail"]["violation"] == "not_active_reviewed"
    assert hierarchy.status_code == 422
    assert hierarchy.json()["detail"]["violation"] == "level_mismatch"
    assert scope.status_code == 422
    assert scope.json()["detail"] == {
        "code": "blueprint_curriculum_scope_mismatch",
        "field": "medium",
        "expected": "en",
        "actual": "ta",
    }
    assert impossible_response.status_code == 422
    assert impossible_response.json()["detail"] == {
        "code": "blueprint_constraint_violation",
        "violation": "total_marks_mismatch",
        "constraint": "specification.total_marks",
        "message": "section marks total 1, expected 2",
        "impossible": True,
    }


@pytest.mark.integration
def test_blueprint_api_atomic_race_and_database_linkage_shape_constraints(
    blueprint_database_url: str,
) -> None:
    payload = request_payload(make_uniform_specification((2,), 2), seed=84_500)
    barrier = Barrier(2)

    def create_once() -> tuple[int, dict[str, Any]]:
        barrier.wait()
        with api_client(blueprint_database_url) as client:
            response = client.post(BASE_PATH, json=payload, headers=ADMIN_HEADERS)
            return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _: create_once(), range(2)))

    assert sorted(status_code for status_code, _ in results) == [200, 201]
    bodies = [body for _, body in results]
    assert bodies[0]["id"] == bodies[1]["id"]
    assert {body["deduplicated"] for body in bodies} == {False, True}

    async def inspect_constraints() -> None:
        engine = create_async_engine(blueprint_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            source = await session.get(PaperBlueprintModel, UUID(bodies[0]["id"]))
            assert source is not None
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(PaperBlueprintModel)
                    .where(PaperBlueprintModel.input_fingerprint == source.input_fingerprint)
                )
                == 1
            )
            audit_count = await session.scalar(
                select(func.count())
                .select_from(AdminAuditEventModel)
                .where(
                    AdminAuditEventModel.action == "blueprint.created",
                    AdminAuditEventModel.resource_id == source.id,
                )
            )
            assert audit_count == 1
            curriculum_version_id = source.curriculum_version_id
            schema_version = source.schema_version
            algorithm_version = source.algorithm_version
            config_version = source.config_version
            seed = source.seed
            total_marks = source.total_marks
            slot_count = source.slot_count
            specification = deepcopy(source.specification)
            blueprint = deepcopy(source.blueprint)

            invalid_link = PaperBlueprintModel(
                id=UUID(int=849_001),
                curriculum_version_id=source.curriculum_version_id,
                analytics_run_id=OTHER_ANALYTICS_RUN_ID,
                blueprint_id="bp_" + "e" * 24,
                schema_version=source.schema_version,
                algorithm_version=source.algorithm_version,
                config_version=source.config_version,
                seed=source.seed,
                total_marks=source.total_marks,
                slot_count=source.slot_count,
                specification_fingerprint="sha256:" + "e" * 64,
                input_fingerprint="sha256:" + "f" * 64,
                result_fingerprint="sha256:" + "e" * 64,
                specification=source.specification,
                blueprint={
                    **source.blueprint,
                    "version": {
                        **cast(dict[str, object], source.blueprint["version"]),
                        "blueprint_id": "bp_" + "e" * 24,
                    },
                },
                taxonomy_snapshot=source.taxonomy_snapshot,
                created_by=ADMIN_ID,
            )
            session.add(invalid_link)
            with pytest.raises(IntegrityError):
                await session.flush()
            await session.rollback()

            invalid_shape = PaperBlueprintModel(
                id=UUID(int=849_002),
                curriculum_version_id=curriculum_version_id,
                analytics_run_id=None,
                blueprint_id="bp_" + "d" * 24,
                schema_version=schema_version,
                algorithm_version=algorithm_version,
                config_version=config_version,
                seed=seed,
                total_marks=total_marks,
                slot_count=slot_count,
                specification_fingerprint="sha256:" + "d" * 64,
                input_fingerprint="sha256:" + "d" * 64,
                result_fingerprint="sha256:" + "d" * 64,
                specification=specification,
                blueprint={
                    **blueprint,
                    "version": {
                        **cast(dict[str, object], blueprint["version"]),
                        "blueprint_id": "bp_" + "d" * 24,
                    },
                },
                taxonomy_snapshot=[],
                created_by=ADMIN_ID,
            )
            session.add(invalid_shape)
            with pytest.raises(IntegrityError):
                await session.flush()
            await session.rollback()
        await engine.dispose()

    asyncio.run(inspect_constraints())

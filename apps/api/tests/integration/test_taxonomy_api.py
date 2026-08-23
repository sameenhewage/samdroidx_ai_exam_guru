import asyncio
from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.auth.ports import AuthenticationError, AuthenticationFailureCode
from exam_guru_api.curriculum.models import (
    CurriculumVersionModel,
    ExamConfigurationModel,
    MediumModel,
)
from exam_guru_api.infrastructure.migrations import upgrade_database
from exam_guru_api.main import create_app

PGVECTOR_IMAGE = "pgvector/pgvector:0.8.6-pg18-trixie"
ADMIN_ID = UUID(int=700)
REVIEWER_ID = UUID(int=701)
CURRICULUM_ID = UUID(int=702)


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


@pytest.fixture(scope="module")
def taxonomy_api_database_url() -> Iterator[str]:
    with PostgresContainer(
        image=PGVECTOR_IMAGE,
        username="exam_guru",
        password="integration-" + "only",
        dbname="exam_guru_taxonomy_api_test",
        driver="asyncpg",
    ) as postgres:
        database_url = postgres.get_connection_url()
        upgrade_database(database_url)

        async def seed() -> None:
            engine = create_async_engine(database_url)
            sessions = async_sessionmaker(engine)
            async with sessions() as session:
                session.add_all(
                    [
                        ExamConfigurationModel(
                            id=UUID(int=703),
                            code="G5S-API",
                            name="Grade 5 Scholarship Examination",
                            grade=5,
                            active=True,
                            created_by=ADMIN_ID,
                            updated_by=ADMIN_ID,
                        ),
                        MediumModel(
                            id=UUID(int=704),
                            code="si",
                            name="Sinhala",
                            active=True,
                            created_by=ADMIN_ID,
                            updated_by=ADMIN_ID,
                        ),
                    ]
                )
                await session.flush()
                session.add(
                    CurriculumVersionModel(
                        id=CURRICULUM_ID,
                        exam_configuration_id=UUID(int=703),
                        medium_id=UUID(int=704),
                        code="2026-API",
                        title="API fixture curriculum",
                        active=True,
                        created_by=ADMIN_ID,
                        updated_by=ADMIN_ID,
                    )
                )
                await session.commit()
            await engine.dispose()

        asyncio.run(seed())
        yield database_url


def api_client(database_url: str) -> TestClient:
    return TestClient(
        create_app(
            identity_provider=StaticIdentityProvider(),
            resource_factory=lambda _: DatabaseTestResources(database_url),
        )
    )


@pytest.mark.integration
def test_admin_creates_audited_taxonomy_node_and_reviewer_can_read_it(
    taxonomy_api_database_url: str,
) -> None:
    path = f"/api/v1/admin/curricula/{CURRICULUM_ID}/taxonomy/nodes"
    payload = {
        "level": "competency",
        "code": "C1",
        "title": "Competency 1",
        "active": True,
    }

    with api_client(taxonomy_api_database_url) as client:
        created = client.post(
            path,
            json=payload,
            headers={"Authorization": "Bearer admin-token"},
        )
        listed = client.get(
            path,
            headers={"Authorization": "Bearer reviewer-token"},
        )
        forbidden = client.post(
            path,
            json={**payload, "code": "C2"},
            headers={"Authorization": "Bearer reviewer-token"},
        )

    assert created.status_code == 201
    created_body = created.json()
    assert created_body["curriculum_version_id"] == str(CURRICULUM_ID)
    assert created_body["parent_id"] is None
    assert created_body["level"] == "competency"
    assert created_body["code"] == "C1"
    assert listed.status_code == 200
    assert listed.json() == [created_body]
    assert forbidden.status_code == 403
    assert forbidden.json() == {"detail": {"code": "permission_denied"}}

    async def read_audit_event() -> AdminAuditEventModel:
        engine = create_async_engine(taxonomy_api_database_url)
        sessions = async_sessionmaker(engine)
        async with sessions() as session:
            event = await session.scalar(
                select(AdminAuditEventModel).where(
                    AdminAuditEventModel.resource_id == UUID(created_body["id"])
                )
            )
            assert event is not None
        await engine.dispose()
        return event

    audit_event = asyncio.run(read_audit_event())
    assert audit_event.actor_id == ADMIN_ID
    assert audit_event.action == "taxonomy.node.created"
    assert audit_event.resource_type == "taxonomy_node"
    assert audit_event.payload["code"] == "C1"


@pytest.mark.integration
def test_admin_audit_events_are_append_only(taxonomy_api_database_url: str) -> None:
    event_id = UUID(int=705)

    async def exercise() -> None:
        engine = create_async_engine(taxonomy_api_database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            session.add(
                AdminAuditEventModel(
                    id=event_id,
                    actor_id=ADMIN_ID,
                    action="taxonomy.node.created",
                    resource_type="taxonomy_node",
                    resource_id=UUID(int=706),
                    payload={"code": "C-AUDIT"},
                )
            )
            await session.commit()

        async with sessions() as session:
            event = await session.get(AdminAuditEventModel, event_id)
            assert event is not None
            event.action = "tampered"
            with pytest.raises(IntegrityError):
                await session.commit()

        async with sessions() as session:
            event = await session.get(AdminAuditEventModel, event_id)
            assert event is not None
            await session.delete(event)
            with pytest.raises(IntegrityError):
                await session.commit()
        await engine.dispose()

    asyncio.run(exercise())


@pytest.mark.integration
def test_taxonomy_api_returns_not_found_for_unknown_curriculum(
    taxonomy_api_database_url: str,
) -> None:
    path = f"/api/v1/admin/curricula/{UUID(int=999_998)}/taxonomy/nodes"

    with api_client(taxonomy_api_database_url) as client:
        response = client.get(
            path,
            headers={"Authorization": "Bearer reviewer-token"},
        )

    assert response.status_code == 404
    assert response.json() == {"detail": {"code": "curriculum_version_not_found"}}


@pytest.mark.integration
def test_taxonomy_api_rejects_unknown_curriculum_on_create(
    taxonomy_api_database_url: str,
) -> None:
    path = f"/api/v1/admin/curricula/{UUID(int=999_997)}/taxonomy/nodes"

    with api_client(taxonomy_api_database_url) as client:
        response = client.post(
            path,
            json={
                "level": "competency",
                "code": "C404",
                "title": "Unknown curriculum competency",
            },
            headers={"Authorization": "Bearer admin-token"},
        )

    assert response.status_code == 404
    assert response.json() == {"detail": {"code": "curriculum_version_not_found"}}


@pytest.mark.integration
def test_taxonomy_api_creates_child_and_rejects_duplicate_sibling(
    taxonomy_api_database_url: str,
) -> None:
    path = f"/api/v1/admin/curricula/{CURRICULUM_ID}/taxonomy/nodes"

    with api_client(taxonomy_api_database_url) as client:
        competency = client.post(
            path,
            json={"level": "competency", "code": "C3", "title": "Competency 3"},
            headers={"Authorization": "Bearer admin-token"},
        )
        child = client.post(
            path,
            json={
                "level": "skill",
                "code": "S3",
                "title": "Skill 3",
                "parent_id": competency.json()["id"],
            },
            headers={"Authorization": "Bearer admin-token"},
        )
        duplicate = client.post(
            path,
            json={"level": "competency", "code": "C3", "title": "Duplicate"},
            headers={"Authorization": "Bearer admin-token"},
        )

    assert competency.status_code == 201
    assert child.status_code == 201
    assert child.json()["parent_id"] == competency.json()["id"]
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "duplicate_sibling_code"


@pytest.mark.integration
def test_taxonomy_api_rejects_invalid_domain_shape(taxonomy_api_database_url: str) -> None:
    path = f"/api/v1/admin/curricula/{CURRICULUM_ID}/taxonomy/nodes"

    with api_client(taxonomy_api_database_url) as client:
        response = client.post(
            path,
            json={"level": "competency", "code": "lowercase", "title": "Invalid code"},
            headers={"Authorization": "Bearer admin-token"},
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_code"


@pytest.mark.integration
def test_taxonomy_api_returns_machine_readable_domain_error(
    taxonomy_api_database_url: str,
) -> None:
    path = f"/api/v1/admin/curricula/{CURRICULUM_ID}/taxonomy/nodes"

    with api_client(taxonomy_api_database_url) as client:
        response = client.post(
            path,
            json={
                "level": "skill",
                "code": "S1",
                "title": "Orphan skill",
                "parent_id": str(UUID(int=999_999)),
                "active": True,
            },
            headers={"Authorization": "Bearer admin-token"},
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "parent_not_found"

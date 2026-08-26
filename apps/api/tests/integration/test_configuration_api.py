import asyncio
from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.auth.ports import AuthenticationError, AuthenticationFailureCode
from exam_guru_api.infrastructure.migrations import upgrade_database
from exam_guru_api.main import create_app

PGVECTOR_IMAGE = "pgvector/pgvector:0.8.6-pg18-trixie"
ADMIN_ID = UUID(int=8_000)
REVIEWER_ID = UUID(int=8_001)


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
def configuration_database_url() -> Iterator[str]:
    with PostgresContainer(
        image=PGVECTOR_IMAGE,
        username="exam_guru",
        password="configuration-" + "only",
        dbname="exam_guru_configuration_test",
        driver="asyncpg",
    ) as postgres:
        database_url = postgres.get_connection_url()
        upgrade_database(database_url)
        yield database_url


def configuration_client(database_url: str) -> TestClient:
    return TestClient(
        create_app(
            identity_provider=StaticIdentityProvider(),
            resource_factory=lambda _: DatabaseTestResources(database_url),
        )
    )


def auth(role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {role}-token"}


@pytest.mark.integration
def test_authorized_configuration_management_and_transactional_audit(
    configuration_database_url: str,
) -> None:
    with configuration_client(configuration_database_url) as client:
        forbidden = client.post(
            "/api/v1/admin/exam-configurations",
            json={"code": "G5S-2026", "name": "Grade 5 Scholarship 2026", "grade": 5},
            headers=auth("reviewer"),
        )
        exam = client.post(
            "/api/v1/admin/exam-configurations",
            json={"code": "G5S-2026", "name": "Grade 5 Scholarship 2026", "grade": 5},
            headers=auth("admin"),
        )
        medium = client.post(
            "/api/v1/admin/media",
            json={"code": "si", "name": "Sinhala"},
            headers=auth("admin"),
        )
        curriculum = client.post(
            "/api/v1/admin/curriculum-versions",
            json={
                "exam_configuration_id": exam.json()["id"],
                "medium_id": medium.json()["id"],
                "code": "2026-V1",
                "title": "Grade 5 Scholarship 2026 Sinhala",
            },
            headers=auth("admin"),
        )
        updated_exam = client.patch(
            f"/api/v1/admin/exam-configurations/{exam.json()['id']}",
            json={"name": "Grade 5 Scholarship Examination 2026"},
            headers=auth("admin"),
        )
        updated_medium = client.patch(
            f"/api/v1/admin/media/{medium.json()['id']}",
            json={"name": "Sinhala Medium"},
            headers=auth("admin"),
        )
        updated_curriculum = client.patch(
            f"/api/v1/admin/curriculum-versions/{curriculum.json()['id']}",
            json={"title": "Grade 5 Scholarship 2026 Sinhala Curriculum"},
            headers=auth("admin"),
        )
        exams = client.get("/api/v1/admin/exam-configurations", headers=auth("reviewer"))
        media = client.get("/api/v1/admin/media", headers=auth("reviewer"))
        curricula = client.get("/api/v1/admin/curriculum-versions", headers=auth("reviewer"))
        audit_response = client.get("/api/v1/admin/audit-events", headers=auth("reviewer"))

    assert forbidden.status_code == 403
    assert exam.status_code == 201
    assert exam.json()["grade"] == 5
    assert medium.status_code == 201
    assert curriculum.status_code == 201
    assert updated_exam.status_code == 200
    assert updated_exam.json()["name"] == "Grade 5 Scholarship Examination 2026"
    assert updated_medium.status_code == 200
    assert updated_medium.json()["name"] == "Sinhala Medium"
    assert updated_curriculum.status_code == 200
    assert updated_curriculum.json()["title"] == "Grade 5 Scholarship 2026 Sinhala Curriculum"
    assert exams.status_code == 200
    assert len(exams.json()) == 1
    assert media.status_code == 200
    assert len(media.json()) == 1
    assert curricula.status_code == 200
    assert len(curricula.json()) == 1
    assert audit_response.status_code == 200
    assert {event["action"] for event in audit_response.json()} >= {
        "exam_configuration.created",
        "medium.created",
        "curriculum_version.created",
    }

    async def audit_actions() -> list[str]:
        engine = create_async_engine(configuration_database_url)
        sessions = async_sessionmaker(engine)
        async with sessions() as session:
            actions = list(
                await session.scalars(
                    select(AdminAuditEventModel.action).order_by(AdminAuditEventModel.created_at)
                )
            )
        await engine.dispose()
        return actions

    assert asyncio.run(audit_actions()) == [
        "exam_configuration.created",
        "medium.created",
        "curriculum_version.created",
        "exam_configuration.updated",
        "medium.updated",
        "curriculum_version.updated",
    ]


@pytest.mark.integration
def test_grade_seven_subject_unit_lesson_configuration_and_deactivation_rules(
    configuration_database_url: str,
) -> None:
    with configuration_client(configuration_database_url) as client:
        forbidden = client.post(
            "/api/v1/admin/subjects",
            json={"code": "FORBIDDEN", "name": "Forbidden"},
            headers=auth("reviewer"),
        )
        subject = client.post(
            "/api/v1/admin/subjects",
            json={"code": "MATHEMATICS", "name": "Mathematics"},
            headers=auth("admin"),
        )
        exam = client.post(
            "/api/v1/admin/exam-configurations",
            json={"code": "Z-SCHOOL-G7", "name": "School Grade 7", "grade": 7},
            headers=auth("admin"),
        )
        medium = client.post(
            "/api/v1/admin/media",
            json={"code": "zz-en", "name": "English"},
            headers=auth("admin"),
        )
        curriculum = client.post(
            "/api/v1/admin/curriculum-versions",
            json={
                "exam_configuration_id": exam.json()["id"],
                "medium_id": medium.json()["id"],
                "subject_id": subject.json()["id"],
                "code": "Z-G7-MATH-V1",
                "title": "Grade 7 Mathematics",
            },
            headers=auth("admin"),
        )
        unit = client.post(
            f"/api/v1/admin/curriculum-versions/{curriculum.json()['id']}/units",
            json={"code": "UNIT-01", "title": "Numbers", "ordinal": 1},
            headers=auth("admin"),
        )
        lesson = client.post(
            f"/api/v1/admin/curriculum-versions/{curriculum.json()['id']}/lessons",
            json={
                "unit_id": unit.json()["id"],
                "code": "LESSON-01",
                "title": "Whole numbers",
                "ordinal": 1,
                "taxonomy_node_ids": [],
            },
            headers=auth("admin"),
        )
        listed_subjects = client.get("/api/v1/admin/subjects", headers=auth("reviewer"))
        listed_units = client.get(
            f"/api/v1/admin/curriculum-versions/{curriculum.json()['id']}/units",
            headers=auth("reviewer"),
        )
        updated_subject = client.patch(
            f"/api/v1/admin/subjects/{subject.json()['id']}",
            json={"name": "Mathematics updated"},
            headers=auth("admin"),
        )
        updated_unit = client.patch(
            f"/api/v1/admin/curriculum-versions/{curriculum.json()['id']}"
            f"/units/{unit.json()['id']}",
            json={"title": "Numbers updated"},
            headers=auth("admin"),
        )
        updated_lesson = client.patch(
            f"/api/v1/admin/curriculum-versions/{curriculum.json()['id']}"
            f"/lessons/{lesson.json()['id']}",
            json={"title": "Whole numbers updated"},
            headers=auth("admin"),
        )
        mapped_lesson = client.put(
            f"/api/v1/admin/curriculum-versions/{curriculum.json()['id']}"
            f"/lessons/{lesson.json()['id']}/taxonomy",
            json={"taxonomy_node_ids": []},
            headers=auth("admin"),
        )
        listed_lessons = client.get(
            f"/api/v1/admin/curriculum-versions/{curriculum.json()['id']}/lessons",
            headers=auth("reviewer"),
        )
        blocked_subject = client.post(
            f"/api/v1/admin/subjects/{subject.json()['id']}/deactivate",
            headers=auth("admin"),
        )
        blocked_unit = client.post(
            f"/api/v1/admin/curriculum-versions/{curriculum.json()['id']}"
            f"/units/{unit.json()['id']}/deactivate",
            headers=auth("admin"),
        )
        deactivated_lesson = client.post(
            f"/api/v1/admin/curriculum-versions/{curriculum.json()['id']}"
            f"/lessons/{lesson.json()['id']}/deactivate",
            headers=auth("admin"),
        )
        deactivated_unit = client.post(
            f"/api/v1/admin/curriculum-versions/{curriculum.json()['id']}"
            f"/units/{unit.json()['id']}/deactivate",
            headers=auth("admin"),
        )
        deactivated_curriculum = client.post(
            f"/api/v1/admin/curriculum-versions/{curriculum.json()['id']}/deactivate",
            headers=auth("admin"),
        )
        deactivated_subject = client.post(
            f"/api/v1/admin/subjects/{subject.json()['id']}/deactivate",
            headers=auth("admin"),
        )

    assert forbidden.status_code == 403
    assert subject.status_code == 201
    assert exam.status_code == 201
    assert exam.json()["grade"] == 7
    assert curriculum.status_code == 201
    assert curriculum.json()["subject_id"] == subject.json()["id"]
    assert unit.status_code == 201
    assert lesson.status_code == 201
    assert lesson.json()["unit_id"] == unit.json()["id"]
    assert lesson.json()["taxonomy_node_ids"] == []
    assert listed_subjects.status_code == 200
    assert subject.json()["id"] in {item["id"] for item in listed_subjects.json()}
    assert listed_units.status_code == 200
    assert [item["id"] for item in listed_units.json()] == [unit.json()["id"]]
    assert updated_subject.json()["name"] == "Mathematics updated"
    assert updated_unit.json()["title"] == "Numbers updated"
    assert updated_lesson.json()["title"] == "Whole numbers updated"
    assert mapped_lesson.status_code == 200
    assert mapped_lesson.json()["taxonomy_node_ids"] == []
    assert listed_lessons.status_code == 200
    assert [item["title"] for item in listed_lessons.json()] == ["Whole numbers updated"]
    assert blocked_subject.status_code == 409
    assert blocked_subject.json()["detail"]["code"] == "configuration_in_use"
    assert blocked_unit.status_code == 409
    assert blocked_unit.json()["detail"]["code"] == "configuration_in_use"
    assert deactivated_lesson.status_code == 200
    assert deactivated_lesson.json()["active"] is False
    assert deactivated_unit.status_code == 200
    assert deactivated_unit.json()["active"] is False
    assert deactivated_curriculum.status_code == 200
    assert deactivated_curriculum.json()["active"] is False
    assert deactivated_subject.status_code == 200
    assert deactivated_subject.json()["active"] is False


@pytest.mark.integration
def test_configuration_validation_conflicts_and_safe_deactivation(
    configuration_database_url: str,
) -> None:
    with configuration_client(configuration_database_url) as client:
        invalid_grade = client.post(
            "/api/v1/admin/exam-configurations",
            json={"code": "G14", "name": "Invalid", "grade": 14},
            headers=auth("admin"),
        )
        duplicate_exam = client.post(
            "/api/v1/admin/exam-configurations",
            json={"code": "G5S-2026", "name": "Duplicate", "grade": 5},
            headers=auth("admin"),
        )
        missing_parent = client.post(
            "/api/v1/admin/curriculum-versions",
            json={
                "exam_configuration_id": str(UUID(int=999_001)),
                "medium_id": str(UUID(int=999_002)),
                "code": "MISSING",
                "title": "Missing parents",
            },
            headers=auth("admin"),
        )
        exam = client.get("/api/v1/admin/exam-configurations", headers=auth("admin")).json()[0]
        medium = client.get("/api/v1/admin/media", headers=auth("admin")).json()[0]
        curriculum = client.get("/api/v1/admin/curriculum-versions", headers=auth("admin")).json()[
            0
        ]
        blocked_exam = client.post(
            f"/api/v1/admin/exam-configurations/{exam['id']}/deactivate",
            headers=auth("admin"),
        )
        blocked_medium = client.post(
            f"/api/v1/admin/media/{medium['id']}/deactivate",
            headers=auth("admin"),
        )
        deactivated_curriculum = client.post(
            f"/api/v1/admin/curriculum-versions/{curriculum['id']}/deactivate",
            headers=auth("admin"),
        )
        deactivated_exam = client.post(
            f"/api/v1/admin/exam-configurations/{exam['id']}/deactivate",
            headers=auth("admin"),
        )
        deactivated_medium = client.post(
            f"/api/v1/admin/media/{medium['id']}/deactivate",
            headers=auth("admin"),
        )
        terminal_update = client.patch(
            f"/api/v1/admin/exam-configurations/{exam['id']}",
            json={"name": "Cannot reactivate or edit"},
            headers=auth("admin"),
        )

    assert invalid_grade.status_code == 422
    assert duplicate_exam.status_code == 409
    assert duplicate_exam.json()["detail"]["code"] == "configuration_conflict"
    assert missing_parent.status_code == 404
    assert blocked_exam.status_code == 409
    assert blocked_exam.json()["detail"]["code"] == "configuration_in_use"
    assert blocked_medium.status_code == 409
    assert deactivated_curriculum.status_code == 200
    assert deactivated_curriculum.json()["active"] is False
    assert deactivated_exam.status_code == 200
    assert deactivated_medium.status_code == 200
    assert terminal_update.status_code == 409
    assert terminal_update.json()["detail"]["code"] == "configuration_inactive"

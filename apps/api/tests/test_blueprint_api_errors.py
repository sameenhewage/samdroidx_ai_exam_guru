import asyncio
from typing import cast
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.blueprints.analytics import PersistedAnalyticsEvidenceError
from exam_guru_api.blueprints.domain import (
    ImpossibleBlueprintError,
    Violation,
)
from exam_guru_api.blueprints.repository import (
    BlueprintFingerprintConflictError,
    PaperBlueprintNotFoundError,
)
from exam_guru_api.blueprints.serialization import BlueprintSnapshotError
from exam_guru_api.blueprints.service import (
    BlueprintAnalyticsCurriculumMismatchError,
    BlueprintAnalyticsRunNotFoundError,
    BlueprintCurriculumInactiveError,
    BlueprintCurriculumNotFoundError,
    BlueprintCurriculumScopeMismatchError,
    BlueprintSnapshotLimitError,
    BlueprintTaxonomyValidationError,
    TaxonomySnapshotViolation,
)

CURRICULUM_ID = UUID(int=83_001)
RESOURCE_ID = UUID(int=83_002)
FINGERPRINT = "sha256:" + "a" * 64


class RollbackSession:
    def __init__(self) -> None:
        self.rolled_back = False

    async def rollback(self) -> None:
        self.rolled_back = True


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (
            BlueprintCurriculumNotFoundError(CURRICULUM_ID),
            404,
            "curriculum_version_not_found",
        ),
        (PaperBlueprintNotFoundError(RESOURCE_ID), 404, "paper_blueprint_not_found"),
        (
            BlueprintCurriculumInactiveError(CURRICULUM_ID),
            409,
            "blueprint_curriculum_inactive",
        ),
        (
            BlueprintCurriculumScopeMismatchError("medium", "si", "ta"),
            422,
            "blueprint_curriculum_scope_mismatch",
        ),
        (
            BlueprintTaxonomyValidationError(
                RESOURCE_ID,
                TaxonomySnapshotViolation.HIERARCHY_MISMATCH,
            ),
            422,
            "blueprint_taxonomy_invalid",
        ),
        (
            BlueprintAnalyticsRunNotFoundError(RESOURCE_ID),
            422,
            "blueprint_analytics_run_not_found",
        ),
        (
            BlueprintAnalyticsCurriculumMismatchError(RESOURCE_ID),
            422,
            "blueprint_analytics_cross_curriculum",
        ),
        (
            PersistedAnalyticsEvidenceError("tampered"),
            422,
            "blueprint_analytics_evidence_invalid",
        ),
        (
            BlueprintSnapshotLimitError("blueprint", 100, 101),
            422,
            "blueprint_snapshot_limit_exceeded",
        ),
        (
            BlueprintSnapshotError("specification", "invalid"),
            422,
            "blueprint_specification_invalid",
        ),
        (
            ImpossibleBlueprintError(
                Violation.TOTAL_MARKS_MISMATCH,
                "specification.total_marks",
                "does not add up",
            ),
            422,
            "blueprint_constraint_violation",
        ),
        (
            BlueprintFingerprintConflictError(FINGERPRINT),
            409,
            "blueprint_fingerprint_conflict",
        ),
    ],
)
def test_blueprint_errors_have_stable_clear_http_contracts(
    error: Exception,
    status_code: int,
    code: str,
) -> None:
    from exam_guru_api.api.routes.blueprints import _execute_blueprint_operation

    async def exercise() -> None:
        async def fail() -> None:
            raise error

        with pytest.raises(HTTPException) as raised:
            await _execute_blueprint_operation(cast(AsyncSession, object()), fail)

        assert raised.value.status_code == status_code
        detail = cast(dict[str, object], raised.value.detail)
        assert detail["code"] == code
        if isinstance(error, BlueprintCurriculumScopeMismatchError):
            assert detail == {
                "code": code,
                "field": "medium",
                "expected": "si",
                "actual": "ta",
            }
        if isinstance(error, BlueprintTaxonomyValidationError):
            assert detail["violation"] == "hierarchy_mismatch"
            assert detail["node_id"] == str(RESOURCE_ID)
        if isinstance(error, ImpossibleBlueprintError):
            assert detail == {
                "code": code,
                "violation": "total_marks_mismatch",
                "constraint": "specification.total_marks",
                "message": "does not add up",
                "impossible": True,
            }
        if isinstance(error, BlueprintSnapshotLimitError):
            assert detail == {
                "code": code,
                "snapshot": "blueprint",
                "maximum": 100,
                "actual": 101,
            }

    asyncio.run(exercise())


def test_blueprint_integrity_error_rolls_back_to_a_stable_conflict() -> None:
    from exam_guru_api.api.routes.blueprints import _execute_blueprint_operation

    async def exercise() -> None:
        session = RollbackSession()

        async def fail() -> None:
            raise IntegrityError("INSERT", {}, RuntimeError("constraint"))

        with pytest.raises(HTTPException) as raised:
            await _execute_blueprint_operation(cast(AsyncSession, session), fail)
        assert raised.value.status_code == 409
        assert cast(dict[str, object], raised.value.detail) == {
            "code": "blueprint_persistence_conflict"
        }
        assert session.rolled_back

        async def succeed() -> str:
            return "ok"

        assert await _execute_blueprint_operation(cast(AsyncSession, session), succeed) == "ok"

    asyncio.run(exercise())


def test_blueprint_route_functions_serialize_create_list_and_get(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import Response

    from exam_guru_api.api.routes.blueprints import (
        create_paper_blueprint,
        get_paper_blueprint,
        list_paper_blueprints,
    )
    from exam_guru_api.auth.domain import AdminRole, Principal
    from exam_guru_api.blueprints.repository import PaperBlueprintRecord
    from exam_guru_api.blueprints.schemas import (
        BlueprintCreateRequest,
        PaperBlueprintResponse,
        PaperBlueprintSummaryResponse,
    )
    from exam_guru_api.blueprints.service import BlueprintCreationResult
    from tests.test_blueprint_api_contract import baseline_request_payload

    async def exercise() -> None:
        record = cast(PaperBlueprintRecord, object())
        principal = Principal(RESOURCE_ID, frozenset({AdminRole.ADMIN}))
        full_response = cast(PaperBlueprintResponse, object())
        summary_response = cast(PaperBlueprintSummaryResponse, object())
        deduplicated = iter((False, True))

        class FakeService:
            async def create_blueprint(
                self,
                *args: object,
                **kwargs: object,
            ) -> BlueprintCreationResult:
                return BlueprintCreationResult(record, deduplicated=next(deduplicated))

            async def list_blueprints(
                self,
                *args: object,
                **kwargs: object,
            ) -> tuple[PaperBlueprintRecord, ...]:
                return (record,)

            async def get_blueprint(
                self,
                *args: object,
                **kwargs: object,
            ) -> PaperBlueprintRecord:
                return record

        monkeypatch.setattr(
            "exam_guru_api.api.routes.blueprints.BlueprintGenerationService",
            lambda session: FakeService(),
        )
        monkeypatch.setattr(
            PaperBlueprintResponse,
            "from_record",
            classmethod(lambda cls, value, **kwargs: full_response),
        )
        monkeypatch.setattr(
            PaperBlueprintSummaryResponse,
            "from_record",
            classmethod(lambda cls, value: summary_response),
        )
        request = BlueprintCreateRequest.model_validate(baseline_request_payload())
        created_response = Response()
        duplicate_response = Response()

        created = await create_paper_blueprint(
            CURRICULUM_ID,
            request,
            created_response,
            principal,
            cast(AsyncSession, object()),
        )
        duplicate = await create_paper_blueprint(
            CURRICULUM_ID,
            request,
            duplicate_response,
            principal,
            cast(AsyncSession, object()),
        )
        listed = await list_paper_blueprints(
            CURRICULUM_ID,
            principal,
            cast(AsyncSession, object()),
            limit=10,
            offset=0,
        )
        fetched = await get_paper_blueprint(
            CURRICULUM_ID,
            RESOURCE_ID,
            principal,
            cast(AsyncSession, object()),
        )

        assert created is full_response
        assert duplicate is full_response
        assert created_response.status_code == 201
        assert duplicate_response.status_code == 200
        assert listed == [summary_response]
        assert fetched is full_response

    asyncio.run(exercise())

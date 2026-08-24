import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.blueprints.models import PaperBlueprintModel
from exam_guru_api.blueprints.repository import (
    BlueprintFingerprintConflictError,
    PaperBlueprintNotFoundError,
    PaperBlueprintWrite,
    ReviewedTaxonomyNodeRecord,
    SqlAlchemyBlueprintRepository,
)
from exam_guru_api.curriculum.domain import TaxonomyLevel, TaxonomyReviewState
from tests.test_analytics_repository import run_model as analytics_run_model

NOW = datetime(2025, 1, 1, tzinfo=UTC)
FINGERPRINT = "sha256:" + "a" * 64


class ScalarRows:
    def __init__(self, rows: tuple[object, ...]) -> None:
        self.rows = rows

    def __iter__(self) -> Iterator[object]:
        return iter(self.rows)


class ScriptedSession:
    def __init__(
        self,
        *,
        scalar_results: tuple[object | None, ...] = (),
        scalar_rows: tuple[object, ...] = (),
        get_result: object | None = None,
        execute_row: object | None = None,
    ) -> None:
        self.scalar_results = list(scalar_results)
        self.scalar_rows = scalar_rows
        self.get_result = get_result
        self.execute_row = execute_row

    async def scalar(self, statement: object) -> object | None:
        del statement
        return self.scalar_results.pop(0)

    async def scalars(self, statement: object) -> ScalarRows:
        del statement
        return ScalarRows(self.scalar_rows)

    async def get(self, model: object, identifier: UUID) -> object | None:
        del model, identifier
        return self.get_result

    async def execute(self, statement: object) -> object:
        del statement
        row = self.execute_row

        class Result:
            def one_or_none(self) -> object | None:
                return row

        return Result()


def blueprint_write() -> PaperBlueprintWrite:
    return PaperBlueprintWrite(
        id=UUID(int=1),
        curriculum_version_id=UUID(int=2),
        analytics_run_id=None,
        blueprint_id="bp_" + "a" * 24,
        schema_version="1",
        algorithm_version="algorithm-v1",
        config_version="config-v1",
        seed=7,
        total_marks=2,
        slot_count=1,
        specification_fingerprint=FINGERPRINT,
        input_fingerprint=FINGERPRINT,
        result_fingerprint=FINGERPRINT,
        specification={"paper_code": "P1"},
        blueprint={"slots": []},
        taxonomy_snapshot=[{"id": str(UUID(int=3))}],
        created_by=UUID(int=4),
    )


def blueprint_model(write: PaperBlueprintWrite | None = None) -> PaperBlueprintModel:
    value = write or blueprint_write()
    return PaperBlueprintModel(
        id=value.id,
        curriculum_version_id=value.curriculum_version_id,
        analytics_run_id=value.analytics_run_id,
        blueprint_id=value.blueprint_id,
        schema_version=value.schema_version,
        algorithm_version=value.algorithm_version,
        config_version=value.config_version,
        seed=value.seed,
        total_marks=value.total_marks,
        slot_count=value.slot_count,
        specification_fingerprint=value.specification_fingerprint,
        input_fingerprint=value.input_fingerprint,
        result_fingerprint=value.result_fingerprint,
        specification=value.specification,
        blueprint=value.blueprint,
        taxonomy_snapshot=value.taxonomy_snapshot,
        created_by=value.created_by,
        created_at=NOW,
    )


def test_repository_creates_deduplicates_gets_and_lists_blueprints() -> None:
    async def exercise() -> None:
        write = blueprint_write()
        model = blueprint_model(write)
        created = await SqlAlchemyBlueprintRepository(
            cast(AsyncSession, ScriptedSession(scalar_results=(model,)))
        ).store_blueprint(write)
        duplicate = await SqlAlchemyBlueprintRepository(
            cast(AsyncSession, ScriptedSession(scalar_results=(None, model)))
        ).store_blueprint(write)
        fetched = await SqlAlchemyBlueprintRepository(
            cast(AsyncSession, ScriptedSession(scalar_results=(model,)))
        ).get_blueprint(write.curriculum_version_id, write.id)
        listed = await SqlAlchemyBlueprintRepository(
            cast(AsyncSession, ScriptedSession(scalar_rows=(model,)))
        ).list_blueprints(write.curriculum_version_id, limit=10, offset=0)

        assert created.created is True
        assert duplicate.created is False
        assert created.record == duplicate.record == fetched == listed[0]

    asyncio.run(exercise())


def test_repository_reports_missing_and_both_absent_and_mismatched_collisions() -> None:
    async def exercise() -> None:
        write = blueprint_write()
        missing = SqlAlchemyBlueprintRepository(
            cast(AsyncSession, ScriptedSession(scalar_results=(None,)))
        )
        with pytest.raises(PaperBlueprintNotFoundError):
            await missing.get_blueprint(write.curriculum_version_id, write.id)

        absent = SqlAlchemyBlueprintRepository(
            cast(AsyncSession, ScriptedSession(scalar_results=(None, None)))
        )
        with pytest.raises(BlueprintFingerprintConflictError):
            await absent.store_blueprint(write)

        changed_model = blueprint_model()
        changed_model.result_fingerprint = "sha256:" + "b" * 64
        changed = SqlAlchemyBlueprintRepository(
            cast(AsyncSession, ScriptedSession(scalar_results=(None, changed_model)))
        )
        with pytest.raises(BlueprintFingerprintConflictError):
            await changed.store_blueprint(write)

    asyncio.run(exercise())


def test_repository_loads_curriculum_taxonomy_and_analytics_evidence() -> None:
    async def exercise() -> None:
        curriculum_id = UUID(int=10)
        scope_repository = SqlAlchemyBlueprintRepository(
            cast(
                AsyncSession,
                ScriptedSession(
                    execute_row=(curriculum_id, 5, "si", True, True, True),
                ),
            )
        )
        scope = await scope_repository.get_curriculum_scope(curriculum_id)
        assert scope is not None
        assert (scope.grade, scope.medium, scope.curriculum_active) == (5, "si", True)

        absent_scope = await SqlAlchemyBlueprintRepository(
            cast(AsyncSession, ScriptedSession(execute_row=None))
        ).get_curriculum_scope(curriculum_id)
        assert absent_scope is None

        assert (
            await SqlAlchemyBlueprintRepository(
                cast(AsyncSession, ScriptedSession())
            ).list_taxonomy_nodes(curriculum_id, frozenset())
            == ()
        )

        taxonomy_model = type(
            "TaxonomyModel",
            (),
            {
                "id": UUID(int=11),
                "curriculum_version_id": curriculum_id,
                "parent_id": None,
                "level": TaxonomyLevel.COMPETENCY,
                "code": "C1",
                "title": "Competency",
                "active": True,
                "review_state": TaxonomyReviewState.REVIEWED,
                "updated_at": NOW,
                "updated_by": UUID(int=12),
            },
        )()
        taxonomy = await SqlAlchemyBlueprintRepository(
            cast(AsyncSession, ScriptedSession(scalar_rows=(taxonomy_model,)))
        ).list_taxonomy_nodes(curriculum_id, frozenset({taxonomy_model.id}))
        assert taxonomy == (
            ReviewedTaxonomyNodeRecord(
                id=taxonomy_model.id,
                curriculum_version_id=curriculum_id,
                parent_id=None,
                level=TaxonomyLevel.COMPETENCY,
                code="C1",
                title="Competency",
                active=True,
                review_state=TaxonomyReviewState.REVIEWED,
                reviewed_at=NOW,
                reviewed_by=UUID(int=12),
            ),
        )
        assert taxonomy[0].to_snapshot() == {
            "id": str(taxonomy_model.id),
            "curriculum_version_id": str(curriculum_id),
            "parent_id": None,
            "level": "competency",
            "code": "C1",
            "title": "Competency",
            "active": True,
            "review_state": "reviewed",
            "reviewed_at": NOW.isoformat(),
            "reviewed_by": str(UUID(int=12)),
        }

        analytics_model = analytics_run_model()
        analytics = await SqlAlchemyBlueprintRepository(
            cast(AsyncSession, ScriptedSession(get_result=analytics_model))
        ).get_analytics_run(analytics_model.id)
        assert analytics is not None
        assert analytics.id == analytics_model.id
        assert analytics.result == analytics_model.result
        assert (
            await SqlAlchemyBlueprintRepository(
                cast(AsyncSession, ScriptedSession(get_result=None))
            ).get_analytics_run(UUID(int=999))
            is None
        )

    asyncio.run(exercise())

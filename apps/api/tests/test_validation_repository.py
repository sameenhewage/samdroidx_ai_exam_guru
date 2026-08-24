import asyncio
from collections.abc import Iterator
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.generation.models import GenerationAttemptModel, GenerationRunModel
from exam_guru_api.validation.models import ValidationFindingModel, ValidationRunModel
from exam_guru_api.validation.repository import (
    SqlAlchemyValidationRepository,
    ValidationGenerationNotFoundError,
    ValidationRunNotFoundError,
)

CURRICULUM_ID = UUID(int=970_001)
GENERATION_ID = UUID(int=970_002)
ATTEMPT_ID = UUID(int=970_003)
VALIDATION_ID = UUID(int=970_004)
OTHER_GENERATION_ID = UUID(int=970_005)
HISTORICAL_ID = UUID(int=970_006)
SOURCE_ID = UUID(int=970_007)
FINDING_ID = UUID(int=970_008)


class Rows:
    def __init__(self, values: tuple[object, ...]) -> None:
        self._values = values

    def __iter__(self) -> Iterator[object]:
        return iter(self._values)


class ExecuteResult:
    def __init__(self, *, row: object | None = None, rows: tuple[object, ...] = ()) -> None:
        self._row = row
        self._rows = rows

    def one_or_none(self) -> object | None:
        return self._row

    def all(self) -> list[object]:
        return list(self._rows)


class ScriptedSession:
    def __init__(
        self,
        *,
        scalar_results: tuple[object | None, ...] = (),
        execute_results: tuple[ExecuteResult, ...] = (),
        scalar_rows: tuple[object, ...] = (),
    ) -> None:
        self.scalar_results = list(scalar_results)
        self.execute_results = list(execute_results)
        self.scalar_rows = scalar_rows
        self.added: list[object] = []
        self.flushes = 0

    async def scalar(self, statement: object) -> object | None:
        del statement
        return self.scalar_results.pop(0)

    async def execute(self, statement: object) -> ExecuteResult:
        del statement
        return self.execute_results.pop(0)

    async def scalars(self, statement: object) -> Rows:
        del statement
        return Rows(self.scalar_rows)

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flushes += 1


def generation() -> GenerationRunModel:
    return GenerationRunModel(id=GENERATION_ID, candidate={"stem": "Generated bank stem"})


def attempt() -> GenerationAttemptModel:
    return GenerationAttemptModel(id=ATTEMPT_ID, generation_run_id=GENERATION_ID)


def validation() -> ValidationRunModel:
    return ValidationRunModel(
        id=VALIDATION_ID,
        curriculum_version_id=CURRICULUM_ID,
        generation_run_id=GENERATION_ID,
        generation_attempt_id=ATTEMPT_ID,
        pipeline_version="pipeline.v1",
        pipeline_fingerprint="a" * 64,
        generation_result_fingerprint="b" * 64,
        report_fingerprint="c" * 64,
    )


def finding() -> ValidationFindingModel:
    return ValidationFindingModel(id=FINDING_ID, validation_run_id=VALIDATION_ID, ordinal=0)


def test_validation_repository_lookup_and_missing_boundaries() -> None:
    async def exercise() -> None:
        run = generation()
        result_attempt = attempt()
        repository = SqlAlchemyValidationRepository(
            cast(
                AsyncSession,
                ScriptedSession(
                    scalar_results=(CURRICULUM_ID, validation(), object()),
                    execute_results=(ExecuteResult(row=(run, result_attempt)),),
                ),
            )
        )
        assert await repository.curriculum_exists(CURRICULUM_ID) is True
        record = await repository.get_generation(CURRICULUM_ID, GENERATION_ID)
        assert record.run is run
        assert record.attempt is result_attempt
        assert (
            await repository.get_for_generation_pipeline(GENERATION_ID, "pipeline.v1") is not None
        )
        assert await repository.get_for_generation_pipeline(GENERATION_ID, "pipeline.v2") is None

        missing_generation = SqlAlchemyValidationRepository(
            cast(
                AsyncSession,
                ScriptedSession(execute_results=(ExecuteResult(row=None),)),
            )
        )
        with pytest.raises(ValidationGenerationNotFoundError):
            await missing_generation.get_generation(CURRICULUM_ID, GENERATION_ID)

        assert (
            await SqlAlchemyValidationRepository(
                cast(AsyncSession, ScriptedSession(scalar_results=(None,)))
            ).curriculum_exists(CURRICULUM_ID)
            is False
        )
        with pytest.raises(ValidationRunNotFoundError):
            await SqlAlchemyValidationRepository(
                cast(AsyncSession, ScriptedSession(scalar_results=(None,)))
            ).get_run(CURRICULUM_ID, VALIDATION_ID)

    asyncio.run(exercise())


def test_validation_repository_loads_bounded_historical_and_generated_duplicates() -> None:
    async def exercise() -> None:
        historical = (HISTORICAL_ID, "Historical stem", 3, SOURCE_ID, 7)
        generated_valid = GenerationRunModel(
            id=OTHER_GENERATION_ID,
            candidate={"stem": "Generated stem"},
        )
        generated_duplicate = GenerationRunModel(
            id=OTHER_GENERATION_ID,
            candidate={"stem": "Older generated stem"},
        )
        generated_missing_candidate = GenerationRunModel(
            id=UUID(int=970_010),
            candidate=None,
        )
        generated_blank_stem = GenerationRunModel(
            id=UUID(int=970_011),
            candidate={"stem": " "},
        )
        generated_non_text_stem = GenerationRunModel(
            id=UUID(int=970_012),
            candidate={"stem": 42},
        )
        report = validation()
        report.generation_result_fingerprint = "d" * 64
        repository = SqlAlchemyValidationRepository(
            cast(
                AsyncSession,
                ScriptedSession(
                    execute_results=(
                        ExecuteResult(rows=(historical,)),
                        ExecuteResult(
                            rows=(
                                (generated_valid, report),
                                (generated_duplicate, report),
                                (generated_missing_candidate, report),
                                (generated_blank_stem, report),
                                (generated_non_text_stem, report),
                            )
                        ),
                    )
                ),
            )
        )

        references = await repository.list_duplicate_references(
            CURRICULUM_ID,
            exclude_generation_run_id=GENERATION_ID,
            limit=3,
        )

        assert [item.reference_kind for item in references] == ["historical", "generated"]
        assert references[0].record_version == "historical-question.v3"
        assert references[0].source_document_id == SOURCE_ID
        assert references[1].record_id == OTHER_GENERATION_ID
        assert references[1].validation_run_id == VALIDATION_ID
        assert references[1].text == "Generated stem"

        historical_only = await SqlAlchemyValidationRepository(
            cast(
                AsyncSession,
                ScriptedSession(execute_results=(ExecuteResult(rows=(historical,)),)),
            )
        ).list_duplicate_references(
            CURRICULUM_ID,
            exclude_generation_run_id=GENERATION_ID,
            limit=1,
        )
        assert len(historical_only) == 1

        limit_reached_repository = SqlAlchemyValidationRepository(
            cast(
                AsyncSession,
                ScriptedSession(
                    execute_results=(
                        ExecuteResult(rows=()),
                        ExecuteResult(
                            rows=((generated_valid, report), (generated_blank_stem, report))
                        ),
                    )
                ),
            )
        )
        limited = await limit_reached_repository.list_duplicate_references(
            CURRICULUM_ID,
            exclude_generation_run_id=GENERATION_ID,
            limit=1,
        )
        assert len(limited) == 1

    asyncio.run(exercise())


def test_validation_repository_store_list_and_finding_paths() -> None:
    async def exercise() -> None:
        report = validation()
        report_finding = finding()
        created_session = ScriptedSession(scalar_results=(report,))
        created = await SqlAlchemyValidationRepository(
            cast(AsyncSession, created_session)
        ).store_report(
            {
                "generation_run_id": GENERATION_ID,
                "pipeline_version": "pipeline.v1",
            },
            (report_finding,),
        )
        assert created.created is True
        assert created.run is report
        assert created_session.added == [report_finding]
        assert created_session.flushes == 1

        duplicate = await SqlAlchemyValidationRepository(
            cast(AsyncSession, ScriptedSession(scalar_results=(None, report)))
        ).store_report(
            {
                "generation_run_id": GENERATION_ID,
                "pipeline_version": "pipeline.v1",
            },
            (),
        )
        assert duplicate.created is False
        assert duplicate.run is report

        with pytest.raises(RuntimeError, match="winner"):
            await SqlAlchemyValidationRepository(
                cast(AsyncSession, ScriptedSession(scalar_results=(None, None)))
            ).store_report(
                {
                    "generation_run_id": GENERATION_ID,
                    "pipeline_version": "pipeline.v1",
                },
                (),
            )

        run_and_list_repository = SqlAlchemyValidationRepository(
            cast(
                AsyncSession,
                ScriptedSession(
                    scalar_results=(report, report),
                    scalar_rows=(report,),
                ),
            )
        )
        assert await run_and_list_repository.get_run(CURRICULUM_ID, VALIDATION_ID) is report
        assert await run_and_list_repository.list_runs(
            CURRICULUM_ID,
            limit=10,
            offset=0,
        ) == (report,)
        finding_repository = SqlAlchemyValidationRepository(
            cast(
                AsyncSession,
                ScriptedSession(
                    scalar_results=(report,),
                    scalar_rows=(report_finding,),
                ),
            )
        )
        assert await finding_repository.list_findings(
            CURRICULUM_ID,
            VALIDATION_ID,
            limit=10,
            offset=0,
        ) == (report_finding,)

    asyncio.run(exercise())

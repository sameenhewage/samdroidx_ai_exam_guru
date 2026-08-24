import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.blueprints.domain import (
    BlueprintSpecification,
    CurriculumScope,
    TaxonomyRequirement,
    TaxonomyTarget,
)
from exam_guru_api.blueprints.repository import (
    CurriculumScopeRecord,
    PaperBlueprintRecord,
    PaperBlueprintWrite,
    RepositoryPaperBlueprintResult,
    ReviewedTaxonomyNodeRecord,
    SqlAlchemyBlueprintRepository,
)
from exam_guru_api.blueprints.serialization import (
    deserialize_blueprint,
    deserialize_specification,
    fingerprint_snapshot,
)
from exam_guru_api.blueprints.service import (
    BlueprintAnalyticsCurriculumMismatchError,
    BlueprintAnalyticsRunNotFoundError,
    BlueprintCurriculumInactiveError,
    BlueprintCurriculumNotFoundError,
    BlueprintCurriculumScopeMismatchError,
    BlueprintGenerationService,
    BlueprintSnapshotLimitError,
    BlueprintTaxonomyValidationError,
    TaxonomySnapshotViolation,
)
from exam_guru_api.curriculum.domain import TaxonomyLevel, TaxonomyReviewState
from tests.test_blueprint_domain import (
    baseline_priority,
    make_uniform_specification,
)
from tests.test_blueprint_persisted_analytics import analytics_record

ACTOR_ID = UUID(int=82_001)
NOW = datetime(2025, 1, 1, tzinfo=UTC)


class FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.commit_count = 0

    def add(self, value: object) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commit_count += 1


class FakeBlueprintRepository:
    def __init__(
        self,
        scope: CurriculumScopeRecord | None,
        taxonomy: tuple[ReviewedTaxonomyNodeRecord, ...],
        *,
        analytics: object | None = None,
        created_sequence: tuple[bool, ...] = (True,),
    ) -> None:
        self.scope = scope
        self.taxonomy = taxonomy
        self.analytics = analytics
        self.created_sequence = list(created_sequence)
        self.stored: PaperBlueprintRecord | None = None

    async def get_curriculum_scope(
        self, curriculum_version_id: UUID
    ) -> CurriculumScopeRecord | None:
        del curriculum_version_id
        return self.scope

    async def list_taxonomy_nodes(
        self,
        curriculum_version_id: UUID,
        node_ids: frozenset[UUID],
    ) -> tuple[ReviewedTaxonomyNodeRecord, ...]:
        del curriculum_version_id, node_ids
        return self.taxonomy

    async def get_analytics_run(self, run_id: UUID) -> object | None:
        del run_id
        return self.analytics

    async def store_blueprint(self, write: PaperBlueprintWrite) -> RepositoryPaperBlueprintResult:
        created = self.created_sequence.pop(0)
        if self.stored is None:
            self.stored = PaperBlueprintRecord(
                id=write.id,
                curriculum_version_id=write.curriculum_version_id,
                analytics_run_id=write.analytics_run_id,
                blueprint_id=write.blueprint_id,
                schema_version=write.schema_version,
                algorithm_version=write.algorithm_version,
                config_version=write.config_version,
                seed=write.seed,
                total_marks=write.total_marks,
                slot_count=write.slot_count,
                specification_fingerprint=write.specification_fingerprint,
                input_fingerprint=write.input_fingerprint,
                result_fingerprint=write.result_fingerprint,
                specification=write.specification,
                blueprint=write.blueprint,
                taxonomy_snapshot=write.taxonomy_snapshot,
                created_by=write.created_by,
                created_at=NOW,
            )
        return RepositoryPaperBlueprintResult(self.stored, created=created)

    async def get_blueprint(
        self,
        curriculum_version_id: UUID,
        paper_blueprint_id: UUID,
    ) -> PaperBlueprintRecord:
        del curriculum_version_id, paper_blueprint_id
        assert self.stored is not None
        return self.stored

    async def list_blueprints(
        self,
        curriculum_version_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[PaperBlueprintRecord, ...]:
        del curriculum_version_id
        assert (limit, offset) == (10, 0)
        assert self.stored is not None
        return (self.stored,)


def taxonomy_for(
    target: TaxonomyTarget,
    curriculum_id: UUID,
) -> tuple[ReviewedTaxonomyNodeRecord, ...]:
    competency = ReviewedTaxonomyNodeRecord(
        id=target.competency_id,
        curriculum_version_id=curriculum_id,
        parent_id=None,
        level=TaxonomyLevel.COMPETENCY,
        code="C1",
        title="Competency",
        active=True,
        review_state=TaxonomyReviewState.REVIEWED,
        reviewed_at=NOW,
        reviewed_by=ACTOR_ID,
    )
    if target.skill_id is None:
        return (competency,)
    skill = ReviewedTaxonomyNodeRecord(
        id=target.skill_id,
        curriculum_version_id=curriculum_id,
        parent_id=target.competency_id,
        level=TaxonomyLevel.SKILL,
        code="S1",
        title="Skill",
        active=True,
        review_state=TaxonomyReviewState.REVIEWED,
        reviewed_at=NOW,
        reviewed_by=ACTOR_ID,
    )
    return competency, skill


def active_scope(specification: BlueprintSpecification) -> CurriculumScopeRecord:
    scope = specification.curriculum_scope
    return CurriculumScopeRecord(
        curriculum_version_id=scope.curriculum_version_id,
        grade=scope.grade,
        medium=scope.medium,
        curriculum_active=True,
        exam_active=True,
        medium_active=True,
    )


def service_with(
    repository: FakeBlueprintRepository,
) -> tuple[BlueprintGenerationService, FakeSession]:
    session = FakeSession()
    service = BlueprintGenerationService(cast(AsyncSession, session))
    service._repository = cast(SqlAlchemyBlueprintRepository, repository)
    return service, session


def test_service_persists_full_snapshots_audits_once_and_reuses_identical_input() -> None:
    async def exercise() -> None:
        specification = make_uniform_specification((2,), 2)
        target = specification.taxonomy_requirements[0].target
        repository = FakeBlueprintRepository(
            active_scope(specification),
            taxonomy_for(target, specification.curriculum_scope.curriculum_version_id),
            created_sequence=(True, False),
        )
        service, session = service_with(repository)

        created = await service.create_blueprint(
            specification.curriculum_scope.curriculum_version_id,
            specification,
            seed=2025,
            analytics_run_id=None,
            actor_id=ACTOR_ID,
        )
        duplicate = await service.create_blueprint(
            specification.curriculum_scope.curriculum_version_id,
            specification,
            seed=2025,
            analytics_run_id=None,
            actor_id=UUID(int=82_999),
        )
        fetched = await service.get_blueprint(
            specification.curriculum_scope.curriculum_version_id,
            created.record.id,
        )
        listed = await service.list_blueprints(
            specification.curriculum_scope.curriculum_version_id,
            limit=10,
            offset=0,
        )

        assert created.deduplicated is False
        assert duplicate.deduplicated is True
        assert duplicate.record == created.record == fetched == listed[0]
        assert deserialize_specification(created.record.specification) == specification
        blueprint = deserialize_blueprint(created.record.blueprint)
        assert blueprint.seed == 2025
        assert blueprint.total_marks == specification.total_marks
        assert len(blueprint.slots) == created.record.slot_count == 2
        assert created.record.specification_fingerprint == fingerprint_snapshot(
            created.record.specification
        )
        assert created.record.result_fingerprint == fingerprint_snapshot(created.record.blueprint)
        assert created.record.input_fingerprint.startswith("sha256:")
        assert len(created.record.taxonomy_snapshot) == 2
        assert session.commit_count == 1
        assert len(session.added) == 1
        audit = cast(AdminAuditEventModel, session.added[0])
        assert audit.action == "blueprint.created"
        assert audit.resource_id == created.record.id
        assert audit.actor_id == ACTOR_ID
        assert audit.payload["slot_count"] == 2
        assert audit.payload["analytics_run_id"] is None

    asyncio.run(exercise())


def analytics_specification() -> BlueprintSpecification:
    record = analytics_record()
    response = cast(dict[str, object], record.result["backtest"])
    recommended = cast(dict[str, object], response["recommended_run"])
    priorities = cast(list[dict[str, object]], recommended["priorities"])
    targets = tuple(
        TaxonomyTarget(
            competency_id=UUID(cast(str, item["competency_id"])),
            skill_id=UUID(cast(str, item["skill_id"])),
        )
        for item in priorities
    )
    base = make_uniform_specification((2,), 2)
    requirements = tuple(
        TaxonomyRequirement(
            target=target,
            minimum_slots=1,
            maximum_slots=1,
            priority=baseline_priority(f"client-{index}", score=99_999),
            retrieval_query_hints=(f"target {index}",),
            generation_instructions=(f"generate target {index}",),
        )
        for index, target in enumerate(targets, start=1)
    )
    return replace(
        base,
        curriculum_scope=CurriculumScope(record.curriculum_version_id, 5, "si"),
        taxonomy_requirements=requirements,
    )


def test_service_uses_only_linked_persisted_analytics_priorities_and_rejects_cross_scope() -> None:
    async def exercise() -> None:
        specification = analytics_specification()
        record = analytics_record()
        taxonomy = tuple(
            node
            for requirement in specification.taxonomy_requirements
            for node in taxonomy_for(requirement.target, record.curriculum_version_id)
        )
        taxonomy = tuple({node.id: node for node in taxonomy}.values())
        repository = FakeBlueprintRepository(
            active_scope(specification),
            taxonomy,
            analytics=record,
        )
        service, _ = service_with(repository)

        result = await service.create_blueprint(
            record.curriculum_version_id,
            specification,
            seed=7,
            analytics_run_id=record.id,
            actor_id=ACTOR_ID,
        )

        persisted = deserialize_specification(result.record.specification)
        assert result.record.analytics_run_id == record.id
        assert all(
            requirement.priority.baseline_score != 99_999
            for requirement in persisted.taxonomy_requirements
        )
        assert all(
            f"analytics:persisted-run:{record.id}" in requirement.priority.forecast_evidence_refs
            for requirement in persisted.taxonomy_requirements
        )

        repository.analytics = replace(record, curriculum_version_id=UUID(int=999))
        with pytest.raises(BlueprintAnalyticsCurriculumMismatchError):
            await service.create_blueprint(
                record.curriculum_version_id,
                specification,
                seed=8,
                analytics_run_id=record.id,
                actor_id=ACTOR_ID,
            )
        repository.analytics = None
        with pytest.raises(BlueprintAnalyticsRunNotFoundError):
            await service.create_blueprint(
                record.curriculum_version_id,
                specification,
                seed=8,
                analytics_run_id=record.id,
                actor_id=ACTOR_ID,
            )

    asyncio.run(exercise())


def test_service_rejects_curriculum_scope_taxonomy_and_snapshot_limit_violations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        specification = make_uniform_specification((1,), 1)
        target = specification.taxonomy_requirements[0].target
        valid_taxonomy = taxonomy_for(
            target,
            specification.curriculum_scope.curriculum_version_id,
        )

        missing_service, _ = service_with(FakeBlueprintRepository(None, valid_taxonomy))
        with pytest.raises(BlueprintCurriculumNotFoundError):
            await missing_service.create_blueprint(
                specification.curriculum_scope.curriculum_version_id,
                specification,
                seed=0,
                analytics_run_id=None,
                actor_id=ACTOR_ID,
            )

        inactive_scope = replace(active_scope(specification), curriculum_active=False)
        inactive_service, _ = service_with(FakeBlueprintRepository(inactive_scope, valid_taxonomy))
        with pytest.raises(BlueprintCurriculumInactiveError):
            await inactive_service.create_blueprint(
                specification.curriculum_scope.curriculum_version_id,
                specification,
                seed=0,
                analytics_run_id=None,
                actor_id=ACTOR_ID,
            )

        mismatches = (
            replace(
                specification,
                curriculum_scope=replace(specification.curriculum_scope, grade=6),
            ),
            replace(
                specification,
                curriculum_scope=replace(specification.curriculum_scope, medium="ta"),
            ),
            replace(
                specification,
                curriculum_scope=replace(
                    specification.curriculum_scope,
                    curriculum_version_id=UUID(int=999),
                ),
            ),
        )
        for mismatch in mismatches:
            mismatch_service, _ = service_with(
                FakeBlueprintRepository(active_scope(specification), valid_taxonomy)
            )
            with pytest.raises(BlueprintCurriculumScopeMismatchError):
                await mismatch_service.create_blueprint(
                    specification.curriculum_scope.curriculum_version_id,
                    mismatch,
                    seed=0,
                    analytics_run_id=None,
                    actor_id=ACTOR_ID,
                )

        invalid_taxonomies = (
            ((), TaxonomySnapshotViolation.NODE_NOT_FOUND),
            (
                (replace(valid_taxonomy[1], level=TaxonomyLevel.SUB_SKILL), *valid_taxonomy[:1]),
                TaxonomySnapshotViolation.LEVEL_MISMATCH,
            ),
            (
                (replace(valid_taxonomy[1], parent_id=UUID(int=998)), *valid_taxonomy[:1]),
                TaxonomySnapshotViolation.HIERARCHY_MISMATCH,
            ),
            (
                (
                    replace(
                        valid_taxonomy[1],
                        active=False,
                        review_state=TaxonomyReviewState.DEPRECATED,
                    ),
                    *valid_taxonomy[:1],
                ),
                TaxonomySnapshotViolation.NOT_ACTIVE_REVIEWED,
            ),
            (
                (
                    replace(valid_taxonomy[1], curriculum_version_id=UUID(int=997)),
                    *valid_taxonomy[:1],
                ),
                TaxonomySnapshotViolation.CROSS_CURRICULUM,
            ),
        )
        for taxonomy, violation in invalid_taxonomies:
            invalid_service, _ = service_with(
                FakeBlueprintRepository(active_scope(specification), taxonomy)
            )
            with pytest.raises(BlueprintTaxonomyValidationError) as raised:
                await invalid_service.create_blueprint(
                    specification.curriculum_scope.curriculum_version_id,
                    specification,
                    seed=0,
                    analytics_run_id=None,
                    actor_id=ACTOR_ID,
                )
            assert raised.value.violation is violation

        for constant, snapshot in (
            ("MAX_BLUEPRINT_SLOTS", "slot_count"),
            ("MAX_BLUEPRINT_TOTAL_MARKS", "total_marks"),
            ("MAX_TAXONOMY_SNAPSHOT_NODES", "taxonomy_node_count"),
        ):
            with monkeypatch.context() as context:
                context.setattr(f"exam_guru_api.blueprints.service.{constant}", 0)
                limit_service, _ = service_with(
                    FakeBlueprintRepository(active_scope(specification), valid_taxonomy)
                )
                with pytest.raises(BlueprintSnapshotLimitError) as limit_raised:
                    await limit_service.create_blueprint(
                        specification.curriculum_scope.curriculum_version_id,
                        specification,
                        seed=0,
                        analytics_run_id=None,
                        actor_id=ACTOR_ID,
                    )
                assert limit_raised.value.snapshot == snapshot

        with monkeypatch.context() as context:
            context.setattr(
                "exam_guru_api.blueprints.service.MAX_SPECIFICATION_SNAPSHOT_BYTES",
                1,
            )
            limit_service, _ = service_with(
                FakeBlueprintRepository(active_scope(specification), valid_taxonomy)
            )
            with pytest.raises(BlueprintSnapshotLimitError):
                await limit_service.create_blueprint(
                    specification.curriculum_scope.curriculum_version_id,
                    specification,
                    seed=0,
                    analytics_run_id=None,
                    actor_id=ACTOR_ID,
                )

    asyncio.run(exercise())


def test_service_validates_and_snapshots_complete_sub_skill_concept_hierarchy() -> None:
    async def exercise() -> None:
        specification = make_uniform_specification((1,), 1)
        original = specification.taxonomy_requirements[0]
        target = TaxonomyTarget(
            competency_id=original.target.competency_id,
            skill_id=original.target.skill_id,
            sub_skill_id=UUID(int=82_100),
            learning_concept_id=UUID(int=82_101),
        )
        specification = replace(
            specification,
            taxonomy_requirements=(replace(original, target=target),),
        )
        curriculum_id = specification.curriculum_scope.curriculum_version_id
        competency, skill = taxonomy_for(target, curriculum_id)
        taxonomy = (
            competency,
            skill,
            ReviewedTaxonomyNodeRecord(
                id=cast(UUID, target.sub_skill_id),
                curriculum_version_id=curriculum_id,
                parent_id=target.skill_id,
                level=TaxonomyLevel.SUB_SKILL,
                code="SS1",
                title="Sub-skill",
                active=True,
                review_state=TaxonomyReviewState.REVIEWED,
                reviewed_at=NOW,
                reviewed_by=ACTOR_ID,
            ),
            ReviewedTaxonomyNodeRecord(
                id=cast(UUID, target.learning_concept_id),
                curriculum_version_id=curriculum_id,
                parent_id=target.sub_skill_id,
                level=TaxonomyLevel.LEARNING_CONCEPT,
                code="LC1",
                title="Learning concept",
                active=True,
                review_state=TaxonomyReviewState.REVIEWED,
                reviewed_at=NOW,
                reviewed_by=ACTOR_ID,
            ),
        )
        service, _ = service_with(FakeBlueprintRepository(active_scope(specification), taxonomy))

        created = await service.create_blueprint(
            curriculum_id,
            specification,
            seed=1,
            analytics_run_id=None,
            actor_id=ACTOR_ID,
        )

        assert len(created.record.taxonomy_snapshot) == 4
        assert {item["level"] for item in created.record.taxonomy_snapshot} >= {
            "sub_skill",
            "learning_concept",
        }

        competency_target = TaxonomyTarget(original.target.competency_id)
        competency_specification = replace(
            specification,
            taxonomy_requirements=(replace(original, target=competency_target),),
        )
        competency_service, _ = service_with(
            FakeBlueprintRepository(
                active_scope(competency_specification),
                (competency,),
            )
        )
        competency_created = await competency_service.create_blueprint(
            curriculum_id,
            competency_specification,
            seed=2,
            analytics_run_id=None,
            actor_id=ACTOR_ID,
        )
        assert len(competency_created.record.taxonomy_snapshot) == 1

    asyncio.run(exercise())

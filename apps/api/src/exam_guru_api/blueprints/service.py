from dataclasses import dataclass, replace
from enum import StrEnum
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.curriculum.domain import TaxonomyLevel, TaxonomyReviewState

from .analytics import adapt_persisted_analytics_priorities
from .domain import BlueprintSpecification, TaxonomyTarget
from .generator import generate_blueprint
from .models import (
    MAX_BLUEPRINT_SLOTS,
    MAX_BLUEPRINT_SNAPSHOT_BYTES,
    MAX_BLUEPRINT_TOTAL_MARKS,
    MAX_SPECIFICATION_SNAPSHOT_BYTES,
    MAX_TAXONOMY_SNAPSHOT_BYTES,
    MAX_TAXONOMY_SNAPSHOT_NODES,
)
from .repository import (
    CurriculumScopeRecord,
    PaperBlueprintRecord,
    PaperBlueprintWrite,
    RepositoryPaperBlueprintResult,
    ReviewedTaxonomyNodeRecord,
    SqlAlchemyBlueprintRepository,
)
from .serialization import (
    canonical_snapshot_bytes,
    fingerprint_snapshot,
    serialize_blueprint,
    serialize_specification,
)

_BLUEPRINT_NAMESPACE = uuid5(NAMESPACE_URL, "exam-guru/paper-blueprints")


class BlueprintCurriculumNotFoundError(LookupError):
    def __init__(self, curriculum_version_id: UUID) -> None:
        self.curriculum_version_id = curriculum_version_id
        super().__init__(f"curriculum version not found: {curriculum_version_id}")


class BlueprintCurriculumInactiveError(ValueError):
    def __init__(self, curriculum_version_id: UUID) -> None:
        self.curriculum_version_id = curriculum_version_id
        super().__init__(f"curriculum scope is inactive: {curriculum_version_id}")


class BlueprintCurriculumScopeMismatchError(ValueError):
    def __init__(self, field: str, expected: object, actual: object) -> None:
        self.field = field
        self.expected = expected
        self.actual = actual
        super().__init__(f"curriculum scope {field} mismatch: expected {expected}, got {actual}")


class TaxonomySnapshotViolation(StrEnum):
    NODE_NOT_FOUND = "node_not_found"
    LEVEL_MISMATCH = "level_mismatch"
    HIERARCHY_MISMATCH = "hierarchy_mismatch"
    NOT_ACTIVE_REVIEWED = "not_active_reviewed"
    CROSS_CURRICULUM = "cross_curriculum"


class BlueprintTaxonomyValidationError(ValueError):
    def __init__(self, node_id: UUID, violation: TaxonomySnapshotViolation) -> None:
        self.node_id = node_id
        self.violation = violation
        super().__init__(f"invalid blueprint taxonomy {node_id}: {violation.value}")


class BlueprintAnalyticsRunNotFoundError(LookupError):
    def __init__(self, analytics_run_id: UUID) -> None:
        self.analytics_run_id = analytics_run_id
        super().__init__(f"analytics run not found: {analytics_run_id}")


class BlueprintAnalyticsCurriculumMismatchError(ValueError):
    def __init__(self, analytics_run_id: UUID) -> None:
        self.analytics_run_id = analytics_run_id
        super().__init__(f"analytics run has a different curriculum: {analytics_run_id}")


class BlueprintSnapshotLimitError(ValueError):
    def __init__(self, snapshot: str, maximum_bytes: int, actual_bytes: int) -> None:
        self.snapshot = snapshot
        self.maximum_bytes = maximum_bytes
        self.actual_bytes = actual_bytes
        super().__init__(f"{snapshot} snapshot exceeds {maximum_bytes} bytes: {actual_bytes}")


@dataclass(frozen=True, slots=True)
class BlueprintCreationResult:
    record: PaperBlueprintRecord
    deduplicated: bool


class BlueprintGenerationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = SqlAlchemyBlueprintRepository(session)

    async def create_blueprint(
        self,
        curriculum_version_id: UUID,
        specification: BlueprintSpecification,
        *,
        seed: int,
        analytics_run_id: UUID | None,
        actor_id: UUID,
    ) -> BlueprintCreationResult:
        scope = await self._require_curriculum_scope(curriculum_version_id)
        self._validate_scope(scope, specification)
        if not (scope.curriculum_active and scope.exam_active and scope.medium_active):
            raise BlueprintCurriculumInactiveError(curriculum_version_id)

        node_ids = _taxonomy_node_ids(specification)
        taxonomy = await self._repository.list_taxonomy_nodes(
            curriculum_version_id,
            node_ids,
        )
        _validate_reviewed_taxonomy(curriculum_version_id, specification, taxonomy)
        canonical_taxonomy = tuple(sorted(taxonomy, key=lambda item: item.id.int))

        analytics_result_fingerprint: str | None = None
        materialized_specification = specification
        if analytics_run_id is not None:
            analytics = await self._repository.get_analytics_run(analytics_run_id)
            if analytics is None:
                raise BlueprintAnalyticsRunNotFoundError(analytics_run_id)
            if analytics.curriculum_version_id != curriculum_version_id:
                raise BlueprintAnalyticsCurriculumMismatchError(analytics_run_id)
            priorities = adapt_persisted_analytics_priorities(
                analytics,
                (requirement.target for requirement in specification.taxonomy_requirements),
            )
            materialized_specification = replace(
                specification,
                taxonomy_requirements=tuple(
                    replace(requirement, priority=priorities[requirement.target])
                    for requirement in specification.taxonomy_requirements
                ),
            )
            analytics_result_fingerprint = analytics.result_fingerprint

        blueprint = generate_blueprint(materialized_specification, seed=seed)
        if len(blueprint.slots) > MAX_BLUEPRINT_SLOTS:
            raise BlueprintSnapshotLimitError(
                "slot_count",
                MAX_BLUEPRINT_SLOTS,
                len(blueprint.slots),
            )
        if blueprint.total_marks > MAX_BLUEPRINT_TOTAL_MARKS:
            raise BlueprintSnapshotLimitError(
                "total_marks",
                MAX_BLUEPRINT_TOTAL_MARKS,
                blueprint.total_marks,
            )
        if len(canonical_taxonomy) > MAX_TAXONOMY_SNAPSHOT_NODES:
            raise BlueprintSnapshotLimitError(
                "taxonomy_node_count",
                MAX_TAXONOMY_SNAPSHOT_NODES,
                len(canonical_taxonomy),
            )

        specification_snapshot = serialize_specification(materialized_specification)
        blueprint_snapshot = serialize_blueprint(blueprint)
        taxonomy_snapshot = [node.to_snapshot() for node in canonical_taxonomy]
        _validate_snapshot_size(
            "specification",
            specification_snapshot,
            MAX_SPECIFICATION_SNAPSHOT_BYTES,
        )
        _validate_snapshot_size(
            "blueprint",
            blueprint_snapshot,
            MAX_BLUEPRINT_SNAPSHOT_BYTES,
        )
        _validate_snapshot_size(
            "taxonomy",
            taxonomy_snapshot,
            MAX_TAXONOMY_SNAPSHOT_BYTES,
        )

        specification_fingerprint = fingerprint_snapshot(specification_snapshot)
        result_fingerprint = fingerprint_snapshot(blueprint_snapshot)
        taxonomy_fingerprint = fingerprint_snapshot(taxonomy_snapshot)
        input_fingerprint = fingerprint_snapshot(
            {
                "algorithm_version": blueprint.version.algorithm_version,
                "analytics_result_fingerprint": analytics_result_fingerprint,
                "analytics_run_id": str(analytics_run_id) if analytics_run_id else None,
                "config_version": blueprint.version.config_version,
                "schema_version": blueprint.version.schema_version,
                "seed": seed,
                "specification_fingerprint": specification_fingerprint,
                "taxonomy_fingerprint": taxonomy_fingerprint,
            }
        )
        write = PaperBlueprintWrite(
            id=uuid5(_BLUEPRINT_NAMESPACE, input_fingerprint),
            curriculum_version_id=curriculum_version_id,
            analytics_run_id=analytics_run_id,
            blueprint_id=blueprint.version.blueprint_id,
            schema_version=blueprint.version.schema_version,
            algorithm_version=blueprint.version.algorithm_version,
            config_version=blueprint.version.config_version,
            seed=seed,
            total_marks=blueprint.total_marks,
            slot_count=len(blueprint.slots),
            specification_fingerprint=specification_fingerprint,
            input_fingerprint=input_fingerprint,
            result_fingerprint=result_fingerprint,
            specification=dict(specification_snapshot),
            blueprint=dict(blueprint_snapshot),
            taxonomy_snapshot=taxonomy_snapshot,
            created_by=actor_id,
        )
        stored = await self._repository.store_blueprint(write)
        if stored.created:
            self._audit_created(stored, actor_id=actor_id)
            await self._session.commit()
        return BlueprintCreationResult(
            record=stored.record,
            deduplicated=not stored.created,
        )

    async def get_blueprint(
        self,
        curriculum_version_id: UUID,
        paper_blueprint_id: UUID,
    ) -> PaperBlueprintRecord:
        await self._require_curriculum_scope(curriculum_version_id)
        return await self._repository.get_blueprint(
            curriculum_version_id,
            paper_blueprint_id,
        )

    async def list_blueprints(
        self,
        curriculum_version_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[PaperBlueprintRecord, ...]:
        await self._require_curriculum_scope(curriculum_version_id)
        return await self._repository.list_blueprints(
            curriculum_version_id,
            limit=limit,
            offset=offset,
        )

    async def _require_curriculum_scope(
        self,
        curriculum_version_id: UUID,
    ) -> CurriculumScopeRecord:
        scope = await self._repository.get_curriculum_scope(curriculum_version_id)
        if scope is None:
            raise BlueprintCurriculumNotFoundError(curriculum_version_id)
        return scope

    @staticmethod
    def _validate_scope(
        scope: CurriculumScopeRecord,
        specification: BlueprintSpecification,
    ) -> None:
        supplied = specification.curriculum_scope
        expected_values = {
            "curriculum_version_id": scope.curriculum_version_id,
            "grade": scope.grade,
            "medium": scope.medium,
        }
        actual_values = {
            "curriculum_version_id": supplied.curriculum_version_id,
            "grade": supplied.grade,
            "medium": supplied.medium,
        }
        for field, expected in expected_values.items():
            actual = actual_values[field]
            if actual != expected:
                raise BlueprintCurriculumScopeMismatchError(field, expected, actual)

    def _audit_created(
        self,
        stored: RepositoryPaperBlueprintResult,
        *,
        actor_id: UUID,
    ) -> None:
        record = stored.record
        self._session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=actor_id,
                action="blueprint.created",
                resource_type="paper_blueprint",
                resource_id=record.id,
                payload={
                    "curriculum_version_id": str(record.curriculum_version_id),
                    "analytics_run_id": (
                        str(record.analytics_run_id)
                        if record.analytics_run_id is not None
                        else None
                    ),
                    "blueprint_id": record.blueprint_id,
                    "schema_version": record.schema_version,
                    "algorithm_version": record.algorithm_version,
                    "config_version": record.config_version,
                    "seed": record.seed,
                    "total_marks": record.total_marks,
                    "slot_count": record.slot_count,
                    "specification_fingerprint": record.specification_fingerprint,
                    "input_fingerprint": record.input_fingerprint,
                    "result_fingerprint": record.result_fingerprint,
                    "taxonomy_node_count": len(record.taxonomy_snapshot),
                },
            )
        )


def _taxonomy_node_ids(specification: BlueprintSpecification) -> frozenset[UUID]:
    return frozenset(
        node_id
        for requirement in specification.taxonomy_requirements
        for node_id in _target_path_ids(requirement.target)
    )


def _target_path_ids(target: TaxonomyTarget) -> tuple[UUID, ...]:
    return tuple(
        node_id
        for node_id in (
            target.competency_id,
            target.skill_id,
            target.sub_skill_id,
            target.learning_concept_id,
        )
        if node_id is not None
    )


def _expected_target_nodes(
    target: TaxonomyTarget,
) -> tuple[tuple[UUID, TaxonomyLevel, UUID | None], ...]:
    expected: list[tuple[UUID, TaxonomyLevel, UUID | None]] = [
        (target.competency_id, TaxonomyLevel.COMPETENCY, None)
    ]
    if target.skill_id is not None:
        expected.append((target.skill_id, TaxonomyLevel.SKILL, target.competency_id))
    if target.sub_skill_id is not None:
        expected.append((target.sub_skill_id, TaxonomyLevel.SUB_SKILL, target.skill_id))
    if target.learning_concept_id is not None:
        expected.append(
            (
                target.learning_concept_id,
                TaxonomyLevel.LEARNING_CONCEPT,
                target.sub_skill_id,
            )
        )
    return tuple(expected)


def _validate_reviewed_taxonomy(
    curriculum_version_id: UUID,
    specification: BlueprintSpecification,
    records: tuple[ReviewedTaxonomyNodeRecord, ...],
) -> None:
    records_by_id = {record.id: record for record in records}
    for requirement in specification.taxonomy_requirements:
        for node_id, expected_level, expected_parent_id in _expected_target_nodes(
            requirement.target
        ):
            record = records_by_id.get(node_id)
            if record is None:
                raise BlueprintTaxonomyValidationError(
                    node_id,
                    TaxonomySnapshotViolation.NODE_NOT_FOUND,
                )
            if record.curriculum_version_id != curriculum_version_id:
                raise BlueprintTaxonomyValidationError(
                    node_id,
                    TaxonomySnapshotViolation.CROSS_CURRICULUM,
                )
            if record.level is not expected_level:
                raise BlueprintTaxonomyValidationError(
                    node_id,
                    TaxonomySnapshotViolation.LEVEL_MISMATCH,
                )
            if record.parent_id != expected_parent_id:
                raise BlueprintTaxonomyValidationError(
                    node_id,
                    TaxonomySnapshotViolation.HIERARCHY_MISMATCH,
                )
            if not record.active or record.review_state is not TaxonomyReviewState.REVIEWED:
                raise BlueprintTaxonomyValidationError(
                    node_id,
                    TaxonomySnapshotViolation.NOT_ACTIVE_REVIEWED,
                )


def _validate_snapshot_size(snapshot: str, value: object, maximum_bytes: int) -> None:
    actual_bytes = len(canonical_snapshot_bytes(value))
    if actual_bytes > maximum_bytes:
        raise BlueprintSnapshotLimitError(snapshot, maximum_bytes, actual_bytes)

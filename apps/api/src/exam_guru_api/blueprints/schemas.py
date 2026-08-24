from copy import deepcopy
from datetime import datetime
from typing import Annotated, Any, Self, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from exam_guru_api.curriculum.domain import TaxonomyLevel, TaxonomyReviewState

from .domain import (
    BlueprintSpecification,
    Difficulty,
    PriorityMode,
    QuestionType,
)
from .serialization import deserialize_specification

StrictSeed = Annotated[int, Field(strict=True, ge=-(2**63), le=2**63 - 1)]
PositiveScore = Annotated[int, Field(strict=True, ge=1, le=2_147_483_647)]
PositiveMarks = Annotated[int, Field(strict=True, ge=1, le=100_000)]
PositiveSlots = Annotated[int, Field(strict=True, ge=1, le=200)]
VersionText = Annotated[str, Field(min_length=1, max_length=128)]
ShortText = Annotated[str, Field(min_length=1, max_length=255)]
ReferenceText = Annotated[str, Field(min_length=1, max_length=512)]
InstructionText = Annotated[str, Field(min_length=1, max_length=1_000)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _FrozenStrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CurriculumScopeRequest(_StrictModel):
    curriculum_version_id: UUID
    grade: Annotated[int, Field(strict=True, ge=1, le=13)]
    medium: Annotated[str, Field(min_length=2, max_length=16, pattern=r"^[a-z][a-z0-9-]+$")]


class TaxonomyTargetRequest(_StrictModel):
    competency_id: UUID
    skill_id: UUID | None = None
    sub_skill_id: UUID | None = None
    learning_concept_id: UUID | None = None


class BaselinePracticePriorityRequest(_StrictModel):
    baseline_score: PositiveScore
    baseline_version: VersionText
    baseline_evidence_refs: Annotated[
        tuple[ReferenceText, ...],
        Field(min_length=1, max_length=100),
    ]


class SectionSpecificationRequest(_StrictModel):
    section_id: Annotated[str, Field(min_length=1, max_length=64)]
    title: ShortText
    marks: PositiveMarks
    question_count: PositiveSlots
    allowed_marks_per_slot: Annotated[
        tuple[Annotated[int, Field(strict=True, ge=1, le=10_000)], ...],
        Field(min_length=1, max_length=100),
    ]
    allowed_question_types: Annotated[
        tuple[QuestionType, ...],
        Field(min_length=1, max_length=3),
    ] = tuple(QuestionType)
    allowed_difficulties: Annotated[
        tuple[Difficulty, ...],
        Field(min_length=1, max_length=3),
    ] = tuple(Difficulty)
    allowed_taxonomy_targets: Annotated[
        tuple[TaxonomyTargetRequest, ...],
        Field(max_length=200),
    ] = ()
    retrieval_query_hints: Annotated[
        tuple[InstructionText, ...],
        Field(max_length=50),
    ] = ()


class QuestionTypeAllocationRequest(_StrictModel):
    question_type: QuestionType
    exact_slots: PositiveSlots
    archetypes: Annotated[tuple[ShortText, ...], Field(min_length=1, max_length=50)]
    exact_marks: PositiveMarks | None = None


class DifficultyAllocationRequest(_StrictModel):
    difficulty: Difficulty
    exact_slots: PositiveSlots
    exact_marks: PositiveMarks | None = None


class TaxonomyRequirementRequest(_StrictModel):
    target: TaxonomyTargetRequest
    minimum_slots: PositiveSlots
    priority: BaselinePracticePriorityRequest
    retrieval_query_hints: Annotated[
        tuple[InstructionText, ...],
        Field(min_length=1, max_length=50),
    ]
    generation_instructions: Annotated[
        tuple[InstructionText, ...],
        Field(min_length=1, max_length=50),
    ]
    maximum_slots: PositiveSlots | None = None
    allowed_section_ids: Annotated[
        tuple[Annotated[str, Field(min_length=1, max_length=64)], ...],
        Field(max_length=20),
    ] = ()


class UniquenessPolicyRequest(_StrictModel):
    forbid_duplicate_stems: bool = True
    forbid_verbatim_sources: bool = True
    max_similarity_basis_points: Annotated[int, Field(strict=True, ge=0, le=9_999)] = 8_500
    minimum_distinct_contexts: Annotated[int, Field(strict=True, ge=1, le=100)] = 1


class GenerationPolicyRequest(_StrictModel):
    response_language: Annotated[str, Field(min_length=2, max_length=16)]
    instructions: Annotated[
        tuple[InstructionText, ...],
        Field(min_length=1, max_length=100),
    ]
    answer_requirements: Annotated[
        tuple[InstructionText, ...],
        Field(min_length=1, max_length=100),
    ]
    retrieval_query_hints: Annotated[
        tuple[InstructionText, ...],
        Field(min_length=1, max_length=100),
    ]
    uniqueness: UniquenessPolicyRequest


class BlueprintSpecificationRequest(_StrictModel):
    config_version: VersionText
    paper_code: Annotated[str, Field(min_length=1, max_length=64)]
    title: ShortText
    total_marks: PositiveMarks
    curriculum_scope: CurriculumScopeRequest
    sections: Annotated[
        tuple[SectionSpecificationRequest, ...],
        Field(min_length=1, max_length=20),
    ]
    question_type_allocations: Annotated[
        tuple[QuestionTypeAllocationRequest, ...],
        Field(min_length=1, max_length=3),
    ]
    difficulty_allocations: Annotated[
        tuple[DifficultyAllocationRequest, ...],
        Field(min_length=1, max_length=3),
    ]
    taxonomy_requirements: Annotated[
        tuple[TaxonomyRequirementRequest, ...],
        Field(min_length=1, max_length=200),
    ]
    generation_policy: GenerationPolicyRequest

    def to_domain(self) -> BlueprintSpecification:
        payload = deepcopy(self.model_dump(mode="json"))
        requirements = cast(list[dict[str, Any]], payload["taxonomy_requirements"])
        for requirement in requirements:
            priority = cast(dict[str, Any], requirement["priority"])
            priority.update(
                {
                    "forecast_score": None,
                    "forecast_version": None,
                    "baseline_backtest_score": None,
                    "forecast_backtest_score": None,
                    "minimum_backtest_improvement": 1,
                    "forecast_evidence_refs": [],
                }
            )
        return deserialize_specification(payload)


class BlueprintCreateRequest(_StrictModel):
    seed: StrictSeed = 0
    analytics_run_id: UUID | None = None
    specification: BlueprintSpecificationRequest

    def to_domain(self) -> BlueprintSpecification:
        return self.specification.to_domain()


class CurriculumScopeResponse(_FrozenStrictModel):
    curriculum_version_id: UUID
    grade: int
    medium: str


class TaxonomyTargetResponse(_FrozenStrictModel):
    competency_id: UUID
    skill_id: UUID | None
    sub_skill_id: UUID | None
    learning_concept_id: UUID | None


class PracticePriorityResponse(_FrozenStrictModel):
    baseline_score: int
    baseline_version: str
    baseline_evidence_refs: list[str]
    forecast_score: int | None
    forecast_version: str | None
    baseline_backtest_score: int | None
    forecast_backtest_score: int | None
    minimum_backtest_improvement: int
    forecast_evidence_refs: list[str]


class SectionSpecificationResponse(_FrozenStrictModel):
    section_id: str
    title: str
    marks: int
    question_count: int
    allowed_marks_per_slot: list[int]
    allowed_question_types: list[QuestionType]
    allowed_difficulties: list[Difficulty]
    allowed_taxonomy_targets: list[TaxonomyTargetResponse]
    retrieval_query_hints: list[str]


class QuestionTypeAllocationResponse(_FrozenStrictModel):
    question_type: QuestionType
    exact_slots: int
    archetypes: list[str]
    exact_marks: int | None


class DifficultyAllocationResponse(_FrozenStrictModel):
    difficulty: Difficulty
    exact_slots: int
    exact_marks: int | None


class TaxonomyRequirementResponse(_FrozenStrictModel):
    target: TaxonomyTargetResponse
    minimum_slots: int
    priority: PracticePriorityResponse
    retrieval_query_hints: list[str]
    generation_instructions: list[str]
    maximum_slots: int | None
    allowed_section_ids: list[str]


class UniquenessPolicyResponse(_FrozenStrictModel):
    forbid_duplicate_stems: bool
    forbid_verbatim_sources: bool
    max_similarity_basis_points: int
    minimum_distinct_contexts: int


class GenerationPolicyResponse(_FrozenStrictModel):
    response_language: str
    instructions: list[str]
    answer_requirements: list[str]
    retrieval_query_hints: list[str]
    uniqueness: UniquenessPolicyResponse


class BlueprintSpecificationResponse(_FrozenStrictModel):
    config_version: str
    paper_code: str
    title: str
    total_marks: int
    curriculum_scope: CurriculumScopeResponse
    sections: list[SectionSpecificationResponse]
    question_type_allocations: list[QuestionTypeAllocationResponse]
    difficulty_allocations: list[DifficultyAllocationResponse]
    taxonomy_requirements: list[TaxonomyRequirementResponse]
    generation_policy: GenerationPolicyResponse


class BlueprintVersionResponse(_FrozenStrictModel):
    blueprint_id: str
    schema_version: str
    algorithm_version: str
    config_version: str
    input_fingerprint: str


class BlueprintSectionResponse(_FrozenStrictModel):
    section_id: str
    title: str
    marks: int
    slot_count: int


class SlotGenerationConstraintsResponse(_FrozenStrictModel):
    curriculum_scope: CurriculumScopeResponse
    taxonomy_target: TaxonomyTargetResponse
    required_question_type: QuestionType
    required_archetype: str
    required_difficulty: Difficulty
    exact_marks: int
    response_language: str
    instructions: list[str]
    answer_requirements: list[str]
    retrieval_query_hints: list[str]
    uniqueness: UniquenessPolicyResponse
    diversity_key: str


class SlotRationaleResponse(_FrozenStrictModel):
    priority_mode: PriorityMode
    effective_priority_score: int
    summary: str


class SlotEvidenceResponse(_FrozenStrictModel):
    config_version: str
    baseline_version: str
    baseline_score: int
    evidence_refs: list[str]
    forecast_version: str | None
    forecast_score: int | None
    baseline_backtest_score: int | None
    forecast_backtest_score: int | None
    minimum_backtest_improvement: int


class BlueprintSlotResponse(_FrozenStrictModel):
    slot_id: str
    ordinal: int
    paper_code: str
    section_id: str
    section_title: str
    section_ordinal: int
    taxonomy_target: TaxonomyTargetResponse
    question_type: QuestionType
    archetype: str
    difficulty: Difficulty
    marks: int
    generation_constraints: SlotGenerationConstraintsResponse
    rationale: SlotRationaleResponse
    evidence: SlotEvidenceResponse


class PaperBlueprintSnapshotResponse(_FrozenStrictModel):
    version: BlueprintVersionResponse
    paper_code: str
    title: str
    seed: int
    total_marks: int
    curriculum_scope: CurriculumScopeResponse
    sections: list[BlueprintSectionResponse]
    question_type_allocations: list[QuestionTypeAllocationResponse]
    difficulty_allocations: list[DifficultyAllocationResponse]
    taxonomy_requirements: list[TaxonomyRequirementResponse]
    slots: list[BlueprintSlotResponse]


class ReviewedTaxonomyNodeSnapshotResponse(_FrozenStrictModel):
    id: UUID
    curriculum_version_id: UUID
    parent_id: UUID | None
    level: TaxonomyLevel
    code: str
    title: str
    active: bool
    review_state: TaxonomyReviewState
    reviewed_at: datetime
    reviewed_by: UUID


class PaperBlueprintResponse(_FrozenStrictModel):
    id: UUID
    curriculum_version_id: UUID
    analytics_run_id: UUID | None
    blueprint_id: str
    schema_version: str
    algorithm_version: str
    config_version: str
    seed: int
    total_marks: int
    slot_count: int
    specification_fingerprint: str
    input_fingerprint: str
    result_fingerprint: str
    specification: BlueprintSpecificationResponse
    blueprint: PaperBlueprintSnapshotResponse
    taxonomy_snapshot: list[ReviewedTaxonomyNodeSnapshotResponse]
    created_by: UUID
    created_at: datetime
    deduplicated: bool = False

    @classmethod
    def from_record(cls, record: object, *, deduplicated: bool = False) -> Self:
        from .repository import PaperBlueprintRecord

        if not isinstance(record, PaperBlueprintRecord):
            raise TypeError("record must be a PaperBlueprintRecord")
        return cls(
            id=record.id,
            curriculum_version_id=record.curriculum_version_id,
            analytics_run_id=record.analytics_run_id,
            blueprint_id=record.blueprint_id,
            schema_version=record.schema_version,
            algorithm_version=record.algorithm_version,
            config_version=record.config_version,
            seed=record.seed,
            total_marks=record.total_marks,
            slot_count=record.slot_count,
            specification_fingerprint=record.specification_fingerprint,
            input_fingerprint=record.input_fingerprint,
            result_fingerprint=record.result_fingerprint,
            specification=BlueprintSpecificationResponse.model_validate(record.specification),
            blueprint=PaperBlueprintSnapshotResponse.model_validate(record.blueprint),
            taxonomy_snapshot=[
                ReviewedTaxonomyNodeSnapshotResponse.model_validate(item)
                for item in record.taxonomy_snapshot
            ],
            created_by=record.created_by,
            created_at=record.created_at,
            deduplicated=deduplicated,
        )


class PaperBlueprintSummaryResponse(_FrozenStrictModel):
    id: UUID
    curriculum_version_id: UUID
    analytics_run_id: UUID | None
    blueprint_id: str
    schema_version: str
    algorithm_version: str
    config_version: str
    paper_code: str
    title: str
    seed: int
    total_marks: int
    slot_count: int
    specification_fingerprint: str
    input_fingerprint: str
    result_fingerprint: str
    created_by: UUID
    created_at: datetime

    @classmethod
    def from_record(cls, record: object) -> Self:
        from .repository import PaperBlueprintRecord

        if not isinstance(record, PaperBlueprintRecord):
            raise TypeError("record must be a PaperBlueprintRecord")
        specification = BlueprintSpecificationResponse.model_validate(record.specification)
        return cls(
            id=record.id,
            curriculum_version_id=record.curriculum_version_id,
            analytics_run_id=record.analytics_run_id,
            blueprint_id=record.blueprint_id,
            schema_version=record.schema_version,
            algorithm_version=record.algorithm_version,
            config_version=record.config_version,
            paper_code=specification.paper_code,
            title=specification.title,
            seed=record.seed,
            total_marks=record.total_marks,
            slot_count=record.slot_count,
            specification_fingerprint=record.specification_fingerprint,
            input_fingerprint=record.input_fingerprint,
            result_fingerprint=record.result_fingerprint,
            created_by=record.created_by,
            created_at=record.created_at,
        )

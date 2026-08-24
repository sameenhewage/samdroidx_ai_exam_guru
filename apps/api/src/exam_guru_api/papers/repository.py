from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import and_, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.curriculum.models import CurriculumVersionModel
from exam_guru_api.generation.models import GenerationAttemptModel, GenerationRunModel
from exam_guru_api.papers.domain import (
    CandidateRevision,
    CandidateState,
    GenerationLineage,
    QuestionCandidate,
    QuestionContent,
    QuestionOption,
    ReviewAction,
    ReviewDecision,
    ReviewRecord,
    SourceProvenance,
    ValidationEvidence,
)
from exam_guru_api.validation.models import ValidationFindingModel, ValidationRunModel
from exam_guru_api.validation.repository import ValidationGenerationRecord

from .models import (
    CandidateReviewEventModel,
    QuestionCandidateModel,
    QuestionCandidateRevisionModel,
)


class ReviewValidationRunNotFoundError(LookupError):
    pass


class ReviewCandidateNotFoundError(LookupError):
    pass


class ReviewCurriculumNotFoundError(LookupError):
    pass


class CandidatePersistenceIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ReviewCreationSource:
    validation: ValidationRunModel
    generation: ValidationGenerationRecord
    findings: tuple[ValidationFindingModel, ...]


@dataclass(frozen=True, slots=True)
class StoredQuestionCandidate:
    candidate: QuestionCandidateModel
    revisions: tuple[QuestionCandidateRevisionModel, ...]
    events: tuple[CandidateReviewEventModel, ...]
    domain: QuestionCandidate


@dataclass(frozen=True, slots=True)
class ReviewCandidateSummary:
    id: UUID
    curriculum_version_id: UUID
    generation_run_id: UUID
    generation_attempt_id: UUID
    validation_run_id: UUID
    paper_blueprint_id: UUID
    blueprint_id: str
    blueprint_version: str
    blueprint_slot_id: str
    state: CandidateState
    version: int
    current_revision: int
    created_by: UUID
    created_at: datetime
    question_type: str
    stem_preview: str
    marks: int
    current_revision_created_at: datetime


@dataclass(frozen=True, slots=True)
class StoredCandidateInsert:
    record: StoredQuestionCandidate
    created: bool


def question_content_payload(content: QuestionContent) -> dict[str, object]:
    return {
        "question_type": content.question_type,
        "stem": content.stem,
        "options": [
            {"option_id": option.option_id, "text": option.text} for option in content.options
        ],
        "answer": content.answer,
        "explanation": content.explanation,
        "marks": content.marks,
        "marking_guide": list(content.marking_guide),
    }


def generation_lineage_payload(
    lineage: GenerationLineage,
    *,
    paper_blueprint_id: UUID,
) -> dict[str, object]:
    return {
        "generation_id": str(lineage.generation_id),
        "generation_attempt_id": str(lineage.generation_attempt_id),
        "paper_blueprint_id": str(paper_blueprint_id),
        "blueprint_id": lineage.blueprint_id,
        "blueprint_version": lineage.blueprint_version,
        "blueprint_slot_id": lineage.blueprint_slot_id,
        "prompt_version": lineage.prompt_version,
        "provider": lineage.provider,
        "model_version": lineage.model_version,
        "retrieval_version": lineage.retrieval_version,
        "schema_version": lineage.schema_version,
        "provenance": [
            {
                "source_document_id": item.source_document_id,
                "source_version": item.source_version,
                "page_number": item.page_number,
                "chunk_id": item.chunk_id,
            }
            for item in lineage.provenance
        ],
    }


def validation_evidence_payload(evidence: ValidationEvidence) -> dict[str, object]:
    return {
        "validation_run_id": str(evidence.validation_run_id),
        "validator_version": evidence.validator_version,
        "finding_refs": list(evidence.finding_refs),
        "passed": evidence.passed,
        "validated_revision": evidence.validated_revision,
    }


def _mapping(value: object, *, keys: frozenset[str], label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or frozenset(value) != keys:
        raise CandidatePersistenceIntegrityError(f"{label} has an invalid shape")
    return cast(Mapping[str, object], value)


def _sequence(value: object, *, label: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise CandidatePersistenceIntegrityError(f"{label} must be an array")
    return cast(Sequence[object], value)


def _text(value: object, *, label: str) -> str:
    if not isinstance(value, str):
        raise CandidatePersistenceIntegrityError(f"{label} must be text")
    return value


def _integer(value: object, *, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise CandidatePersistenceIntegrityError(f"{label} must be an integer")
    return value


def question_content_from_payload(value: object) -> QuestionContent:
    content = _mapping(
        value,
        keys=frozenset(
            {
                "question_type",
                "stem",
                "options",
                "answer",
                "explanation",
                "marks",
                "marking_guide",
            }
        ),
        label="candidate content",
    )
    options = tuple(
        QuestionOption(
            option_id=_text(
                _mapping(
                    raw_option,
                    keys=frozenset({"option_id", "text"}),
                    label="candidate option",
                )["option_id"],
                label="option_id",
            ),
            text=_text(
                cast(Mapping[str, object], raw_option)["text"],
                label="option text",
            ),
        )
        for raw_option in _sequence(content["options"], label="candidate options")
    )
    return QuestionContent(
        question_type=_text(content["question_type"], label="question_type"),
        stem=_text(content["stem"], label="stem"),
        options=options,
        answer=_text(content["answer"], label="answer"),
        explanation=_text(content["explanation"], label="explanation"),
        marks=_integer(content["marks"], label="marks"),
        marking_guide=tuple(
            _text(item, label="marking guide")
            for item in _sequence(content["marking_guide"], label="marking guide")
        ),
    )


def _lineage_from_payload(candidate: QuestionCandidateModel) -> GenerationLineage:
    lineage = _mapping(
        candidate.generation_lineage,
        keys=frozenset(
            {
                "generation_id",
                "generation_attempt_id",
                "paper_blueprint_id",
                "blueprint_id",
                "blueprint_version",
                "blueprint_slot_id",
                "prompt_version",
                "provider",
                "model_version",
                "retrieval_version",
                "schema_version",
                "provenance",
            }
        ),
        label="candidate lineage",
    )
    provenance = tuple(
        SourceProvenance(
            source_document_id=_text(
                _mapping(
                    raw_item,
                    keys=frozenset(
                        {"source_document_id", "source_version", "page_number", "chunk_id"}
                    ),
                    label="candidate provenance",
                )["source_document_id"],
                label="source_document_id",
            ),
            source_version=_text(
                cast(Mapping[str, object], raw_item)["source_version"],
                label="source_version",
            ),
            page_number=_integer(
                cast(Mapping[str, object], raw_item)["page_number"],
                label="page_number",
            ),
            chunk_id=_text(
                cast(Mapping[str, object], raw_item)["chunk_id"],
                label="chunk_id",
            ),
        )
        for raw_item in _sequence(lineage["provenance"], label="candidate provenance")
    )
    try:
        generation_id = UUID(_text(lineage["generation_id"], label="generation_id"))
        attempt_id = UUID(_text(lineage["generation_attempt_id"], label="generation_attempt_id"))
        paper_blueprint_id = UUID(_text(lineage["paper_blueprint_id"], label="paper_blueprint_id"))
    except ValueError as error:
        raise CandidatePersistenceIntegrityError("candidate lineage UUID is invalid") from error
    if (
        generation_id != candidate.generation_run_id
        or attempt_id != candidate.generation_attempt_id
        or paper_blueprint_id != candidate.paper_blueprint_id
    ):
        raise CandidatePersistenceIntegrityError("candidate lineage columns are inconsistent")
    return GenerationLineage(
        generation_id=generation_id,
        generation_attempt_id=attempt_id,
        blueprint_id=_text(lineage["blueprint_id"], label="blueprint_id"),
        blueprint_version=_text(lineage["blueprint_version"], label="blueprint_version"),
        blueprint_slot_id=_text(lineage["blueprint_slot_id"], label="blueprint_slot_id"),
        prompt_version=_text(lineage["prompt_version"], label="prompt_version"),
        provider=_text(lineage["provider"], label="provider"),
        model_version=_text(lineage["model_version"], label="model_version"),
        retrieval_version=_text(lineage["retrieval_version"], label="retrieval_version"),
        schema_version=_text(lineage["schema_version"], label="schema_version"),
        provenance=provenance,
    )


def _validation_from_payload(candidate: QuestionCandidateModel) -> ValidationEvidence:
    evidence = _mapping(
        candidate.validation_evidence,
        keys=frozenset(
            {
                "validation_run_id",
                "validator_version",
                "finding_refs",
                "passed",
                "validated_revision",
            }
        ),
        label="candidate validation evidence",
    )
    try:
        run_id = UUID(_text(evidence["validation_run_id"], label="validation_run_id"))
    except ValueError as error:
        raise CandidatePersistenceIntegrityError("validation_run_id is invalid") from error
    if run_id != candidate.validation_run_id:
        raise CandidatePersistenceIntegrityError("candidate validation columns are inconsistent")
    passed = evidence["passed"]
    if not isinstance(passed, bool):
        raise CandidatePersistenceIntegrityError("validation passed marker must be boolean")
    return ValidationEvidence(
        validation_run_id=run_id,
        validator_version=_text(evidence["validator_version"], label="validator_version"),
        finding_refs=tuple(
            _text(item, label="finding reference")
            for item in _sequence(evidence["finding_refs"], label="finding references")
        ),
        passed=passed,
        validated_revision=_integer(
            evidence["validated_revision"],
            label="validated_revision",
        ),
    )


def _domain_candidate(
    candidate: QuestionCandidateModel,
    revisions: tuple[QuestionCandidateRevisionModel, ...],
    events: tuple[CandidateReviewEventModel, ...],
) -> QuestionCandidate:
    try:
        state = CandidateState(candidate.state)
        domain_revisions = tuple(
            CandidateRevision(
                revision=revision.revision,
                content=question_content_from_payload(revision.content),
                reviewer_id=revision.reviewer_id,
                reason=revision.reason,
            )
            for revision in revisions
        )
        history = tuple(
            ReviewRecord(
                action=ReviewAction(event.action),
                reviewer_id=event.reviewer_id,
                candidate_version=event.candidate_version,
                reason=event.reason,
            )
            for event in events
        )
        decision = None
        if state in {CandidateState.APPROVED, CandidateState.REJECTED}:
            final = events[-1]
            decision = ReviewDecision(
                state=state,
                reviewer_id=final.reviewer_id,
                candidate_version=final.candidate_version,
                reason=final.reason,
            )
        domain = QuestionCandidate(
            candidate_id=candidate.id,
            state=state,
            version=candidate.version,
            lineage=_lineage_from_payload(candidate),
            revisions=domain_revisions,
            validation=_validation_from_payload(candidate),
            review_history=history,
            decision=decision,
        )
    except (IndexError, TypeError, ValueError) as error:
        raise CandidatePersistenceIntegrityError(
            "persisted question candidate cannot be reconstructed"
        ) from error
    if candidate.current_revision != len(revisions):
        raise CandidatePersistenceIntegrityError("candidate current revision is inconsistent")
    return domain


class SqlAlchemyReviewCandidateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def curriculum_exists(self, curriculum_version_id: UUID) -> bool:
        return (
            await self._session.scalar(
                select(CurriculumVersionModel.id).where(
                    CurriculumVersionModel.id == curriculum_version_id
                )
            )
            is not None
        )

    async def get_creation_source(
        self,
        curriculum_version_id: UUID,
        validation_run_id: UUID,
    ) -> ReviewCreationSource:
        row = (
            await self._session.execute(
                select(ValidationRunModel, GenerationRunModel, GenerationAttemptModel)
                .join(
                    GenerationRunModel,
                    GenerationRunModel.id == ValidationRunModel.generation_run_id,
                )
                .outerjoin(
                    GenerationAttemptModel,
                    GenerationAttemptModel.id == ValidationRunModel.generation_attempt_id,
                )
                .where(
                    ValidationRunModel.id == validation_run_id,
                    ValidationRunModel.curriculum_version_id == curriculum_version_id,
                    GenerationRunModel.curriculum_version_id == curriculum_version_id,
                )
            )
        ).one_or_none()
        if row is None:
            raise ReviewValidationRunNotFoundError(validation_run_id)
        validation, generation, attempt = row
        findings = tuple(
            await self._session.scalars(
                select(ValidationFindingModel)
                .join(
                    ValidationRunModel,
                    ValidationRunModel.id == ValidationFindingModel.validation_run_id,
                )
                .where(
                    ValidationFindingModel.validation_run_id == validation_run_id,
                    ValidationRunModel.curriculum_version_id == curriculum_version_id,
                )
                .order_by(ValidationFindingModel.ordinal)
            )
        )
        return ReviewCreationSource(
            validation=validation,
            generation=ValidationGenerationRecord(generation, attempt),
            findings=findings,
        )

    async def find_by_validation(
        self,
        curriculum_version_id: UUID,
        validation_run_id: UUID,
    ) -> StoredQuestionCandidate | None:
        candidate_id = await self._session.scalar(
            select(QuestionCandidateModel.id).where(
                QuestionCandidateModel.curriculum_version_id == curriculum_version_id,
                QuestionCandidateModel.validation_run_id == validation_run_id,
            )
        )
        if candidate_id is None:
            return None
        return await self.get(curriculum_version_id, candidate_id)

    async def insert_initial(
        self,
        *,
        curriculum_version_id: UUID,
        paper_blueprint_id: UUID,
        candidate: QuestionCandidate,
        actor_id: UUID,
    ) -> bool:
        if candidate.validation is None:
            raise CandidatePersistenceIntegrityError("validated candidate lacks evidence")
        values: dict[str, object] = {
            "id": candidate.candidate_id,
            "curriculum_version_id": curriculum_version_id,
            "generation_run_id": candidate.lineage.generation_id,
            "generation_attempt_id": candidate.lineage.generation_attempt_id,
            "validation_run_id": candidate.validation.validation_run_id,
            "paper_blueprint_id": paper_blueprint_id,
            "blueprint_id": candidate.lineage.blueprint_id,
            "blueprint_version": candidate.lineage.blueprint_version,
            "blueprint_slot_id": candidate.lineage.blueprint_slot_id,
            "state": candidate.state.value,
            "version": candidate.version,
            "current_revision": candidate.revisions[-1].revision,
            "generation_lineage": generation_lineage_payload(
                candidate.lineage,
                paper_blueprint_id=paper_blueprint_id,
            ),
            "validation_evidence": validation_evidence_payload(candidate.validation),
            "created_by": actor_id,
        }
        inserted_id = await self._session.scalar(
            insert(QuestionCandidateModel)
            .values(**values)
            .on_conflict_do_nothing()
            .returning(QuestionCandidateModel.id)
        )
        if inserted_id is None:
            return False
        self._session.add(
            QuestionCandidateRevisionModel(
                candidate_id=candidate.candidate_id,
                revision=1,
                candidate_version=2,
                content=question_content_payload(candidate.content),
                reviewer_id=None,
                reason=None,
            )
        )
        await self._session.flush()
        return True

    async def get(
        self,
        curriculum_version_id: UUID,
        candidate_id: UUID,
    ) -> StoredQuestionCandidate:
        candidate = await self._session.scalar(
            select(QuestionCandidateModel)
            .where(
                QuestionCandidateModel.id == candidate_id,
                QuestionCandidateModel.curriculum_version_id == curriculum_version_id,
            )
            .execution_options(populate_existing=True)
        )
        if candidate is None:
            raise ReviewCandidateNotFoundError(candidate_id)
        revisions = tuple(
            await self._session.scalars(
                select(QuestionCandidateRevisionModel)
                .join(
                    QuestionCandidateModel,
                    QuestionCandidateModel.id == QuestionCandidateRevisionModel.candidate_id,
                )
                .where(
                    QuestionCandidateRevisionModel.candidate_id == candidate_id,
                    QuestionCandidateModel.curriculum_version_id == curriculum_version_id,
                )
                .order_by(QuestionCandidateRevisionModel.revision)
                .execution_options(populate_existing=True)
            )
        )
        events = tuple(
            await self._session.scalars(
                select(CandidateReviewEventModel)
                .join(
                    QuestionCandidateModel,
                    QuestionCandidateModel.id == CandidateReviewEventModel.candidate_id,
                )
                .where(
                    CandidateReviewEventModel.candidate_id == candidate_id,
                    QuestionCandidateModel.curriculum_version_id == curriculum_version_id,
                )
                .order_by(CandidateReviewEventModel.candidate_version)
                .execution_options(populate_existing=True)
            )
        )
        return StoredQuestionCandidate(
            candidate=candidate,
            revisions=revisions,
            events=events,
            domain=_domain_candidate(candidate, revisions, events),
        )

    async def list(
        self,
        curriculum_version_id: UUID,
        *,
        state: CandidateState | None,
        paper_blueprint_id: UUID | None,
        blueprint_slot_id: str | None,
        limit: int,
        offset: int,
    ) -> tuple[ReviewCandidateSummary, ...]:
        if not await self.curriculum_exists(curriculum_version_id):
            raise ReviewCurriculumNotFoundError(curriculum_version_id)
        statement = (
            select(
                QuestionCandidateModel.id,
                QuestionCandidateModel.curriculum_version_id,
                QuestionCandidateModel.generation_run_id,
                QuestionCandidateModel.generation_attempt_id,
                QuestionCandidateModel.validation_run_id,
                QuestionCandidateModel.paper_blueprint_id,
                QuestionCandidateModel.blueprint_id,
                QuestionCandidateModel.blueprint_version,
                QuestionCandidateModel.blueprint_slot_id,
                QuestionCandidateModel.state,
                QuestionCandidateModel.version,
                QuestionCandidateModel.current_revision,
                QuestionCandidateModel.created_by,
                QuestionCandidateModel.created_at,
                QuestionCandidateRevisionModel.content["question_type"].as_string(),
                func.left(QuestionCandidateRevisionModel.content["stem"].as_string(), 512),
                QuestionCandidateRevisionModel.content["marks"].as_integer(),
                QuestionCandidateRevisionModel.created_at,
            )
            .join(
                QuestionCandidateRevisionModel,
                and_(
                    QuestionCandidateRevisionModel.candidate_id == QuestionCandidateModel.id,
                    QuestionCandidateRevisionModel.revision
                    == QuestionCandidateModel.current_revision,
                ),
            )
            .where(QuestionCandidateModel.curriculum_version_id == curriculum_version_id)
        )
        if state is not None:
            statement = statement.where(QuestionCandidateModel.state == state.value)
        if paper_blueprint_id is not None:
            statement = statement.where(
                QuestionCandidateModel.paper_blueprint_id == paper_blueprint_id
            )
        if blueprint_slot_id is not None:
            statement = statement.where(
                QuestionCandidateModel.blueprint_slot_id == blueprint_slot_id
            )
        rows = (
            await self._session.execute(
                statement.order_by(
                    QuestionCandidateModel.created_at.desc(),
                    QuestionCandidateModel.id.desc(),
                )
                .offset(offset)
                .limit(limit)
            )
        ).all()
        return tuple(
            ReviewCandidateSummary(
                id=cast(UUID, row[0]),
                curriculum_version_id=cast(UUID, row[1]),
                generation_run_id=cast(UUID, row[2]),
                generation_attempt_id=cast(UUID, row[3]),
                validation_run_id=cast(UUID, row[4]),
                paper_blueprint_id=cast(UUID, row[5]),
                blueprint_id=cast(str, row[6]),
                blueprint_version=cast(str, row[7]),
                blueprint_slot_id=cast(str, row[8]),
                state=CandidateState(cast(str, row[9])),
                version=cast(int, row[10]),
                current_revision=cast(int, row[11]),
                created_by=cast(UUID, row[12]),
                created_at=cast(datetime, row[13]),
                question_type=cast(str, row[14]),
                stem_preview=cast(str, row[15])[:512],
                marks=cast(int, row[16]),
                current_revision_created_at=cast(datetime, row[17]),
            )
            for row in rows
        )

    async def cas_update(
        self,
        *,
        curriculum_version_id: UUID,
        candidate_id: UUID,
        expected_version: int,
        expected_state: CandidateState,
        state: CandidateState,
        version: int,
        current_revision: int,
    ) -> bool:
        updated_id = await self._session.scalar(
            update(QuestionCandidateModel)
            .where(
                QuestionCandidateModel.id == candidate_id,
                QuestionCandidateModel.curriculum_version_id == curriculum_version_id,
                QuestionCandidateModel.version == expected_version,
                QuestionCandidateModel.state == expected_state.value,
            )
            .values(
                state=state.value,
                version=version,
                current_revision=current_revision,
            )
            .returning(QuestionCandidateModel.id)
            .execution_options(synchronize_session=False)
        )
        return updated_id is not None

    def add_revision(
        self,
        *,
        candidate_id: UUID,
        revision: int,
        candidate_version: int,
        content: QuestionContent,
        reviewer_id: UUID,
        reason: str,
    ) -> None:
        self._session.add(
            QuestionCandidateRevisionModel(
                candidate_id=candidate_id,
                revision=revision,
                candidate_version=candidate_version,
                content=question_content_payload(content),
                reviewer_id=reviewer_id,
                reason=reason,
            )
        )

    def add_event(
        self,
        *,
        candidate_id: UUID,
        candidate_version: int,
        action: ReviewAction,
        reviewer_id: UUID,
        revision: int,
        reason: str | None,
    ) -> None:
        self._session.add(
            CandidateReviewEventModel(
                candidate_id=candidate_id,
                candidate_version=candidate_version,
                action=action.value,
                reviewer_id=reviewer_id,
                revision=revision,
                reason=reason,
            )
        )

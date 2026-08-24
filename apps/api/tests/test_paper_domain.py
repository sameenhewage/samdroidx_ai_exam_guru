from collections.abc import Callable
from dataclasses import FrozenInstanceError, replace
from typing import cast
from uuid import UUID

import pytest

from exam_guru_api.papers import (
    AssemblyViolation,
    CandidateInvariantError,
    CandidateState,
    ConcurrentVersionError,
    GenerationLineage,
    InvalidCandidateTransitionError,
    InvalidPaperTransitionError,
    PaperAssemblyError,
    PaperBlueprintReference,
    PaperDraft,
    PaperState,
    QuestionCandidate,
    QuestionContent,
    QuestionOption,
    ServiceBoundaryRequiredError,
    SourceProvenance,
    ValidationEvidence,
    ValidationNotPassedError,
    approve_candidate,
    assemble_paper_draft,
    create_generated_candidate,
    edit_candidate,
    record_candidate_validation,
    reject_candidate,
    start_candidate_review,
)

REVIEWER_ID = UUID("00000000-0000-0000-0000-000000000101")
OTHER_REVIEWER_ID = UUID("00000000-0000-0000-0000-000000000102")


def provenance(*, page_number: int = 7) -> SourceProvenance:
    return SourceProvenance(
        source_document_id="grade-5-maths-guide",
        source_version="reviewed-v3",
        page_number=page_number,
        chunk_id=f"chunk-{page_number}",
    )


def lineage(
    slot_id: str,
    *,
    generation_number: int = 1,
    candidate_blueprint_id: str = "grade5-paper-blueprint",
    candidate_blueprint_version: str = "blueprint-v4",
) -> GenerationLineage:
    return GenerationLineage(
        generation_id=UUID(int=1_000 + generation_number),
        generation_attempt_id=UUID(int=2_000 + generation_number),
        blueprint_id=candidate_blueprint_id,
        blueprint_version=candidate_blueprint_version,
        blueprint_slot_id=slot_id,
        prompt_version="question-prompt-v6",
        provider="deterministic-fake",
        model_version="fixture-model-2026-08",
        retrieval_version="hybrid-v3",
        schema_version="question.v1",
        provenance=(provenance(page_number=6 + generation_number),),
    )


def content(label: str, *, stem: str | None = None) -> QuestionContent:
    return QuestionContent(
        question_type="multiple_choice",
        stem=stem or f"What is {label}?",
        options=(
            QuestionOption(option_id="A", text=f"{label} option A"),
            QuestionOption(option_id="B", text=f"{label} option B"),
        ),
        answer="B",
        explanation=f"{label} explanation",
        marks=2,
        marking_guide=("Award two marks for the correct option.",),
    )


def validation(*, passed: bool = True, run_number: int = 1) -> ValidationEvidence:
    return ValidationEvidence(
        validation_run_id=UUID(int=3_000 + run_number),
        validator_version="validator-suite-v5",
        finding_refs=(f"validation:{run_number}:schema", f"validation:{run_number}:grounding"),
        passed=passed,
    )


def generated_candidate(
    slot_id: str,
    *,
    candidate_number: int = 1,
    candidate_id: UUID | None = None,
    candidate_content: QuestionContent | None = None,
    candidate_blueprint_id: str = "grade5-paper-blueprint",
    candidate_blueprint_version: str = "blueprint-v4",
) -> QuestionCandidate:
    return create_generated_candidate(
        candidate_id=candidate_id or UUID(int=4_000 + candidate_number),
        lineage=lineage(
            slot_id,
            generation_number=candidate_number,
            candidate_blueprint_id=candidate_blueprint_id,
            candidate_blueprint_version=candidate_blueprint_version,
        ),
        content=candidate_content or content(f"candidate-{candidate_number}"),
    )


def validated_candidate(slot_id: str, *, candidate_number: int = 1) -> QuestionCandidate:
    candidate = generated_candidate(slot_id, candidate_number=candidate_number)
    return record_candidate_validation(
        candidate,
        validation(run_number=candidate_number),
        expected_version=candidate.version,
    )


def in_review_candidate(slot_id: str, *, candidate_number: int = 1) -> QuestionCandidate:
    candidate = validated_candidate(slot_id, candidate_number=candidate_number)
    return start_candidate_review(
        candidate,
        reviewer_id=REVIEWER_ID,
        expected_version=candidate.version,
    )


def approved_candidate(
    slot_id: str,
    *,
    candidate_number: int = 1,
    candidate_id: UUID | None = None,
    candidate_content: QuestionContent | None = None,
    candidate_blueprint_id: str = "grade5-paper-blueprint",
    candidate_blueprint_version: str = "blueprint-v4",
) -> QuestionCandidate:
    candidate = generated_candidate(
        slot_id,
        candidate_number=candidate_number,
        candidate_id=candidate_id,
        candidate_content=candidate_content,
        candidate_blueprint_id=candidate_blueprint_id,
        candidate_blueprint_version=candidate_blueprint_version,
    )
    candidate = record_candidate_validation(
        candidate,
        validation(run_number=candidate_number),
        expected_version=candidate.version,
    )
    candidate = start_candidate_review(
        candidate,
        reviewer_id=REVIEWER_ID,
        expected_version=candidate.version,
    )
    return approve_candidate(
        candidate,
        reviewer_id=REVIEWER_ID,
        expected_version=candidate.version,
        note="Reviewed against the source and validation findings.",
    )


def blueprint(*slot_ids: str) -> PaperBlueprintReference:
    return PaperBlueprintReference(
        blueprint_id="grade5-paper-blueprint",
        blueprint_version="blueprint-v4",
        slot_ids=slot_ids or ("slot-a", "slot-b"),
    )


def assembled_draft() -> PaperDraft:
    return assemble_paper_draft(
        paper_id=UUID("00000000-0000-0000-0000-000000005001"),
        title="Grade 5 Scholarship Practice Paper",
        blueprint=blueprint(),
        candidates=(
            approved_candidate("slot-b", candidate_number=2),
            approved_candidate("slot-a", candidate_number=1),
        ),
    )


def test_candidate_follows_validation_and_human_review_lifecycle() -> None:
    generated = generated_candidate("slot-a")
    validated = record_candidate_validation(
        generated,
        validation(),
        expected_version=generated.version,
    )
    in_review = start_candidate_review(
        validated,
        reviewer_id=REVIEWER_ID,
        expected_version=validated.version,
    )
    approved = approve_candidate(
        in_review,
        reviewer_id=REVIEWER_ID,
        expected_version=in_review.version,
        note=None,
    )

    assert [generated.state, validated.state, in_review.state, approved.state] == [
        CandidateState.GENERATED,
        CandidateState.VALIDATED,
        CandidateState.IN_REVIEW,
        CandidateState.APPROVED,
    ]
    assert [generated.version, validated.version, in_review.version, approved.version] == [
        1,
        2,
        3,
        4,
    ]
    assert approved.validation == validation()
    assert approved.review_history[0].reviewer_id == REVIEWER_ID
    assert approved.decision is not None
    assert approved.decision.reviewer_id == REVIEWER_ID
    assert approved.decision.state is CandidateState.APPROVED


def test_reviewer_edit_is_a_new_immutable_revision_with_original_lineage() -> None:
    original = in_review_candidate("slot-a")
    revised_content = content("reviewed", stem="A reviewer-corrected Grade 5 question")

    edited = edit_candidate(
        original,
        content=revised_content,
        reviewer_id=OTHER_REVIEWER_ID,
        reason="Correct an ambiguous stem without changing its source grounding.",
        expected_version=original.version,
    )

    assert edited is not original
    assert edited.state is CandidateState.IN_REVIEW
    assert edited.version == original.version + 1
    assert edited.lineage is original.lineage
    assert edited.lineage.provenance == original.lineage.provenance
    assert edited.revisions[:-1] == original.revisions
    assert edited.revisions[-1].revision == 2
    assert edited.revisions[-1].content == revised_content
    assert edited.revisions[-1].reviewer_id == OTHER_REVIEWER_ID
    assert edited.content == revised_content
    assert original.content != revised_content
    assert edited.review_history[-1].candidate_version == edited.version


def test_candidate_can_be_rejected_only_from_review_and_requires_reason() -> None:
    in_review = in_review_candidate("slot-a")

    with pytest.raises(CandidateInvariantError, match="rejection reason"):
        reject_candidate(
            in_review,
            reviewer_id=REVIEWER_ID,
            reason=" ",
            expected_version=in_review.version,
        )

    rejected = reject_candidate(
        in_review,
        reviewer_id=REVIEWER_ID,
        reason="The answer is not uniquely supported by the source.",
        expected_version=in_review.version,
    )

    assert rejected.state is CandidateState.REJECTED
    assert rejected.decision is not None
    assert rejected.decision.reason == "The answer is not uniquely supported by the source."


@pytest.mark.parametrize(
    ("operation", "target"),
    [
        (
            lambda candidate: start_candidate_review(
                candidate,
                reviewer_id=REVIEWER_ID,
                expected_version=candidate.version,
            ),
            CandidateState.IN_REVIEW,
        ),
        (
            lambda candidate: approve_candidate(
                candidate,
                reviewer_id=REVIEWER_ID,
                expected_version=candidate.version,
            ),
            CandidateState.APPROVED,
        ),
        (
            lambda candidate: reject_candidate(
                candidate,
                reviewer_id=REVIEWER_ID,
                reason="Not acceptable.",
                expected_version=candidate.version,
            ),
            CandidateState.REJECTED,
        ),
    ],
)
def test_generated_candidate_cannot_bypass_validation_or_review(
    operation: Callable[[QuestionCandidate], QuestionCandidate],
    target: CandidateState,
) -> None:
    candidate = generated_candidate("slot-a")

    with pytest.raises(InvalidCandidateTransitionError) as raised:
        operation(candidate)

    assert raised.value.current is CandidateState.GENERATED
    assert raised.value.target is target


def test_failed_validation_cannot_promote_candidate() -> None:
    candidate = generated_candidate("slot-a")

    with pytest.raises(ValidationNotPassedError):
        record_candidate_validation(
            candidate,
            validation(passed=False),
            expected_version=candidate.version,
        )

    assert candidate.state is CandidateState.GENERATED


def test_terminal_candidate_decisions_cannot_be_rewound_or_edited() -> None:
    approved = approved_candidate("slot-a")
    rejected_source = in_review_candidate("slot-b", candidate_number=2)
    rejected = reject_candidate(
        rejected_source,
        reviewer_id=REVIEWER_ID,
        reason="Rejected after source review.",
        expected_version=rejected_source.version,
    )

    with pytest.raises(InvalidCandidateTransitionError):
        edit_candidate(
            approved,
            content=content("tampered"),
            reviewer_id=REVIEWER_ID,
            reason="Attempt to mutate approved content.",
            expected_version=approved.version,
        )
    with pytest.raises(InvalidCandidateTransitionError):
        reject_candidate(
            approved,
            reviewer_id=REVIEWER_ID,
            reason="Attempt to reverse approval.",
            expected_version=approved.version,
        )
    with pytest.raises(InvalidCandidateTransitionError):
        approve_candidate(
            rejected,
            reviewer_id=REVIEWER_ID,
            expected_version=rejected.version,
        )


def test_candidate_optimistic_version_rejects_stale_reviewer_command() -> None:
    original = in_review_candidate("slot-a")
    edited = edit_candidate(
        original,
        content=content("fresh edit"),
        reviewer_id=REVIEWER_ID,
        reason="Clarify the question.",
        expected_version=original.version,
    )

    with pytest.raises(ConcurrentVersionError) as raised:
        approve_candidate(
            edited,
            reviewer_id=REVIEWER_ID,
            expected_version=original.version,
        )

    assert raised.value.expected == original.version
    assert raised.value.actual == edited.version
    assert edited.state is CandidateState.IN_REVIEW


def test_candidate_constructor_invariants_block_state_forgery() -> None:
    generated = generated_candidate("slot-a")

    with pytest.raises(CandidateInvariantError):
        replace(generated, state=CandidateState.APPROVED)


def test_paper_draft_uses_approved_candidates_in_exact_blueprint_order() -> None:
    slot_a = approved_candidate("slot-a", candidate_number=1)
    slot_b = approved_candidate("slot-b", candidate_number=2)

    draft = assemble_paper_draft(
        paper_id=UUID(int=5_001),
        title="Grade 5 Scholarship Practice Paper",
        blueprint=blueprint(),
        candidates=(slot_b, slot_a),
    )

    assert draft.state is PaperState.DRAFT
    assert draft.version == 1
    assert draft.previous_version is None
    assert tuple(candidate.lineage.blueprint_slot_id for candidate in draft.candidates) == (
        "slot-a",
        "slot-b",
    )
    assert sum(candidate.content.marks for candidate in draft.candidates) == 4


@pytest.mark.parametrize(
    ("candidates", "violation"),
    [
        ((approved_candidate("slot-a"),), AssemblyViolation.SLOT_COVERAGE),
        (
            (
                approved_candidate("slot-a"),
                approved_candidate("slot-extra", candidate_number=2),
            ),
            AssemblyViolation.SLOT_COVERAGE,
        ),
        (
            (
                approved_candidate(
                    "slot-a",
                    candidate_blueprint_version="other-blueprint-version",
                ),
                approved_candidate("slot-b", candidate_number=2),
            ),
            AssemblyViolation.BLUEPRINT_MISMATCH,
        ),
        (
            (
                validated_candidate("slot-a"),
                approved_candidate("slot-b", candidate_number=2),
            ),
            AssemblyViolation.NOT_APPROVED,
        ),
    ],
)
def test_paper_draft_rejects_incomplete_extra_mismatched_or_unapproved_candidates(
    candidates: tuple[QuestionCandidate, ...],
    violation: AssemblyViolation,
) -> None:
    with pytest.raises(PaperAssemblyError) as raised:
        assemble_paper_draft(
            paper_id=UUID(int=5_001),
            title="Grade 5 Scholarship Practice Paper",
            blueprint=blueprint(),
            candidates=candidates,
        )

    assert raised.value.violation is violation


def test_paper_draft_rejects_duplicate_candidate_identity_across_slots() -> None:
    duplicate_id = UUID(int=9_999)
    slot_a = approved_candidate("slot-a", candidate_id=duplicate_id)
    slot_b = approved_candidate(
        "slot-b",
        candidate_number=2,
        candidate_id=duplicate_id,
    )

    with pytest.raises(PaperAssemblyError) as raised:
        assemble_paper_draft(
            paper_id=UUID(int=5_001),
            title="Grade 5 Scholarship Practice Paper",
            blueprint=blueprint(),
            candidates=(slot_a, slot_b),
        )

    assert raised.value.violation is AssemblyViolation.DUPLICATE_CANDIDATE


def test_draft_invariants_recheck_approval_against_constructor_bypass() -> None:
    draft = assembled_draft()
    unapproved = validated_candidate("slot-a", candidate_number=7)

    with pytest.raises(PaperAssemblyError) as raised:
        replace(draft, candidates=(unapproved, draft.candidates[1]))

    assert raised.value.violation is AssemblyViolation.NOT_APPROVED


def test_paper_blueprint_reference_rejects_duplicate_slots() -> None:
    with pytest.raises(CandidateInvariantError, match="slot_ids"):
        blueprint("slot-a", "slot-a")


def test_domain_snapshots_are_deeply_immutable_values() -> None:
    draft = assembled_draft()

    with pytest.raises(FrozenInstanceError):
        draft.title = "Mutated"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        draft.candidates[0].content.stem = "Mutated"  # type: ignore[misc]
    with pytest.raises(TypeError):
        draft.candidates[0].lineage.provenance[0] = provenance()  # type: ignore[index]


def test_published_and_archived_types_cannot_be_directly_constructed() -> None:
    from exam_guru_api.papers import ArchivedPaperSnapshot, PublishedPaperSnapshot

    with pytest.raises(ServiceBoundaryRequiredError):
        PublishedPaperSnapshot(
            paper_id=UUID(int=1),
            version=1,
            title="Forged publication",
            blueprint=blueprint("slot-a"),
            questions=(),
            published_by=REVIEWER_ID,
        )

    with pytest.raises(ServiceBoundaryRequiredError):
        ArchivedPaperSnapshot(
            publication=cast("PublishedPaperSnapshot", object()),
            archived_by=REVIEWER_ID,
            reason="Forged archive",
        )


def test_archived_paper_is_terminal_at_the_domain_boundary() -> None:
    error = InvalidPaperTransitionError(PaperState.ARCHIVED, PaperState.DRAFT)

    assert error.current is PaperState.ARCHIVED
    assert error.target is PaperState.DRAFT

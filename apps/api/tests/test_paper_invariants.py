from collections.abc import Callable
from dataclasses import replace
from typing import cast
from uuid import UUID

import pytest

import exam_guru_api.papers.domain as paper_domain
from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.papers import (
    ArchivedPaperSnapshot,
    ArchivePaperCommand,
    AssemblyViolation,
    CandidateInvariantError,
    CandidateRevision,
    CandidateState,
    GenerationLineage,
    PaperAssemblyError,
    PaperBlueprintReference,
    PaperDraft,
    PaperWorkflowService,
    PublishedPaperSnapshot,
    PublishedQuestion,
    PublishPaperCommand,
    QuestionCandidate,
    QuestionContent,
    QuestionOption,
    ReviewAction,
    ReviewDecision,
    ReviewRecord,
    RevisePaperCommand,
    SourceProvenance,
    ValidationEvidence,
    assemble_paper_draft,
    edit_candidate,
    record_candidate_validation,
)
from tests.test_paper_domain import (
    REVIEWER_ID,
    approved_candidate,
    assembled_draft,
    blueprint,
    content,
    generated_candidate,
    in_review_candidate,
    lineage,
    provenance,
    validated_candidate,
    validation,
)

ADMIN = Principal(subject_id=UUID(int=8_000), roles=frozenset({AdminRole.ADMIN}))


def test_assembly_rejects_malformed_candidate_values_at_the_domain_boundary() -> None:
    with pytest.raises(PaperAssemblyError) as raised:
        assemble_paper_draft(
            paper_id=UUID(int=8_001),
            title="Malformed candidate paper",
            blueprint=blueprint("slot-a"),
            candidates=cast(tuple[QuestionCandidate, ...], ("not-a-candidate",)),
        )

    assert raised.value.violation is AssemblyViolation.SLOT_COVERAGE

    with pytest.raises(CandidateInvariantError, match="PaperBlueprintReference"):
        assemble_paper_draft(
            paper_id=UUID(int=8_001),
            title="Malformed blueprint paper",
            blueprint=cast(PaperBlueprintReference, object()),
            candidates=(),
        )


@pytest.mark.parametrize(
    "build",
    [
        lambda: QuestionOption(option_id=cast(str, 7), text="value"),
        lambda: QuestionOption(option_id=" ", text="value"),
        lambda: QuestionOption(option_id=" A", text="value"),
        lambda: SourceProvenance("source", "v1", cast(int, "1"), "chunk"),
        lambda: SourceProvenance("source", "v1", cast(int, True), "chunk"),
        lambda: SourceProvenance("source", "v1", 0, "chunk"),
        lambda: ValidationEvidence(cast(UUID, "run"), "v1", ("finding",), True),
    ],
)
def test_scalar_contracts_reject_malformed_values(build: Callable[[], object]) -> None:
    with pytest.raises(CandidateInvariantError):
        build()


@pytest.mark.parametrize(
    "build",
    [
        lambda: replace(lineage("slot-a"), provenance=cast(tuple[SourceProvenance, ...], [])),
        lambda: replace(lineage("slot-a"), provenance=()),
        lambda: replace(
            lineage("slot-a"),
            provenance=cast(tuple[SourceProvenance, ...], ("bad",)),
        ),
        lambda: replace(
            lineage("slot-a"),
            provenance=(provenance(), provenance()),
        ),
    ],
)
def test_generation_lineage_requires_unique_immutable_provenance(
    build: Callable[[], object],
) -> None:
    with pytest.raises(CandidateInvariantError):
        build()


@pytest.mark.parametrize(
    "build",
    [
        lambda: replace(content("base"), options=cast(tuple[QuestionOption, ...], [])),
        lambda: replace(
            content("base"),
            options=cast(tuple[QuestionOption, ...], ("bad",)),
        ),
        lambda: replace(
            content("base"),
            options=(
                QuestionOption("A", "first"),
                QuestionOption("A", "second"),
            ),
        ),
        lambda: replace(content("base"), marking_guide=cast(tuple[str, ...], [])),
        lambda: replace(content("base"), marking_guide=()),
        lambda: replace(content("base"), marking_guide=(" ",)),
        lambda: replace(content("base"), answer="missing-option"),
        lambda: replace(content("base"), options=(), answer="A"),
    ],
)
def test_question_content_rejects_mutable_or_ambiguous_structure(
    build: Callable[[], object],
) -> None:
    with pytest.raises(CandidateInvariantError):
        build()


@pytest.mark.parametrize(
    "build",
    [
        lambda: replace(validation(), finding_refs=cast(tuple[str, ...], [])),
        lambda: replace(validation(), finding_refs=()),
        lambda: replace(validation(), finding_refs=("same", "same")),
        lambda: replace(validation(), passed=cast(bool, 1)),
        lambda: replace(validation(), validated_revision=0),
        lambda: replace(validation(), validated_revision=2),
    ],
)
def test_validation_evidence_is_typed_nonempty_and_unique(
    build: Callable[[], object],
) -> None:
    with pytest.raises(CandidateInvariantError):
        build()


@pytest.mark.parametrize(
    "build",
    [
        lambda: CandidateRevision(1, cast(QuestionContent, "bad")),
        lambda: CandidateRevision(1, content("base"), reason="forged edit"),
        lambda: CandidateRevision(1, content("base"), REVIEWER_ID, None),
        lambda: ReviewRecord(cast(ReviewAction, "bad"), REVIEWER_ID, 3),
        lambda: ReviewRecord(ReviewAction.EDITED, REVIEWER_ID, 3),
        lambda: ReviewDecision(CandidateState.IN_REVIEW, REVIEWER_ID, 3),
        lambda: ReviewDecision(CandidateState.REJECTED, REVIEWER_ID, 4),
    ],
)
def test_review_history_values_reject_forged_or_incomplete_records(
    build: Callable[[], object],
) -> None:
    with pytest.raises(CandidateInvariantError):
        build()


def test_candidate_history_has_a_small_persistable_revision_bound() -> None:
    assert paper_domain.MAX_CANDIDATE_REVISIONS == 32
    assert paper_domain.MAX_CANDIDATE_VERSION == 35
    with pytest.raises(CandidateInvariantError, match="revision"):
        CandidateRevision(33, content("overflow"), REVIEWER_ID, "Overflow edit.")
    with pytest.raises(CandidateInvariantError, match="version"):
        ReviewRecord(ReviewAction.EDITED, REVIEWER_ID, 36, "Overflow edit.")
    with pytest.raises(CandidateInvariantError, match="version"):
        ReviewDecision(CandidateState.APPROVED, REVIEWER_ID, 36)
    candidate = validated_candidate("slot-a")
    with pytest.raises(CandidateInvariantError, match="version"):
        replace(candidate, version=36)
    with pytest.raises(CandidateInvariantError, match="32 revisions"):
        replace(candidate, revisions=(candidate.revisions[0],) * 33)


def _second_revision() -> CandidateRevision:
    return CandidateRevision(
        revision=2,
        content=content("second"),
        reviewer_id=REVIEWER_ID,
        reason="Reviewer edit.",
    )


def _started_record(*, version: int = 3) -> ReviewRecord:
    return ReviewRecord(ReviewAction.STARTED, REVIEWER_ID, version)


def _approved_decision(*, version: int = 4) -> ReviewDecision:
    return ReviewDecision(CandidateState.APPROVED, REVIEWER_ID, version)


@pytest.mark.parametrize(
    "build",
    [
        lambda: replace(generated_candidate("slot-a"), state=cast(CandidateState, "bad")),
        lambda: replace(generated_candidate("slot-a"), lineage=cast(GenerationLineage, object())),
        lambda: replace(
            generated_candidate("slot-a"),
            revisions=cast(tuple[CandidateRevision, ...], []),
        ),
        lambda: replace(generated_candidate("slot-a"), revisions=()),
        lambda: replace(
            generated_candidate("slot-a"),
            revisions=cast(tuple[CandidateRevision, ...], ("bad",)),
        ),
        lambda: replace(
            generated_candidate("slot-a"),
            revisions=(CandidateRevision(2, content("base")),),
        ),
        lambda: replace(
            generated_candidate("slot-a"),
            revisions=(
                CandidateRevision(
                    1,
                    content("base"),
                    reviewer_id=REVIEWER_ID,
                    reason="Forged initial revision.",
                ),
            ),
        ),
        lambda: replace(
            generated_candidate("slot-a"),
            review_history=cast(tuple[ReviewRecord, ...], []),
        ),
        lambda: replace(
            generated_candidate("slot-a"),
            review_history=cast(tuple[ReviewRecord, ...], ("bad",)),
        ),
    ],
)
def test_candidate_rejects_malformed_core_history(build: Callable[[], object]) -> None:
    with pytest.raises(CandidateInvariantError):
        build()


@pytest.mark.parametrize(
    "build",
    [
        lambda: replace(generated_candidate("slot-a"), version=2),
        lambda: replace(
            generated_candidate("slot-a"),
            revisions=(
                generated_candidate("slot-a").revisions[0],
                _second_revision(),
            ),
        ),
        lambda: replace(generated_candidate("slot-a"), validation=validation()),
        lambda: replace(generated_candidate("slot-a"), review_history=(_started_record(),)),
        lambda: replace(generated_candidate("slot-a"), decision=_approved_decision(version=1)),
    ],
)
def test_generated_state_cannot_forge_later_lifecycle_evidence(
    build: Callable[[], object],
) -> None:
    with pytest.raises(CandidateInvariantError, match="generated candidate history"):
        build()


@pytest.mark.parametrize(
    "build",
    [
        lambda: replace(
            generated_candidate("slot-a"),
            state=CandidateState.VALIDATED,
            version=2,
        ),
        lambda: replace(
            generated_candidate("slot-a"),
            state=CandidateState.VALIDATED,
            version=2,
            validation=validation(passed=False),
        ),
    ],
)
def test_post_generation_state_requires_successful_validation(
    build: Callable[[], object],
) -> None:
    with pytest.raises(CandidateInvariantError, match="passed validation"):
        build()


@pytest.mark.parametrize(
    "build",
    [
        lambda: replace(validated_candidate("slot-a"), version=3),
        lambda: replace(
            validated_candidate("slot-a"),
            review_history=(_started_record(),),
        ),
        lambda: replace(
            validated_candidate("slot-a"),
            decision=_approved_decision(version=2),
        ),
    ],
)
def test_validated_state_cannot_contain_review_history_or_decision(
    build: Callable[[], object],
) -> None:
    with pytest.raises(CandidateInvariantError, match="validated candidate history"):
        build()


@pytest.mark.parametrize(
    "build",
    [
        lambda: replace(in_review_candidate("slot-a"), review_history=()),
        lambda: replace(
            in_review_candidate("slot-a"),
            review_history=(
                ReviewRecord(
                    ReviewAction.EDITED,
                    REVIEWER_ID,
                    3,
                    reason="No start record.",
                ),
            ),
        ),
        lambda: replace(
            in_review_candidate("slot-a"),
            review_history=(_started_record(version=4),),
        ),
        lambda: replace(in_review_candidate("slot-a"), version=4),
        lambda: replace(
            in_review_candidate("slot-a"),
            revisions=(in_review_candidate("slot-a").revisions[0], _second_revision()),
        ),
        lambda: replace(
            in_review_candidate("slot-a"),
            decision=_approved_decision(version=3),
        ),
        lambda: replace(
            in_review_candidate("slot-a"),
            version=4,
            review_history=(
                _started_record(),
                ReviewRecord(ReviewAction.APPROVED, REVIEWER_ID, 4),
            ),
        ),
    ],
)
def test_in_review_state_requires_contiguous_nonterminal_history(
    build: Callable[[], object],
) -> None:
    with pytest.raises(CandidateInvariantError):
        build()


@pytest.mark.parametrize(
    "build",
    [
        lambda: replace(approved_candidate("slot-a"), decision=None),
        lambda: replace(
            approved_candidate("slot-a"),
            decision=ReviewDecision(
                CandidateState.REJECTED,
                REVIEWER_ID,
                4,
                reason="Mismatched decision.",
            ),
        ),
        lambda: replace(
            approved_candidate("slot-a"),
            decision=_approved_decision(version=5),
        ),
        lambda: replace(
            approved_candidate("slot-a"),
            review_history=(
                _started_record(),
                ReviewRecord(
                    ReviewAction.REJECTED,
                    REVIEWER_ID,
                    4,
                    reason="Mismatched final action.",
                ),
            ),
        ),
    ],
)
def test_terminal_state_requires_matching_final_decision(
    build: Callable[[], object],
) -> None:
    with pytest.raises(CandidateInvariantError, match="terminal candidate"):
        build()


def test_expected_version_rejects_boolean_and_numeric_coercion() -> None:
    draft = assembled_draft()
    service = PaperWorkflowService()

    for malformed_version in (cast(int, True), cast(int, 1.0)):
        with pytest.raises(CandidateInvariantError, match="expected_version"):
            service.publish(
                ADMIN,
                PublishPaperCommand(
                    draft=draft,
                    expected_version=malformed_version,
                ),
            )


def test_validation_and_edit_commands_reject_wrong_boundary_values() -> None:
    generated = generated_candidate("slot-a")
    with pytest.raises(CandidateInvariantError, match="ValidationEvidence"):
        record_candidate_validation(
            generated,
            cast(ValidationEvidence, object()),
            expected_version=generated.version,
        )

    reviewing = in_review_candidate("slot-a")
    with pytest.raises(CandidateInvariantError, match="QuestionContent"):
        edit_candidate(
            reviewing,
            content=cast(QuestionContent, object()),
            reviewer_id=REVIEWER_ID,
            reason="Malformed edit.",
            expected_version=reviewing.version,
        )


@pytest.mark.parametrize(
    "slot_ids",
    [cast(tuple[str, ...], []), ()],
)
def test_blueprint_reference_requires_an_immutable_nonempty_slot_set(
    slot_ids: tuple[str, ...],
) -> None:
    with pytest.raises(CandidateInvariantError, match="slot_ids"):
        PaperBlueprintReference("blueprint", "v1", slot_ids)


def test_duplicate_slot_assignment_is_not_exact_blueprint_coverage() -> None:
    first = approved_candidate("slot-a", candidate_number=1)
    second = approved_candidate("slot-a", candidate_number=2)

    with pytest.raises(PaperAssemblyError) as raised:
        assemble_paper_draft(
            paper_id=UUID(int=8_002),
            title="Duplicate slot paper",
            blueprint=blueprint(),
            candidates=(first, second),
        )

    assert raised.value.violation is AssemblyViolation.SLOT_COVERAGE


@pytest.mark.parametrize(
    "build",
    [
        lambda: PaperDraft(
            UUID(int=1),
            1,
            "Paper",
            cast(PaperBlueprintReference, object()),
            (),
        ),
        lambda: replace(assembled_draft(), candidates=cast(tuple[QuestionCandidate, ...], [])),
        lambda: replace(
            assembled_draft(),
            candidates=cast(tuple[QuestionCandidate, ...], ("bad",)),
        ),
        lambda: replace(assembled_draft(), previous_version=0),
        lambda: replace(assembled_draft(), version=3, previous_version=1),
        lambda: replace(
            assembled_draft(),
            version=2,
            previous_version=1,
            supersedes_content_hash="bad",
        ),
        lambda: replace(
            assembled_draft(),
            version=2,
            previous_version=1,
            supersedes_content_hash="G" * 64,
        ),
        lambda: PaperDraft(
            paper_id=assembled_draft().paper_id,
            version=1,
            title=assembled_draft().title,
            blueprint=assembled_draft().blueprint,
            candidates=tuple(reversed(assembled_draft().candidates)),
        ),
    ],
)
def test_draft_constructor_rejects_forged_versions_and_structure(
    build: Callable[[], object],
) -> None:
    with pytest.raises((CandidateInvariantError, PaperAssemblyError)):
        build()


def test_publishability_rechecks_deterministic_candidate_order() -> None:
    draft = assembled_draft()
    object.__setattr__(draft, "candidates", tuple(reversed(draft.candidates)))

    with pytest.raises(PaperAssemblyError) as raised:
        draft.assert_publishable()

    assert raised.value.violation is AssemblyViolation.SLOT_COVERAGE


def _published() -> PublishedPaperSnapshot:
    draft = assembled_draft()
    return PaperWorkflowService().publish(
        ADMIN,
        PublishPaperCommand(draft=draft, expected_version=draft.version),
    )


@pytest.mark.parametrize(
    "build",
    [
        lambda: replace(_published().questions[0], slot_id="different-slot"),
        lambda: replace(_published().questions[0], content=content("different")),
        lambda: replace(
            _published().questions[0],
            validation=validation(passed=False),
        ),
        lambda: replace(
            _published().questions[0],
            decision=ReviewDecision(
                CandidateState.REJECTED,
                REVIEWER_ID,
                4,
                reason="Not approved.",
            ),
        ),
        lambda: replace(
            _published().questions[0],
            decision=_approved_decision(version=5),
        ),
    ],
)
def test_published_question_rejects_broken_approval_or_lineage(
    build: Callable[[], object],
) -> None:
    with pytest.raises(CandidateInvariantError):
        build()


def _internal_snapshot(
    base: PublishedPaperSnapshot,
    *,
    blueprint_reference: PaperBlueprintReference | None = None,
    questions: tuple[PublishedQuestion, ...] | None = None,
    version: int = 1,
    previous_version: int | None = None,
    supersedes_content_hash: str | None = None,
) -> PublishedPaperSnapshot:
    return PublishedPaperSnapshot(
        paper_id=base.paper_id,
        version=version,
        title=base.title,
        blueprint=blueprint_reference or base.blueprint,
        questions=base.questions if questions is None else questions,
        published_by=base.published_by,
        previous_version=previous_version,
        supersedes_content_hash=supersedes_content_hash,
        _service_capability=paper_domain._PUBLISH_CAPABILITY,
    )


def test_internal_publication_factory_rechecks_snapshot_structure() -> None:
    base = _published()
    duplicate_second = replace(
        base.questions[1],
        candidate_id=base.questions[0].candidate_id,
    )
    foreign_blueprint_questions = (
        paper_domain._published_question(
            approved_candidate(
                "slot-a",
                candidate_number=21,
                candidate_blueprint_id="foreign-blueprint",
            )
        ),
        paper_domain._published_question(
            approved_candidate(
                "slot-b",
                candidate_number=22,
                candidate_blueprint_id="foreign-blueprint",
            )
        ),
    )
    malformed_cases: tuple[Callable[[], object], ...] = (
        lambda: _internal_snapshot(
            base,
            blueprint_reference=cast(PaperBlueprintReference, object()),
        ),
        lambda: _internal_snapshot(
            base,
            questions=cast(tuple[PublishedQuestion, ...], []),
        ),
        lambda: _internal_snapshot(
            base,
            questions=cast(tuple[PublishedQuestion, ...], ("bad",)),
        ),
        lambda: _internal_snapshot(base, questions=(base.questions[0],)),
        lambda: _internal_snapshot(base, questions=foreign_blueprint_questions),
        lambda: _internal_snapshot(
            base,
            questions=(base.questions[0], duplicate_second),
        ),
        lambda: _internal_snapshot(base, previous_version=0),
        lambda: _internal_snapshot(base, version=2, previous_version=0),
    )

    for build in malformed_cases:
        with pytest.raises((CandidateInvariantError, PaperAssemblyError)):
            build()


def test_internal_archive_factory_rejects_nonpublication_value() -> None:
    with pytest.raises(CandidateInvariantError, match="PublishedPaperSnapshot"):
        ArchivedPaperSnapshot(
            publication=cast(PublishedPaperSnapshot, object()),
            archived_by=ADMIN.subject_id,
            reason="Malformed archive.",
            _service_capability=paper_domain._ARCHIVE_CAPABILITY,
        )


def test_internal_question_snapshot_requires_approved_candidate_and_decision() -> None:
    with pytest.raises(PaperAssemblyError):
        paper_domain._published_question(validated_candidate("slot-a"))

    approved = approved_candidate("slot-a")
    object.__setattr__(approved, "decision", None)
    with pytest.raises(CandidateInvariantError, match="review decision"):
        paper_domain._published_question(approved)


def test_service_domain_guards_reject_wrong_paper_aggregate_types() -> None:
    service = PaperWorkflowService()
    publication = _published()
    draft = assembled_draft()
    state_tampered_draft = assembled_draft()
    object.__setattr__(state_tampered_draft, "state", paper_domain.PaperState.ARCHIVED)
    state_tampered_revision = _published()
    object.__setattr__(state_tampered_revision, "state", paper_domain.PaperState.ARCHIVED)
    state_tampered_archive = _published()
    object.__setattr__(state_tampered_archive, "state", paper_domain.PaperState.DRAFT)

    with pytest.raises(paper_domain.InvalidPaperTransitionError):
        service.publish(
            ADMIN,
            PublishPaperCommand(
                draft=state_tampered_draft,
                expected_version=state_tampered_draft.version,
            ),
        )
    with pytest.raises(paper_domain.InvalidPaperTransitionError):
        service.revise(
            ADMIN,
            RevisePaperCommand(
                source=state_tampered_revision,
                candidates=draft.candidates,
                expected_version=state_tampered_revision.version,
            ),
        )
    with pytest.raises(paper_domain.InvalidPaperTransitionError):
        service.archive(
            ADMIN,
            ArchivePaperCommand(
                publication=state_tampered_archive,
                reason="State-tampered publication.",
                expected_version=state_tampered_archive.version,
            ),
        )
    with pytest.raises(paper_domain.InvalidPaperTransitionError):
        service.publish(
            ADMIN,
            PublishPaperCommand(
                draft=cast(PaperDraft, publication),
                expected_version=publication.version,
            ),
        )
    with pytest.raises(paper_domain.InvalidPaperTransitionError):
        service.revise(
            ADMIN,
            RevisePaperCommand(
                source=cast(PaperDraft, object()),
                candidates=draft.candidates,
                expected_version=draft.version,
            ),
        )
    with pytest.raises(paper_domain.InvalidPaperTransitionError):
        service.archive(
            ADMIN,
            ArchivePaperCommand(
                publication=cast(PublishedPaperSnapshot, draft),
                reason="Wrong aggregate.",
                expected_version=draft.version,
            ),
        )

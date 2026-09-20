"""Source V2 human-gate rules and the hard invariant.

These are pure domain tests: no database, no provider, no HTTP. If one of them
fails, source fidelity is broken regardless of what the rest of the stack does.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from exam_guru_api.source_v2.domain import (
    DocumentResolution,
    MachineCandidate,
    NotVerifiedError,
    PageReview,
    RegionState,
    RegionType,
    ReviewAction,
    SourceV2Error,
    StaleReviewError,
    require_verified_source,
)

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
SHA = "a" * 64
OTHER_SHA = "b" * 64


def candidate(region_id="p156-r002", text="පාසල් වත්තේ", abstained=False, revision=1):
    return MachineCandidate(
        candidate_id=uuid4(),
        region_id=region_id,
        region_type=RegionType.TEXT,
        text=text,
        abstained=abstained,
        revision=revision,
    )


def page(*candidates) -> PageReview:
    review = PageReview(document_id="mawbasa-teacher-guide", page_number=156, image_sha256=SHA)
    for item in candidates:
        review.candidates[item.region_id] = item
    return review


def reviewer() -> object:
    return uuid4()


# --- confirm ------------------------------------------------------------------


def test_confirm_creates_verified_content_bound_to_the_compared_page() -> None:
    first = candidate()
    review = page(first)
    verified = review.confirm(
        region_id=first.region_id,
        candidate_id=first.candidate_id,
        revision=first.revision,
        reviewer_id=reviewer(),
        at=NOW,
        compared_with_image_sha256=SHA,
    )
    assert verified.text == first.text
    assert verified.image_sha256 == SHA
    assert verified.candidate_id == first.candidate_id
    assert review.state_of(first.region_id) is RegionState.VERIFIED


def test_confirmation_must_cite_the_page_that_was_actually_compared() -> None:
    first = candidate()
    review = page(first)
    with pytest.raises(StaleReviewError):
        review.confirm(
            region_id=first.region_id,
            candidate_id=first.candidate_id,
            revision=first.revision,
            reviewer_id=reviewer(),
            at=NOW,
            compared_with_image_sha256=OTHER_SHA,
        )
    assert review.state_of(first.region_id) is RegionState.UNVERIFIED


def test_a_stale_candidate_cannot_be_confirmed() -> None:
    first = candidate()
    review = page(first)
    with pytest.raises(StaleReviewError):
        review.confirm(
            region_id=first.region_id,
            candidate_id=uuid4(),
            revision=first.revision,
            reviewer_id=reviewer(),
            at=NOW,
            compared_with_image_sha256=SHA,
        )


def test_an_abstention_cannot_be_rubber_stamped() -> None:
    first = candidate(text="", abstained=True)
    review = page(first)
    with pytest.raises(SourceV2Error):
        review.confirm(
            region_id=first.region_id,
            candidate_id=first.candidate_id,
            revision=first.revision,
            reviewer_id=reviewer(),
            at=NOW,
            compared_with_image_sha256=SHA,
        )


# --- correct ------------------------------------------------------------------


def test_correction_creates_an_unverified_child_and_does_not_verify() -> None:
    first = candidate()
    review = page(first)
    child = review.correct(
        region_id=first.region_id,
        candidate_id=first.candidate_id,
        revision=first.revision,
        reviewer_id=reviewer(),
        at=NOW,
        corrected_text="නිවැරදි කළ පෙළ",
        child_candidate_id=uuid4(),
    )
    assert child.parent_candidate_id == first.candidate_id
    assert child.revision == first.revision + 1
    assert review.state_of(first.region_id) is RegionState.UNVERIFIED
    assert first.region_id not in review.verified


def test_correcting_a_verified_region_withdraws_the_verification() -> None:
    first = candidate()
    review = page(first)
    review.confirm(
        region_id=first.region_id,
        candidate_id=first.candidate_id,
        revision=first.revision,
        reviewer_id=reviewer(),
        at=NOW,
        compared_with_image_sha256=SHA,
    )
    review.correct(
        region_id=first.region_id,
        candidate_id=first.candidate_id,
        revision=first.revision,
        reviewer_id=reviewer(),
        at=NOW,
        corrected_text="සංශෝධිත",
        child_candidate_id=uuid4(),
    )
    assert review.state_of(first.region_id) is RegionState.UNVERIFIED
    assert first.region_id not in review.verified


def test_the_old_candidate_cannot_be_confirmed_after_a_correction() -> None:
    first = candidate()
    review = page(first)
    review.correct(
        region_id=first.region_id,
        candidate_id=first.candidate_id,
        revision=first.revision,
        reviewer_id=reviewer(),
        at=NOW,
        corrected_text="සංශෝධිත",
        child_candidate_id=uuid4(),
    )
    with pytest.raises(StaleReviewError):
        review.confirm(
            region_id=first.region_id,
            candidate_id=first.candidate_id,
            revision=first.revision,
            reviewer_id=reviewer(),
            at=NOW,
            compared_with_image_sha256=SHA,
        )


# --- exclude ------------------------------------------------------------------


def test_exclusion_needs_a_reason_and_removes_the_region_from_use() -> None:
    first = candidate()
    review = page(first)
    with pytest.raises(SourceV2Error):
        review.exclude(
            region_id=first.region_id,
            candidate_id=first.candidate_id,
            revision=first.revision,
            reviewer_id=reviewer(),
            at=NOW,
            note="   ",
        )
    review.exclude(
        region_id=first.region_id,
        candidate_id=first.candidate_id,
        revision=first.revision,
        reviewer_id=reviewer(),
        at=NOW,
        note="printer artefact, not source content",
    )
    assert review.state_of(first.region_id) is RegionState.EXCLUDED


# --- audit --------------------------------------------------------------------


def test_every_decision_is_appended_and_nothing_is_rewritten() -> None:
    first = candidate()
    review = page(first)
    review.correct(
        region_id=first.region_id,
        candidate_id=first.candidate_id,
        revision=first.revision,
        reviewer_id=reviewer(),
        at=NOW,
        corrected_text="සංශෝධිත",
        child_candidate_id=uuid4(),
    )
    child = review.candidates[first.region_id]
    review.confirm(
        region_id=child.region_id,
        candidate_id=child.candidate_id,
        revision=child.revision,
        reviewer_id=reviewer(),
        at=NOW,
        compared_with_image_sha256=SHA,
    )
    assert [event.action for event in review.events] == [
        ReviewAction.CORRECT,
        ReviewAction.CONFIRM,
    ]
    assert all(event.compared_with_image_sha256 == SHA for event in review.events)


# --- the hard invariant -------------------------------------------------------


def verified_page(number: int) -> PageReview:
    first = candidate(region_id=f"p{number:03d}-r001")
    review = PageReview(document_id="mawbasa-teacher-guide", page_number=number, image_sha256=SHA)
    review.candidates[first.region_id] = first
    review.confirm(
        region_id=first.region_id,
        candidate_id=first.candidate_id,
        revision=first.revision,
        reviewer_id=reviewer(),
        at=NOW,
        compared_with_image_sha256=SHA,
    )
    return review


def unresolved_page(number: int) -> PageReview:
    first = candidate(region_id=f"p{number:03d}-r001")
    review = PageReview(document_id="mawbasa-teacher-guide", page_number=number, image_sha256=SHA)
    review.candidates[first.region_id] = first
    return review


@pytest.mark.parametrize(
    "purpose", ["educational analysis", "knowledge", "embeddings", "retrieval", "generation"]
)
def test_downstream_use_is_refused_without_verified_source(purpose: str) -> None:
    resolution = DocumentResolution(
        document_id="mawbasa-teacher-guide", pages={156: unresolved_page(156)}
    )
    with pytest.raises(NotVerifiedError):
        require_verified_source(resolution, purpose=purpose)


def test_a_verified_page_cannot_carry_an_unresolved_sibling() -> None:
    resolution = DocumentResolution(
        document_id="mawbasa-teacher-guide",
        pages={156: verified_page(156), 186: unresolved_page(186)},
    )
    with pytest.raises(NotVerifiedError) as error:
        require_verified_source(resolution, purpose="knowledge")
    assert "186" in str(error.value)


def test_excluding_everything_is_not_verification() -> None:
    review = unresolved_page(156)
    only = next(iter(review.candidates.values()))
    review.exclude(
        region_id=only.region_id,
        candidate_id=only.candidate_id,
        revision=only.revision,
        reviewer_id=reviewer(),
        at=NOW,
        note="blank page",
    )
    resolution = DocumentResolution(document_id="mawbasa-teacher-guide", pages={156: review})
    with pytest.raises(NotVerifiedError):
        require_verified_source(resolution, purpose="retrieval")


def test_an_empty_document_is_refused() -> None:
    with pytest.raises(NotVerifiedError):
        require_verified_source(
            DocumentResolution(document_id="mawbasa-teacher-guide", pages={}),
            purpose="generation",
        )


def test_a_fully_resolved_document_is_allowed() -> None:
    excluded = unresolved_page(157)
    only = next(iter(excluded.candidates.values()))
    excluded.exclude(
        region_id=only.region_id,
        candidate_id=only.candidate_id,
        revision=only.revision,
        reviewer_id=reviewer(),
        at=NOW,
        note="decorative divider page",
    )
    resolution = DocumentResolution(
        document_id="mawbasa-teacher-guide",
        pages={156: verified_page(156), 157: excluded},
    )
    require_verified_source(resolution, purpose="knowledge")


# --- one machine reader -------------------------------------------------------


def test_a_machine_candidate_carries_no_reader_ensemble_fields() -> None:
    """There is one machine reading, so there is nothing to choose between."""

    fields = {field.name for field in dataclasses.fields(MachineCandidate)}
    assert fields == {
        "candidate_id",
        "region_id",
        "region_type",
        "text",
        "abstained",
        "revision",
        "parent_candidate_id",
    }


def test_a_fresh_machine_candidate_is_not_verified_source_content() -> None:
    """A proposal is not trust, however confident the reading looks."""

    review = page(candidate())
    assert review.state_of("p156-r002") is RegionState.UNVERIFIED
    assert review.verified == {}
    assert review.events == []
    assert not review.carries_source
    with pytest.raises(NotVerifiedError):
        require_verified_source(
            DocumentResolution(document_id="mawbasa-teacher-guide", pages={156: review}),
            purpose="knowledge",
        )

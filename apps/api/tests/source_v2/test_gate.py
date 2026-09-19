"""The downstream gate: what may cross the source-fidelity line, and what may not."""

from __future__ import annotations

import pytest

from exam_guru_api.source_v2.domain import NotVerifiedError
from exam_guru_api.source_v2.gate import DownstreamPurpose, LegacyPolicy, evaluate

DOCUMENT = "b0ebb4ef-062c-47fe-b23d-6dc269610716"


def counts(unverified=0, verified=0, excluded=0) -> dict[str, int]:
    return {"unverified": unverified, "verified": verified, "excluded": excluded}


@pytest.mark.parametrize("purpose", list(DownstreamPurpose))
def test_every_downstream_purpose_is_refused_while_a_page_is_unresolved(purpose) -> None:
    with pytest.raises(NotVerifiedError) as error:
        evaluate(
            {156: counts(verified=6), 186: counts(unverified=4)},
            document_id=DOCUMENT,
            purpose=str(purpose),
        )
    assert "186" in str(error.value)
    assert str(purpose) in str(error.value)


def test_a_document_with_no_source_v2_pages_is_refused() -> None:
    with pytest.raises(NotVerifiedError):
        evaluate({}, document_id=DOCUMENT, purpose="knowledge")


def test_excluding_every_region_is_not_verification() -> None:
    with pytest.raises(NotVerifiedError) as error:
        evaluate(
            {156: counts(excluded=6), 186: counts(excluded=4)},
            document_id=DOCUMENT,
            purpose="embeddings",
        )
    assert "no verified page" in str(error.value)


def test_a_fully_resolved_document_passes() -> None:
    evaluate(
        {156: counts(verified=5, excluded=1), 186: counts(excluded=4)},
        document_id=DOCUMENT,
        purpose="generation",
    )


def test_the_legacy_policy_is_an_explicit_named_choice_not_a_default_hole() -> None:
    # Both members exist and are distinct, and the strict one is the default of
    # assert_document_usable. Phase 7 deletes PRE_CUTOVER; if this test starts
    # failing because the member is gone, the cutover is done.
    assert LegacyPolicy.PRE_CUTOVER is not LegacyPolicy.CUTOVER
    assert {member.value for member in LegacyPolicy} == {"pre-cutover", "cutover"}

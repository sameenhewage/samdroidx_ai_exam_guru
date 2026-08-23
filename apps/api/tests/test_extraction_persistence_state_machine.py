import pytest

from exam_guru_api.documents.domain import ExtractionStatus
from exam_guru_api.documents.extraction import (
    InvalidExtractionTransitionError,
    transition_extraction_status,
)

_ALLOWED_TRANSITIONS = {
    (ExtractionStatus.UPLOADED, ExtractionStatus.EXTRACTION_PENDING),
    (ExtractionStatus.EXTRACTION_PENDING, ExtractionStatus.EXTRACTED),
    (ExtractionStatus.EXTRACTION_PENDING, ExtractionStatus.FAILED),
    (ExtractionStatus.FAILED, ExtractionStatus.EXTRACTION_PENDING),
    (ExtractionStatus.EXTRACTED, ExtractionStatus.IN_REVIEW),
    (ExtractionStatus.IN_REVIEW, ExtractionStatus.TRUSTED),
}


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (current, target)
        for current in ExtractionStatus
        for target in ExtractionStatus
        if current is target or (current, target) in _ALLOWED_TRANSITIONS
    ],
)
def test_extraction_state_machine_accepts_only_safe_forward_and_recovery_transitions(
    current: ExtractionStatus,
    target: ExtractionStatus,
) -> None:
    assert transition_extraction_status(current, target) is target


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (current, target)
        for current in ExtractionStatus
        for target in ExtractionStatus
        if current is not target and (current, target) not in _ALLOWED_TRANSITIONS
    ],
)
def test_extraction_state_machine_rejects_skips_rewinds_and_trust_bypass(
    current: ExtractionStatus,
    target: ExtractionStatus,
) -> None:
    with pytest.raises(InvalidExtractionTransitionError) as raised:
        transition_extraction_status(current, target)

    assert raised.value.current is current
    assert raised.value.target is target

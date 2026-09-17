import json
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from exam_guru_api.auth.domain import AuthorizationError
from exam_guru_api.documents.source_reading import SourceReadCandidate
from exam_guru_api.documents.source_verification import VerifiedSourceContent, verify_source_reading
from exam_guru_api.documents.understanding_contracts import EducationalUnderstanding
from exam_guru_api.documents.understanding_verification import (
    ObservationCandidate,
    verify_understanding,
)
from tests.test_document_understanding_verification import ADMIN, REVIEWER, candidate


def source_candidate() -> ObservationCandidate:
    previous = candidate()
    content = previous.content.model_copy(update={"education": EducationalUnderstanding(claims=())})
    return ObservationCandidate.model_validate(previous.model_copy(update={"content": content}))


def verified(value: ObservationCandidate | None = None, **changes: Any) -> VerifiedSourceContent:
    value = value or source_candidate()
    arguments: dict[str, Any] = {
        "principal": ADMIN,
        "identifier": UUID(int=99901),
        "revision": 1,
        "page_version": 2,
        "compared_with_original": True,
        "reviewed_region_keys": tuple(r.key for r in value.content.observation.regions),
        "resolved_uncertainty_keys": tuple(u.key for u in value.content.uncertainties),
        "reason": "Compared all source text and positions with the original",
    }
    arguments.update(changes)
    return verify_source_reading(value, verify_understanding(value), **arguments)


def test_human_verification_creates_source_only_snapshot_not_trusted_educational_knowledge() -> (
    None
):
    value = source_candidate()
    result = verified(value)
    assert result.schema_version == "verified-source-content.v1"
    assert result.candidate_id == value.id
    assert result.candidate_fingerprint == value.fingerprint
    assert result.source == value.source
    assert result.content.observation == value.content.observation
    assert isinstance(result.content, SourceReadCandidate)
    assert not hasattr(result, "education")
    assert result.decision.actor_id == ADMIN.subject_id
    assert result.decision.compared_with_original is True
    assert VerifiedSourceContent.model_validate_json(result.model_dump_json()) == result
    with pytest.raises(ValidationError):
        result.__setattr__("revision", 2)


def test_provider_completion_never_supplies_human_confirmation() -> None:
    with pytest.raises(ValueError, match="comparison"):
        verified(compared_with_original=False)
    with pytest.raises(AuthorizationError):
        verified(principal=REVIEWER)
    with pytest.raises(ValueError, match="region"):
        verified(reviewed_region_keys=("heading",))


def test_combined_preverification_education_cannot_be_promoted_as_verified_source() -> None:
    with pytest.raises(ValueError, match=r"source.only"):
        verified(candidate())


def test_unreadable_cells_cannot_be_cleared_by_a_confirmation_checkbox() -> None:
    value = source_candidate()
    payload = value.model_dump(mode="json")
    for region in payload["content"]["observation"]["regions"]:
        if region["table"] is not None:
            region["table"]["cells"][0].update({"state": "unreadable", "exact_text": ""})
            break
    changed = ObservationCandidate.model_validate_json(json.dumps(payload))
    with pytest.raises(ValueError, match="unreadable"):
        verified(changed)


def test_verified_content_cannot_be_changed_while_retaining_the_decision_fingerprint() -> None:
    value = verified()
    payload = value.model_dump(mode="json")
    payload["content"]["observation"]["regions"][0]["exact_text"] = "Substituted source text"
    with pytest.raises(ValidationError):
        VerifiedSourceContent.model_validate_json(json.dumps(payload))


def test_empty_text_regions_fail_instead_of_becoming_verified_readable_pages() -> None:
    value = source_candidate()
    payload = value.model_dump(mode="json")
    first = payload["content"]["observation"]["regions"][0]
    first.update({"kind": "paragraph", "exact_text": "", "equations": [], "visual_facts": []})
    changed = ObservationCandidate.model_validate_json(json.dumps(payload))
    with pytest.raises(ValueError, match="empty"):
        verified(changed)

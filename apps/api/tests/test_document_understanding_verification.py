import json
from uuid import UUID

import pytest

from exam_guru_api.auth.domain import AdminRole, AuthorizationError, Principal
from exam_guru_api.documents.fidelity import ALGORITHM_VERSION
from exam_guru_api.documents.understanding_verification import (
    ObservationCandidate,
    PageArtifactIdentity,
    PageVerificationReport,
    SourceAnchor,
    TrustedPageKnowledge,
    UnderstandingVerificationError,
    accept_trusted_page,
    verify_understanding,
)
from tests.test_document_understanding_contracts import counting_candidate, parse

ADMIN = Principal(UUID(int=99201), frozenset({AdminRole.ADMIN}))
REVIEWER = Principal(UUID(int=99202), frozenset({AdminRole.REVIEWER}))


def candidate() -> ObservationCandidate:
    return ObservationCandidate(
        id=UUID(int=99203),
        run_id=UUID(int=99204),
        revision=1,
        method="visual_ai",
        source=PageArtifactIdentity(
            document_id=UUID(int=99205),
            source_sha256="a" * 64,
            page_number=1,
            image_sha256="b" * 64,
        ),
        content=parse(counting_candidate()),
    )


def approve(value: ObservationCandidate) -> TrustedPageKnowledge:
    return accept_trusted_page(
        value,
        verify_understanding(value),
        principal=ADMIN,
        decision_id=UUID(int=99206),
        revision=1,
        reason="Compared every observed region with the original",
        compared_with_original=True,
        reviewed_region_keys=("heading", "sequence", "groups", "answer"),
        accepted_claim_keys=("grouping",),
        resolved_uncertainty_keys=(),
    )


def test_visual_ai_is_never_its_own_verification_authority() -> None:
    value = candidate()
    report = verify_understanding(value)
    assert report.state == "needs_human_review"
    assert report.can_auto_verify is False
    with pytest.raises(UnderstandingVerificationError, match="comparison"):
        accept_trusted_page(
            value,
            report,
            principal=ADMIN,
            decision_id=UUID(int=99206),
            revision=1,
            reason="Compared every observed region with the original",
            compared_with_original=False,
            reviewed_region_keys=("heading", "sequence", "groups", "answer"),
            accepted_claim_keys=("grouping",),
            resolved_uncertainty_keys=(),
        )
    with pytest.raises(AuthorizationError):
        accept_trusted_page(
            value,
            report,
            principal=REVIEWER,
            decision_id=UUID(int=99206),
            revision=1,
            reason="Compared every observed region with the original",
            compared_with_original=True,
            reviewed_region_keys=("heading", "sequence", "groups", "answer"),
            accepted_claim_keys=("grouping",),
            resolved_uncertainty_keys=(),
        )


def test_acceptance_preserves_observations_and_only_explicitly_accepted_educational_claims() -> (
    None
):
    value = candidate()
    trusted = accept_trusted_page(
        value,
        verify_understanding(value),
        principal=ADMIN,
        decision_id=UUID(int=99206),
        revision=1,
        reason="Compared every observed region with the original",
        compared_with_original=True,
        reviewed_region_keys=("heading", "sequence", "groups", "answer"),
        accepted_claim_keys=("grouping",),
        resolved_uncertainty_keys=(),
    )
    assert trusted.observation == value.content.observation
    assert trusted.education.claims == value.content.education.claims
    assert trusted.source == value.source
    assert trusted.decision.actor_id == ADMIN.subject_id
    assert trusted.decision.candidate_id == value.id
    assert trusted.observation.regions[2].visual_facts[0].printed_total is None
    assert trusted.observation.regions[3].table is not None
    assert trusted.observation.regions[3].table.cells[1].exact_text == ""


@pytest.mark.parametrize("corruption", ["wkd \ufffd", "\ue001\ue002", "lvqZZaa;%%"])
def test_corrupt_source_text_cannot_enter_trusted_page_knowledge(corruption: str) -> None:
    payload = counting_candidate()
    payload["observation"]["regions"][0]["exact_text"] = corruption
    value = candidate().model_copy(update={"content": parse(payload)})
    report = verify_understanding(value)
    assert report.state == "needs_reprocessing"
    with pytest.raises(UnderstandingVerificationError, match="blocking"):
        approve(value)


def test_exact_equations_and_blank_cells_require_source_matched_evidence() -> None:
    value = candidate()
    anchors = (
        SourceAnchor(
            source=value.source,
            evidence_id=UUID(int=99207),
            region_key="sequence",
            kind="equation",
            exact_text="3 \u00d7 9 = 27",
            row=None,
            column=None,
        ),
        SourceAnchor(
            source=value.source,
            evidence_id=UUID(int=99208),
            region_key="answer",
            kind="blank_cell",
            exact_text="",
            row=0,
            column=1,
        ),
    )
    report = verify_understanding(value, anchors=anchors)
    assert "observation.anchor_mismatch" in {finding.code for finding in report.findings}
    assert report.state == "needs_reprocessing"
    payload = counting_candidate()
    payload["observation"]["regions"][1]["equations"] = ["3 \u00d7 9 = 27"]
    corrected = value.model_copy(update={"content": parse(payload)})
    assert verify_understanding(corrected, anchors=anchors).state == "needs_human_review"
    payload["observation"]["regions"][3]["table"]["cells"][1].update(
        state="visible", exact_text="24"
    )
    invented = value.model_copy(update={"content": parse(payload)})
    assert verify_understanding(invented, anchors=anchors).state == "needs_reprocessing"


def test_unresolved_uncertainty_or_unreviewed_regions_cannot_be_silently_accepted() -> None:
    payload = counting_candidate()
    payload["uncertainties"] = [
        {
            "key": "object-count",
            "region_keys": ["groups"],
            "field": "group_count",
            "reason": "One illustration is unclear",
            "alternatives": ["5", "6"],
        }
    ]
    value = candidate().model_copy(update={"content": parse(payload)})
    with pytest.raises(UnderstandingVerificationError, match="uncertainty"):
        approve(value)
    with pytest.raises(UnderstandingVerificationError, match="regions"):
        accept_trusted_page(
            candidate(),
            verify_understanding(candidate()),
            principal=ADMIN,
            decision_id=UUID(int=99206),
            revision=1,
            reason="Compared every observed region with the original",
            compared_with_original=True,
            reviewed_region_keys=("heading",),
            accepted_claim_keys=("grouping",),
            resolved_uncertainty_keys=(),
        )


def test_report_cannot_be_reused_after_a_candidate_or_source_revision_changes() -> None:
    value = candidate()
    report = verify_understanding(value)
    changed = value.model_copy(update={"revision": 2})
    with pytest.raises(UnderstandingVerificationError, match="stale"):
        accept_trusted_page(
            changed,
            report,
            principal=ADMIN,
            decision_id=UUID(int=99206),
            revision=1,
            reason="Compared every observed region with the original",
            compared_with_original=True,
            reviewed_region_keys=("heading", "sequence", "groups", "answer"),
            accepted_claim_keys=("grouping",),
            resolved_uncertainty_keys=(),
        )


def test_forged_empty_report_cannot_override_intrinsic_source_corruption() -> None:
    payload = counting_candidate()
    payload["observation"]["regions"][0]["exact_text"] = "\ufffd"
    value = candidate().model_copy(update={"content": parse(payload)})
    forged = PageVerificationReport(
        candidate_id=value.id,
        candidate_fingerprint=value.fingerprint,
        source_checker_version=ALGORITHM_VERSION,
        findings=(),
    )
    with pytest.raises(UnderstandingVerificationError, match="blocking"):
        accept_trusted_page(
            value,
            forged,
            principal=ADMIN,
            decision_id=UUID(int=99206),
            revision=1,
            reason="An empty report must not override source checks",
            compared_with_original=True,
            reviewed_region_keys=("heading", "sequence", "groups", "answer"),
            accepted_claim_keys=("grouping",),
            resolved_uncertainty_keys=(),
        )


@pytest.mark.parametrize("layer", ["education", "visual_fact"])
def test_corrupt_educational_or_visual_descriptions_cannot_be_trusted(layer: str) -> None:
    payload = counting_candidate()
    if layer == "education":
        payload["education"]["claims"][0]["description"] = "\ue001"
    else:
        payload["observation"]["regions"][2]["visual_facts"][0]["description"] = "\ufffd"
    value = candidate().model_copy(update={"content": parse(payload)})
    assert verify_understanding(value).state == "needs_reprocessing"


def test_number_only_page_does_not_invent_missing_language_text() -> None:
    payload = counting_candidate()
    payload["observation"]["regions"][0]["exact_text"] = ""
    value = candidate().model_copy(update={"content": parse(payload)})
    assert verify_understanding(value).state == "needs_human_review"


def test_successful_anchor_evidence_is_retained_in_verification_lineage() -> None:
    value = candidate()
    anchor = SourceAnchor(
        source=value.source,
        evidence_id=UUID(int=99207),
        region_key="sequence",
        kind="equation",
        exact_text="2 + 2 = 4",
        row=None,
        column=None,
    )
    report = verify_understanding(value, anchors=(anchor,))
    assert report.anchor_evidence_ids == (anchor.evidence_id,)
    assert report.fingerprint != verify_understanding(value).fingerprint


@pytest.mark.parametrize("reason", [" ", "\n", "review\x01note"])
def test_verification_decision_requires_a_meaningful_safe_reason(reason: str) -> None:
    value = candidate()
    with pytest.raises(ValueError, match="reason"):
        accept_trusted_page(
            value,
            verify_understanding(value),
            principal=ADMIN,
            decision_id=UUID(int=99206),
            revision=1,
            reason=reason,
            compared_with_original=True,
            reviewed_region_keys=("heading", "sequence", "groups", "answer"),
            accepted_claim_keys=("grouping",),
            resolved_uncertainty_keys=(),
        )


@pytest.mark.parametrize(
    ("kind", "row", "column", "text"),
    [("cell", None, 1, "12"), ("text", 0, None, "text"), ("blank_cell", 0, 1, "24")],
)
def test_anchor_positions_and_blank_intent_cannot_be_forged(
    kind: str, row: int | None, column: int | None, text: str
) -> None:
    value = {
        "source": candidate().source.model_dump(mode="json"),
        "evidence_id": str(UUID(int=99207)),
        "region_key": "answer",
        "kind": kind,
        "exact_text": text,
        "row": row,
        "column": column,
    }
    with pytest.raises(ValueError, match="anchor"):
        SourceAnchor.model_validate_json(json.dumps(value))


def test_all_anchor_locations_fail_closed_without_erasing_passing_evidence() -> None:
    value = candidate()
    valid = SourceAnchor(
        source=value.source,
        evidence_id=UUID(int=99207),
        region_key="heading",
        kind="text",
        exact_text="ගණන් කිරීම",
        row=None,
        column=None,
    )
    assert verify_understanding(value, anchors=(valid,)).state == "needs_human_review"
    for anchor in (
        valid.model_copy(update={"exact_text": "Different visible text"}),
        valid.model_copy(update={"region_key": "missing"}),
        SourceAnchor(
            source=value.source,
            evidence_id=UUID(int=99208),
            region_key="heading",
            kind="cell",
            exact_text="12",
            row=0,
            column=0,
        ),
        SourceAnchor(
            source=value.source,
            evidence_id=UUID(int=99209),
            region_key="answer",
            kind="cell",
            exact_text="12",
            row=4,
            column=4,
        ),
    ):
        assert verify_understanding(value, anchors=(anchor,)).state == "needs_reprocessing"
    with pytest.raises(UnderstandingVerificationError, match="bounded"):
        verify_understanding(value, anchors=(valid,) * 513)


@pytest.mark.parametrize(
    "field", ["source", "reviewed_region_keys", "accepted_claim_keys", "resolved_uncertainty_keys"]
)
def test_trusted_page_snapshot_cannot_be_changed_after_its_decision(field: str) -> None:
    trusted = approve(candidate())
    if field == "source":
        changed = trusted.model_copy(
            update={"source": trusted.source.model_copy(update={"page_number": 2})}
        )
    else:
        changed = trusted.model_copy(
            update={"decision": trusted.decision.model_copy(update={field: ("foreign",)})}
        )
    with pytest.raises(ValueError, match="trusted"):
        TrustedPageKnowledge.model_validate(changed)


def test_repeated_attestation_keys_cannot_masquerade_as_a_complete_decision() -> None:
    trusted = approve(candidate())
    decision = trusted.decision.model_copy(
        update={"reviewed_region_keys": trusted.decision.reviewed_region_keys * 2}
    )
    with pytest.raises(ValueError, match="unique"):
        TrustedPageKnowledge.model_validate(trusted.model_copy(update={"decision": decision}))


def test_verification_binds_the_underlying_source_checker_version() -> None:
    value = candidate()
    report = verify_understanding(value)
    assert report.source_checker_version == ALGORITHM_VERSION
    assert approve(value).decision.source_checker_version == ALGORITHM_VERSION
    stale = report.model_copy(update={"source_checker_version": "source-fidelity-obsolete"})
    with pytest.raises(UnderstandingVerificationError, match="stale"):
        accept_trusted_page(
            value,
            stale,
            principal=ADMIN,
            decision_id=UUID(int=99206),
            revision=1,
            reason="Do not silently reuse an obsolete checker",
            compared_with_original=True,
            reviewed_region_keys=("heading", "sequence", "groups", "answer"),
            accepted_claim_keys=("grouping",),
            resolved_uncertainty_keys=(),
        )


def test_foreign_source_evidence_cannot_authorize_a_page() -> None:
    value = candidate()
    foreign = value.source.model_copy(update={"page_number": 2})
    anchor = SourceAnchor(
        source=foreign,
        evidence_id=UUID(int=99207),
        region_key="sequence",
        kind="equation",
        exact_text="2 + 2 = 4",
        row=None,
        column=None,
    )
    with pytest.raises(UnderstandingVerificationError, match="source"):
        verify_understanding(value, anchors=(anchor,))

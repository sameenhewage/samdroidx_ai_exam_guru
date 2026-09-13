import json
from copy import deepcopy
from typing import Any, Literal
from uuid import UUID

import pytest

from exam_guru_api.knowledge.unit_review import KnowledgeUnitReview
from exam_guru_api.knowledge.units import (
    KnowledgeEvidence,
    KnowledgeProjectionReference,
    derive_knowledge_units,
    project_knowledge_unit,
)
from exam_guru_api.retrieval.domain import (
    deserialize_retrieval_filters,
    deserialize_retrieval_scope,
)
from exam_guru_api.subject_quality.domain import (
    canonical_fingerprint,
    validation_input_from_eval_snapshot,
)
from exam_guru_api.validation.domain import FindingStatus
from exam_guru_api.validation.subject import TrustedSubjectScopeValidator
from tests.test_document_understanding_verification import approve, candidate
from tests.test_knowledge_units import scope
from tests.test_programme_replay import programme_replay_snapshot
from tests.test_subject_quality_feedback import CURRICULUM_ID

type OriginalAssignment = Literal["unit_and_lesson", "unit_only", "unassigned"]


def _programme_replay_with_knowledge(
    original_assignment: OriginalAssignment,
) -> tuple[dict[str, Any], KnowledgeEvidence]:
    snapshot = programme_replay_snapshot()
    proof = snapshot["programme_context"]
    recorded_source = deepcopy(next(iter(proof["sources"].values())))
    reviewed_scope = deserialize_retrieval_scope(recorded_source)

    policy_source = proof["policy_snapshot"]["scopes"][0]["source"]
    policy_source.update(unit_id=None, lesson_id=None)
    proof["filters"]["scopes"] = [{**recorded_source, "unit_ids": [], "lesson_ids": []}]
    proof["binding"]["policy_content_hash"] = canonical_fingerprint(
        proof["policy_snapshot"]
    ).removeprefix("sha256:")

    trusted = approve(candidate())
    original_scope = scope(trusted).model_copy(
        update={
            "curriculum_version_id": reviewed_scope.curriculum_version_id,
            "grade": reviewed_scope.grade,
            "medium_id": reviewed_scope.medium_id,
            "subject_id": reviewed_scope.subject_id,
            "curriculum_unit_id": (
                None if original_assignment == "unassigned" else reviewed_scope.unit_ids[0]
            ),
            "lesson_id": (
                reviewed_scope.lesson_ids[0] if original_assignment == "unit_and_lesson" else None
            ),
        }
    )
    unit = derive_knowledge_units(trusted, original_scope)[1]
    projection = project_knowledge_unit(unit)
    review = KnowledgeUnitReview(
        id=UUID(int=51201),
        unit_id=unit.id,
        unit_fingerprint=unit.fingerprint,
        curriculum_version_id=unit.scope.curriculum_version_id,
        version=1,
        actor_id=trusted.decision.actor_id,
        state="reviewed",
        confirmed_mapping=True,
        curriculum_unit_id=reviewed_scope.unit_ids[0],
        lesson_id=reviewed_scope.lesson_ids[0],
        competency_id=reviewed_scope.taxonomy.competency_id,
        reason="Synthetic review preserves known source scope and refines unknown fields.",
    )
    evidence = KnowledgeEvidence(
        unit=unit,
        reference=KnowledgeProjectionReference(
            projection_id=projection.id,
            projection_fingerprint=projection.fingerprint,
            unit_id=unit.id,
            unit_fingerprint=unit.fingerprint,
            trusted_page_id=unit.trusted_page_id,
            trusted_fingerprint=unit.trusted_fingerprint,
            review_id=review.id,
            review_fingerprint=review.fingerprint,
            review_version=review.version,
        ),
    )
    context_id = "knowledge_projection:" + str(projection.id)
    snapshot["grounding_sources"] = [
        {
            "context_id": context_id,
            "text": projection.text,
            "source_document_id": str(unit.source.document_id),
            "source_version": "sha256:" + unit.source.source_sha256,
            "page_number": unit.source.page_number,
            "chunk_id": str(projection.id),
            "trust": "untrusted_data",
            "knowledge_evidence": evidence.model_dump(mode="json"),
        }
    ]
    snapshot["candidate"]["context_references"] = [context_id]
    snapshot["context_scope_bindings"][0]["context_id"] = context_id
    proof["sources"] = {context_id: recorded_source}
    return snapshot, evidence


@pytest.mark.parametrize(
    "original_assignment",
    [
        pytest.param("unit_and_lesson", id="known-unit-and-lesson"),
        pytest.param("unit_only", id="unknown-lesson-refined"),
        pytest.param("unassigned", id="unknown-unit-and-lesson-refined"),
    ],
)
def test_programme_replay_accepts_reviewed_refinement_of_original_learning_scope(
    original_assignment: OriginalAssignment,
) -> None:
    snapshot, evidence = _programme_replay_with_knowledge(original_assignment)
    replay = validation_input_from_eval_snapshot(
        snapshot, expected_curriculum_version_id=CURRICULUM_ID
    )
    assert replay.grounding_sources[0].knowledge_evidence == evidence
    assert replay.grounding_sources[0].text == project_knowledge_unit(evidence.unit).text
    binding = replay.context_scope_bindings[0]
    recorded_scope = deserialize_retrieval_scope(
        snapshot["programme_context"]["sources"][binding.context_id]
    )
    assert binding.programme_authorized is True
    assert binding.unit_id == recorded_scope.unit_ids[0]
    assert binding.lesson_id == recorded_scope.lesson_ids[0]
    assert binding.unit_id is not None
    assert binding.lesson_id is not None
    for original, reviewed in (
        (evidence.unit.scope.curriculum_unit_id, binding.unit_id),
        (evidence.unit.scope.lesson_id, binding.lesson_id),
    ):
        if original is not None:
            assert reviewed == original
    findings = TrustedSubjectScopeValidator().validate(replay)
    assert findings
    assert all(finding.status is FindingStatus.PASS for finding in findings)


@pytest.mark.parametrize(
    ("original_assignment", "replace_unit"),
    [
        pytest.param("unit_and_lesson", True, id="known-unit-and-lesson-replaced"),
        pytest.param("unit_and_lesson", False, id="known-lesson-replaced"),
        pytest.param("unit_only", True, id="known-unit-replaced-unknown-lesson-refined"),
    ],
)
def test_programme_replay_rejects_replacement_of_known_original_learning_scope(
    original_assignment: OriginalAssignment,
    replace_unit: bool,
) -> None:
    snapshot, evidence = _programme_replay_with_knowledge(original_assignment)
    baseline = validation_input_from_eval_snapshot(
        snapshot, expected_curriculum_version_id=CURRICULUM_ID
    )
    assert baseline.grounding_sources[0].knowledge_evidence == evidence
    before = deepcopy(snapshot)
    proof = snapshot["programme_context"]
    binding = snapshot["context_scope_bindings"][0]
    recorded_source = proof["sources"][binding["context_id"]]

    if replace_unit:
        other_unit = str(UUID(int=51202))
        recorded_source["unit_ids"] = [other_unit]
        binding.update(unit_id=other_unit, snapshot_unit_id=other_unit)
    other_lesson = str(UUID(int=51203))
    recorded_source["lesson_ids"] = [other_lesson]
    binding.update(lesson_id=other_lesson, snapshot_lesson_id=other_lesson)

    assert {key for key in snapshot if snapshot[key] != before[key]} == {
        "programme_context",
        "context_scope_bindings",
    }
    assert {key for key in proof if proof[key] != before["programme_context"][key]} == {"sources"}
    assert snapshot["grounding_sources"] == before["grounding_sources"]
    unchanged_evidence = KnowledgeEvidence.model_validate_json(
        json.dumps(snapshot["grounding_sources"][0]["knowledge_evidence"], ensure_ascii=False)
    )
    assert unchanged_evidence == evidence
    assert unchanged_evidence.fingerprint == evidence.fingerprint
    relabeled_scope = deserialize_retrieval_scope(recorded_source)
    assert relabeled_scope.curriculum_version_id == evidence.unit.scope.curriculum_version_id
    assert deserialize_retrieval_filters(proof["filters"]).allows(relabeled_scope)

    with pytest.raises(ValueError, match=r"programme replay.*scope"):
        validation_input_from_eval_snapshot(snapshot, expected_curriculum_version_id=CURRICULUM_ID)

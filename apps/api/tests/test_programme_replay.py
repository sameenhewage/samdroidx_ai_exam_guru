from copy import deepcopy
from typing import Any, cast
from uuid import UUID

import pytest

from exam_guru_api.generation.domain import ProgrammeContextBinding
from exam_guru_api.retrieval.domain import RetrievalScope, TaxonomyScope, serialize_retrieval_scope
from exam_guru_api.subject_quality.domain import (
    canonical_fingerprint,
    validation_input_from_eval_snapshot,
)
from exam_guru_api.validation.domain import FindingStatus
from exam_guru_api.validation.subject import TrustedSubjectScopeValidator
from tests.test_subject_quality_feedback import (
    CURRICULUM_ID,
    LESSON_ID,
    SUBJECT_ID,
    UNIT_ID,
    replay_snapshot,
)


def programme_replay_snapshot() -> dict[str, Any]:
    snapshot = cast(dict[str, Any], replay_snapshot())
    policy_id, scope_id, medium_id, source_curriculum = (
        UUID(int=value) for value in range(51001, 51005)
    )
    competency_id = UUID(int=51005)
    taxonomy = {
        "competency_id": str(competency_id),
        "skill_id": None,
        "sub_skill_id": None,
        "learning_concept_id": None,
    }
    source = RetrievalScope(
        grade=3,
        exam_id=UUID(int=51006),
        medium_id=medium_id,
        subject_id=SUBJECT_ID,
        curriculum_version_id=source_curriculum,
        unit_ids=(UUID(int=51007),),
        lesson_ids=(UUID(int=51008),),
        taxonomy=TaxonomyScope(competency_id=competency_id),
    )
    scope = serialize_retrieval_scope(source)
    anchor = {
        "curriculum_version_id": str(CURRICULUM_ID),
        "unit_id": str(UNIT_ID),
        "lesson_id": str(LESSON_ID),
        **taxonomy,
    }
    policy = {
        "schema": "assessment-programme-policy.v1",
        "id": str(policy_id),
        "code": "SYNTHETIC-PROGRAMME",
        "version": "fixture.v1",
        "title": "Synthetic programme replay",
        "programme_exam_configuration_id": str(UUID(int=51009)),
        "medium_id": str(medium_id),
        "anchor_curriculum_version_id": str(CURRICULUM_ID),
        "parts": {
            "paper_i": {"profile_version": "fixture.v1", "question_weight": 1},
            "paper_ii": {"profile_version": "fixture.v1", "question_weight": 1},
        },
        "scopes": [
            {
                "id": str(scope_id),
                "part": "paper_ii",
                "ordinal": 1,
                "anchor": anchor,
                "source": {
                    "grade": 3,
                    "exam_configuration_id": str(source.exam_id),
                    "medium_id": str(medium_id),
                    "subject_id": str(SUBJECT_ID),
                    "curriculum_version_id": str(source_curriculum),
                    "unit_id": str(source.unit_ids[0]),
                    "lesson_id": str(source.lesson_ids[0]),
                    **taxonomy,
                },
            }
        ],
    }
    binding = ProgrammeContextBinding(
        policy_id, canonical_fingerprint(policy).removeprefix("sha256:"), (scope_id,)
    )
    snapshot["generation"] = {"generation_run_id": str(UUID(int=51010))}
    snapshot["context_scope_bindings"][0].update(
        curriculum_version_id=str(source_curriculum),
        unit_id=str(source.unit_ids[0]),
        lesson_id=str(source.lesson_ids[0]),
        snapshot_unit_id=str(source.unit_ids[0]),
        snapshot_lesson_id=str(source.lesson_ids[0]),
        programme_authorized=True,
    )
    slot = {
        "slot_id": "slot-1",
        "question_type": "multiple_choice",
        "marks": 1,
        "section_id": "paper_ii-multiple_choice",
        "taxonomy_target": taxonomy,
        "generation_constraints": {
            "curriculum_scope": {
                key: deepcopy(value)
                for key, value in snapshot["subject_scope"].items()
                if key
                in {
                    "curriculum_version_id",
                    "grade",
                    "medium",
                    "subject_id",
                    "unit_ids",
                    "lesson_ids",
                }
            }
        },
    }
    snapshot["programme_context"] = {
        "schema_version": "programme-replay-evidence.v1",
        "generation_run_id": snapshot["generation"]["generation_run_id"],
        "medium": {"id": str(medium_id), "code": "en"},
        "binding": binding.to_snapshot(),
        "policy_snapshot": policy,
        "slot": slot,
        "filters": {"kind": "scope_set", "policy_version": "descriptive-only", "scopes": [scope]},
        "sources": {"knowledge_chunk:fixture": scope},
    }
    return snapshot


def test_programme_replay_preserves_reviewed_scope_without_using_a_label_as_authority() -> None:
    snapshot = programme_replay_snapshot()
    replay = validation_input_from_eval_snapshot(
        snapshot, expected_curriculum_version_id=CURRICULUM_ID
    )
    assert replay.context_scope_bindings[0].programme_authorized is True
    assert replay.context_scope_bindings[0].curriculum_version_id != CURRICULUM_ID
    assert replay.grounding_sources[0].text == snapshot["grounding_sources"][0]["text"]
    findings = TrustedSubjectScopeValidator().validate(replay)
    assert findings
    assert all(finding.status is FindingStatus.PASS for finding in findings)


def test_programme_replay_supports_a_reviewed_taxonomy_only_source_scope() -> None:
    snapshot = programme_replay_snapshot()
    proof = snapshot["programme_context"]
    source = proof["policy_snapshot"]["scopes"][0]["source"]
    source["unit_id"], source["lesson_id"] = None, None
    proof["filters"]["scopes"] = [
        {**proof["filters"]["scopes"][0], "unit_ids": [], "lesson_ids": []}
    ]
    proof["binding"]["policy_content_hash"] = canonical_fingerprint(
        proof["policy_snapshot"]
    ).removeprefix("sha256:")
    replay = validation_input_from_eval_snapshot(
        snapshot, expected_curriculum_version_id=CURRICULUM_ID
    )
    assert replay.context_scope_bindings[0].programme_authorized is True


@pytest.mark.parametrize(
    "corruption",
    [
        "flag_only",
        "root_type",
        "root_shape",
        "root_version",
        "generation",
        "generation_missing",
        "generation_uuid",
        "generation_case",
        "medium_type",
        "policy_size",
        "identity_duplicate",
        "hash",
        "policy_id",
        "anchor_curriculum",
        "medium",
        "scope_id_type",
        "ordinal_type",
        "part_type",
        "slot_curriculum",
        "slot_grade",
        "slot_marks",
        "slot_marks_type",
        "slot_taxonomy_missing",
        "policy_scopes",
        "scope_duplicate",
        "scope_missing",
        "part",
        "anchor_lesson",
        "anchor_taxonomy",
        "mapping_membership",
        "filters",
        "filter_kind",
        "sources_empty",
        "source_identity",
        "source_scope",
        "binding_identity",
        "binding_scope",
        "grounding",
    ],
)
def test_programme_replay_rejects_inconsistent_scope_evidence(corruption: str) -> None:
    snapshot = programme_replay_snapshot()
    proof = snapshot["programme_context"]
    if corruption == "flag_only":
        snapshot.pop("programme_context")
    elif corruption == "root_type":
        snapshot["programme_context"] = None
    elif corruption == "scope_id_type":
        proof["policy_snapshot"]["scopes"][0]["id"] = {}
    elif corruption == "ordinal_type":
        proof["policy_snapshot"]["scopes"][0]["ordinal"] = True
    elif corruption == "part_type":
        proof["policy_snapshot"]["scopes"][0]["part"] = []
    elif corruption == "root_shape":
        proof["extra"] = "not authority"
    elif corruption == "root_version":
        proof["schema_version"] = "unknown"
    elif corruption == "generation":
        proof["generation_run_id"] = str(UUID(int=1))
    elif corruption == "generation_missing":
        proof["generation_run_id"] = None
        snapshot["generation"]["generation_run_id"] = None
    elif corruption == "generation_uuid":
        proof["generation_run_id"] = "invalid"
        snapshot["generation"]["generation_run_id"] = "invalid"
    elif corruption == "generation_case":
        proof["generation_run_id"] = proof["generation_run_id"].upper()
        snapshot["generation"]["generation_run_id"] = proof["generation_run_id"]
    elif corruption == "medium_type":
        proof["medium"]["id"] = 42
        proof["policy_snapshot"]["medium_id"] = 42
    elif corruption == "policy_size":
        proof["policy_snapshot"]["title"] = "a" * 1_048_576
    elif corruption == "identity_duplicate":
        other = deepcopy(proof["policy_snapshot"]["scopes"][0])
        other["ordinal"] = 2
        proof["policy_snapshot"]["scopes"].append(other)
    elif corruption == "hash":
        proof["binding"]["policy_content_hash"] = "f" * 64
    elif corruption == "policy_id":
        proof["policy_snapshot"]["id"] = str(UUID(int=1))
    elif corruption == "anchor_curriculum":
        proof["policy_snapshot"]["anchor_curriculum_version_id"] = str(UUID(int=1))
    elif corruption == "medium":
        proof["medium"]["code"] = "si"
    elif corruption == "slot_curriculum":
        proof["slot"]["generation_constraints"]["curriculum_scope"]["curriculum_version_id"] = str(
            UUID(int=1)
        )
    elif corruption == "slot_grade":
        proof["slot"]["generation_constraints"]["curriculum_scope"]["grade"] = 7.0
    elif corruption == "slot_marks":
        proof["slot"]["marks"] = 2
    elif corruption == "slot_marks_type":
        proof["slot"]["marks"] = True
    elif corruption == "slot_taxonomy_missing":
        proof["slot"]["taxonomy_target"] = {}
    elif corruption == "policy_scopes":
        proof["policy_snapshot"]["scopes"] = []
    elif corruption == "scope_duplicate":
        proof["policy_snapshot"]["scopes"] *= 2
    elif corruption == "scope_missing":
        proof["binding"]["scope_ids"] = [str(UUID(int=1))]
    elif corruption == "part":
        proof["slot"]["section_id"] = "paper_i-multiple_choice"
    elif corruption == "anchor_lesson":
        proof["policy_snapshot"]["scopes"][0]["anchor"]["lesson_id"] = str(UUID(int=1))
    elif corruption == "anchor_taxonomy":
        proof["slot"]["taxonomy_target"]["competency_id"] = str(UUID(int=1))
    elif corruption == "mapping_membership":
        other = deepcopy(proof["policy_snapshot"]["scopes"][0])
        other["id"], other["ordinal"] = str(UUID(int=1)), 2
        proof["policy_snapshot"]["scopes"].append(other)
    elif corruption == "filters":
        proof["filters"]["scopes"][0]["grade"] = 4
    elif corruption == "filter_kind":
        proof["filters"] = {"kind": "scope", "scope": proof["filters"]["scopes"][0]}
    elif corruption == "sources_empty":
        proof["sources"] = {}
    elif corruption == "source_identity":
        proof["sources"] = {"": next(iter(proof["sources"].values()))}
    elif corruption == "source_scope":
        proof["sources"]["knowledge_chunk:fixture"] = {
            **proof["sources"]["knowledge_chunk:fixture"],
            "grade": 4,
        }
    elif corruption == "binding_identity":
        snapshot["context_scope_bindings"][0]["context_id"] = "foreign"
    elif corruption == "binding_scope":
        snapshot["context_scope_bindings"][0]["subject_id"] = str(UUID(int=1))
    else:
        snapshot["grounding_sources"] = []
    if corruption in {
        "policy_id",
        "anchor_curriculum",
        "policy_scopes",
        "scope_duplicate",
        "anchor_lesson",
        "mapping_membership",
        "scope_id_type",
        "ordinal_type",
        "part_type",
        "medium_type",
        "policy_size",
        "identity_duplicate",
    }:
        proof["binding"]["policy_content_hash"] = canonical_fingerprint(
            proof["policy_snapshot"]
        ).removeprefix("sha256:")
    with pytest.raises(ValueError, match=r"(programme|eval)"):
        validation_input_from_eval_snapshot(snapshot, expected_curriculum_version_id=CURRICULUM_ID)

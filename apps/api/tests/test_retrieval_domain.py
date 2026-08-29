import math
from collections.abc import Callable
from dataclasses import replace
from typing import cast
from uuid import UUID

import pytest

from exam_guru_api.retrieval.domain import (
    LexicalCandidate,
    RetrievalContractError,
    RetrievalRecord,
    RetrievalScope,
    RetrievalScopeSet,
    SourceProvenance,
    TaxonomyScope,
    VectorCandidate,
    deserialize_retrieval_filters,
    deserialize_retrieval_scope,
    serialize_retrieval_filters,
    serialize_retrieval_scope,
)
from tests.test_retrieval_fixtures import (
    COMPETENCY_ID,
    CURRICULUM_ID,
    EMBEDDING_FINGERPRINT,
    EXAM_ID,
    LEARNING_CONCEPT_ID,
    MEDIUM_ID,
    OTHER_MEDIUM_ID,
    SKILL_ID,
    SUB_SKILL_ID,
    grade_five_filter,
    grade_five_scope,
    retrieval_record,
)

SUBJECT_ID = UUID(int=51)
UNIT_ID = UUID(int=52)
LESSON_ID = UUID(int=53)


def test_grade_seven_subject_unit_and_lesson_are_hard_scope_boundaries() -> None:
    selected = RetrievalScope(
        grade=7,
        exam_id=EXAM_ID,
        medium_id=MEDIUM_ID,
        subject_id=SUBJECT_ID,
        curriculum_version_id=CURRICULUM_ID,
        unit_ids=(UNIT_ID,),
        lesson_ids=(LESSON_ID,),
        taxonomy=TaxonomyScope(competency_id=COMPETENCY_ID),
    )

    assert selected.allows(selected)
    assert not selected.allows(replace(selected, subject_id=UUID(int=54)))
    assert not selected.allows(replace(selected, unit_ids=(UUID(int=55),)))
    assert not selected.allows(replace(selected, lesson_ids=(UUID(int=56),)))


def test_scholarship_scope_set_allows_only_policy_owned_cross_grade_sources() -> None:
    scopes = tuple(
        RetrievalScope(
            grade=grade,
            exam_id=UUID(int=60 + grade),
            medium_id=MEDIUM_ID,
            subject_id=UUID(int=70 + grade),
            curriculum_version_id=UUID(int=80 + grade),
            unit_ids=(UUID(int=90 + grade),),
            lesson_ids=(UUID(int=100 + grade),),
            taxonomy=TaxonomyScope(competency_id=UUID(int=110 + grade)),
        )
        for grade in (3, 4, 5)
    )
    policy_scope = RetrievalScopeSet(
        policy_version="grade5-scholarship-paper-ii.v1",
        scopes=scopes,
    )

    assert {scope.grade for scope in policy_scope.scopes} == {3, 4, 5}
    assert all(policy_scope.allows(scope) for scope in scopes)
    assert not policy_scope.allows(replace(scopes[2], lesson_ids=(UUID(int=999),)))
    assert not policy_scope.allows(replace(scopes[1], medium_id=OTHER_MEDIUM_ID))
    assert not policy_scope.allows(replace(scopes[0], grade=6))
    assert not policy_scope.allows(cast(RetrievalScope, "not-a-scope"))


def test_retrieval_filter_snapshots_round_trip_without_losing_policy_scope() -> None:
    scope = grade_five_scope()
    scope_set = RetrievalScopeSet(policy_version="scholarship.v1", scopes=(scope,))

    assert deserialize_retrieval_scope(serialize_retrieval_scope(scope)) == scope
    assert deserialize_retrieval_filters(serialize_retrieval_filters(scope)) == scope
    assert deserialize_retrieval_filters(serialize_retrieval_filters(scope_set)) == scope_set

    for invalid in (None, "scope", {}, {"kind": "unknown"}):
        with pytest.raises(RetrievalContractError):
            deserialize_retrieval_filters(invalid)
    with pytest.raises(RetrievalContractError):
        serialize_retrieval_filters(cast(RetrievalScope, "scope"))
    with pytest.raises(RetrievalContractError):
        serialize_retrieval_scope(cast(RetrievalScope, "scope"))

    snapshot = serialize_retrieval_scope(scope)
    with pytest.raises(RetrievalContractError):
        deserialize_retrieval_scope({**snapshot, "extra": True})
    with pytest.raises(RetrievalContractError):
        deserialize_retrieval_scope({**snapshot, "taxonomy": "invalid"})
    with pytest.raises(RetrievalContractError):
        deserialize_retrieval_scope({**snapshot, "exam_id": "not-a-uuid"})
    with pytest.raises(RetrievalContractError):
        deserialize_retrieval_scope({**snapshot, "exam_id": 123})
    with pytest.raises(RetrievalContractError):
        deserialize_retrieval_scope({**snapshot, "unit_ids": "not-an-array"})
    with pytest.raises(RetrievalContractError):
        deserialize_retrieval_filters(
            {
                "kind": "scope_set",
                "policy_version": "policy.v1",
                "scopes": "not-an-array",
            }
        )


def test_hard_scope_requires_every_grade_exam_medium_curriculum_and_taxonomy_match() -> None:
    filters = grade_five_filter()
    candidate = grade_five_scope()

    mismatches = (
        replace(candidate, grade=6),
        replace(candidate, exam_id=UUID(int=11)),
        replace(candidate, medium_id=OTHER_MEDIUM_ID),
        replace(candidate, curriculum_version_id=UUID(int=31)),
        replace(
            candidate,
            taxonomy=replace(candidate.taxonomy, competency_id=UUID(int=44)),
        ),
        replace(
            candidate,
            taxonomy=replace(candidate.taxonomy, skill_id=UUID(int=45)),
        ),
    )

    assert filters.allows(candidate)
    assert all(not filters.allows(mismatch) for mismatch in mismatches)


def test_taxonomy_filter_may_be_broader_but_never_crosses_its_selected_path() -> None:
    competency_filter = replace(
        grade_five_filter(),
        taxonomy=TaxonomyScope(competency_id=COMPETENCY_ID),
    )
    skill_filter = grade_five_filter()

    assert competency_filter.allows(grade_five_scope())
    assert skill_filter.allows(grade_five_scope())
    assert not skill_filter.allows(
        grade_five_scope(
            taxonomy=TaxonomyScope(
                competency_id=COMPETENCY_ID,
                skill_id=UUID(int=999),
            )
        )
    )
    assert not skill_filter.taxonomy.allows(cast(TaxonomyScope, "not-taxonomy"))
    assert not skill_filter.allows(cast(RetrievalScope, "not-scope"))


def test_candidate_contracts_preserve_opaque_text_scope_provenance_and_fingerprint() -> None:
    record = retrieval_record(100, "Reviewed Grade 5 source text", block_id=1_001)
    lexical = LexicalCandidate(record=record, score=7.5)
    vector = VectorCandidate(
        record=record,
        score=0.875,
        embedding_config_fingerprint=EMBEDDING_FINGERPRINT,
    )

    assert lexical.record is record
    assert vector.record is record
    assert record.scope.grade == 5
    assert record.provenance.page_number == 2
    assert vector.embedding_config_fingerprint == EMBEDDING_FINGERPRINT


@pytest.mark.parametrize(
    "build",
    [
        lambda: TaxonomyScope(competency_id=cast(UUID, "competency")),
        lambda: TaxonomyScope(
            competency_id=COMPETENCY_ID,
            skill_id=cast(UUID, "skill"),
        ),
        lambda: TaxonomyScope(competency_id=COMPETENCY_ID, sub_skill_id=SUB_SKILL_ID),
        lambda: TaxonomyScope(
            competency_id=COMPETENCY_ID,
            skill_id=SKILL_ID,
            learning_concept_id=LEARNING_CONCEPT_ID,
        ),
        lambda: RetrievalScope(
            grade=0,
            exam_id=EXAM_ID,
            medium_id=MEDIUM_ID,
            curriculum_version_id=CURRICULUM_ID,
            taxonomy=TaxonomyScope(competency_id=COMPETENCY_ID),
        ),
        lambda: RetrievalScope(
            grade=cast(int, True),
            exam_id=EXAM_ID,
            medium_id=MEDIUM_ID,
            curriculum_version_id=CURRICULUM_ID,
            taxonomy=TaxonomyScope(competency_id=COMPETENCY_ID),
        ),
        lambda: RetrievalScope(
            grade=14,
            exam_id=EXAM_ID,
            medium_id=MEDIUM_ID,
            curriculum_version_id=CURRICULUM_ID,
            taxonomy=TaxonomyScope(competency_id=COMPETENCY_ID),
        ),
        lambda: RetrievalScope(
            grade=5,
            exam_id=EXAM_ID,
            medium_id=MEDIUM_ID,
            curriculum_version_id=CURRICULUM_ID,
            taxonomy=cast(TaxonomyScope, "taxonomy"),
        ),
        lambda: RetrievalScope(
            grade=5,
            exam_id=EXAM_ID,
            medium_id=MEDIUM_ID,
            curriculum_version_id=CURRICULUM_ID,
            taxonomy=TaxonomyScope(competency_id=COMPETENCY_ID),
            subject_id=cast(UUID, "subject"),
        ),
        lambda: RetrievalScope(
            grade=5,
            exam_id=EXAM_ID,
            medium_id=MEDIUM_ID,
            curriculum_version_id=CURRICULUM_ID,
            taxonomy=TaxonomyScope(competency_id=COMPETENCY_ID),
            unit_ids=cast(tuple[UUID, ...], [UNIT_ID]),
        ),
        lambda: RetrievalScope(
            grade=5,
            exam_id=EXAM_ID,
            medium_id=MEDIUM_ID,
            curriculum_version_id=CURRICULUM_ID,
            taxonomy=TaxonomyScope(competency_id=COMPETENCY_ID),
            unit_ids=(UNIT_ID, UNIT_ID),
        ),
        lambda: RetrievalScope(
            grade=5,
            exam_id=EXAM_ID,
            medium_id=MEDIUM_ID,
            curriculum_version_id=CURRICULUM_ID,
            taxonomy=TaxonomyScope(competency_id=COMPETENCY_ID),
            lesson_ids=(LESSON_ID,),
        ),
        lambda: RetrievalScopeSet(cast(str, 123), (grade_five_scope(),)),
        lambda: RetrievalScopeSet("", (grade_five_scope(),)),
        lambda: RetrievalScopeSet(" padded ", (grade_five_scope(),)),
        lambda: RetrievalScopeSet("x" * 129, (grade_five_scope(),)),
        lambda: RetrievalScopeSet("policy.v1", ()),
        lambda: RetrievalScopeSet(
            "policy.v1",
            cast(tuple[RetrievalScope, ...], [grade_five_scope()]),
        ),
        lambda: RetrievalScopeSet(
            "policy.v1",
            (grade_five_scope(), grade_five_scope()),
        ),
        lambda: RetrievalScopeSet(
            "policy.v1",
            (grade_five_scope(), replace(grade_five_scope(), medium_id=OTHER_MEDIUM_ID)),
        ),
        lambda: RetrievalScopeSet(
            "policy.v1",
            tuple(replace(grade_five_scope(), grade=(index % 13) + 1) for index in range(65)),
        ),
        lambda: SourceProvenance(source_document_id=cast(UUID, "document"), page_number=1),
        lambda: SourceProvenance(source_document_id=UUID(int=1), page_number=0),
        lambda: SourceProvenance(
            source_document_id=UUID(int=1),
            page_number=cast(int, True),
        ),
        lambda: SourceProvenance(
            source_document_id=UUID(int=1),
            page_number=1,
            source_block_id=cast(UUID, "block"),
        ),
        lambda: RetrievalRecord(
            chunk_id=cast(UUID, "chunk"),
            text="text",
            scope=grade_five_scope(),
            provenance=SourceProvenance(UUID(int=2), 1),
        ),
        lambda: RetrievalRecord(
            chunk_id=UUID(int=1),
            text=" ",
            scope=grade_five_scope(),
            provenance=SourceProvenance(UUID(int=2), 1),
        ),
        lambda: RetrievalRecord(
            chunk_id=UUID(int=1),
            text=cast(str, 123),
            scope=grade_five_scope(),
            provenance=SourceProvenance(UUID(int=2), 1),
        ),
        lambda: RetrievalRecord(
            chunk_id=UUID(int=1),
            text="x" * 100_001,
            scope=grade_five_scope(),
            provenance=SourceProvenance(UUID(int=2), 1),
        ),
        lambda: RetrievalRecord(
            chunk_id=UUID(int=1),
            text="source\x00text",
            scope=grade_five_scope(),
            provenance=SourceProvenance(UUID(int=2), 1),
        ),
        lambda: RetrievalRecord(
            chunk_id=UUID(int=1),
            text="source\x1b[31mtext",
            scope=grade_five_scope(),
            provenance=SourceProvenance(UUID(int=2), 1),
        ),
        lambda: RetrievalRecord(
            chunk_id=UUID(int=1),
            text="text",
            scope=grade_five_scope(),
            provenance=cast(SourceProvenance, "provenance"),
        ),
        lambda: LexicalCandidate(cast(RetrievalRecord, "record"), 1.0),
        lambda: LexicalCandidate(retrieval_record(1, "text"), math.inf),
        lambda: LexicalCandidate(retrieval_record(1, "text"), cast(float, True)),
        lambda: VectorCandidate(
            cast(RetrievalRecord, "record"),
            0.5,
            EMBEDDING_FINGERPRINT,
        ),
        lambda: VectorCandidate(retrieval_record(1, "text"), math.nan, EMBEDDING_FINGERPRINT),
        lambda: VectorCandidate(retrieval_record(1, "text"), 0.5, ""),
        lambda: VectorCandidate(retrieval_record(1, "text"), 0.5, " "),
        lambda: VectorCandidate(retrieval_record(1, "text"), 0.5, "x" * 513),
        lambda: VectorCandidate(retrieval_record(1, "text"), 0.5, cast(str, 123)),
    ],
)
def test_retrieval_contracts_reject_malformed_values(build: Callable[[], object]) -> None:
    with pytest.raises(RetrievalContractError):
        build()


def test_scope_and_record_runtime_types_are_not_silently_coerced() -> None:
    with pytest.raises(RetrievalContractError):
        RetrievalScope(
            grade=5,
            exam_id=cast(UUID, "exam"),
            medium_id=MEDIUM_ID,
            curriculum_version_id=CURRICULUM_ID,
            taxonomy=TaxonomyScope(competency_id=COMPETENCY_ID),
        )

    with pytest.raises(RetrievalContractError):
        RetrievalRecord(
            chunk_id=UUID(int=1),
            text="text",
            scope=cast(RetrievalScope, "scope"),
            provenance=SourceProvenance(UUID(int=2), 1),
        )

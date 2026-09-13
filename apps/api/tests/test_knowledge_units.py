import asyncio
import hashlib
import unicodedata
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid5

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.documents.domain import SourceDocumentType
from exam_guru_api.documents.understanding_verification import (
    TrustedPageKnowledge,
    accept_trusted_page,
    verify_understanding,
)
from exam_guru_api.knowledge.unit_models import KnowledgeProjectionModel, KnowledgeUnitModel
from exam_guru_api.knowledge.unit_service import (
    KnowledgePreparationError,
    KnowledgeUnitService,
    _projection,
    _unit,
)
from exam_guru_api.knowledge.units import (
    KnowledgeDerivationError,
    KnowledgeEvidence,
    KnowledgeProjection,
    KnowledgeProjectionReference,
    KnowledgeScope,
    KnowledgeUnit,
    derive_knowledge_units,
    project_knowledge_unit,
)
from tests.test_document_understanding_contracts import counting_candidate, parse
from tests.test_document_understanding_verification import ADMIN, approve, candidate


def scope(trusted: TrustedPageKnowledge, grade: int = 5) -> KnowledgeScope:
    return KnowledgeScope(
        document_id=trusted.source.document_id,
        source_sha256=trusted.source.source_sha256,
        material_type=SourceDocumentType.TEACHER_GUIDE,
        year=None,
        paper_code=None,
        metadata_scope_version=1,
        curriculum_version_id=UUID(int=99301),
        grade=grade,
        medium_id=UUID(int=99302),
        subject_id=UUID(int=99303),
        catalogue_decision_id=UUID(int=99304),
        catalogue_version=1,
        catalogue_scope_fingerprint="sha256:" + "c" * 64,
        curriculum_unit_id=None,
        lesson_id=None,
    )


def test_structured_evidence_binds_lossless_unit_and_projection_identities() -> None:
    trusted = approve(candidate())
    unit = derive_knowledge_units(trusted, scope(trusted))[1]
    projection = project_knowledge_unit(unit)
    reference = KnowledgeProjectionReference(
        projection_id=projection.id,
        projection_fingerprint=projection.fingerprint,
        unit_id=unit.id,
        unit_fingerprint=unit.fingerprint,
        trusted_page_id=unit.trusted_page_id,
        trusted_fingerprint=unit.trusted_fingerprint,
        review_id=UUID(int=99309),
        review_fingerprint="d" * 64,
        review_version=1,
    )
    evidence = KnowledgeEvidence(unit=unit, reference=reference)
    assert evidence.unit.observation == unit.observation
    assert evidence.unit.education == unit.education
    for field, value in (
        ("unit_id", UUID(int=99310)),
        ("trusted_page_id", UUID(int=99311)),
        ("projection_id", UUID(int=99312)),
        ("unit_fingerprint", "e" * 64),
        ("projection_fingerprint", "f" * 64),
        ("trusted_fingerprint", "0" * 64),
    ):
        with pytest.raises(ValueError, match=r"evidence.*identity"):
            KnowledgeEvidence(unit=unit, reference=reference.model_copy(update={field: value}))


def test_units_are_deterministic_source_components_and_preserve_exact_evidence() -> None:
    trusted = approve(candidate())
    units = derive_knowledge_units(trusted, scope(trusted))
    assert units == derive_knowledge_units(trusted, scope(trusted))
    assert [tuple(region.key for region in unit.observation.regions) for unit in units] == [
        ("heading",),
        ("sequence", "groups", "answer"),
    ]
    assert units[1].education == trusted.education
    assert units[1].region_ids == tuple(
        uuid5(trusted.decision.candidate_id, key) for key in ("sequence", "groups", "answer")
    )
    assert units[1].observation.regions[2].table is not None
    assert units[1].observation.regions[2].table.cells[1].exact_text == ""
    assert units[1].observation.regions[1].visual_facts[0].printed_total is None
    assert units[0].trusted_page_id == trusted.id


def test_unaccepted_meaning_cannot_change_unit_grouping_or_reappear_in_projection() -> None:
    payload = counting_candidate()
    payload["education"]["claims"].append(
        {
            "key": "unaccepted",
            "kind": "concept",
            "description": "Invented teaching meaning",
            "region_keys": ["heading", "groups"],
        }
    )
    value = candidate().model_copy(update={"content": parse(payload)})
    trusted = approve(value)
    units = derive_knowledge_units(trusted, scope(trusted))
    assert len(units) == 2
    assert all(claim.key != "unaccepted" for unit in units for claim in unit.education.claims)
    assert all(
        "Invented teaching meaning" not in project_knowledge_unit(unit).text for unit in units
    )


def test_projection_is_a_versioned_nfc_view_not_a_replacement_for_source_observation() -> None:
    payload = counting_candidate()
    raw = "ගණන් කිරීම — a\u0301"
    payload["observation"]["regions"][0]["exact_text"] = raw
    trusted = approve(candidate().model_copy(update={"content": parse(payload)}))
    units = derive_knowledge_units(trusted, scope(trusted))
    projection = project_knowledge_unit(units[0])
    assert units[0].observation.regions[0].exact_text == raw
    assert unicodedata.normalize("NFC", raw) in projection.text
    assert projection.text_sha256 == hashlib.sha256(projection.text.encode("utf-8")).hexdigest()
    assert projection.unit_fingerprint == units[0].fingerprint
    assert projection == project_knowledge_unit(units[0])
    exercise = project_knowledge_unit(units[1]).text
    assert "Printed equation: 2 + 2 = 4" in exercise
    assert "[blank]" in exercise
    assert "Visible groups: 6" in exercise
    assert "Items per group: 2" in exercise
    assert "Printed total:" not in exercise
    assert "Accepted educational meaning" in exercise


def test_reading_order_does_not_merge_unrelated_educational_components() -> None:
    payload = counting_candidate()
    payload["observation"]["relationships"].append(
        {"source_key": "heading", "target_key": "sequence", "kind": "reading_next"}
    )
    trusted = approve(candidate().model_copy(update={"content": parse(payload)}))
    units = derive_knowledge_units(trusted, scope(trusted))
    assert len(units) == 2
    assert [unit.sequence for unit in units] == [0, 1]


def test_parent_regions_remain_with_their_children() -> None:
    payload = counting_candidate()
    payload["observation"]["regions"][1]["parent_key"] = "heading"
    trusted = approve(candidate().model_copy(update={"content": parse(payload)}))
    units = derive_knowledge_units(trusted, scope(trusted))
    assert len(units) == 1
    assert units[0].observation == trusted.observation


@pytest.mark.parametrize("field", ["document_id", "source_sha256"])
def test_scope_for_another_source_is_never_accepted(field: str) -> None:
    trusted = approve(candidate())
    changed = scope(trusted).model_copy(
        update={field: UUID(int=99399) if field == "document_id" else "f" * 64}
    )
    with pytest.raises(KnowledgeDerivationError, match="source"):
        derive_knowledge_units(trusted, changed)


def test_changed_scope_versions_produce_new_units_instead_of_rebinding_old_units() -> None:
    trusted = approve(candidate())
    first = derive_knowledge_units(trusted, scope(trusted))
    changed = derive_knowledge_units(
        trusted, scope(trusted).model_copy(update={"metadata_scope_version": 2})
    )
    other_grade = derive_knowledge_units(trusted, scope(trusted, grade=7))
    assert first[0].id != changed[0].id
    assert first[0].id != other_grade[0].id
    assert first[0].scope.grade == 5
    assert other_grade[0].scope.grade == 7


def test_candidate_output_and_corrupt_trusted_snapshots_cannot_be_derived() -> None:
    with pytest.raises(ValueError, match="TrustedPageKnowledge"):
        derive_knowledge_units(cast(TrustedPageKnowledge, candidate()), scope(approve(candidate())))
    trusted = approve(candidate())
    corrupted = trusted.model_copy(
        update={"observation": trusted.observation.model_copy(update={"language": "en"})}
    )
    with pytest.raises(ValueError, match="snapshot"):
        derive_knowledge_units(corrupted, scope(trusted))


def test_resolved_cross_region_uncertainty_preserves_its_evidence_component() -> None:
    payload = counting_candidate()
    payload["uncertainties"] = [
        {
            "key": "layout",
            "region_keys": ["heading", "sequence"],
            "field": "reading_order",
            "reason": "Synthetic cross-region layout check",
            "alternatives": [],
        }
    ]
    value = candidate().model_copy(update={"content": parse(payload)})
    trusted = accept_trusted_page(
        value,
        verify_understanding(value),
        principal=ADMIN,
        decision_id=UUID(int=99311),
        revision=1,
        reason="Compared the complete synthetic layout",
        compared_with_original=True,
        reviewed_region_keys=tuple(region.key for region in value.content.observation.regions),
        accepted_claim_keys=("grouping",),
        resolved_uncertainty_keys=("layout",),
    )
    units = derive_knowledge_units(trusted, scope(trusted))
    assert len(units) == 1
    assert units[0].resolved_uncertainties == trusted.resolved_uncertainties


def test_scope_cannot_carry_a_lesson_without_its_curriculum_unit() -> None:
    trusted = approve(candidate())
    invalid = scope(trusted).model_copy(update={"lesson_id": UUID(int=99312)})
    with pytest.raises(ValueError, match="curriculum unit"):
        derive_knowledge_units(trusted, invalid)


@pytest.mark.parametrize("field", ["id", "region_ids", "scope"])
def test_modified_unit_identities_fail_revalidation(field: str) -> None:
    trusted = approve(candidate())
    unit = derive_knowledge_units(trusted, scope(trusted))[0]
    changed = {
        "id": UUID(int=99313),
        "region_ids": (UUID(int=99314),),
        "scope": scope(trusted).model_copy(update={"document_id": UUID(int=99315)}),
    }[field]
    with pytest.raises(ValueError, match="knowledge"):
        KnowledgeUnit.model_validate(unit.model_copy(update={field: changed}))


@pytest.mark.parametrize("field", ["id", "text_sha256", "text"])
def test_modified_projection_identity_or_text_cannot_pass(field: str) -> None:
    trusted = approve(candidate())
    projection = project_knowledge_unit(derive_knowledge_units(trusted, scope(trusted))[0])
    changed = {"id": UUID(int=99316), "text_sha256": "f" * 64, "text": "a\u0301"}[field]
    with pytest.raises(ValueError, match="projection"):
        KnowledgeProjection.model_validate(projection.model_copy(update={field: changed}))
    assert len(projection.fingerprint) == 64


def test_projection_never_turns_a_printed_total_into_a_computed_count() -> None:
    payload = counting_candidate()
    fact = payload["observation"]["regions"][2]["visual_facts"][0]
    fact.update({"group_count": None, "items_per_group": None, "printed_total": "11"})
    trusted = approve(candidate().model_copy(update={"content": parse(payload)}))
    projection = project_knowledge_unit(derive_knowledge_units(trusted, scope(trusted))[1])
    assert "Printed total: 11" in projection.text
    assert "Visible groups:" not in projection.text
    assert "Items per group:" not in projection.text


def test_empty_decorative_evidence_is_retained_but_not_invented_as_retrieval_text() -> None:
    payload = counting_candidate()
    decorative = payload["observation"]["regions"][0]
    decorative.update({"kind": "decorative_image", "exact_text": ""})
    payload["observation"]["regions"] = [decorative]
    payload["observation"]["relationships"] = []
    payload["education"]["claims"] = []
    value = candidate().model_copy(update={"content": parse(payload)})
    trusted = accept_trusted_page(
        value,
        verify_understanding(value),
        principal=ADMIN,
        decision_id=UUID(int=99317),
        revision=1,
        reason="Compared a synthetic decorative region",
        compared_with_original=True,
        reviewed_region_keys=("heading",),
        accepted_claim_keys=(),
        resolved_uncertainty_keys=(),
    )
    unit = derive_knowledge_units(trusted, scope(trusted))[0]
    assert unit.observation.regions[0].kind == "decorative_image"
    with pytest.raises(KnowledgeDerivationError, match="no retrievable"):
        project_knowledge_unit(unit)


def test_persisted_snapshot_hashes_are_revalidated_before_readback() -> None:
    trusted = approve(candidate())
    unit = derive_knowledge_units(trusted, scope(trusted))[0]
    row = KnowledgeUnitModel.from_domain(
        unit, actor_id=ADMIN.subject_id, audit_event_id=UUID(int=99321)
    )
    row.fingerprint = "f" * 64
    with pytest.raises(KnowledgePreparationError, match="stored_knowledge_unit"):
        _unit(row)
    projection = project_knowledge_unit(unit)
    projected = KnowledgeProjectionModel.from_domain(
        projection, actor_id=ADMIN.subject_id, audit_event_id=UUID(int=99321)
    )
    projected.text = "Different index text"
    with pytest.raises(KnowledgePreparationError, match="stored_knowledge_projection"):
        _projection(projected)


@pytest.mark.parametrize(
    "field", ["trusted_page_id", "candidate_id", "metadata_scope_version", "catalogue_version"]
)
def test_unit_readback_rejects_conflicting_index_and_version_columns(field: str) -> None:
    trusted = approve(candidate())
    unit = derive_knowledge_units(trusted, scope(trusted))[0]
    row = KnowledgeUnitModel.from_domain(
        unit, actor_id=ADMIN.subject_id, audit_event_id=UUID(int=99324)
    )
    setattr(row, field, 99 if field.endswith("version") else UUID(int=99325))
    with pytest.raises(KnowledgePreparationError, match="stored_knowledge_unit"):
        _unit(row)


@pytest.mark.parametrize("field", ["unit_fingerprint", "transformation_version"])
def test_projection_readback_rejects_conflicting_lineage_columns(field: str) -> None:
    trusted = approve(candidate())
    projection = project_knowledge_unit(derive_knowledge_units(trusted, scope(trusted))[0])
    row = KnowledgeProjectionModel.from_domain(
        projection, actor_id=ADMIN.subject_id, audit_event_id=UUID(int=99324)
    )
    setattr(row, field, "f" * 64 if field == "unit_fingerprint" else "unknown-projection.v2")
    with pytest.raises(KnowledgePreparationError, match="stored_knowledge_projection"):
        _projection(row)


@pytest.mark.parametrize("page_number", [0, -1, True])
def test_invalid_preparation_page_never_touches_storage(page_number: int) -> None:
    session = AsyncMock(spec=AsyncSession)
    with pytest.raises(KnowledgePreparationError, match="page_invalid"):
        asyncio.run(
            KnowledgeUnitService(session).prepare_page(
                principal=ADMIN,
                document_id=UUID(int=99322),
                page_number=page_number,
                expected_trusted_page_id=UUID(int=99323),
            )
        )
    session.execute.assert_not_awaited()


def test_oversized_projections_fail_explicitly_instead_of_truncating_source() -> None:
    payload = counting_candidate()
    payload["observation"]["language"] = "en"
    payload["observation"]["regions"][0]["exact_text"] = "Observed source lesson text. " * 2000
    trusted = approve(candidate().model_copy(update={"content": parse(payload)}))
    unit = derive_knowledge_units(trusted, scope(trusted))[0]
    with pytest.raises(KnowledgeDerivationError, match=r"projection.*bound"):
        project_knowledge_unit(unit)
    assert unit.observation.regions[0].exact_text.endswith("lesson text. ")

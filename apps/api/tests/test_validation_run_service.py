import asyncio
from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.blueprints.serialization import serialize_blueprint
from exam_guru_api.generation.domain import (
    ContextProvenance,
    GenerationIdentity,
    GenerationResult,
    ProvenanceContext,
    RetrievedContextItem,
)
from exam_guru_api.generation.models import GenerationAttemptModel, GenerationRunModel
from exam_guru_api.generation.run_service import _candidate_snapshot
from exam_guru_api.validation import (
    BlueprintRequirements,
    FindingEvidence,
    FindingStatus,
    GenerationAdapterError,
    ValidationFinding,
    ValidationPipeline,
    ValidationReport,
    adapt_generation_result,
    build_default_pipeline,
    generation_result_fingerprint,
)
from exam_guru_api.validation import service as validation_service
from exam_guru_api.validation.models import (
    MAX_VALIDATION_DUPLICATE_REFERENCES,
    MAX_VALIDATION_EVIDENCE_PER_FINDING,
    MAX_VALIDATION_FINDINGS,
    MAX_VALIDATION_VALIDATORS,
    ValidationFindingModel,
    ValidationRunModel,
)
from exam_guru_api.validation.repository import (
    DuplicateReferenceRecord,
    StoredValidationReport,
    ValidationGenerationRecord,
)
from exam_guru_api.validation.service import (
    ValidationCurriculumNotFoundError,
    ValidationGenerationIntegrityError,
    ValidationGenerationNotSucceededError,
    ValidationIdempotencyConflictError,
    ValidationPipelineVersionConflictError,
    ValidationReportIntegrityError,
    ValidationResourceLimitError,
    ValidationRunService,
    _array,
    _duplicate_references,
    _finding_models,
    _fingerprint,
    _input_snapshot,
    _integer,
    _object,
    _optional_text,
    _plain_json,
    _question_from_snapshot,
    _request_fingerprint_payload,
    _text,
    _validation_creation_failure_code,
    grade_age_bounds,
    reconstruct_generation_result,
    reconstruct_validation_report,
)
from tests.test_operational_telemetry import telemetry
from tests.test_validation_generation_integration import _PAPER, _result

NOW = datetime(2026, 1, 1, tzinfo=UTC)
CURRICULUM_ID = _PAPER.curriculum_scope.curriculum_version_id
RUN_ID = UUID(int=980_001)
ATTEMPT_ID = UUID(int=980_002)
BLUEPRINT_DB_ID = UUID(int=980_003)
ACTOR_ID = UUID(int=980_004)
CHUNK_ID = UUID(int=980_005)
QUESTION_ID = UUID(int=980_006)
SOURCE_A_ID = UUID(int=980_007)
SOURCE_B_ID = UUID(int=980_008)
BLOCK_A_ID = UUID(int=980_009)
BLOCK_B_ID = UUID(int=980_010)


def test_validation_age_bounds_follow_the_selected_grade() -> None:
    assert grade_age_bounds(5) == (9, 11)
    assert grade_age_bounds(7) == (11, 13)
    assert grade_age_bounds(13) == (17, 19)
    with pytest.raises(ValidationGenerationIntegrityError):
        grade_age_bounds(14)


def _context_snapshot(result: GenerationResult) -> dict[str, object]:
    generation_result = result
    records = (
        ("knowledge_chunk", CHUNK_ID, SOURCE_A_ID, BLOCK_A_ID),
        ("historical_question", QUESTION_ID, SOURCE_B_ID, BLOCK_B_ID),
    )
    return {
        "items": [
            {
                "context_id": item.context_id,
                "record_kind": record_kind,
                "record_id": str(record_id),
                "record_version": index + 2,
                "text": item.text,
                "trust": "untrusted_data",
                "provenance": {
                    "source_document_id": item.provenance.source_document_id,
                    "source_version": item.provenance.source_version,
                    "page_number": item.provenance.page_number,
                    "chunk_id": item.provenance.chunk_id,
                    "source_block_id": str(block_id),
                },
                "taxonomy": {
                    "competency_id": str(
                        result.request.blueprint_slot.taxonomy_target.competency_id
                    ),
                    "skill_id": None,
                    "sub_skill_id": None,
                    "learning_concept_id": None,
                },
            }
            for index, (item, (record_kind, record_id, _source_id, block_id)) in enumerate(
                zip(generation_result.request.context.items, records, strict=True)
            )
        ],
        "trust": "untrusted_data",
    }


def generation_record() -> tuple[ValidationGenerationRecord, GenerationResult]:
    base = _result()
    original_items = base.request.context.items
    context = ProvenanceContext(
        items=(
            RetrievedContextItem(
                context_id=f"knowledge_chunk:{CHUNK_ID}",
                text=original_items[0].text,
                provenance=ContextProvenance(
                    source_document_id=str(SOURCE_A_ID),
                    source_version=original_items[0].provenance.source_version,
                    page_number=original_items[0].provenance.page_number,
                    chunk_id=str(CHUNK_ID),
                ),
            ),
            RetrievedContextItem(
                context_id=f"historical_question:{QUESTION_ID}",
                text=original_items[1].text,
                provenance=ContextProvenance(
                    source_document_id=str(SOURCE_B_ID),
                    source_version=original_items[1].provenance.source_version,
                    page_number=original_items[1].provenance.page_number,
                    chunk_id=str(QUESTION_ID),
                ),
            ),
        )
    )
    identity = GenerationIdentity(
        generation_id=RUN_ID,
        attempt_id=ATTEMPT_ID,
        idempotency_key=f"generation-{RUN_ID.hex}",
        attempt_number=1,
    )
    result = replace(base, request=replace(base.request, identity=identity, context=context))
    blueprint_snapshot = cast(dict[str, object], serialize_blueprint(_PAPER))
    raw_slots = cast(list[dict[str, object]], blueprint_snapshot["slots"])
    slot_snapshot = next(
        item for item in raw_slots if item["slot_id"] == result.request.blueprint_slot.slot_id
    )
    candidate = _candidate_snapshot(result.question)
    run = GenerationRunModel(
        id=RUN_ID,
        curriculum_version_id=CURRICULUM_ID,
        paper_blueprint_id=BLUEPRINT_DB_ID,
        retry_of_run_id=None,
        retry_depth=0,
        slot_id=result.request.blueprint_slot.slot_id,
        idempotency_key_hash="sha256:" + "1" * 64,
        request_fingerprint="sha256:" + "0" * 64,
        blueprint_version=result.request.versions.blueprint_version,
        blueprint_snapshot=blueprint_snapshot,
        blueprint_slot_snapshot=slot_snapshot,
        knowledge_chunk_ids=[str(CHUNK_ID)],
        historical_question_ids=[str(QUESTION_ID)],
        context_snapshot=_context_snapshot(result),
        prompt_id=result.request.versions.prompt_id,
        prompt_version=result.request.versions.prompt_version,
        provider=result.request.versions.provider,
        provider_version=result.request.versions.provider_version,
        model=result.request.versions.model,
        model_version=result.request.versions.model_version,
        retrieval_version=result.request.versions.retrieval_version,
        schema_version=result.request.versions.schema_version,
        pricing_version="pricing.v1",
        input_microusd_per_million_tokens=10,
        output_microusd_per_million_tokens=20,
        generation_parameters={
            "temperature": result.request.parameters.temperature,
            "max_output_tokens": result.request.parameters.max_output_tokens,
            "seed": result.request.parameters.seed,
        },
        max_attempts=3,
        max_input_tokens=10_000,
        max_output_tokens=2_000,
        max_cost_microusd=1_000_000,
        status="succeeded",
        version=2,
        started_at=NOW,
        completed_at=NOW,
        failure_code=None,
        result_attempt_id=ATTEMPT_ID,
        attempt_count=1,
        input_tokens=result.accounting.input_tokens,
        output_tokens=result.accounting.output_tokens,
        total_tokens=result.accounting.total_tokens,
        cost_microusd=result.accounting.cost_microusd,
        latency_ms=result.accounting.latency_ms,
        candidate=candidate,
        disposition="requires_validation",
        created_by=ACTOR_ID,
        created_at=NOW,
    )
    run.request_fingerprint = _fingerprint(_request_fingerprint_payload(run))
    attempt = GenerationAttemptModel(
        id=ATTEMPT_ID,
        generation_run_id=RUN_ID,
        attempt_number=1,
        retry_of_attempt_id=None,
        provider_idempotency_key=identity.idempotency_key,
        status="succeeded",
        failure_code=None,
        retry_after_ms=None,
        accounting_known=True,
        input_tokens=result.accounting.input_tokens,
        output_tokens=result.accounting.output_tokens,
        total_tokens=result.accounting.total_tokens,
        cost_microusd=result.accounting.cost_microusd,
        latency_ms=result.accounting.latency_ms,
        candidate=deepcopy(candidate),
        disposition="requires_validation",
        started_at=NOW,
        completed_at=NOW,
    )
    return ValidationGenerationRecord(run, attempt), result


def _refingerprint(run: GenerationRunModel) -> None:
    run.request_fingerprint = _fingerprint(_request_fingerprint_payload(run))


def test_reconstructs_the_exact_generation_result_from_persisted_snapshots() -> None:
    record, expected = generation_record()

    reconstructed = reconstruct_generation_result(record)

    assert reconstructed == expected
    assert generation_result_fingerprint(reconstructed) == (
        generation_result_fingerprint(cast(Any, expected))
    )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("status", "pending"),
        ("disposition", None),
        ("result_attempt_id", None),
        ("candidate", None),
    ],
)
def test_reconstruction_rejects_non_succeeded_generation_state(
    field_name: str,
    value: object,
) -> None:
    record, _ = generation_record()
    setattr(record.run, field_name, value)

    with pytest.raises(ValidationGenerationNotSucceededError):
        reconstruct_generation_result(record)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("id", UUID(int=999_001)),
        ("generation_run_id", UUID(int=999_002)),
        ("status", "failed"),
        ("disposition", None),
        ("accounting_known", False),
        ("candidate", None),
        ("candidate", {"stem": "different"}),
        ("input_tokens", None),
        ("output_tokens", None),
        ("total_tokens", None),
        ("cost_microusd", None),
    ],
)
def test_reconstruction_rejects_inconsistent_result_attempts(
    field_name: str,
    value: object,
) -> None:
    record, _ = generation_record()
    assert record.attempt is not None
    setattr(record.attempt, field_name, value)

    with pytest.raises(ValidationGenerationIntegrityError, match="attempt"):
        reconstruct_generation_result(record)


def test_reconstruction_rejects_missing_result_attempt() -> None:
    record, _ = generation_record()
    with pytest.raises(ValidationGenerationIntegrityError, match="attempt"):
        reconstruct_generation_result(replace(record, attempt=None))


def _mutate_context(
    mutation: Callable[[dict[str, object]], None],
) -> ValidationGenerationRecord:
    record, _ = generation_record()
    snapshot = deepcopy(record.run.context_snapshot)
    mutation(snapshot)
    record.run.context_snapshot = snapshot
    _refingerprint(record.run)
    return record


@pytest.mark.parametrize(
    "mutation",
    [
        lambda snapshot: snapshot.update({"trust": "trusted"}),
        lambda snapshot: cast(list[dict[str, object]], snapshot["items"])[0].update(
            {"trust": "trusted"}
        ),
        lambda snapshot: cast(list[dict[str, object]], snapshot["items"])[0].update(
            {"context_id": "knowledge_chunk:wrong"}
        ),
        lambda snapshot: cast(
            dict[str, object],
            cast(list[dict[str, object]], snapshot["items"])[0]["provenance"],
        ).update({"chunk_id": str(QUESTION_ID)}),
        lambda snapshot: cast(list[dict[str, object]], snapshot["items"])[0].update(
            {"record_id": str(UUID(int=999_003))}
        ),
        lambda snapshot: cast(list[dict[str, object]], snapshot["items"]).append(
            deepcopy(cast(list[dict[str, object]], snapshot["items"])[0])
        ),
    ],
)
def test_reconstruction_rejects_tampered_context_identity(
    mutation: Callable[[dict[str, object]], None],
) -> None:
    with pytest.raises(ValidationGenerationIntegrityError):
        reconstruct_generation_result(_mutate_context(mutation))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda snapshot: snapshot.update({"extra": True}),
        lambda snapshot: snapshot.update({"items": "not-an-array"}),
        lambda snapshot: cast(list[object], snapshot["items"]).__setitem__(0, "bad-item"),
        lambda snapshot: cast(list[dict[str, object]], snapshot["items"])[0].update(
            {"provenance": "bad"}
        ),
        lambda snapshot: cast(list[dict[str, object]], snapshot["items"])[0].update(
            {"taxonomy": "bad"}
        ),
        lambda snapshot: cast(list[dict[str, object]], snapshot["items"])[0].update(
            {"learning_scope": "bad"}
        ),
        lambda snapshot: cast(list[dict[str, object]], snapshot["items"])[0].update(
            {
                "learning_scope": {
                    "unit_id": None,
                    "lesson_id": str(UUID(int=999_001)),
                }
            }
        ),
        lambda snapshot: cast(list[dict[str, object]], snapshot["items"])[0].update(
            {"record_kind": 1}
        ),
        lambda snapshot: cast(list[dict[str, object]], snapshot["items"])[0].update({"text": 1}),
        lambda snapshot: cast(
            dict[str, object],
            cast(list[dict[str, object]], snapshot["items"])[0]["provenance"],
        ).update({"page_number": True}),
    ],
)
def test_reconstruction_rejects_malformed_context_shape(
    mutation: Callable[[dict[str, object]], None],
) -> None:
    with pytest.raises(ValidationGenerationIntegrityError):
        reconstruct_generation_result(_mutate_context(mutation))


def test_low_level_reconstruction_guards_reject_wrong_scalar_container_types() -> None:
    with pytest.raises(ValidationGenerationIntegrityError):
        _object([], keys=frozenset(), label="fixture")
    with pytest.raises(ValidationGenerationIntegrityError):
        _object({1: "value"}, keys=frozenset({"1"}), label="fixture")
    with pytest.raises(ValidationGenerationIntegrityError):
        _array("text", label="fixture")
    with pytest.raises(ValidationGenerationIntegrityError):
        _text(1, label="fixture")
    with pytest.raises(ValidationGenerationIntegrityError):
        _integer(True, label="fixture")
    assert _optional_text(None, label="fixture") is None
    assert _optional_text("value", label="fixture") == "value"


def valid_candidate() -> dict[str, object]:
    record, _ = generation_record()
    return deepcopy(cast(dict[str, object], record.run.candidate))


@pytest.mark.parametrize(
    "candidate",
    [
        [],
        {**valid_candidate(), "extra": True},
        {**valid_candidate(), "options": "bad"},
        {**valid_candidate(), "options": ["bad"]},
        {**valid_candidate(), "question_type": "essay"},
        {**valid_candidate(), "stem": 1},
        {**valid_candidate(), "answer": "bad"},
        {
            **valid_candidate(),
            "answer": {
                "explanation": "answer",
                "correct_option_id": "B",
                "accepted_responses": "bad",
            },
        },
        {**valid_candidate(), "marking": "bad"},
        {
            **valid_candidate(),
            "marking": {"total_marks": 2, "criteria": ["bad"]},
        },
    ],
)
def test_question_snapshot_parser_fails_closed(candidate: object) -> None:
    with pytest.raises(ValidationGenerationIntegrityError):
        _question_from_snapshot(candidate)


def test_reconstruction_rejects_fingerprint_blueprint_slot_and_candidate_tampering() -> None:
    record, _ = generation_record()
    record.run.request_fingerprint = "sha256:" + "0" * 64
    with pytest.raises(ValidationGenerationIntegrityError, match="request fingerprint"):
        reconstruct_generation_result(record)

    record, _ = generation_record()
    record.run.curriculum_version_id = UUID(int=999_004)
    _refingerprint(record.run)
    with pytest.raises(ValidationGenerationIntegrityError, match="blueprint scope"):
        reconstruct_generation_result(record)

    record, _ = generation_record()
    record.run.blueprint_version = "wrong-version"
    _refingerprint(record.run)
    with pytest.raises(ValidationGenerationIntegrityError, match="blueprint scope"):
        reconstruct_generation_result(record)

    record, _ = generation_record()
    record.run.slot_id = "missing-slot"
    record.run.blueprint_slot_snapshot = {"slot_id": "missing-slot"}
    _refingerprint(record.run)
    with pytest.raises(ValidationGenerationIntegrityError, match="slot is absent"):
        reconstruct_generation_result(record)

    record, _ = generation_record()
    record.run.blueprint_slot_snapshot = {**record.run.blueprint_slot_snapshot, "marks": 99}
    _refingerprint(record.run)
    with pytest.raises(ValidationGenerationIntegrityError, match="slot snapshot"):
        reconstruct_generation_result(record)

    record, _ = generation_record()
    assert record.run.candidate is not None
    record.run.candidate = {**record.run.candidate, "extra": True}
    assert record.attempt is not None
    record.attempt.candidate = deepcopy(record.run.candidate)
    with pytest.raises(ValidationGenerationIntegrityError, match="candidate"):
        reconstruct_generation_result(record)

    record, _ = generation_record()
    record.run.blueprint_snapshot = {"invalid": True}
    record.run.blueprint_slot_snapshot = {"slot_id": record.run.slot_id}
    _refingerprint(record.run)
    with pytest.raises(ValidationGenerationIntegrityError, match="reconstructed"):
        reconstruct_generation_result(record)


def test_duplicate_snapshot_and_resource_helpers_are_bounded_and_versioned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oversized = "x" * 16_001
    historical = DuplicateReferenceRecord(
        reference_kind="historical",
        record_id=QUESTION_ID,
        text=oversized,
        record_version="historical-question.v4",
        source_document_id=SOURCE_A_ID,
        source_page_number=3,
    )
    generated = DuplicateReferenceRecord(
        reference_kind="generated",
        record_id=RUN_ID,
        text="Generated stem",
        record_version="generation-result:abc",
        validation_run_id=UUID(int=980_020),
        generation_run_id=RUN_ID,
        report_fingerprint="a" * 64,
        pipeline_version="pipeline.v1",
    )

    references, provenance = _duplicate_references((historical, generated))

    assert references[0].text is None
    assert references[0].content_sha256 is not None
    assert provenance[references[0].question_id]["source_document_id"] == str(SOURCE_A_ID)
    assert provenance[references[1].question_id]["validation_run_id"] == str(UUID(int=980_020))
    assert _plain_json({"values": (1, None, "x")}) == {"values": [1, None, "x"]}
    with pytest.raises(TypeError):
        _plain_json({1, 2})

    record, result = generation_record()
    validation_input = adapt_generation_result(
        cast(Any, result),
        requirements=BlueprintRequirements(
            slot_id=result.request.blueprint_slot.slot_id,
            schema_version=result.request.versions.schema_version,
            question_type=result.request.blueprint_slot.question_type.value,
            marks=result.request.blueprint_slot.marks,
            language=result.request.blueprint_slot.generation_constraints.response_language,
            minimum_age=9,
            maximum_age=11,
        ),
        duplicate_references=references,
    )
    snapshot = _input_snapshot(record.run, validation_input, provenance)
    assert snapshot["trust"] == "server_reconstructed"
    monkeypatch.setattr(validation_service, "MAX_VALIDATION_INPUT_SNAPSHOT_BYTES", 1)
    with pytest.raises(ValidationResourceLimitError, match="snapshot"):
        _input_snapshot(record.run, validation_input, provenance)


def custom_findings(count: int, *, evidence_count: int = 1) -> tuple[ValidationFinding, ...]:
    return tuple(
        ValidationFinding(
            validator_id="fixture-validator",
            validator_version="1.0.0",
            code=f"fixture.code-{index}",
            status=FindingStatus.PASS,
            message="Fixture finding.",
            evidence=tuple(
                FindingEvidence("$", "expected", f"observed-{evidence_index}")
                for evidence_index in range(evidence_count)
            ),
        )
        for index in range(count)
    )


def test_finding_models_enforce_service_resource_bounds() -> None:
    with pytest.raises(ValidationResourceLimitError, match="finding count"):
        _finding_models(
            UUID(int=1),
            ValidationReport(
                candidate_id="candidate",
                pipeline_version="pipeline.v1",
                findings=custom_findings(MAX_VALIDATION_FINDINGS + 1),
            ),
        )
    with pytest.raises(ValidationResourceLimitError, match="evidence count"):
        _finding_models(
            UUID(int=1),
            ValidationReport(
                candidate_id="candidate",
                pipeline_version="pipeline.v1",
                findings=custom_findings(
                    1,
                    evidence_count=MAX_VALIDATION_EVIDENCE_PER_FINDING + 1,
                ),
            ),
        )
    report = ValidationReport(
        candidate_id="candidate",
        pipeline_version="pipeline.v1",
        findings=custom_findings(1),
    )
    models = _finding_models(UUID(int=1), report)
    assert len(models) == 1
    assert models[0].evidence_count == 1


def persisted_validation_report() -> tuple[
    ValidationRunModel,
    tuple[ValidationFindingModel, ...],
    ValidationReport,
]:
    report = ValidationReport(
        candidate_id="a" * 64,
        pipeline_version="pipeline.v1",
        findings=custom_findings(2),
    )
    run_id = UUID(int=980_090)
    findings = _finding_models(run_id, report)
    run = ValidationRunModel(
        id=run_id,
        generation_result_fingerprint=report.candidate_id,
        pipeline_version=report.pipeline_version,
        report_schema_version=report.report_schema_version,
        report_fingerprint=report.report_fingerprint,
        overall_status=report.overall_status.value,
        finding_count=len(report.findings),
        validator_count=1,
        validator_lineage=[
            {
                "validator_id": "fixture-validator",
                "validator_version": "1.0.0",
            }
        ],
        limitations=list(report.limitations),
    )
    return run, findings, report


def test_reconstructs_canonical_validation_report_with_actual_finding_ids() -> None:
    run, findings, expected = persisted_validation_report()

    reconstructed = reconstruct_validation_report(run, findings)

    assert reconstructed.report == expected
    assert reconstructed.finding_ids == tuple(finding.id for finding in findings)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("report_fingerprint", "0" * 64),
        ("overall_status", "warn"),
        ("finding_count", 1),
        ("validator_count", 2),
        ("report_schema_version", "question-validation-report.v999"),
        ("limitations", ["forged limitation"]),
    ],
)
def test_validation_report_reconstruction_rejects_tampered_run_metadata(
    field_name: str,
    value: object,
) -> None:
    run, findings, _report = persisted_validation_report()
    setattr(run, field_name, value)

    with pytest.raises(ValidationReportIntegrityError):
        reconstruct_validation_report(run, findings)


def test_validation_report_reconstruction_rejects_malformed_persisted_boundaries() -> None:
    run, findings, _report = persisted_validation_report()
    with pytest.raises(ValidationReportIntegrityError, match="invalid type"):
        reconstruct_validation_report(cast(ValidationRunModel, object()), findings)

    run, findings, _report = persisted_validation_report()
    findings[0].validation_run_id = UUID(int=123)
    with pytest.raises(ValidationReportIntegrityError, match="run identity"):
        reconstruct_validation_report(run, findings)

    run, findings, _report = persisted_validation_report()
    findings[1].id = findings[0].id
    with pytest.raises(ValidationReportIntegrityError, match="identities"):
        reconstruct_validation_report(run, findings)

    run, findings, _report = persisted_validation_report()
    findings[0].evidence = [
        {
            "location": "$",
            "expected": "expected",
            "observed": "observed",
            "extra": "forged",
        }
    ]
    with pytest.raises(ValidationReportIntegrityError, match="evidence shape"):
        reconstruct_validation_report(run, findings)

    run, findings, _report = persisted_validation_report()
    findings[0].status = "invalid"
    with pytest.raises(ValidationReportIntegrityError, match="canonically"):
        reconstruct_validation_report(run, findings)

    run, findings, _report = persisted_validation_report()
    findings[0].code, findings[1].code = findings[1].code, findings[0].code
    with pytest.raises(ValidationReportIntegrityError, match="order"):
        reconstruct_validation_report(run, findings)

    run, findings, _report = persisted_validation_report()
    run.validator_lineage = [
        {
            "validator_id": "fixture-validator",
            "validator_version": "1.0.0",
            "extra": "forged",
        }
    ]
    with pytest.raises(ValidationReportIntegrityError, match="lineage shape"):
        reconstruct_validation_report(run, findings)


def test_validation_report_reconstruction_rejects_incomplete_lineage_and_findings() -> None:
    run, findings, _report = persisted_validation_report()
    run.validator_lineage = [
        {
            "validator_id": "fixture-validator",
            "validator_version": "forged-version",
        }
    ]
    with pytest.raises(ValidationReportIntegrityError, match="lineage"):
        reconstruct_validation_report(run, findings)

    run, findings, _report = persisted_validation_report()
    with pytest.raises(ValidationReportIntegrityError, match=r"count|contiguous"):
        reconstruct_validation_report(run, findings[:-1])

    run, findings, _report = persisted_validation_report()
    findings[0].ordinal = 1
    with pytest.raises(ValidationReportIntegrityError, match="contiguous"):
        reconstruct_validation_report(run, findings)

    run, findings, _report = persisted_validation_report()
    findings[0].evidence_count = 2
    with pytest.raises(ValidationReportIntegrityError, match="evidence"):
        reconstruct_validation_report(run, findings)


class FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.commits = 0

    def add(self, value: object) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commits += 1


class FakeRepository:
    def __init__(
        self,
        record: ValidationGenerationRecord,
        *,
        existing: ValidationRunModel | None = None,
        stored_created: bool = True,
        curriculum_exists: bool = True,
    ) -> None:
        self.record = record
        self.existing = existing
        self.stored_created = stored_created
        self.has_curriculum = curriculum_exists
        self.stored_findings: tuple[ValidationFindingModel, ...] = ()
        self.run_values: dict[str, object] | None = None
        self.listed_run = existing or ValidationRunModel(id=UUID(int=980_100))
        self.listed_finding = ValidationFindingModel(id=UUID(int=980_101))

    async def get_generation(self, curriculum_id: UUID, generation_id: UUID) -> object:
        assert curriculum_id == CURRICULUM_ID
        assert generation_id == RUN_ID
        return self.record

    async def get_for_generation_pipeline(
        self,
        generation_id: UUID,
        pipeline_version: str,
    ) -> ValidationRunModel | None:
        assert generation_id == RUN_ID
        assert pipeline_version
        return self.existing

    async def list_duplicate_references(
        self,
        curriculum_id: UUID,
        *,
        exclude_generation_run_id: UUID,
        limit: int,
    ) -> tuple[DuplicateReferenceRecord, ...]:
        assert curriculum_id == CURRICULUM_ID
        assert exclude_generation_run_id == RUN_ID
        assert limit == MAX_VALIDATION_DUPLICATE_REFERENCES
        return (
            DuplicateReferenceRecord(
                reference_kind="historical",
                record_id=UUID(int=980_110),
                text="A different historical question",
                record_version="historical-question.v1",
            ),
        )

    async def store_report(
        self,
        run_values: dict[str, object],
        findings: tuple[ValidationFindingModel, ...],
    ) -> StoredValidationReport:
        self.run_values = run_values
        self.stored_findings = findings
        run = ValidationRunModel(**run_values, created_at=NOW)
        return StoredValidationReport(run, self.stored_created)

    async def curriculum_exists(self, curriculum_id: UUID) -> bool:
        assert curriculum_id == CURRICULUM_ID
        return self.has_curriculum

    async def get_run(self, curriculum_id: UUID, run_id: UUID) -> ValidationRunModel:
        del curriculum_id, run_id
        return self.listed_run

    async def list_runs(
        self,
        curriculum_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[ValidationRunModel, ...]:
        del curriculum_id, limit, offset
        return (self.listed_run,)

    async def list_findings(
        self,
        curriculum_id: UUID,
        run_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[ValidationFindingModel, ...]:
        del curriculum_id, run_id, limit, offset
        return (self.listed_finding,)


def service_with_fake(
    repository: FakeRepository,
    *,
    pipeline: ValidationPipeline | None = None,
) -> tuple[ValidationRunService, FakeSession]:
    session = FakeSession()
    service = ValidationRunService(
        cast(AsyncSession, session),
        pipeline or build_default_pipeline(),
    )
    service._repository = cast(Any, repository)
    return service, session


def test_validation_service_creates_audits_and_delegates_bounded_reads() -> None:
    async def exercise() -> None:
        record, _ = generation_record()
        repository = FakeRepository(record)
        service, session = service_with_fake(repository)
        operational, telemetry_logger, _tracer = telemetry()
        service._telemetry = operational

        created = await service.create(
            CURRICULUM_ID,
            generation_run_id=RUN_ID,
            actor_id=ACTOR_ID,
        )

        assert created.deduplicated is False
        assert created.run.overall_status == "pass"
        assert len(repository.stored_findings) == 13
        assert repository.run_values is not None
        assert repository.run_values["generation_result_fingerprint"]
        assert len(session.added) == 1
        assert session.commits == 1
        assert telemetry_logger.records == [
            (
                "Operational event",
                {
                    "event_name": "validation.creation",
                    "outcome": "succeeded",
                    "failure_code": None,
                    "overall_status": "pass",
                    "finding_count": 13,
                    "deduplicated": False,
                },
            )
        ]
        assert await service.get_run(CURRICULUM_ID, created.run.id) is repository.listed_run
        assert await service.list_runs(CURRICULUM_ID, limit=10, offset=0) == (
            repository.listed_run,
        )
        assert await service.list_findings(
            CURRICULUM_ID,
            created.run.id,
            limit=10,
            offset=0,
        ) == (repository.listed_finding,)

        missing_repository = FakeRepository(record, curriculum_exists=False)
        missing_service, _ = service_with_fake(missing_repository)
        with pytest.raises(ValidationCurriculumNotFoundError):
            await missing_service.list_runs(CURRICULUM_ID, limit=10, offset=0)

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ValidationCurriculumNotFoundError(), "validation_curriculum_not_found"),
        (ValidationGenerationNotSucceededError(), "validation_generation_not_succeeded"),
        (ValidationGenerationIntegrityError(), "validation_generation_integrity"),
        (ValidationPipelineVersionConflictError(), "validation_pipeline_conflict"),
        (ValidationIdempotencyConflictError(), "validation_idempotency_conflict"),
        (ValidationResourceLimitError(), "validation_resource_limit"),
        (ValidationReportIntegrityError(), "validation_report_integrity"),
        (RuntimeError("raw candidate secret"), "validation_internal_error"),
    ],
)
def test_validation_creation_failure_codes_are_fixed(error: Exception, code: str) -> None:
    assert _validation_creation_failure_code(error) == code
    assert "secret" not in code


def test_validation_service_sanitizes_failed_creation_telemetry() -> None:
    async def exercise() -> None:
        record, _ = generation_record()
        service, _session = service_with_fake(FakeRepository(record))
        operational, telemetry_logger, _tracer = telemetry()
        service._telemetry = operational

        async def crash(*args: object, **kwargs: object) -> object:
            del args, kwargs
            raise RuntimeError("raw candidate and source secret")

        service._repository.get_generation = crash  # type: ignore[assignment]
        with pytest.raises(RuntimeError, match="raw candidate"):
            await service.create(
                CURRICULUM_ID,
                generation_run_id=RUN_ID,
                actor_id=ACTOR_ID,
            )

        assert telemetry_logger.records == [
            (
                "Operational event",
                {
                    "event_name": "validation.creation",
                    "outcome": "failed",
                    "failure_code": "validation_internal_error",
                    "overall_status": None,
                    "finding_count": 0,
                    "deduplicated": False,
                },
            )
        ]
        assert "source secret" not in str(telemetry_logger.records)

    asyncio.run(exercise())


def test_validation_service_deduplicates_existing_and_race_winner_reports() -> None:
    async def exercise() -> None:
        record, result = generation_record()
        fingerprint = generation_result_fingerprint(cast(Any, result))
        pipeline = build_default_pipeline()
        existing = ValidationRunModel(
            id=UUID(int=980_120),
            curriculum_version_id=CURRICULUM_ID,
            generation_run_id=RUN_ID,
            pipeline_version=pipeline.version,
            pipeline_fingerprint=pipeline.pipeline_fingerprint,
            generation_result_fingerprint=fingerprint,
        )
        existing_service, session = service_with_fake(
            FakeRepository(record, existing=existing),
            pipeline=pipeline,
        )
        duplicate = await existing_service.create(
            CURRICULUM_ID,
            generation_run_id=RUN_ID,
            actor_id=ACTOR_ID,
        )
        assert duplicate == validation_service.ValidationCreationResult(existing, True)
        assert session.commits == 0

        race_repository = FakeRepository(record, stored_created=False)
        race_service, race_session = service_with_fake(race_repository, pipeline=pipeline)
        race = await race_service.create(
            CURRICULUM_ID,
            generation_run_id=RUN_ID,
            actor_id=ACTOR_ID,
        )
        assert race.deduplicated is True
        assert race_session.added == []
        assert race_session.commits == 1

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "field_name",
    ["curriculum_version_id", "pipeline_fingerprint", "generation_result_fingerprint"],
)
def test_existing_validation_pipeline_identity_must_match(field_name: str) -> None:
    record, result = generation_record()
    pipeline = build_default_pipeline()
    existing = ValidationRunModel(
        id=UUID(int=980_130),
        curriculum_version_id=CURRICULUM_ID,
        pipeline_fingerprint=pipeline.pipeline_fingerprint,
        generation_result_fingerprint=generation_result_fingerprint(cast(Any, result)),
    )
    setattr(existing, field_name, "wrong" if field_name != "curriculum_version_id" else UUID(int=1))
    service, _ = service_with_fake(FakeRepository(record), pipeline=pipeline)

    with pytest.raises(ValidationPipelineVersionConflictError):
        service._assert_existing(
            existing,
            curriculum_version_id=CURRICULUM_ID,
            generation_fingerprint=generation_result_fingerprint(cast(Any, result)),
        )


def test_stored_validation_identity_and_snapshots_must_match() -> None:
    expected: dict[str, object] = {
        "id": UUID(int=1),
        "curriculum_version_id": CURRICULUM_ID,
        "generation_run_id": RUN_ID,
        "generation_attempt_id": ATTEMPT_ID,
        "pipeline_version": "pipeline.v1",
        "pipeline_fingerprint": "a" * 64,
        "input_schema_version": "input.v1",
        "report_schema_version": "report.v1",
        "generation_result_fingerprint": "b" * 64,
        "input_fingerprint": "c" * 64,
        "candidate_fingerprint": "d" * 64,
        "report_fingerprint": "e" * 64,
        "overall_status": "pass",
        "finding_count": 1,
        "validator_count": 1,
        "grounding_source_count": 1,
        "duplicate_reference_count": 0,
        "input_snapshot": {"input": True},
        "validator_lineage": [{"validator_id": "v", "validator_version": "1"}],
        "limitations": ["limit"],
    }
    model = ValidationRunModel(**expected)
    ValidationRunService._assert_stored(StoredValidationReport(model, True), expected)

    scalar_mismatch = ValidationRunModel(**expected)
    scalar_mismatch.report_fingerprint = "f" * 64
    with pytest.raises(ValidationIdempotencyConflictError):
        ValidationRunService._assert_stored(
            StoredValidationReport(scalar_mismatch, False),
            expected,
        )
    snapshot_mismatch = ValidationRunModel(**expected)
    snapshot_mismatch.limitations = ["changed"]
    with pytest.raises(ValidationIdempotencyConflictError):
        ValidationRunService._assert_stored(
            StoredValidationReport(snapshot_mismatch, False),
            expected,
        )


def test_validation_service_rejects_invalid_pipeline_boundary_and_validator_limit() -> None:
    record, _ = generation_record()
    with pytest.raises(TypeError):
        ValidationRunService(cast(AsyncSession, FakeSession()), cast(ValidationPipeline, object()))

    class ManyValidator:
        def __init__(self, index: int) -> None:
            self.validator_id = f"validator-{index}"
            self.validator_version = "1.0.0"

        def validate(self, validation_input: object) -> tuple[ValidationFinding, ...]:
            del validation_input
            return custom_findings(1)

    too_many = ValidationPipeline(
        cast(
            Any,
            tuple(ManyValidator(index) for index in range(MAX_VALIDATION_VALIDATORS + 1)),
        ),
        "too-many.v1",
    )
    with pytest.raises(ValidationResourceLimitError, match="validator count"):
        service_with_fake(FakeRepository(record), pipeline=too_many)


def test_service_rejects_a_pipeline_report_with_foreign_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        record, _ = generation_record()
        service, _ = service_with_fake(FakeRepository(record))
        original_validate = ValidationPipeline.validate

        def foreign_report(
            pipeline: ValidationPipeline,
            validation_input: object,
        ) -> ValidationReport:
            report = original_validate(pipeline, cast(Any, validation_input))
            object.__setattr__(report, "candidate_id", "foreign-candidate")
            return report

        monkeypatch.setattr(ValidationPipeline, "validate", foreign_report)
        with pytest.raises(ValidationGenerationIntegrityError, match="report binding"):
            await service.create(
                CURRICULUM_ID,
                generation_run_id=RUN_ID,
                actor_id=ACTOR_ID,
            )

    asyncio.run(exercise())


def test_service_wraps_adapter_errors_and_rejects_adapter_fingerprint_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        record, _ = generation_record()
        service, _ = service_with_fake(FakeRepository(record))

        def reject_adapter(*args: object, **kwargs: object) -> object:
            del args, kwargs
            raise GenerationAdapterError("fixture")

        monkeypatch.setattr(validation_service, "adapt_generation_result", reject_adapter)
        with pytest.raises(ValidationGenerationIntegrityError, match="adapter"):
            await service.create(
                CURRICULUM_ID,
                generation_run_id=RUN_ID,
                actor_id=ACTOR_ID,
            )

    asyncio.run(exercise())

    async def mismatch() -> None:
        record, _ = generation_record()
        service, _ = service_with_fake(FakeRepository(record))
        original = adapt_generation_result

        def mismatching_adapter(*args: object, **kwargs: object) -> object:
            validation_input = original(*args, **kwargs)  # type: ignore[arg-type]
            object.__setattr__(validation_input, "candidate_id", "wrong-fingerprint")
            return validation_input

        monkeypatch.setattr(validation_service, "adapt_generation_result", mismatching_adapter)
        with pytest.raises(ValidationGenerationIntegrityError, match="survive adaptation"):
            await service.create(
                CURRICULUM_ID,
                generation_run_id=RUN_ID,
                actor_id=ACTOR_ID,
            )

    asyncio.run(mismatch())

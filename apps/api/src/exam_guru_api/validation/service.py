from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.blueprints.domain import QuestionType
from exam_guru_api.blueprints.serialization import BlueprintSnapshotError, deserialize_blueprint
from exam_guru_api.generation.domain import (
    CandidateDisposition,
    ContextProvenance,
    GeneratedQuestion,
    GenerationAccounting,
    GenerationContractError,
    GenerationIdentity,
    GenerationParameters,
    GenerationRequest,
    GenerationResult,
    GenerationVersions,
    MarkingCriterion,
    MarkingScheme,
    ProvenanceContext,
    QuestionAnswer,
    QuestionOption,
    RetrievedContextItem,
)
from exam_guru_api.generation.models import (
    GenerationAttemptModel,
    GenerationAttemptStatus,
    GenerationRunModel,
    GenerationRunStatus,
)
from exam_guru_api.observability import OperationalTelemetry, get_operational_telemetry
from exam_guru_api.retrieval.domain import RetrievalContractError as RetrievalScopeError
from exam_guru_api.retrieval.domain import (
    RetrievalFilters,
    RetrievalScopeSet,
    deserialize_retrieval_filters,
    deserialize_retrieval_scope,
)
from exam_guru_api.validation.domain import (
    MAX_DUPLICATE_TEXT_CHARACTERS,
    REPORT_SCHEMA_VERSION,
    BlueprintRequirements,
    ContextScopeBinding,
    DuplicateReference,
    FindingEvidence,
    FindingStatus,
    TrustedSubjectScope,
    ValidationContractError,
    ValidationFinding,
    ValidationInput,
    ValidationReport,
    canonical_text_sha256,
)
from exam_guru_api.validation.domain import (
    grade_age_bounds as domain_grade_age_bounds,
)
from exam_guru_api.validation.generation_adapter import (
    GenerationAdapterError,
    adapt_generation_result,
    generation_result_fingerprint,
)
from exam_guru_api.validation.models import (
    MAX_VALIDATION_DUPLICATE_REFERENCES,
    MAX_VALIDATION_EVIDENCE_PER_FINDING,
    MAX_VALIDATION_FINDINGS,
    MAX_VALIDATION_INPUT_SNAPSHOT_BYTES,
    MAX_VALIDATION_VALIDATORS,
    ValidationFindingModel,
    ValidationRunModel,
)
from exam_guru_api.validation.pipeline import ValidationPipeline
from exam_guru_api.validation.repository import (
    DuplicateReferenceRecord,
    SqlAlchemyValidationRepository,
    StoredValidationReport,
    ValidationGenerationRecord,
)

VALIDATION_INPUT_SCHEMA_VERSION = "question-validation-input.v3"
_VALIDATION_NAMESPACE = uuid5(NAMESPACE_URL, "exam-guru/validation-runs")


class ValidationCurriculumNotFoundError(LookupError):
    pass


class ValidationGenerationNotSucceededError(RuntimeError):
    pass


class ValidationGenerationIntegrityError(RuntimeError):
    pass


class ValidationPipelineVersionConflictError(RuntimeError):
    pass


class ValidationIdempotencyConflictError(RuntimeError):
    pass


class ValidationResourceLimitError(RuntimeError):
    pass


class ValidationReportIntegrityError(RuntimeError):
    pass


def _validation_creation_failure_code(error: Exception) -> str:
    if isinstance(error, ValidationCurriculumNotFoundError):
        return "validation_curriculum_not_found"
    if isinstance(error, ValidationGenerationNotSucceededError):
        return "validation_generation_not_succeeded"
    if isinstance(error, ValidationGenerationIntegrityError):
        return "validation_generation_integrity"
    if isinstance(error, ValidationPipelineVersionConflictError):
        return "validation_pipeline_conflict"
    if isinstance(error, ValidationIdempotencyConflictError):
        return "validation_idempotency_conflict"
    if isinstance(error, ValidationResourceLimitError):
        return "validation_resource_limit"
    if isinstance(error, ValidationReportIntegrityError):
        return "validation_report_integrity"
    return "validation_internal_error"


@dataclass(frozen=True, slots=True)
class ReconstructedValidationReport:
    report: ValidationReport
    finding_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class ValidationCreationResult:
    run: ValidationRunModel
    deduplicated: bool


def _fingerprint(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def grade_age_bounds(grade: int) -> tuple[int, int]:
    try:
        return domain_grade_age_bounds(grade)
    except ValidationContractError as error:
        raise ValidationGenerationIntegrityError(
            "blueprint grade must be between 1 and 13"
        ) from error


def _request_fingerprint_payload(run: GenerationRunModel) -> dict[str, object]:
    return {
        "paper_blueprint_id": str(run.paper_blueprint_id),
        "blueprint_version": run.blueprint_version,
        "blueprint_snapshot": run.blueprint_snapshot,
        "slot_id": run.slot_id,
        "blueprint_slot_snapshot": run.blueprint_slot_snapshot,
        "knowledge_chunk_ids": run.knowledge_chunk_ids,
        "historical_question_ids": run.historical_question_ids,
        "context_snapshot": run.context_snapshot,
        "versions": {
            "prompt_id": run.prompt_id,
            "prompt_version": run.prompt_version,
            "provider": run.provider,
            "provider_version": run.provider_version,
            "model": run.model,
            "model_version": run.model_version,
            "retrieval_version": run.retrieval_version,
            "schema_version": run.schema_version,
            "pricing_version": run.pricing_version,
        },
        "pricing": {
            "input_microusd_per_million_tokens": run.input_microusd_per_million_tokens,
            "output_microusd_per_million_tokens": run.output_microusd_per_million_tokens,
        },
        "parameters": run.generation_parameters,
        "budgets": {
            "max_attempts": run.max_attempts,
            "max_input_tokens": run.max_input_tokens,
            "max_output_tokens": run.max_output_tokens,
            "max_cost_microusd": run.max_cost_microusd,
        },
    }


def _object(value: object, *, keys: frozenset[str], label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValidationGenerationIntegrityError(f"{label} must be an object")
    if frozenset(value.keys()) != keys or any(not isinstance(key, str) for key in value):
        raise ValidationGenerationIntegrityError(f"{label} has an invalid shape")
    return cast(Mapping[str, object], value)


def _array(value: object, *, label: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValidationGenerationIntegrityError(f"{label} must be an array")
    return cast(Sequence[object], value)


def _text(value: object, *, label: str) -> str:
    if not isinstance(value, str):
        raise ValidationGenerationIntegrityError(f"{label} must be text")
    return value


def _integer(value: object, *, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationGenerationIntegrityError(f"{label} must be an integer")
    return value


def _optional_text(value: object, *, label: str) -> str | None:
    return None if value is None else _text(value, label=label)


def _context_snapshot_root(
    snapshot: object,
) -> tuple[Mapping[str, object], RetrievalFilters | None]:
    raw_keys = frozenset(snapshot) if isinstance(snapshot, Mapping) else frozenset()
    keys = (
        frozenset({"items", "trust", "retrieval_filters"})
        if "retrieval_filters" in raw_keys
        else frozenset({"items", "trust"})
    )
    root = _object(snapshot, keys=keys, label="generation context")
    if "retrieval_filters" not in root:
        return root, None
    try:
        filters = deserialize_retrieval_filters(root["retrieval_filters"])
    except RetrievalScopeError as error:
        raise ValidationGenerationIntegrityError(
            "generation retrieval filters are invalid"
        ) from error
    return root, filters


def _context_from_snapshot(run: GenerationRunModel) -> ProvenanceContext:
    root, retrieval_filters = _context_snapshot_root(run.context_snapshot)
    if root["trust"] != "untrusted_data":
        raise ValidationGenerationIntegrityError("generation context trust marker is invalid")
    raw_items = _array(root["items"], label="generation context items")
    expected_records = {
        *(("knowledge_chunk", value) for value in run.knowledge_chunk_ids),
        *(("historical_question", value) for value in run.historical_question_ids),
    }
    observed_records: set[tuple[str, str]] = set()
    items: list[RetrievedContextItem] = []
    legacy_item_keys = frozenset(
        {
            "context_id",
            "record_kind",
            "record_id",
            "record_version",
            "text",
            "trust",
            "provenance",
            "taxonomy",
        }
    )
    for index, raw_item in enumerate(raw_items):
        raw_item_keys = frozenset(raw_item) if isinstance(raw_item, Mapping) else frozenset()
        item_keys = legacy_item_keys
        if "learning_scope" in raw_item_keys:
            item_keys |= {"learning_scope"}
        if "retrieval_scope" in raw_item_keys:
            item_keys |= {"retrieval_scope"}
        item = _object(
            raw_item,
            keys=item_keys,
            label=f"generation context item {index}",
        )
        provenance = _object(
            item["provenance"],
            keys=frozenset(
                {
                    "source_document_id",
                    "source_version",
                    "page_number",
                    "chunk_id",
                    "source_block_id",
                }
            ),
            label=f"generation context provenance {index}",
        )
        taxonomy = _object(
            item["taxonomy"],
            keys=frozenset({"competency_id", "skill_id", "sub_skill_id", "learning_concept_id"}),
            label=f"generation context taxonomy {index}",
        )
        unit_id: str | None = None
        lesson_id: str | None = None
        if "learning_scope" in item:
            learning_scope = _object(
                item["learning_scope"],
                keys=frozenset({"unit_id", "lesson_id"}),
                label=f"generation context learning scope {index}",
            )
            unit_id = _optional_text(learning_scope["unit_id"], label="unit_id")
            lesson_id = _optional_text(learning_scope["lesson_id"], label="lesson_id")
            if lesson_id is not None and unit_id is None:
                raise ValidationGenerationIntegrityError(
                    "generation context lesson scope requires a unit"
                )
        raw_retrieval_scope = item.get("retrieval_scope")
        if retrieval_filters is not None:
            try:
                retrieval_scope = deserialize_retrieval_scope(raw_retrieval_scope)
            except RetrievalScopeError as error:
                raise ValidationGenerationIntegrityError(
                    "generation context retrieval scope is invalid"
                ) from error
            expected_taxonomy = {
                "competency_id": str(retrieval_scope.taxonomy.competency_id),
                "skill_id": (
                    None
                    if retrieval_scope.taxonomy.skill_id is None
                    else str(retrieval_scope.taxonomy.skill_id)
                ),
                "sub_skill_id": (
                    None
                    if retrieval_scope.taxonomy.sub_skill_id is None
                    else str(retrieval_scope.taxonomy.sub_skill_id)
                ),
                "learning_concept_id": (
                    None
                    if retrieval_scope.taxonomy.learning_concept_id is None
                    else str(retrieval_scope.taxonomy.learning_concept_id)
                ),
            }
            if (
                not retrieval_filters.allows(retrieval_scope)
                or dict(taxonomy) != expected_taxonomy
                or tuple(() if unit_id is None else (unit_id,))
                != tuple(str(value) for value in retrieval_scope.unit_ids)
                or tuple(() if lesson_id is None else (lesson_id,))
                != tuple(str(value) for value in retrieval_scope.lesson_ids)
            ):
                raise ValidationGenerationIntegrityError(
                    "generation context retrieval scope is inconsistent"
                )
        elif raw_retrieval_scope is not None:
            raise ValidationGenerationIntegrityError(
                "generation context retrieval scope requires declared filters"
            )
        record_kind = _text(item["record_kind"], label="record_kind")
        record_id = _text(item["record_id"], label="record_id")
        context_id = _text(item["context_id"], label="context_id")
        if (
            item["trust"] != "untrusted_data"
            or context_id != f"{record_kind}:{record_id}"
            or provenance["chunk_id"] != record_id
        ):
            raise ValidationGenerationIntegrityError("generation context identity is inconsistent")
        observed_records.add((record_kind, record_id))
        items.append(
            RetrievedContextItem(
                context_id=context_id,
                text=_text(item["text"], label="context text"),
                provenance=ContextProvenance(
                    source_document_id=_text(
                        provenance["source_document_id"],
                        label="source_document_id",
                    ),
                    source_version=_text(
                        provenance["source_version"],
                        label="source_version",
                    ),
                    page_number=_integer(provenance["page_number"], label="page_number"),
                    chunk_id=_text(provenance["chunk_id"], label="chunk_id"),
                ),
            )
        )
    if observed_records != expected_records or len(observed_records) != len(raw_items):
        raise ValidationGenerationIntegrityError("generation context references are inconsistent")
    return ProvenanceContext(items=tuple(items))


def _optional_uuid(value: object, *, label: str) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(_text(value, label=label))
    except ValueError as error:
        raise ValidationGenerationIntegrityError(f"{label} must be a UUID or null") from error


def _context_learning_scopes(
    run: GenerationRunModel,
) -> dict[str, tuple[UUID | None, UUID | None]]:
    root, _ = _context_snapshot_root(run.context_snapshot)
    scopes: dict[str, tuple[UUID | None, UUID | None]] = {}
    for index, raw_item in enumerate(_array(root["items"], label="generation context items")):
        if not isinstance(raw_item, Mapping):
            raise ValidationGenerationIntegrityError(
                f"generation context item {index} must be an object"
            )
        context_id = _text(raw_item.get("context_id"), label="context_id")
        raw_scope = raw_item.get("learning_scope")
        if raw_scope is None:
            scopes[context_id] = (None, None)
            continue
        learning_scope = _object(
            raw_scope,
            keys=frozenset({"unit_id", "lesson_id"}),
            label=f"generation context learning scope {index}",
        )
        scopes[context_id] = (
            _optional_uuid(learning_scope["unit_id"], label="unit_id"),
            _optional_uuid(learning_scope["lesson_id"], label="lesson_id"),
        )
    return scopes


def _programme_context_ids(run: GenerationRunModel) -> frozenset[str]:
    root, filters = _context_snapshot_root(run.context_snapshot)
    if not isinstance(filters, RetrievalScopeSet):
        return frozenset()
    authorized: set[str] = set()
    for index, raw_item in enumerate(_array(root["items"], label="generation context items")):
        if not isinstance(raw_item, Mapping):
            raise ValidationGenerationIntegrityError(
                f"generation context item {index} must be an object"
            )
        context_id = _text(raw_item.get("context_id"), label="context_id")
        try:
            scope = deserialize_retrieval_scope(raw_item.get("retrieval_scope"))
        except RetrievalScopeError as error:
            raise ValidationGenerationIntegrityError(
                "generation programme context scope is invalid"
            ) from error
        if not filters.allows(scope):
            raise ValidationGenerationIntegrityError(
                "generation programme context is outside its policy"
            )
        authorized.add(context_id)
    return frozenset(authorized)


def _question_from_snapshot(candidate: object) -> GeneratedQuestion:
    root = _object(
        candidate,
        keys=frozenset({"question_type", "stem", "options", "answer", "marking"}),
        label="generation candidate",
    )
    raw_options = _array(root["options"], label="candidate options")
    options = tuple(
        QuestionOption(
            option_id=_text(
                _object(
                    option,
                    keys=frozenset({"option_id", "text"}),
                    label=f"candidate option {index}",
                )["option_id"],
                label="option_id",
            ),
            text=_text(
                cast(Mapping[str, object], option)["text"],
                label="option text",
            ),
        )
        for index, option in enumerate(raw_options)
    )
    answer = _object(
        root["answer"],
        keys=frozenset({"explanation", "correct_option_id", "accepted_responses"}),
        label="candidate answer",
    )
    accepted_responses = tuple(
        _text(item, label="accepted response")
        for item in _array(answer["accepted_responses"], label="accepted responses")
    )
    marking = _object(
        root["marking"],
        keys=frozenset({"total_marks", "criteria"}),
        label="candidate marking",
    )
    criteria: list[MarkingCriterion] = []
    for index, raw_criterion in enumerate(
        _array(marking["criteria"], label="candidate marking criteria")
    ):
        criterion = _object(
            raw_criterion,
            keys=frozenset({"criterion_id", "description", "marks"}),
            label=f"candidate marking criterion {index}",
        )
        criteria.append(
            MarkingCriterion(
                criterion_id=_text(criterion["criterion_id"], label="criterion_id"),
                description=_text(criterion["description"], label="criterion description"),
                marks=_integer(criterion["marks"], label="criterion marks"),
            )
        )
    try:
        question_type = QuestionType(_text(root["question_type"], label="question_type"))
    except ValueError as error:
        raise ValidationGenerationIntegrityError("candidate question_type is invalid") from error
    return GeneratedQuestion(
        question_type=question_type,
        stem=_text(root["stem"], label="candidate stem"),
        options=options,
        answer=QuestionAnswer(
            explanation=_text(answer["explanation"], label="answer explanation"),
            correct_option_id=_optional_text(
                answer["correct_option_id"],
                label="correct_option_id",
            ),
            accepted_responses=accepted_responses,
        ),
        marking=MarkingScheme(
            total_marks=_integer(marking["total_marks"], label="total_marks"),
            criteria=tuple(criteria),
        ),
    )


def reconstruct_generation_result(record: ValidationGenerationRecord) -> GenerationResult:
    run = record.run
    attempt = record.attempt
    if (
        run.status != GenerationRunStatus.SUCCEEDED.value
        or run.disposition != CandidateDisposition.REQUIRES_VALIDATION.value
        or run.result_attempt_id is None
        or run.candidate is None
    ):
        raise ValidationGenerationNotSucceededError(run.id)
    if (
        not isinstance(attempt, GenerationAttemptModel)
        or attempt.id != run.result_attempt_id
        or attempt.generation_run_id != run.id
        or attempt.status != GenerationAttemptStatus.SUCCEEDED.value
        or attempt.disposition != CandidateDisposition.REQUIRES_VALIDATION.value
        or not attempt.accounting_known
        or attempt.candidate is None
        or attempt.candidate != run.candidate
        or attempt.input_tokens is None
        or attempt.output_tokens is None
        or attempt.total_tokens is None
        or attempt.cost_microusd is None
    ):
        raise ValidationGenerationIntegrityError("generation result attempt is inconsistent")
    if _fingerprint(_request_fingerprint_payload(run)) != run.request_fingerprint:
        raise ValidationGenerationIntegrityError("generation request fingerprint is inconsistent")

    try:
        blueprint = deserialize_blueprint(run.blueprint_snapshot)
        if (
            blueprint.curriculum_scope.curriculum_version_id != run.curriculum_version_id
            or blueprint.version.blueprint_id != run.blueprint_version
        ):
            raise ValidationGenerationIntegrityError("generation blueprint scope is inconsistent")
        slot = next((item for item in blueprint.slots if item.slot_id == run.slot_id), None)
        if slot is None:
            raise ValidationGenerationIntegrityError("generation blueprint slot is absent")
        raw_slots = run.blueprint_snapshot.get("slots")
        persisted_slot = (
            next(
                (
                    item
                    for item in raw_slots
                    if isinstance(item, dict) and item.get("slot_id") == run.slot_id
                ),
                None,
            )
            if isinstance(raw_slots, list)
            else None
        )
        if persisted_slot != run.blueprint_slot_snapshot:
            raise ValidationGenerationIntegrityError("generation blueprint slot snapshot differs")
        request = GenerationRequest(
            identity=GenerationIdentity(
                generation_id=run.id,
                attempt_id=attempt.id,
                idempotency_key=attempt.provider_idempotency_key,
                attempt_number=attempt.attempt_number,
                retry_of_attempt_id=attempt.retry_of_attempt_id,
            ),
            blueprint_version=blueprint.version,
            blueprint_slot=slot,
            context=_context_from_snapshot(run),
            versions=GenerationVersions(
                blueprint_version=run.blueprint_version,
                prompt_id=run.prompt_id,
                prompt_version=run.prompt_version,
                provider=run.provider,
                provider_version=run.provider_version,
                model=run.model,
                model_version=run.model_version,
                retrieval_version=run.retrieval_version,
                schema_version=run.schema_version,
            ),
            parameters=GenerationParameters(
                temperature=cast(float, run.generation_parameters["temperature"]),
                max_output_tokens=cast(int, run.generation_parameters["max_output_tokens"]),
                seed=cast(int | None, run.generation_parameters["seed"]),
            ),
        )
        return GenerationResult(
            request=request,
            question=_question_from_snapshot(run.candidate),
            accounting=GenerationAccounting(
                input_tokens=attempt.input_tokens,
                output_tokens=attempt.output_tokens,
                total_tokens=attempt.total_tokens,
                cost_microusd=attempt.cost_microusd,
                latency_ms=attempt.latency_ms,
            ),
        )
    except ValidationGenerationIntegrityError:
        raise
    except (
        BlueprintSnapshotError,
        GenerationContractError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        raise ValidationGenerationIntegrityError(
            "persisted generation result cannot be reconstructed canonically"
        ) from error


def reconstruct_validation_report(
    run: ValidationRunModel,
    findings: tuple[ValidationFindingModel, ...],
) -> ReconstructedValidationReport:
    """Rebuild and verify one canonical report from its append-only persisted rows."""

    if not isinstance(run, ValidationRunModel):
        raise ValidationReportIntegrityError("validation run has an invalid type")
    if (
        not isinstance(findings, tuple)
        or any(not isinstance(finding, ValidationFindingModel) for finding in findings)
        or len(findings) != run.finding_count
        or not 1 <= len(findings) <= MAX_VALIDATION_FINDINGS
    ):
        raise ValidationReportIntegrityError("validation finding count is incomplete")
    if tuple(finding.ordinal for finding in findings) != tuple(range(len(findings))):
        raise ValidationReportIntegrityError("validation finding ordinals are not contiguous")
    if any(finding.validation_run_id != run.id for finding in findings):
        raise ValidationReportIntegrityError("validation finding run identity is inconsistent")
    finding_ids = tuple(finding.id for finding in findings)
    if any(not isinstance(finding_id, UUID) for finding_id in finding_ids) or len(
        set(finding_ids)
    ) != len(finding_ids):
        raise ValidationReportIntegrityError("validation finding identities are invalid")

    reconstructed_findings: list[ValidationFinding] = []
    try:
        for finding in findings:
            raw_evidence = finding.evidence
            if (
                not isinstance(raw_evidence, list)
                or len(raw_evidence) != finding.evidence_count
                or not 1 <= len(raw_evidence) <= MAX_VALIDATION_EVIDENCE_PER_FINDING
            ):
                raise ValidationReportIntegrityError("validation finding evidence is incomplete")
            evidence: list[FindingEvidence] = []
            for item in raw_evidence:
                required_keys = frozenset({"location", "expected", "observed"})
                if (
                    not isinstance(item, Mapping)
                    or not required_keys.issubset(item)
                    or not frozenset(item).issubset(required_keys | {"details"})
                    or (
                        item.get("details") is not None
                        and not isinstance(item.get("details"), Mapping)
                    )
                ):
                    raise ValidationReportIntegrityError(
                        "validation finding evidence shape is invalid"
                    )
                evidence.append(
                    FindingEvidence(
                        location=_text(item["location"], label="evidence location"),
                        expected=_text(item["expected"], label="evidence expected"),
                        observed=_text(item["observed"], label="evidence observed"),
                        details=cast(Mapping[str, object] | None, item.get("details")),
                    )
                )
            reconstructed_findings.append(
                ValidationFinding(
                    validator_id=finding.validator_id,
                    validator_version=finding.validator_version,
                    code=finding.code,
                    status=FindingStatus(finding.status),
                    message=finding.message,
                    evidence=tuple(evidence),
                )
            )
        report = ValidationReport(
            candidate_id=run.generation_result_fingerprint,
            pipeline_version=run.pipeline_version,
            findings=tuple(reconstructed_findings),
        )
    except ValidationReportIntegrityError:
        raise
    except (TypeError, ValueError, ValidationContractError) as error:
        raise ValidationReportIntegrityError(
            "persisted validation findings cannot be reconstructed canonically"
        ) from error

    persisted_identities = tuple(
        (finding.validator_id, finding.validator_version, finding.code) for finding in findings
    )
    canonical_identities = tuple(
        (finding.validator_id, finding.validator_version, finding.code)
        for finding in report.findings
    )
    if persisted_identities != canonical_identities:
        raise ValidationReportIntegrityError("validation finding order is not canonical")

    lineage = run.validator_lineage
    if not isinstance(lineage, list) or len(lineage) != run.validator_count:
        raise ValidationReportIntegrityError("validation validator lineage count is invalid")
    expected_lineage: set[tuple[str, str]] = set()
    for lineage_item in lineage:
        if (
            not isinstance(lineage_item, Mapping)
            or frozenset(lineage_item) != frozenset({"validator_id", "validator_version"})
            or not isinstance(lineage_item["validator_id"], str)
            or not isinstance(lineage_item["validator_version"], str)
        ):
            raise ValidationReportIntegrityError("validation validator lineage shape is invalid")
        expected_lineage.add((lineage_item["validator_id"], lineage_item["validator_version"]))
    observed_lineage = {
        (finding.validator_id, finding.validator_version) for finding in report.findings
    }
    if (
        len(expected_lineage) != run.validator_count
        or expected_lineage != observed_lineage
        or run.validator_count > MAX_VALIDATION_VALIDATORS
    ):
        raise ValidationReportIntegrityError("validation validator lineage is inconsistent")

    if (
        run.report_schema_version != report.report_schema_version
        or run.overall_status != report.overall_status.value
        or run.report_fingerprint != report.report_fingerprint
        or run.limitations != list(report.limitations)
    ):
        raise ValidationReportIntegrityError("validation report metadata is inconsistent")
    return ReconstructedValidationReport(report=report, finding_ids=finding_ids)


def _duplicate_references(
    records: tuple[DuplicateReferenceRecord, ...],
) -> tuple[tuple[DuplicateReference, ...], dict[str, dict[str, object]]]:
    references: list[DuplicateReference] = []
    provenance: dict[str, dict[str, object]] = {}
    for record in records:
        question_id = f"{record.reference_kind}:{record.record_id}"
        digest = canonical_text_sha256(record.text)
        reference = DuplicateReference(
            question_id=question_id,
            text=record.text if len(record.text) <= MAX_DUPLICATE_TEXT_CHARACTERS else None,
            content_sha256=digest,
        )
        references.append(reference)
        provenance[question_id] = {
            "reference_kind": record.reference_kind,
            "record_id": str(record.record_id),
            "record_version": record.record_version,
            "source_document_id": (
                str(record.source_document_id) if record.source_document_id is not None else None
            ),
            "source_page_number": record.source_page_number,
            "validation_run_id": (
                str(record.validation_run_id) if record.validation_run_id is not None else None
            ),
            "generation_run_id": (
                str(record.generation_run_id) if record.generation_run_id is not None else None
            ),
            "report_fingerprint": record.report_fingerprint,
            "pipeline_version": record.pipeline_version,
        }
    return tuple(references), provenance


def _plain_json(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_plain_json(item) for item in value]
    raise TypeError(f"value of type {type(value).__name__} is not JSON-compatible")


def _input_snapshot(
    generation: GenerationRunModel,
    validation_input: ValidationInput,
    duplicate_provenance: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    request_metadata = cast(Mapping[str, object], validation_input.candidate["generation_metadata"])
    generated_scope = validation_input.generated_scope
    if generated_scope is None:
        raise ValidationGenerationIntegrityError("canonical generated scope is absent")
    snapshot: dict[str, object] = {
        "schema_version": VALIDATION_INPUT_SCHEMA_VERSION,
        "trust": "server_reconstructed",
        "generation": {
            "generation_run_id": str(generation.id),
            "generation_attempt_id": str(generation.result_attempt_id),
            "paper_blueprint_id": str(generation.paper_blueprint_id),
            "request_fingerprint": generation.request_fingerprint,
            "generation_result_fingerprint": validation_input.candidate_id,
            "blueprint_version": generation.blueprint_version,
            "prompt_id": generation.prompt_id,
            "prompt_version": generation.prompt_version,
            "provider": generation.provider,
            "provider_version": generation.provider_version,
            "model": generation.model,
            "model_version": generation.model_version,
            "retrieval_version": generation.retrieval_version,
            "generation_schema_version": generation.schema_version,
            "pricing_version": generation.pricing_version,
            "attempt_number": request_metadata["attempt_number"],
            "retry_of_attempt_id": request_metadata["retry_of_attempt_id"],
        },
        "subject_scope": {
            "trust": "server_owned",
            "grade": validation_input.trusted_scope.grade,
            "medium": validation_input.trusted_scope.medium,
            "subject_id": str(validation_input.trusted_scope.subject_id),
            "subject_code": validation_input.trusted_scope.subject_code,
            "curriculum_version_id": str(validation_input.trusted_scope.curriculum_version_id),
            "unit_ids": [str(value) for value in validation_input.trusted_scope.unit_ids],
            "lesson_ids": [str(value) for value in validation_input.trusted_scope.lesson_ids],
        },
        "generated_scope": {
            "grade": generated_scope.grade,
            "medium": generated_scope.medium,
            "subject_id": str(generated_scope.subject_id),
            "curriculum_version_id": str(generated_scope.curriculum_version_id),
            "unit_ids": [str(value) for value in generated_scope.unit_ids],
            "lesson_ids": [str(value) for value in generated_scope.lesson_ids],
        },
        "context_scope_bindings": [
            {
                "context_id": binding.context_id,
                "curriculum_version_id": str(binding.curriculum_version_id),
                "subject_id": str(binding.subject_id),
                "unit_id": str(binding.unit_id) if binding.unit_id is not None else None,
                "lesson_id": str(binding.lesson_id) if binding.lesson_id is not None else None,
                "snapshot_unit_id": (
                    str(binding.snapshot_unit_id) if binding.snapshot_unit_id is not None else None
                ),
                "snapshot_lesson_id": (
                    str(binding.snapshot_lesson_id)
                    if binding.snapshot_lesson_id is not None
                    else None
                ),
                "programme_authorized": binding.programme_authorized,
            }
            for binding in validation_input.context_scope_bindings
        ],
        "candidate": _plain_json(validation_input.candidate),
        "candidate_fingerprint": validation_input.candidate_fingerprint,
        "input_fingerprint": validation_input.input_fingerprint,
        "blueprint": asdict(validation_input.blueprint),
        "grounding_sources": [
            {
                "context_id": source.context_id,
                "text": source.text,
                "source_document_id": source.source_document_id,
                "source_version": source.source_version,
                "page_number": source.page_number,
                "chunk_id": source.chunk_id,
                "trust": "untrusted_data",
            }
            for source in validation_input.grounding_sources
        ],
        "duplicate_references": [
            {
                "question_id": reference.question_id,
                "text": reference.text,
                "content_sha256": reference.effective_sha256,
                "provenance": _plain_json(duplicate_provenance[reference.question_id]),
            }
            for reference in validation_input.duplicate_references
        ],
    }
    serialized = json.dumps(
        snapshot,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(serialized) > MAX_VALIDATION_INPUT_SNAPSHOT_BYTES:
        raise ValidationResourceLimitError("validation input snapshot exceeds its bound")
    return snapshot


def _validator_lineage(report: ValidationReport) -> list[dict[str, str]]:
    identities = sorted(
        {(finding.validator_id, finding.validator_version) for finding in report.findings}
    )
    return [
        {"validator_id": validator_id, "validator_version": validator_version}
        for validator_id, validator_version in identities
    ]


async def _trusted_scope_and_bindings(
    repository: SqlAlchemyValidationRepository,
    record: ValidationGenerationRecord,
) -> tuple[TrustedSubjectScope | None, tuple[ContextScopeBinding, ...]]:
    trusted_record = record.trusted_scope
    if trusted_record is None:
        return None, ()
    run = record.run
    try:
        persisted_blueprint = deserialize_blueprint(trusted_record.blueprint.blueprint)
    except (BlueprintSnapshotError, TypeError, ValueError) as error:
        raise ValidationGenerationIntegrityError(
            "persisted trusted blueprint cannot be reconstructed"
        ) from error
    scope = persisted_blueprint.curriculum_scope
    if (
        trusted_record.blueprint.id != run.paper_blueprint_id
        or trusted_record.blueprint.curriculum_version_id != run.curriculum_version_id
        or persisted_blueprint.version.blueprint_id != run.blueprint_version
        or scope.curriculum_version_id != run.curriculum_version_id
        or scope.grade != trusted_record.grade
        or scope.medium != trusted_record.medium
        or scope.subject_id != trusted_record.subject_id
    ):
        raise ValidationGenerationIntegrityError(
            "persisted blueprint and curriculum subject scope are inconsistent"
        )
    if not await repository.selected_scope_is_valid(
        run.curriculum_version_id,
        unit_ids=scope.unit_ids,
        lesson_ids=scope.lesson_ids,
    ):
        raise ValidationGenerationIntegrityError(
            "persisted blueprint selected unit or lesson scope is invalid"
        )
    trusted_scope = TrustedSubjectScope(
        grade=trusted_record.grade,
        medium=trusted_record.medium,
        subject_id=trusted_record.subject_id,
        subject_code=trusted_record.subject_code,
        curriculum_version_id=run.curriculum_version_id,
        unit_ids=scope.unit_ids,
        lesson_ids=scope.lesson_ids,
    )
    snapshot_scopes = _context_learning_scopes(run)
    programme_context_ids = _programme_context_ids(run)
    bindings: list[ContextScopeBinding] = []
    for scoped_context in trusted_record.context_records:
        context = scoped_context.context
        context_id = f"{context.record_kind}:{context.id}"
        if context_id not in snapshot_scopes:
            raise ValidationGenerationIntegrityError(
                "persisted context record is missing from generation scope snapshot"
            )
        snapshot_unit_id, snapshot_lesson_id = snapshot_scopes[context_id]
        context_curriculum_id = context.curriculum_version_id
        if context.source_curriculum_version_id != context.curriculum_version_id:
            context_curriculum_id = context.source_curriculum_version_id or UUID(int=0)
        bindings.append(
            ContextScopeBinding(
                context_id=context_id,
                curriculum_version_id=context_curriculum_id,
                subject_id=scoped_context.subject_id,
                unit_id=context.unit_id,
                lesson_id=context.lesson_id,
                snapshot_unit_id=snapshot_unit_id,
                snapshot_lesson_id=snapshot_lesson_id,
                programme_authorized=context_id in programme_context_ids,
            )
        )
    if set(snapshot_scopes) != {binding.context_id for binding in bindings}:
        raise ValidationGenerationIntegrityError("generation context scope bindings are incomplete")
    return trusted_scope, tuple(bindings)


def _finding_models(run_id: UUID, report: ValidationReport) -> tuple[ValidationFindingModel, ...]:
    if len(report.findings) > MAX_VALIDATION_FINDINGS:
        raise ValidationResourceLimitError("validation finding count exceeds its bound")
    models: list[ValidationFindingModel] = []
    for ordinal, finding in enumerate(report.findings):
        if len(finding.evidence) > MAX_VALIDATION_EVIDENCE_PER_FINDING:
            raise ValidationResourceLimitError("validation evidence count exceeds its bound")
        evidence: list[dict[str, object]] = []
        for item in finding.evidence:
            evidence_item: dict[str, object] = {
                "location": item.location,
                "expected": item.expected,
                "observed": item.observed,
            }
            if item.details is not None:
                evidence_item["details"] = _plain_json(item.details)
            evidence.append(evidence_item)
        models.append(
            ValidationFindingModel(
                id=uuid5(
                    _VALIDATION_NAMESPACE,
                    f"{run_id}:finding:{ordinal}:{finding.validator_id}:{finding.code}",
                ),
                validation_run_id=run_id,
                ordinal=ordinal,
                validator_id=finding.validator_id,
                validator_version=finding.validator_version,
                code=str(finding.code),
                status=finding.status.value,
                message=finding.message,
                evidence=evidence,
                evidence_count=len(evidence),
            )
        )
    return tuple(models)


class ValidationRunService:
    def __init__(
        self,
        session: AsyncSession,
        pipeline: ValidationPipeline,
        *,
        telemetry: OperationalTelemetry | None = None,
    ) -> None:
        if not isinstance(pipeline, ValidationPipeline):
            raise TypeError("pipeline must be ValidationPipeline")
        if len(pipeline.validators) > MAX_VALIDATION_VALIDATORS:
            raise ValidationResourceLimitError("validation validator count exceeds its bound")
        self._session = session
        self._pipeline = pipeline
        self._telemetry = telemetry or get_operational_telemetry()
        self._repository = SqlAlchemyValidationRepository(session)

    async def create(
        self,
        curriculum_version_id: UUID,
        *,
        generation_run_id: UUID,
        actor_id: UUID,
    ) -> ValidationCreationResult:
        try:
            result = await self._create(
                curriculum_version_id,
                generation_run_id=generation_run_id,
                actor_id=actor_id,
            )
        except Exception as error:
            await self._session.rollback()
            self._telemetry.validation_creation(
                outcome="failed",
                failure_code=_validation_creation_failure_code(error),
                overall_status=None,
                finding_count=0,
                deduplicated=False,
            )
            raise
        overall_status = result.run.overall_status
        finding_count = result.run.finding_count
        self._telemetry.validation_creation(
            outcome="succeeded",
            failure_code=None,
            overall_status=overall_status if isinstance(overall_status, str) else None,
            finding_count=finding_count if isinstance(finding_count, int) else 0,
            deduplicated=result.deduplicated,
        )
        return result

    async def _create(
        self,
        curriculum_version_id: UUID,
        *,
        generation_run_id: UUID,
        actor_id: UUID,
    ) -> ValidationCreationResult:
        generation_record = await self._repository.get_generation(
            curriculum_version_id,
            generation_run_id,
        )
        result = reconstruct_generation_result(generation_record)
        canonical_generation_fingerprint = generation_result_fingerprint(result)

        await self._repository.lock_generation_for_validation(
            curriculum_version_id,
            generation_run_id,
        )
        existing = await self._repository.get_for_generation_pipeline(
            generation_run_id,
            self._pipeline.version,
        )
        if existing is not None:
            self._assert_existing(
                existing,
                curriculum_version_id=curriculum_version_id,
                generation_fingerprint=canonical_generation_fingerprint,
            )
            return ValidationCreationResult(existing, deduplicated=True)

        duplicate_records = await self._repository.list_duplicate_references(
            curriculum_version_id,
            exclude_generation_run_id=generation_run_id,
            limit=MAX_VALIDATION_DUPLICATE_REFERENCES,
        )
        duplicate_references, duplicate_provenance = _duplicate_references(duplicate_records)
        slot = result.request.blueprint_slot
        minimum_age, maximum_age = grade_age_bounds(
            slot.generation_constraints.curriculum_scope.grade
        )
        trusted_scope, context_scope_bindings = await _trusted_scope_and_bindings(
            self._repository,
            generation_record,
        )
        try:
            validation_input = adapt_generation_result(
                result,
                requirements=BlueprintRequirements(
                    slot_id=slot.slot_id,
                    schema_version=result.request.versions.schema_version,
                    question_type=slot.question_type.value,
                    marks=slot.marks,
                    language=slot.generation_constraints.response_language,
                    minimum_age=minimum_age,
                    maximum_age=maximum_age,
                ),
                duplicate_references=duplicate_references,
                trusted_scope=trusted_scope,
                context_scope_bindings=context_scope_bindings,
            )
        except (GenerationAdapterError, ValidationContractError) as error:
            raise ValidationGenerationIntegrityError(
                "generation result cannot cross the canonical validation adapter"
            ) from error
        if validation_input.candidate_id != canonical_generation_fingerprint:
            raise ValidationGenerationIntegrityError(
                "canonical generation result fingerprint did not survive adaptation"
            )
        report = await asyncio.to_thread(self._pipeline.validate, validation_input)
        if (
            report.candidate_id != canonical_generation_fingerprint
            or report.pipeline_version != self._pipeline.version
            or report.report_schema_version != REPORT_SCHEMA_VERSION
        ):
            raise ValidationGenerationIntegrityError("validation report binding is invalid")

        run_id = uuid5(
            _VALIDATION_NAMESPACE,
            f"{generation_run_id}:{self._pipeline.version}",
        )
        input_snapshot = _input_snapshot(
            generation_record.run,
            validation_input,
            duplicate_provenance,
        )
        lineage = _validator_lineage(report)
        run_values: dict[str, object] = {
            "id": run_id,
            "curriculum_version_id": curriculum_version_id,
            "generation_run_id": generation_run_id,
            "generation_attempt_id": result.request.identity.attempt_id,
            "pipeline_version": self._pipeline.version,
            "pipeline_fingerprint": self._pipeline.pipeline_fingerprint,
            "input_schema_version": VALIDATION_INPUT_SCHEMA_VERSION,
            "report_schema_version": report.report_schema_version,
            "generation_result_fingerprint": canonical_generation_fingerprint,
            "input_fingerprint": validation_input.input_fingerprint,
            "candidate_fingerprint": validation_input.candidate_fingerprint,
            "report_fingerprint": report.report_fingerprint,
            "overall_status": report.overall_status.value,
            "input_snapshot": input_snapshot,
            "validator_lineage": lineage,
            "limitations": list(report.limitations),
            "finding_count": len(report.findings),
            "validator_count": len(lineage),
            "grounding_source_count": len(validation_input.grounding_sources),
            "duplicate_reference_count": len(validation_input.duplicate_references),
            "created_by": actor_id,
        }
        stored = await self._repository.store_report(
            run_values,
            _finding_models(run_id, report),
        )
        self._assert_stored(stored, run_values)
        if stored.created:
            self._session.add(
                AdminAuditEventModel(
                    id=uuid4(),
                    actor_id=actor_id,
                    action="validation_run.created",
                    resource_type="validation_run",
                    resource_id=stored.run.id,
                    payload={
                        "curriculum_version_id": str(curriculum_version_id),
                        "generation_run_id": str(generation_run_id),
                        "generation_attempt_id": str(result.request.identity.attempt_id),
                        "pipeline_version": self._pipeline.version,
                        "pipeline_fingerprint": self._pipeline.pipeline_fingerprint,
                        "generation_result_fingerprint": canonical_generation_fingerprint,
                        "input_fingerprint": validation_input.input_fingerprint,
                        "candidate_fingerprint": validation_input.candidate_fingerprint,
                        "report_fingerprint": report.report_fingerprint,
                        "overall_status": report.overall_status.value,
                        "finding_count": len(report.findings),
                        "validator_count": len(lineage),
                        "grounding_source_count": len(validation_input.grounding_sources),
                        "duplicate_reference_count": len(validation_input.duplicate_references),
                    },
                )
            )
        await self._session.commit()
        return ValidationCreationResult(stored.run, deduplicated=not stored.created)

    async def get_run(
        self,
        curriculum_version_id: UUID,
        validation_run_id: UUID,
    ) -> ValidationRunModel:
        return await self._repository.get_run(curriculum_version_id, validation_run_id)

    async def list_runs(
        self,
        curriculum_version_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[ValidationRunModel, ...]:
        if not await self._repository.curriculum_exists(curriculum_version_id):
            raise ValidationCurriculumNotFoundError(curriculum_version_id)
        return await self._repository.list_runs(
            curriculum_version_id,
            limit=limit,
            offset=offset,
        )

    async def list_findings(
        self,
        curriculum_version_id: UUID,
        validation_run_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[ValidationFindingModel, ...]:
        return await self._repository.list_findings(
            curriculum_version_id,
            validation_run_id,
            limit=limit,
            offset=offset,
        )

    def _assert_existing(
        self,
        existing: ValidationRunModel,
        *,
        curriculum_version_id: UUID,
        generation_fingerprint: str,
    ) -> None:
        if (
            existing.curriculum_version_id != curriculum_version_id
            or existing.pipeline_fingerprint != self._pipeline.pipeline_fingerprint
            or existing.generation_result_fingerprint != generation_fingerprint
        ):
            raise ValidationPipelineVersionConflictError(existing.id)

    @staticmethod
    def _assert_stored(
        stored: StoredValidationReport,
        expected: Mapping[str, object],
    ) -> None:
        run = stored.run
        compared_fields = (
            "id",
            "curriculum_version_id",
            "generation_run_id",
            "generation_attempt_id",
            "pipeline_version",
            "pipeline_fingerprint",
            "input_schema_version",
            "report_schema_version",
            "generation_result_fingerprint",
            "input_fingerprint",
            "candidate_fingerprint",
            "report_fingerprint",
            "overall_status",
            "finding_count",
            "validator_count",
            "grounding_source_count",
            "duplicate_reference_count",
        )
        if any(getattr(run, field_name) != expected[field_name] for field_name in compared_fields):
            raise ValidationIdempotencyConflictError(run.id)
        if (
            run.input_snapshot != expected["input_snapshot"]
            or run.validator_lineage != expected["validator_lineage"]
            or run.limitations != expected["limitations"]
        ):
            raise ValidationIdempotencyConflictError(run.id)

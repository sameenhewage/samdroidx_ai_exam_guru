"""Strict boundary from canonical generation results to validation inputs.

Generation does not carry age or option-count policy, so callers must provide a
``BlueprintRequirements`` value from trusted configuration.  The adapter verifies
all fields that overlap the canonical generation blueprint and refuses to infer the
missing policy.  Retrieved text remains untrusted ``GroundingSource`` data; only its
identifier and immutable provenance are used to construct candidate references.
"""

from exam_guru_api.generation.domain import (
    CandidateDisposition,
    ContextProvenance,
    ContextTrust,
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
from exam_guru_api.validation.domain import (
    QUESTION_SCHEMA_VERSION,
    BlueprintRequirements,
    DuplicateReference,
    GroundingSource,
    ValidationContractError,
    ValidationInput,
)


class GenerationAdapterError(ValidationContractError):
    """Raised when a generation result cannot be mapped without inference."""


def _canonical_result(value: object) -> GenerationResult:
    if not isinstance(value, GenerationResult):
        raise GenerationAdapterError("generation adapter input must be GenerationResult")
    if value.disposition is not CandidateDisposition.REQUIRES_VALIDATION:
        raise GenerationAdapterError("generation disposition is not supported for validation")

    try:
        context = value.request.context
        if context.trust is not ContextTrust.UNTRUSTED_DATA:
            raise GenerationAdapterError("generation context must remain untrusted data")

        identity = value.request.identity
        canonical_identity = GenerationIdentity(
            generation_id=identity.generation_id,
            attempt_id=identity.attempt_id,
            idempotency_key=identity.idempotency_key,
            attempt_number=identity.attempt_number,
            retry_of_attempt_id=identity.retry_of_attempt_id,
        )
        versions = value.request.versions
        canonical_versions = GenerationVersions(
            blueprint_version=versions.blueprint_version,
            prompt_id=versions.prompt_id,
            prompt_version=versions.prompt_version,
            provider=versions.provider,
            provider_version=versions.provider_version,
            model=versions.model,
            model_version=versions.model_version,
            retrieval_version=versions.retrieval_version,
            schema_version=versions.schema_version,
        )
        parameters = value.request.parameters
        canonical_parameters = GenerationParameters(
            temperature=parameters.temperature,
            max_output_tokens=parameters.max_output_tokens,
            seed=parameters.seed,
        )

        ProvenanceContext(items=context.items)
        canonical_context = ProvenanceContext(
            items=tuple(
                RetrievedContextItem(
                    context_id=item.context_id,
                    text=item.text,
                    provenance=ContextProvenance(
                        source_document_id=item.provenance.source_document_id,
                        source_version=item.provenance.source_version,
                        page_number=item.provenance.page_number,
                        chunk_id=item.provenance.chunk_id,
                    ),
                )
                for item in context.items
            )
        )
        canonical_request = GenerationRequest(
            identity=canonical_identity,
            blueprint_version=value.request.blueprint_version,
            blueprint_slot=value.request.blueprint_slot,
            context=canonical_context,
            versions=canonical_versions,
            parameters=canonical_parameters,
        )

        question = value.question
        GeneratedQuestion(
            question_type=question.question_type,
            stem=question.stem,
            options=question.options,
            answer=question.answer,
            marking=question.marking,
        )
        canonical_question = GeneratedQuestion(
            question_type=question.question_type,
            stem=question.stem,
            options=tuple(
                QuestionOption(option_id=option.option_id, text=option.text)
                for option in question.options
            ),
            answer=QuestionAnswer(
                explanation=question.answer.explanation,
                correct_option_id=question.answer.correct_option_id,
                accepted_responses=question.answer.accepted_responses,
            ),
            marking=MarkingScheme(
                total_marks=question.marking.total_marks,
                criteria=tuple(
                    MarkingCriterion(
                        criterion_id=criterion.criterion_id,
                        description=criterion.description,
                        marks=criterion.marks,
                    )
                    for criterion in question.marking.criteria
                ),
            ),
        )
        accounting = value.accounting
        canonical_accounting = GenerationAccounting(
            input_tokens=accounting.input_tokens,
            output_tokens=accounting.output_tokens,
            total_tokens=accounting.total_tokens,
            cost_microusd=accounting.cost_microusd,
            latency_ms=accounting.latency_ms,
        )
        return GenerationResult(
            request=canonical_request,
            question=canonical_question,
            accounting=canonical_accounting,
        )
    except GenerationAdapterError:
        raise
    except (AttributeError, GenerationContractError, TypeError) as error:
        raise GenerationAdapterError("generation result is not canonical") from error


def _require_matching_requirements(
    result: GenerationResult,
    requirements: object,
) -> BlueprintRequirements:
    if not isinstance(requirements, BlueprintRequirements):
        raise GenerationAdapterError("requirements must be BlueprintRequirements")
    try:
        BlueprintRequirements(
            slot_id=requirements.slot_id,
            schema_version=requirements.schema_version,
            question_type=requirements.question_type,
            marks=requirements.marks,
            language=requirements.language,
            minimum_age=requirements.minimum_age,
            maximum_age=requirements.maximum_age,
            minimum_options=requirements.minimum_options,
            maximum_options=requirements.maximum_options,
        )
    except (AttributeError, TypeError, ValidationContractError) as error:
        raise GenerationAdapterError(
            "requirements must be canonical BlueprintRequirements"
        ) from error

    slot = result.request.blueprint_slot
    expected = {
        "slot_id": slot.slot_id,
        "schema_version": result.request.versions.schema_version,
        "question_type": slot.question_type.value,
        "marks": slot.marks,
        "language": slot.generation_constraints.response_language,
    }
    mismatches = tuple(
        field_name
        for field_name, expected_value in expected.items()
        if getattr(requirements, field_name) != expected_value
    )
    if mismatches:
        raise GenerationAdapterError(
            "validation requirements conflict with canonical generation fields: "
            + ", ".join(mismatches)
        )
    return requirements


def _candidate(result: GenerationResult) -> dict[str, object]:
    request = result.request
    question = result.question
    identity = request.identity
    versions = request.versions
    blueprint_version = request.blueprint_version
    return {
        "schema_version": versions.schema_version,
        "question_type": question.question_type.value,
        "stem": question.stem,
        "options": tuple(
            {"option_id": option.option_id, "text": option.text} for option in question.options
        ),
        "answer": {
            "correct_option_id": question.answer.correct_option_id,
            "accepted_responses": question.answer.accepted_responses,
            "explanation": question.answer.explanation,
        },
        "marking": {
            "total_marks": question.marking.total_marks,
            "criteria": tuple(
                {
                    "criterion_id": criterion.criterion_id,
                    "description": criterion.description,
                    "marks": criterion.marks,
                }
                for criterion in question.marking.criteria
            ),
        },
        # These identify all context supplied to this exact generation attempt.  They
        # do not claim that the source text semantically or factually proves the answer.
        "context_references": tuple(item.context_id for item in request.context.items),
        "generation_metadata": {
            "generation_id": str(identity.generation_id),
            "attempt_id": str(identity.attempt_id),
            "attempt_number": identity.attempt_number,
            "retry_of_attempt_id": (
                str(identity.retry_of_attempt_id)
                if identity.retry_of_attempt_id is not None
                else None
            ),
            "blueprint_version": versions.blueprint_version,
            "blueprint_schema_version": blueprint_version.schema_version,
            "blueprint_algorithm_version": blueprint_version.algorithm_version,
            "blueprint_config_version": blueprint_version.config_version,
            "blueprint_input_fingerprint": blueprint_version.input_fingerprint,
            "prompt_id": versions.prompt_id,
            "prompt_version": versions.prompt_version,
            "provider": versions.provider,
            "provider_version": versions.provider_version,
            "model": versions.model,
            "model_version": versions.model_version,
            "retrieval_version": versions.retrieval_version,
            "schema_version": versions.schema_version,
            "disposition": result.disposition.value,
        },
    }


def adapt_generation_result(
    result: GenerationResult,
    *,
    requirements: BlueprintRequirements,
    duplicate_references: tuple[DuplicateReference, ...] = (),
) -> ValidationInput:
    """Map one canonical unvalidated generation attempt into an immutable input.

    ``requirements`` supplies trusted age and option-count policy absent from the
    generation contract.  Slot identity, question type, marks, schema version, and
    language must exactly match generation; no value is rewritten or inferred.
    """

    canonical = _canonical_result(result)
    canonical_requirements = _require_matching_requirements(canonical, requirements)
    if canonical.request.versions.schema_version != QUESTION_SCHEMA_VERSION:
        raise GenerationAdapterError(
            f"generation schema version is unsupported by validation: {QUESTION_SCHEMA_VERSION}"
        )
    if not isinstance(duplicate_references, tuple) or any(
        not isinstance(reference, DuplicateReference) for reference in duplicate_references
    ):
        raise GenerationAdapterError("duplicate_references must be a tuple of DuplicateReference")

    grounding_sources = tuple(
        GroundingSource(
            context_id=item.context_id,
            text=item.text,
            source_document_id=item.provenance.source_document_id,
            source_version=item.provenance.source_version,
            page_number=item.provenance.page_number,
            chunk_id=item.provenance.chunk_id,
        )
        for item in canonical.request.context.items
    )
    try:
        return ValidationInput(
            candidate_id=str(canonical.request.identity.attempt_id),
            candidate=_candidate(canonical),
            blueprint=canonical_requirements,
            grounding_sources=grounding_sources,
            duplicate_references=duplicate_references,
        )
    except ValidationContractError as error:
        raise GenerationAdapterError(
            "canonical generation result cannot satisfy the validation input contract"
        ) from error


generation_result_to_validation_input = adapt_generation_result


__all__ = [
    "GenerationAdapterError",
    "adapt_generation_result",
    "generation_result_to_validation_input",
]

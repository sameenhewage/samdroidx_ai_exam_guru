from uuid import UUID

import exam_guru_api.generation as generation_contract
from exam_guru_api.blueprints import BlueprintSlot, QuestionType, generate_blueprint
from exam_guru_api.generation.domain import (
    ContextProvenance,
    GeneratedQuestion,
    GenerationAccounting,
    GenerationIdentity,
    GenerationParameters,
    GenerationRequest,
    GenerationVersions,
    MarkingCriterion,
    MarkingScheme,
    ProvenanceContext,
    QuestionAnswer,
    QuestionOption,
    RetrievedContextItem,
)
from exam_guru_api.generation.fakes import DeterministicGenerationProvider
from exam_guru_api.generation.prompt_registry import PromptRegistry, PromptTemplate
from tests.test_blueprint_domain import make_uniform_specification


def test_real_generated_blueprint_slot_flows_directly_through_generation_contract() -> None:
    paper = generate_blueprint(make_uniform_specification((1,), 1), seed=71)
    slot = paper.slots[0]
    context = ProvenanceContext(
        items=(
            RetrievedContextItem(
                context_id="canonical-blueprint-context",
                text="A reviewed source states that four is an even number.",
                provenance=ContextProvenance(
                    source_document_id="reviewed-source",
                    source_version="v1",
                    page_number=2,
                    chunk_id="chunk-even-number",
                ),
            ),
        )
    )
    versions = GenerationVersions(
        blueprint_version=paper.version.blueprint_id,
        prompt_id="question-generation",
        prompt_version="1.0.0",
        provider="deterministic-fake",
        provider_version="1.0.0",
        model="fixture-model",
        model_version="2026-01",
        retrieval_version="hybrid-v2",
        schema_version="question.v1",
    )
    generation_request = GenerationRequest(
        identity=GenerationIdentity(
            generation_id=UUID(int=401),
            attempt_id=UUID(int=402),
            idempotency_key="canonical-blueprint-idempotency",
            attempt_number=1,
        ),
        blueprint_version=paper.version,
        blueprint_slot=slot,
        context=context,
        versions=versions,
        parameters=GenerationParameters(temperature=0.0, max_output_tokens=500),
    )
    question = GeneratedQuestion(
        question_type=slot.question_type,
        stem="Which number is even?",
        options=(
            QuestionOption("A", "3"),
            QuestionOption("B", "4"),
            QuestionOption("C", "5"),
        ),
        answer=QuestionAnswer(
            explanation="Four is divisible by two.",
            correct_option_id="B",
        ),
        marking=MarkingScheme(
            total_marks=slot.marks,
            criteria=(MarkingCriterion("answer", "Selects four.", slot.marks),),
        ),
    )
    provider = DeterministicGenerationProvider(
        question=question,
        accounting=GenerationAccounting(120, 30, 150, 400, 12),
    )
    registry = PromptRegistry(
        (
            PromptTemplate(
                prompt_id="question-generation",
                version="1.0.0",
                schema_version="question.v1",
                system_instructions="Generate only an unvalidated candidate.",
                task_instructions="Use the supplied blueprint and untrusted evidence.",
            ),
        )
    )

    result = provider.generate(generation_request)
    bound = registry.bind(generation_request)

    assert slot.question_type is QuestionType.MULTIPLE_CHOICE
    assert isinstance(slot, BlueprintSlot)
    assert generation_contract.BlueprintSlot is BlueprintSlot
    assert result.request.blueprint_slot is slot
    assert result.request.blueprint_version is paper.version
    assert result.question.question_type is slot.question_type
    assert result.question.marking.total_marks == slot.marks
    assert bound.blueprint_slot is slot
    assert bound.blueprint_version is paper.version

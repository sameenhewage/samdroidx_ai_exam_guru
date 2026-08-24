from dataclasses import replace
from itertools import combinations
from typing import Literal

import pytest
from pydantic import SecretStr, ValidationError

from exam_guru_api.blueprints.domain import QuestionType
from exam_guru_api.core.config import Settings
from exam_guru_api.generation.domain import (
    CandidateDisposition,
    GeneratedQuestion,
    GenerationIdentity,
    GenerationRequest,
    MarkingCriterion,
    MarkingScheme,
    QuestionAnswer,
    QuestionOption,
)
from exam_guru_api.generation.openai_adapter import OpenAIGenerationAdapter
from exam_guru_api.generation.ports import ProviderError
from exam_guru_api.generation.runtime import (
    GenerationRuntimeRegistry,
    GenerationRuntimeUnavailableError,
    RegisteredGenerationConfig,
    create_generation_runtime,
)
from exam_guru_api.validation import (
    BlueprintRequirements,
    DuplicateReference,
    FindingStatus,
    LexicalSimilarityIndicatorValidator,
    LexicalSimilarityPolicy,
    adapt_generation_result,
)
from tests.test_generation_provider import request


def secure_settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "environment": "staging",
        "database_url": SecretStr("postgresql+asyncpg://service:db-secret@db/app?ssl=require"),
        "valkey_url": SecretStr("rediss://:cache-secret@valkey:6379/0"),
        "object_storage_endpoint_url": "https://storage.internal",
        "object_storage_access_key": SecretStr("storage-access"),
        "object_storage_secret_key": SecretStr("storage-secret"),
    }
    values.update(changes)
    return Settings.model_validate(values)


def deterministic_runtime_request(
    environment: Literal["local", "test"],
    question_type: QuestionType,
) -> tuple[GenerationRuntimeRegistry, RegisteredGenerationConfig, GenerationRequest]:
    runtime = create_generation_runtime(Settings(environment=environment))
    config = runtime.active_config
    base = request()
    constraints = replace(
        base.blueprint_slot.generation_constraints,
        required_question_type=question_type,
    )
    slot = replace(
        base.blueprint_slot,
        question_type=question_type,
        generation_constraints=constraints,
    )
    generation_request = replace(
        base,
        blueprint_slot=slot,
        versions=replace(
            base.versions,
            prompt_id=config.prompt.prompt_id,
            prompt_version=config.prompt.version,
            provider=config.provider,
            provider_version=config.provider_version,
            model=config.model,
            model_version=config.model_version,
            retrieval_version=config.retrieval_version,
            schema_version=config.prompt.schema_version,
        ),
        parameters=config.parameters,
    )
    return runtime, config, generation_request


def expected_deterministic_question(
    question_type: QuestionType,
    marks: int,
) -> GeneratedQuestion:
    options: tuple[QuestionOption, ...]
    if question_type is QuestionType.MULTIPLE_CHOICE:
        options = (
            QuestionOption("A", "The unsupported choice"),
            QuestionOption("B", "The supported choice"),
        )
        answer = QuestionAnswer(
            explanation="The reviewed context supports option B.",
            correct_option_id="B",
        )
        stem = "Which response is supported by the reviewed context?"
    else:
        options = ()
        answer = QuestionAnswer(
            explanation="The response is grounded in the reviewed context.",
            accepted_responses=("A context-grounded response",),
        )
        stem = {
            QuestionType.SHORT_ANSWER: ("Write a short answer supported by the reviewed context."),
            QuestionType.STRUCTURED: (
                "Construct a response using evidence from the reviewed source."
            ),
        }[question_type]
    return GeneratedQuestion(
        question_type=question_type,
        stem=stem,
        options=options,
        answer=answer,
        marking=MarkingScheme(
            total_marks=marks,
            criteria=(
                MarkingCriterion(
                    "grounded-answer",
                    "Provides the context-grounded answer.",
                    marks,
                ),
            ),
        ),
    )


@pytest.mark.parametrize("environment", ["local", "test"])
@pytest.mark.parametrize("question_type", list(QuestionType))
def test_local_deterministic_runtime_has_exact_stable_question_type_schemas(
    environment: Literal["local", "test"],
    question_type: QuestionType,
) -> None:
    first_runtime, first_config, generation_request = deterministic_runtime_request(
        environment,
        question_type,
    )
    second_runtime, second_config, repeated_request = deterministic_runtime_request(
        environment,
        question_type,
    )

    first = first_runtime.build_provider(first_config).generate(generation_request)
    second = second_runtime.build_provider(second_config).generate(repeated_request)
    expected = expected_deterministic_question(
        question_type,
        generation_request.blueprint_slot.marks,
    )

    assert first.question == expected
    assert second.question == expected
    assert first.question == second.question
    assert first.accounting == second.accounting


def test_deterministic_type_stems_are_pairwise_below_the_p8_lexical_warning_threshold() -> None:
    results = {}
    for question_type in QuestionType:
        runtime, config, generation_request = deterministic_runtime_request("test", question_type)
        results[question_type] = runtime.build_provider(config).generate(generation_request)

    stems = {question_type: result.question.stem for question_type, result in results.items()}
    assert len(set(stems.values())) == len(QuestionType)
    expected_scores = {
        (QuestionType.MULTIPLE_CHOICE, QuestionType.SHORT_ANSWER): 6_336,
        (QuestionType.MULTIPLE_CHOICE, QuestionType.STRUCTURED): 3_925,
        (QuestionType.SHORT_ANSWER, QuestionType.STRUCTURED): 2_727,
    }
    policy = LexicalSimilarityPolicy()
    validator = LexicalSimilarityIndicatorValidator(policy=policy)
    assert policy.warning_threshold_basis_points == 8_000

    for candidate_type, reference_type in combinations(QuestionType, 2):
        result = results[candidate_type]
        slot = result.request.blueprint_slot
        validation_input = adapt_generation_result(
            result,
            requirements=BlueprintRequirements(
                slot_id=slot.slot_id,
                schema_version=result.request.versions.schema_version,
                question_type=slot.question_type.value,
                marks=slot.marks,
                language=slot.generation_constraints.response_language,
                minimum_age=9,
                maximum_age=11,
            ),
            duplicate_references=(
                DuplicateReference(
                    question_id=f"fixture-{reference_type.value}",
                    text=stems[reference_type],
                ),
            ),
        )

        finding = validator.validate(validation_input)[0]
        score = expected_scores[(candidate_type, reference_type)]

        assert score < policy.warning_threshold_basis_points
        assert finding.status is FindingStatus.PASS
        assert finding.evidence[0].expected == "threshold_basis_points=8000"
        assert finding.evidence[0].observed == (
            f"score_basis_points={score}; compared_reference_count=1"
        )
        assert "not semantic paraphrase detection" in finding.message.casefold()


def test_local_and_test_runtime_use_only_the_registered_deterministic_route() -> None:
    runtime = create_generation_runtime(Settings(environment="test"))
    registered = runtime.active_config

    assert registered.provider == "deterministic-fake"
    assert registered.provider_version == "1.0.0"
    assert registered.model == "fixture-model"
    assert registered.model_version == "2026-01"
    assert registered.prompt.prompt_id == "question-generation"
    assert registered.prompt.version == "1.0.0"
    assert registered.prompt.schema_version == "question.v1"
    assert registered.pricing.pricing_version == "deterministic-pricing-v1"
    assert registered.parameters.max_output_tokens <= 8_192
    assert 1 <= registered.budgets.max_attempts <= 3

    base = request()
    generation_request = replace(
        base,
        identity=GenerationIdentity(
            generation_id=base.identity.generation_id,
            attempt_id=base.identity.attempt_id,
            idempotency_key="runtime-deterministic-request",
            attempt_number=1,
        ),
        versions=replace(
            base.versions,
            prompt_id=registered.prompt.prompt_id,
            prompt_version=registered.prompt.version,
            provider=registered.provider,
            provider_version=registered.provider_version,
            model=registered.model,
            model_version=registered.model_version,
            retrieval_version=registered.retrieval_version,
            schema_version=registered.prompt.schema_version,
        ),
        parameters=registered.parameters,
    )

    result = runtime.build_provider(registered).generate(generation_request)

    assert result.request is generation_request
    assert result.disposition is CandidateDisposition.REQUIRES_VALIDATION
    assert result.question.question_type is generation_request.blueprint_slot.question_type
    assert result.question.marking.total_marks == generation_request.blueprint_slot.marks
    assert result.accounting.total_tokens == (
        result.accounting.input_tokens + result.accounting.output_tokens
    )
    assert result.accounting.cost_microusd == registered.pricing.cost_microusd(
        input_tokens=result.accounting.input_tokens,
        output_tokens=result.accounting.output_tokens,
    )


def test_non_test_openai_requires_every_explicit_registered_secret_route_and_price() -> None:
    settings = secure_settings(
        generation_provider="openai",
        generation_openai_api_key=SecretStr("provider-secret-key"),
        generation_model="gpt-generation",
        generation_model_version="gpt-generation-2026-08-01",
        generation_pricing_version="openai-price-2026-08",
        generation_input_microusd_per_million_tokens=250_000,
        generation_output_microusd_per_million_tokens=1_000_000,
        generation_timeout_ms=20_000,
    )

    runtime = create_generation_runtime(settings)
    registered = runtime.active_config
    provider = runtime.build_provider(registered)

    assert registered.provider == "openai"
    assert registered.model == "gpt-generation"
    assert registered.model_version == "gpt-generation-2026-08-01"
    assert registered.pricing.pricing_version == "openai-price-2026-08"
    assert isinstance(provider, OpenAIGenerationAdapter)
    assert "provider-secret-key" not in repr(settings)
    assert "provider-secret-key" not in repr(runtime)


@pytest.mark.parametrize(
    "settings",
    [
        lambda: Settings(environment="test", generation_provider="openai"),
        lambda: secure_settings(generation_provider="deterministic"),
        lambda: secure_settings(
            generation_provider="openai",
            generation_openai_api_key=SecretStr("provider-secret-key"),
        ),
        lambda: secure_settings(generation_model="unowned-model-override"),
        lambda: secure_settings(
            generation_provider="openai",
            generation_openai_api_key=SecretStr(" "),
            generation_model="gpt-generation",
            generation_model_version="gpt-generation-2026-08-01",
            generation_pricing_version="openai-price-2026-08",
            generation_input_microusd_per_million_tokens=250_000,
            generation_output_microusd_per_million_tokens=1_000_000,
            generation_timeout_ms=20_000,
        ),
    ],
)
def test_unsafe_or_incomplete_provider_configuration_is_rejected(settings: object) -> None:
    with pytest.raises(ValidationError):
        settings()  # type: ignore[operator]


def test_non_local_runtime_without_an_explicit_provider_is_unavailable_not_fake() -> None:
    runtime = create_generation_runtime(secure_settings())

    with pytest.raises(GenerationRuntimeUnavailableError):
        _ = runtime.active_config


def test_deterministic_runtime_rejects_route_and_prompt_registry_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = create_generation_runtime(Settings(environment="test"))
    config = runtime.active_config
    base = request()
    canonical = replace(
        base,
        versions=replace(
            base.versions,
            prompt_id=config.prompt.prompt_id,
            prompt_version=config.prompt.version,
            provider=config.provider,
            provider_version=config.provider_version,
            model=config.model,
            model_version=config.model_version,
            retrieval_version=config.retrieval_version,
            schema_version=config.prompt.schema_version,
        ),
        parameters=config.parameters,
    )
    provider = runtime.build_provider(config)

    with pytest.raises(ProviderError):
        provider.generate(replace(canonical, versions=replace(canonical.versions, model="forged")))

    def fail_binding(value: object) -> object:
        del value
        raise ValueError("registry failure")

    monkeypatch.setattr(runtime._prompt_registry, "bind", fail_binding)
    with pytest.raises(ProviderError):
        provider.generate(canonical)


def test_deterministic_runtime_generates_constructed_response_and_rejects_unregistered_config() -> (
    None
):
    runtime = create_generation_runtime(Settings(environment="test"))
    config = runtime.active_config
    base = request()
    constraints = replace(
        base.blueprint_slot.generation_constraints,
        required_question_type=QuestionType.SHORT_ANSWER,
    )
    short_answer_slot = replace(
        base.blueprint_slot,
        question_type=QuestionType.SHORT_ANSWER,
        generation_constraints=constraints,
    )
    generation_request = replace(
        base,
        blueprint_slot=short_answer_slot,
        versions=replace(
            base.versions,
            prompt_id=config.prompt.prompt_id,
            prompt_version=config.prompt.version,
            provider=config.provider,
            provider_version=config.provider_version,
            model=config.model,
            model_version=config.model_version,
            retrieval_version=config.retrieval_version,
            schema_version=config.prompt.schema_version,
        ),
        parameters=config.parameters,
    )

    result = runtime.build_provider(config).generate(generation_request)

    assert result.question.question_type is QuestionType.SHORT_ANSWER
    assert result.question.options == ()
    assert result.question.answer.accepted_responses
    with pytest.raises(GenerationRuntimeUnavailableError):
        runtime.build_provider(replace(config, model="unregistered"))

    unavailable_adapter = type(runtime)(replace(config, provider="openai"))
    with pytest.raises(GenerationRuntimeUnavailableError):
        unavailable_adapter.build_provider(unavailable_adapter.active_config)

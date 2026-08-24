from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import cast
from uuid import UUID

import pytest

from exam_guru_api.blueprints import PaperBlueprint, QuestionType, generate_blueprint
from exam_guru_api.generation.domain import (
    ContextProvenance,
    GeneratedQuestion,
    GenerationAccounting,
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
from exam_guru_api.generation.fakes import DeterministicGenerationProvider
from exam_guru_api.generation.ports import (
    GenerationProvider,
    ProviderError,
    ProviderFailure,
    ProviderFailureCode,
)
from tests.test_blueprint_domain import make_uniform_specification

PAPER_BLUEPRINT = generate_blueprint(make_uniform_specification((1,), 1), seed=31)


def question(*, marks: int = 1) -> GeneratedQuestion:
    return GeneratedQuestion(
        question_type=QuestionType.MULTIPLE_CHOICE,
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
            total_marks=marks,
            criteria=(MarkingCriterion("answer", "Selects four.", marks),),
        ),
    )


def accounting() -> GenerationAccounting:
    return GenerationAccounting(120, 30, 150, 400, 12)


def request(
    *,
    identity: GenerationIdentity | None = None,
    text: str = "An even number is divisible by two.",
    provider: str = "deterministic-fake",
    paper: PaperBlueprint = PAPER_BLUEPRINT,
) -> GenerationRequest:
    return GenerationRequest(
        identity=identity
        or GenerationIdentity(
            generation_id=UUID(int=201),
            attempt_id=UUID(int=202),
            idempotency_key="provider-contract-idempotency",
            attempt_number=1,
        ),
        blueprint_version=paper.version,
        blueprint_slot=paper.slots[0],
        context=ProvenanceContext(
            items=(
                RetrievedContextItem(
                    context_id="context-01",
                    text=text,
                    provenance=ContextProvenance(
                        source_document_id="source-01",
                        source_version="reviewed-v1",
                        page_number=3,
                        chunk_id="chunk-01",
                    ),
                ),
            )
        ),
        versions=GenerationVersions(
            blueprint_version=paper.version.blueprint_id,
            prompt_id="question-generation",
            prompt_version="1.0.0",
            provider=provider,
            provider_version="1.0.0",
            model="fixture-model",
            model_version="2026-01",
            retrieval_version="hybrid-v2",
            schema_version="question.v1",
        ),
        parameters=GenerationParameters(temperature=0.0, max_output_tokens=500, seed=9),
    )


def retry_identity(first: GenerationIdentity) -> GenerationIdentity:
    return GenerationIdentity(
        generation_id=first.generation_id,
        attempt_id=UUID(int=203),
        idempotency_key=first.idempotency_key,
        attempt_number=2,
        retry_of_attempt_id=first.attempt_id,
    )


def generate(
    provider: GenerationProvider,
    generation_request: GenerationRequest,
) -> GenerationResult:
    return provider.generate(generation_request)


def test_deterministic_fake_satisfies_port_and_replays_same_attempt() -> None:
    fake = DeterministicGenerationProvider(question=question(), accounting=accounting())
    generation_request = request()

    first = generate(fake, generation_request)
    second = generate(fake, generation_request)

    assert first == second
    assert first.request.versions.provider == "deterministic-fake"
    assert first.accounting == accounting()
    assert fake.requests == (generation_request, generation_request)
    assert fake.unique_attempt_count == 1


def test_fake_exposes_typed_retryable_failure_then_succeeds_with_explicit_lineage() -> None:
    fake = DeterministicGenerationProvider(
        question=question(),
        accounting=accounting(),
        failures={1: ProviderFailureCode.TIMEOUT},
    )
    first_request = request()

    with pytest.raises(ProviderFailure) as raised:
        fake.generate(first_request)

    assert raised.value.code is ProviderFailureCode.TIMEOUT
    assert raised.value.retryable is True
    assert raised.value.identity == first_request.identity

    second_request = replace(first_request, identity=retry_identity(first_request.identity))
    result = fake.generate(second_request)

    assert result.request.identity.attempt_number == 2
    assert result.request.identity.retry_of_attempt_id == first_request.identity.attempt_id
    assert fake.unique_attempt_count == 2


def test_same_failed_attempt_replays_the_same_typed_failure() -> None:
    fake = DeterministicGenerationProvider(
        question=question(),
        accounting=accounting(),
        failures={1: ProviderFailureCode.RATE_LIMITED},
    )
    generation_request = request()

    for _ in range(2):
        with pytest.raises(ProviderFailure) as raised:
            fake.generate(generation_request)
        assert raised.value.code is ProviderFailureCode.RATE_LIMITED
        assert raised.value.retryable is True

    assert fake.requests == (generation_request, generation_request)
    assert fake.unique_attempt_count == 1


def test_idempotency_key_cannot_be_reused_for_changed_generation_input() -> None:
    fake = DeterministicGenerationProvider(question=question(), accounting=accounting())
    first_request = request()
    fake.generate(first_request)
    changed_request = replace(
        request(
            identity=GenerationIdentity(
                generation_id=first_request.identity.generation_id,
                attempt_id=UUID(int=204),
                idempotency_key=first_request.identity.idempotency_key,
                attempt_number=1,
            ),
            text="Changed grounded context.",
        )
    )

    with pytest.raises(ProviderFailure) as raised:
        fake.generate(changed_request)

    assert raised.value.code is ProviderFailureCode.IDEMPOTENCY_CONFLICT
    assert raised.value.retryable is False


def test_blueprint_version_and_slot_are_part_of_the_idempotency_payload() -> None:
    fake = DeterministicGenerationProvider(question=question(), accounting=accounting())
    first_request = request()
    fake.generate(first_request)
    another_paper = generate_blueprint(make_uniform_specification((1,), 1), seed=32)
    changed_request = request(
        identity=GenerationIdentity(
            generation_id=first_request.identity.generation_id,
            attempt_id=UUID(int=207),
            idempotency_key=first_request.identity.idempotency_key,
            attempt_number=1,
        ),
        paper=another_paper,
    )

    with pytest.raises(ProviderFailure) as raised:
        fake.generate(changed_request)

    assert raised.value.code is ProviderFailureCode.IDEMPOTENCY_CONFLICT


def test_retry_must_reference_the_recorded_immediately_previous_attempt() -> None:
    fake = DeterministicGenerationProvider(question=question(), accounting=accounting())
    first_request = request()
    unrecorded_retry = replace(first_request, identity=retry_identity(first_request.identity))

    with pytest.raises(ProviderFailure) as raised:
        fake.generate(unrecorded_retry)

    assert raised.value.code is ProviderFailureCode.INVALID_REQUEST
    assert raised.value.retryable is False


@pytest.mark.parametrize(
    ("generation_id", "idempotency_key", "attempt_number"),
    [
        (UUID(int=999), "new-idempotency-key", 2),
        (UUID(int=201), "new-idempotency-key", 2),
        (UUID(int=201), "provider-contract-idempotency", 3),
    ],
)
def test_retry_must_share_logical_identity_and_reference_the_prior_number(
    generation_id: UUID,
    idempotency_key: str,
    attempt_number: int,
) -> None:
    fake = DeterministicGenerationProvider(question=question(), accounting=accounting())
    first_request = request()
    fake.generate(first_request)
    invalid_retry = request(
        identity=GenerationIdentity(
            generation_id=generation_id,
            attempt_id=UUID(int=205),
            idempotency_key=idempotency_key,
            attempt_number=attempt_number,
            retry_of_attempt_id=first_request.identity.attempt_id,
        )
    )

    with pytest.raises(ProviderFailure) as raised:
        fake.generate(invalid_retry)

    assert raised.value.code is ProviderFailureCode.INVALID_REQUEST


def test_new_attempt_cannot_repeat_attempt_one_for_an_existing_idempotency_key() -> None:
    fake = DeterministicGenerationProvider(question=question(), accounting=accounting())
    first_request = request()
    fake.generate(first_request)
    repeated_first = replace(
        first_request,
        identity=replace(first_request.identity, attempt_id=UUID(int=206)),
    )

    with pytest.raises(ProviderFailure) as raised:
        fake.generate(repeated_first)

    assert raised.value.code is ProviderFailureCode.IDEMPOTENCY_CONFLICT


def test_attempt_identity_cannot_be_reused_for_a_different_request() -> None:
    fake = DeterministicGenerationProvider(question=question(), accounting=accounting())
    first_request = request()
    fake.generate(first_request)
    changed_identity_owner = request(
        identity=replace(first_request.identity, idempotency_key="different-idempotency-key"),
    )

    with pytest.raises(ProviderFailure) as raised:
        fake.generate(changed_identity_owner)

    assert raised.value.code is ProviderFailureCode.IDEMPOTENCY_CONFLICT


def test_fake_rejects_provider_or_model_routing_mismatch_as_typed_failure() -> None:
    fake = DeterministicGenerationProvider(question=question(), accounting=accounting())

    with pytest.raises(ProviderFailure) as raised:
        fake.generate(request(provider="another-provider"))

    assert raised.value.code is ProviderFailureCode.INVALID_REQUEST


@pytest.mark.parametrize(
    "field_name",
    ["provider_version", "model", "model_version"],
)
def test_fake_rejects_each_mismatched_provider_route_field(field_name: str) -> None:
    fake = DeterministicGenerationProvider(question=question(), accounting=accounting())
    generation_request = request()
    mismatched_versions = replace(
        generation_request.versions,
        **{field_name: "mismatched-version"},
    )

    with pytest.raises(ProviderFailure) as raised:
        fake.generate(replace(generation_request, versions=mismatched_versions))

    assert raised.value.code is ProviderFailureCode.INVALID_REQUEST


def test_fake_rejects_untyped_requests_at_the_port_boundary() -> None:
    fake = DeterministicGenerationProvider(question=question(), accounting=accounting())

    with pytest.raises(ProviderFailure) as raised:
        fake.generate(cast(GenerationRequest, "request"))

    assert raised.value.code is ProviderFailureCode.INVALID_REQUEST
    assert raised.value.identity is None


def test_fake_translates_blueprint_incompatible_output_to_invalid_response() -> None:
    fake = DeterministicGenerationProvider(question=question(marks=2), accounting=accounting())

    with pytest.raises(ProviderFailure) as raised:
        fake.generate(request())

    assert raised.value.code is ProviderFailureCode.INVALID_RESPONSE
    assert raised.value.retryable is False
    assert raised.value.__cause__ is not None


@pytest.mark.parametrize(
    ("code", "retryable"),
    [
        (ProviderFailureCode.RATE_LIMITED, True),
        (ProviderFailureCode.TIMEOUT, True),
        (ProviderFailureCode.UNAVAILABLE, True),
        (ProviderFailureCode.AUTHENTICATION, False),
        (ProviderFailureCode.PERMISSION_DENIED, False),
        (ProviderFailureCode.INVALID_REQUEST, False),
        (ProviderFailureCode.CONTEXT_LIMIT_EXCEEDED, False),
        (ProviderFailureCode.CONTENT_FILTERED, False),
        (ProviderFailureCode.INVALID_RESPONSE, False),
        (ProviderFailureCode.IDEMPOTENCY_CONFLICT, False),
    ],
)
def test_provider_failure_codes_have_deterministic_retry_semantics(
    code: ProviderFailureCode,
    retryable: bool,
) -> None:
    failure = ProviderFailure(code, retry_after_ms=100 if retryable else None)

    assert isinstance(failure, ProviderError)
    assert failure.code is code
    assert failure.retryable is retryable
    assert str(failure) == code.value


@pytest.mark.parametrize(
    "build",
    [
        lambda: ProviderFailure(cast(ProviderFailureCode, "timeout")),
        lambda: ProviderFailure(
            ProviderFailureCode.TIMEOUT,
            identity=cast(GenerationIdentity, "identity"),
        ),
        lambda: ProviderFailure(ProviderFailureCode.TIMEOUT, retry_after_ms=-1),
        lambda: ProviderFailure(
            ProviderFailureCode.TIMEOUT,
            retry_after_ms=cast(int, True),
        ),
        lambda: ProviderFailure(ProviderFailureCode.INVALID_REQUEST, retry_after_ms=1),
    ],
)
def test_provider_failure_rejects_malformed_retry_metadata(
    build: Callable[[], object],
) -> None:
    with pytest.raises(ValueError, match=r"must|valid"):
        build()


@pytest.mark.parametrize(
    "failures",
    [
        cast(dict[int, ProviderFailureCode], {0: ProviderFailureCode.TIMEOUT}),
        cast(dict[int, ProviderFailureCode], {1: "timeout"}),
    ],
)
def test_fake_rejects_invalid_failure_schedules(
    failures: dict[int, ProviderFailureCode],
) -> None:
    with pytest.raises(ValueError, match="failure schedule"):
        DeterministicGenerationProvider(
            question=question(),
            accounting=accounting(),
            failures=failures,
        )


@pytest.mark.parametrize(
    "build",
    [
        lambda: DeterministicGenerationProvider(
            question=cast(GeneratedQuestion, "question"),
            accounting=accounting(),
        ),
        lambda: DeterministicGenerationProvider(
            question=question(),
            accounting=cast(GenerationAccounting, "accounting"),
        ),
        lambda: DeterministicGenerationProvider(
            question=question(),
            accounting=accounting(),
            provider=" ",
        ),
        lambda: DeterministicGenerationProvider(
            question=question(),
            accounting=accounting(),
            failures=cast(Mapping[int, ProviderFailureCode], []),
        ),
    ],
)
def test_fake_rejects_malformed_configuration(build: Callable[[], object]) -> None:
    with pytest.raises(ValueError, match="must"):
        build()

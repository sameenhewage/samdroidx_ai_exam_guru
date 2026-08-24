"""Deterministic in-memory generation provider for unit tests and normal CI."""

from collections.abc import Mapping
from typing import cast
from uuid import UUID

from exam_guru_api.blueprints.domain import BlueprintSlot, BlueprintVersion
from exam_guru_api.generation.domain import (
    MAX_GENERATION_ATTEMPTS,
    GeneratedQuestion,
    GenerationAccounting,
    GenerationContractError,
    GenerationParameters,
    GenerationRequest,
    GenerationResult,
    GenerationVersions,
    ProvenanceContext,
)
from exam_guru_api.generation.ports import ProviderFailure, ProviderFailureCode

type _RequestPayload = tuple[
    UUID,
    BlueprintVersion,
    BlueprintSlot,
    ProvenanceContext,
    GenerationVersions,
    GenerationParameters,
]


def _provider_identifier(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 128
        or any(character.isspace() or not character.isprintable() for character in value)
    ):
        raise ValueError(f"{field_name} must be a bounded non-blank identifier")
    return value


class DeterministicGenerationProvider:
    """A recording fake with deterministic outcomes and retry/idempotency checks."""

    def __init__(
        self,
        *,
        question: GeneratedQuestion,
        accounting: GenerationAccounting,
        provider: str = "deterministic-fake",
        provider_version: str = "1.0.0",
        model: str = "fixture-model",
        model_version: str = "2026-01",
        failures: Mapping[int, ProviderFailureCode] | None = None,
    ) -> None:
        if not isinstance(question, GeneratedQuestion):
            raise ValueError("question must be GeneratedQuestion")
        if not isinstance(accounting, GenerationAccounting):
            raise ValueError("accounting must be GenerationAccounting")
        self._question = question
        self._accounting = accounting
        self._provider = _provider_identifier(provider, "provider")
        self._provider_version = _provider_identifier(provider_version, "provider_version")
        self._model = _provider_identifier(model, "model")
        self._model_version = _provider_identifier(model_version, "model_version")
        self._failures = self._snapshot_failures(failures)
        self._requests: list[GenerationRequest] = []
        self._payload_by_idempotency_key: dict[str, _RequestPayload] = {}
        self._request_by_attempt_id: dict[UUID, GenerationRequest] = {}
        self._result_by_attempt_id: dict[UUID, GenerationResult] = {}

    @staticmethod
    def _snapshot_failures(
        failures: Mapping[int, ProviderFailureCode] | None,
    ) -> dict[int, ProviderFailureCode]:
        if failures is None:
            return {}
        if not isinstance(failures, Mapping):
            raise ValueError("failures must be a mapping")
        snapshot: dict[int, ProviderFailureCode] = {}
        for attempt_number, code in failures.items():
            if (
                not isinstance(attempt_number, int)
                or isinstance(attempt_number, bool)
                or not 1 <= attempt_number <= MAX_GENERATION_ATTEMPTS
                or not isinstance(code, ProviderFailureCode)
            ):
                raise ValueError("failure schedule entries are invalid")
            snapshot[attempt_number] = code
        return snapshot

    @property
    def requests(self) -> tuple[GenerationRequest, ...]:
        return tuple(self._requests)

    @property
    def unique_attempt_count(self) -> int:
        return len(self._request_by_attempt_id)

    @staticmethod
    def _payload(request: GenerationRequest) -> _RequestPayload:
        return (
            request.identity.generation_id,
            request.blueprint_version,
            request.blueprint_slot,
            request.context,
            request.versions,
            request.parameters,
        )

    def _validate_route(self, request: GenerationRequest) -> None:
        versions = request.versions
        if (
            versions.provider != self._provider
            or versions.provider_version != self._provider_version
            or versions.model != self._model
            or versions.model_version != self._model_version
        ):
            raise ProviderFailure(
                ProviderFailureCode.INVALID_REQUEST,
                identity=request.identity,
            )

    def _validate_identity(self, request: GenerationRequest) -> None:
        identity = request.identity
        payload = self._payload(request)
        prior_payload = self._payload_by_idempotency_key.get(identity.idempotency_key)
        prior_attempt = self._request_by_attempt_id.get(identity.attempt_id)

        if prior_payload is not None and prior_payload != payload:
            raise ProviderFailure(
                ProviderFailureCode.IDEMPOTENCY_CONFLICT,
                identity=identity,
            )
        if prior_attempt is not None and prior_attempt != request:
            raise ProviderFailure(
                ProviderFailureCode.IDEMPOTENCY_CONFLICT,
                identity=identity,
            )
        if prior_attempt is not None:
            return
        if identity.attempt_number == 1 and prior_payload is not None:
            raise ProviderFailure(
                ProviderFailureCode.IDEMPOTENCY_CONFLICT,
                identity=identity,
            )
        if identity.attempt_number > 1:
            predecessor_id = cast(UUID, identity.retry_of_attempt_id)
            predecessor = self._request_by_attempt_id.get(predecessor_id)
            if (
                predecessor is None
                or predecessor.identity.generation_id != identity.generation_id
                or predecessor.identity.idempotency_key != identity.idempotency_key
                or predecessor.identity.attempt_number != identity.attempt_number - 1
            ):
                raise ProviderFailure(
                    ProviderFailureCode.INVALID_REQUEST,
                    identity=identity,
                )

        self._payload_by_idempotency_key[identity.idempotency_key] = payload
        self._request_by_attempt_id[identity.attempt_id] = request

    def generate(self, request: GenerationRequest) -> GenerationResult:
        if not isinstance(request, GenerationRequest):
            raise ProviderFailure(ProviderFailureCode.INVALID_REQUEST)
        self._requests.append(request)
        self._validate_route(request)
        self._validate_identity(request)

        cached_result = self._result_by_attempt_id.get(request.identity.attempt_id)
        if cached_result is not None:
            return cached_result

        failure_code = self._failures.get(request.identity.attempt_number)
        if failure_code is not None:
            raise ProviderFailure(failure_code, identity=request.identity)

        try:
            result = GenerationResult(
                request=request,
                question=self._question,
                accounting=self._accounting,
            )
        except GenerationContractError as error:
            raise ProviderFailure(
                ProviderFailureCode.INVALID_RESPONSE,
                identity=request.identity,
            ) from error
        self._result_by_attempt_id[request.identity.attempt_id] = result
        return result


DeterministicFakeGenerationProvider = DeterministicGenerationProvider

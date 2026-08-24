"""Provider-independent contracts for grounded question generation.

Retrieved source text is deliberately represented as opaque, untrusted data.  It
can ground a candidate but cannot grant authority, change workflow state, or
publish content.  A generated result has one possible disposition: it requires
validation before any separate review or publication workflow may consume it.
"""

import math
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from exam_guru_api.blueprints.domain import BlueprintSlot, BlueprintVersion, QuestionType

MAX_CONTEXT_ITEMS = 16
MAX_CONTEXT_ITEM_CHARACTERS = 8_000
MAX_CONTEXT_CHARACTERS = 32_000
MAX_GENERATION_ATTEMPTS = 3
MAX_OUTPUT_TOKENS = 8_192
QUESTION_SCHEMA_VERSION = "question.v1"

_MAX_IDENTIFIER_CHARACTERS = 128
_MAX_QUESTION_CHARACTERS = 8_000
_MAX_OPTION_CHARACTERS = 2_000
_MAX_OPTIONS = 8
_MAX_ACCEPTED_RESPONSES = 16
_MAX_ANSWER_CHARACTERS = 1_000
_MAX_EXPLANATION_CHARACTERS = 8_000
_MAX_MARKING_CRITERIA = 32
_MAX_REPORTED_TOKENS = 10_000_000
_MAX_COST_MICROUSD = 1_000_000_000_000
_MAX_LATENCY_MS = 86_400_000


class GenerationContractError(ValueError):
    """Raised when data crossing the generation boundary is malformed."""


class ContextTrust(StrEnum):
    UNTRUSTED_DATA = "untrusted_data"


class CandidateDisposition(StrEnum):
    REQUIRES_VALIDATION = "requires_validation"


def _require_identifier(
    value: object,
    field_name: str,
    *,
    maximum: int = _MAX_IDENTIFIER_CHARACTERS,
) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(not character.isprintable() or character.isspace() for character in value)
    ):
        raise GenerationContractError(f"{field_name} must be a bounded non-blank identifier")
    return value


def _require_text(value: object, field_name: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise GenerationContractError(f"{field_name} must be bounded non-blank text")
    return value


def _require_integer(
    value: object,
    field_name: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise GenerationContractError(
            f"{field_name} must be an integer between {minimum} and {maximum}"
        )
    return value


def _normalized_text(value: str) -> str:
    return " ".join(value.split()).casefold()


@dataclass(frozen=True, slots=True)
class ContextProvenance:
    """Immutable source/page/chunk identity for one retrieved text segment."""

    source_document_id: str
    source_version: str
    page_number: int
    chunk_id: str

    def __post_init__(self) -> None:
        _require_identifier(self.source_document_id, "source_document_id")
        _require_identifier(self.source_version, "source_version")
        _require_integer(self.page_number, "page_number", minimum=1, maximum=1_000_000)
        _require_identifier(self.chunk_id, "chunk_id")


@dataclass(frozen=True, slots=True)
class RetrievedContextItem:
    """Opaque retrieved text paired with required source provenance."""

    context_id: str
    text: str
    provenance: ContextProvenance

    def __post_init__(self) -> None:
        _require_identifier(self.context_id, "context_id")
        _require_text(
            self.text,
            "retrieved context text",
            maximum=MAX_CONTEXT_ITEM_CHARACTERS,
        )
        if not isinstance(self.provenance, ContextProvenance):
            raise GenerationContractError("provenance must be ContextProvenance")


@dataclass(frozen=True, slots=True)
class ProvenanceContext:
    """A fixed-size envelope of untrusted retrieval data.

    ``trust`` is not caller-controlled and has no authoritative variant.  Text is
    validated only for type and resource bounds; it is never parsed as an
    instruction or rewritten by this contract.
    """

    items: tuple[RetrievedContextItem, ...]
    trust: ContextTrust = field(default=ContextTrust.UNTRUSTED_DATA, init=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.items, tuple)
            or not self.items
            or len(self.items) > MAX_CONTEXT_ITEMS
            or any(not isinstance(item, RetrievedContextItem) for item in self.items)
        ):
            raise GenerationContractError(
                f"context items must be a tuple containing 1 to {MAX_CONTEXT_ITEMS} items"
            )
        context_ids = tuple(item.context_id for item in self.items)
        if len(set(context_ids)) != len(context_ids):
            raise GenerationContractError("context identifiers must be unique")
        provenance = tuple(item.provenance for item in self.items)
        if len(set(provenance)) != len(provenance):
            raise GenerationContractError("context provenance references must be unique")
        if self.total_characters > MAX_CONTEXT_CHARACTERS:
            raise GenerationContractError(
                f"retrieved context cannot exceed {MAX_CONTEXT_CHARACTERS} characters"
            )

    @property
    def total_characters(self) -> int:
        return sum(len(item.text) for item in self.items)


@dataclass(frozen=True, slots=True)
class GenerationVersions:
    """Blueprint, prompt, provider/model, retrieval, and schema identities."""

    blueprint_version: str
    prompt_id: str
    prompt_version: str
    provider: str
    provider_version: str
    model: str
    model_version: str
    retrieval_version: str
    schema_version: str = QUESTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name in (
            "blueprint_version",
            "prompt_id",
            "prompt_version",
            "provider",
            "provider_version",
            "model",
            "model_version",
            "retrieval_version",
            "schema_version",
        ):
            _require_identifier(getattr(self, field_name), field_name)


@dataclass(frozen=True, slots=True)
class GenerationIdentity:
    """Logical generation and physical attempt identity for safe retries."""

    generation_id: UUID
    attempt_id: UUID
    idempotency_key: str
    attempt_number: int
    retry_of_attempt_id: UUID | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.generation_id, UUID):
            raise GenerationContractError("generation_id must be a UUID")
        if not isinstance(self.attempt_id, UUID):
            raise GenerationContractError("attempt_id must be a UUID")
        _require_identifier(self.idempotency_key, "idempotency_key")
        _require_integer(
            self.attempt_number,
            "attempt_number",
            minimum=1,
            maximum=MAX_GENERATION_ATTEMPTS,
        )
        if self.attempt_number == 1 and self.retry_of_attempt_id is not None:
            raise GenerationContractError("the first attempt cannot identify a retry predecessor")
        if self.attempt_number > 1 and not isinstance(self.retry_of_attempt_id, UUID):
            raise GenerationContractError("a retry must identify its predecessor attempt")
        if self.retry_of_attempt_id == self.attempt_id:
            raise GenerationContractError("an attempt cannot retry itself")


@dataclass(frozen=True, slots=True)
class GenerationParameters:
    """A deliberately small, bounded, provider-neutral generation configuration."""

    temperature: float
    max_output_tokens: int
    seed: int | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.temperature, (int, float))
            or isinstance(self.temperature, bool)
            or not math.isfinite(self.temperature)
            or not 0.0 <= self.temperature <= 2.0
        ):
            raise GenerationContractError("temperature must be a finite number between 0 and 2")
        _require_integer(
            self.max_output_tokens,
            "max_output_tokens",
            minimum=1,
            maximum=MAX_OUTPUT_TOKENS,
        )
        if self.seed is not None:
            _require_integer(
                self.seed,
                "seed",
                minimum=-(2**63),
                maximum=2**63 - 1,
            )


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    """Complete provider-neutral input for exactly one generation attempt."""

    identity: GenerationIdentity
    blueprint_version: BlueprintVersion
    blueprint_slot: BlueprintSlot
    context: ProvenanceContext
    versions: GenerationVersions
    parameters: GenerationParameters

    def __post_init__(self) -> None:
        if not isinstance(self.identity, GenerationIdentity):
            raise GenerationContractError("identity must be GenerationIdentity")
        if not isinstance(self.blueprint_version, BlueprintVersion):
            raise GenerationContractError("blueprint_version must be BlueprintVersion")
        if not isinstance(self.blueprint_slot, BlueprintSlot):
            raise GenerationContractError("blueprint_slot must be BlueprintSlot")
        if not isinstance(self.context, ProvenanceContext):
            raise GenerationContractError("context must be ProvenanceContext")
        if not isinstance(self.versions, GenerationVersions):
            raise GenerationContractError("versions must be GenerationVersions")
        if not isinstance(self.parameters, GenerationParameters):
            raise GenerationContractError("parameters must be GenerationParameters")
        if self.versions.blueprint_version != self.blueprint_version.blueprint_id:
            raise GenerationContractError(
                "generation blueprint version must match the canonical blueprint identity"
            )


@dataclass(frozen=True, slots=True)
class QuestionOption:
    option_id: str
    text: str

    def __post_init__(self) -> None:
        _require_identifier(self.option_id, "option_id", maximum=32)
        _require_text(self.text, "option text", maximum=_MAX_OPTION_CHARACTERS)


@dataclass(frozen=True, slots=True)
class QuestionAnswer:
    """Exactly one answer mode: one MCQ option or accepted constructed responses."""

    explanation: str
    correct_option_id: str | None = None
    accepted_responses: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(
            self.explanation,
            "answer explanation",
            maximum=_MAX_EXPLANATION_CHARACTERS,
        )
        if self.correct_option_id is not None:
            _require_identifier(self.correct_option_id, "correct_option_id", maximum=32)
        if not isinstance(self.accepted_responses, tuple):
            raise GenerationContractError("accepted_responses must be a tuple")
        if len(self.accepted_responses) > _MAX_ACCEPTED_RESPONSES:
            raise GenerationContractError("accepted_responses exceeds the fixed limit")
        for response in self.accepted_responses:
            _require_text(
                response,
                "accepted response",
                maximum=_MAX_ANSWER_CHARACTERS,
            )
        normalized_responses = tuple(
            _normalized_text(response) for response in self.accepted_responses
        )
        if len(set(normalized_responses)) != len(normalized_responses):
            raise GenerationContractError("accepted_responses must be unique")
        has_option = self.correct_option_id is not None
        has_responses = bool(self.accepted_responses)
        if has_option == has_responses:
            raise GenerationContractError("answer must use exactly one answer mode")


@dataclass(frozen=True, slots=True)
class MarkingCriterion:
    criterion_id: str
    description: str
    marks: int

    def __post_init__(self) -> None:
        _require_identifier(self.criterion_id, "criterion_id")
        _require_text(
            self.description,
            "marking criterion description",
            maximum=_MAX_EXPLANATION_CHARACTERS,
        )
        _require_integer(self.marks, "criterion marks", minimum=1, maximum=100)


@dataclass(frozen=True, slots=True)
class MarkingScheme:
    total_marks: int
    criteria: tuple[MarkingCriterion, ...]

    def __post_init__(self) -> None:
        _require_integer(self.total_marks, "total_marks", minimum=1, maximum=100)
        if (
            not isinstance(self.criteria, tuple)
            or not self.criteria
            or len(self.criteria) > _MAX_MARKING_CRITERIA
            or any(not isinstance(criterion, MarkingCriterion) for criterion in self.criteria)
        ):
            raise GenerationContractError("criteria must be a bounded non-empty tuple")
        criterion_ids = tuple(criterion.criterion_id for criterion in self.criteria)
        if len(set(criterion_ids)) != len(criterion_ids):
            raise GenerationContractError("marking criterion identifiers must be unique")
        if sum(criterion.marks for criterion in self.criteria) != self.total_marks:
            raise GenerationContractError("marking criterion marks must sum to total_marks")


@dataclass(frozen=True, slots=True)
class GeneratedQuestion:
    """Strict provider output schema for a generated question candidate."""

    question_type: QuestionType
    stem: str
    options: tuple[QuestionOption, ...]
    answer: QuestionAnswer
    marking: MarkingScheme

    def __post_init__(self) -> None:
        if not isinstance(self.question_type, QuestionType):
            raise GenerationContractError("generated question_type must be a QuestionType")
        _require_text(self.stem, "question stem", maximum=_MAX_QUESTION_CHARACTERS)
        if not isinstance(self.options, tuple) or any(
            not isinstance(option, QuestionOption) for option in self.options
        ):
            raise GenerationContractError("options must be a tuple of QuestionOption values")
        if len(self.options) > _MAX_OPTIONS:
            raise GenerationContractError("options exceeds the fixed limit")
        if not isinstance(self.answer, QuestionAnswer):
            raise GenerationContractError("answer must be QuestionAnswer")
        if not isinstance(self.marking, MarkingScheme):
            raise GenerationContractError("marking must be MarkingScheme")

        option_ids = tuple(option.option_id for option in self.options)
        if len(set(option_ids)) != len(option_ids):
            raise GenerationContractError("option identifiers must be unique")
        normalized_options = tuple(_normalized_text(option.text) for option in self.options)
        if len(set(normalized_options)) != len(normalized_options):
            raise GenerationContractError("option text must be unique")

        if self.question_type is QuestionType.MULTIPLE_CHOICE:
            if len(self.options) < 2:
                raise GenerationContractError(
                    "multiple-choice questions require at least two options"
                )
            if self.answer.correct_option_id not in option_ids:
                raise GenerationContractError(
                    "the correct option must identify one supplied option"
                )
        elif self.options:
            raise GenerationContractError("constructed-response questions cannot contain options")
        elif not self.answer.accepted_responses:
            raise GenerationContractError(
                "constructed-response questions require accepted responses"
            )


@dataclass(frozen=True, slots=True)
class GenerationAccounting:
    """Exact per-attempt token, micro-USD cost, and latency accounting."""

    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_microusd: int
    latency_ms: int

    def __post_init__(self) -> None:
        _require_integer(
            self.input_tokens,
            "input_tokens",
            minimum=0,
            maximum=_MAX_REPORTED_TOKENS,
        )
        _require_integer(
            self.output_tokens,
            "output_tokens",
            minimum=0,
            maximum=_MAX_REPORTED_TOKENS,
        )
        _require_integer(
            self.total_tokens,
            "total_tokens",
            minimum=0,
            maximum=_MAX_REPORTED_TOKENS,
        )
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise GenerationContractError("total_tokens must equal input_tokens plus output_tokens")
        _require_integer(
            self.cost_microusd,
            "cost_microusd",
            minimum=0,
            maximum=_MAX_COST_MICROUSD,
        )
        _require_integer(
            self.latency_ms,
            "latency_ms",
            minimum=0,
            maximum=_MAX_LATENCY_MS,
        )

    @property
    def cost_usd(self) -> Decimal:
        return (Decimal(self.cost_microusd) / Decimal(1_000_000)).quantize(Decimal("0.000001"))


@dataclass(frozen=True, slots=True)
class GenerationResult:
    """One unvalidated candidate plus complete reproducibility and usage lineage."""

    request: GenerationRequest
    question: GeneratedQuestion
    accounting: GenerationAccounting
    disposition: CandidateDisposition = field(
        default=CandidateDisposition.REQUIRES_VALIDATION,
        init=False,
    )

    def __post_init__(self) -> None:
        if not isinstance(self.request, GenerationRequest):
            raise GenerationContractError("request must be GenerationRequest")
        if not isinstance(self.question, GeneratedQuestion):
            raise GenerationContractError("question must be GeneratedQuestion")
        if not isinstance(self.accounting, GenerationAccounting):
            raise GenerationContractError("accounting must be GenerationAccounting")
        if self.question.question_type is not self.request.blueprint_slot.question_type:
            raise GenerationContractError("generated question type must match the blueprint slot")
        if self.question.marking.total_marks != self.request.blueprint_slot.marks:
            raise GenerationContractError("generated marking marks must match the blueprint slot")
        if self.accounting.output_tokens > self.request.parameters.max_output_tokens:
            raise GenerationContractError("reported output tokens exceed the requested token limit")

    @property
    def context_provenance(self) -> tuple[ContextProvenance, ...]:
        return tuple(item.provenance for item in self.request.context.items)

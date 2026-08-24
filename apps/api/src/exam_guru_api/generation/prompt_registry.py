"""Append-only, exactly-versioned prompt templates with safe context binding."""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from exam_guru_api.blueprints.domain import BlueprintSlot, BlueprintVersion
from exam_guru_api.generation.domain import (
    ContextTrust,
    GenerationRequest,
    ProvenanceContext,
)

_MAX_PROMPT_IDENTIFIER_CHARACTERS = 128
_MAX_PROMPT_INSTRUCTION_CHARACTERS = 20_000
_PLACEHOLDER_PATTERN = re.compile(r"\{([^{}]+)\}")
_RESERVED_CONTEXT_PLACEHOLDERS = frozenset({"context", "retrievedtext"})


class PromptRegistryError(ValueError):
    """Base error for prompt registration, lookup, and binding."""


class PromptAlreadyRegisteredError(PromptRegistryError):
    pass


class PromptNotFoundError(PromptRegistryError):
    pass


class PromptBindingError(PromptRegistryError):
    pass


def _require_prompt_identifier(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > _MAX_PROMPT_IDENTIFIER_CHARACTERS
        or any(character.isspace() or not character.isprintable() for character in value)
    ):
        raise PromptRegistryError(f"{field_name} must be a bounded non-blank identifier")
    return value


def _require_instructions(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > _MAX_PROMPT_INSTRUCTION_CHARACTERS
    ):
        raise PromptRegistryError(f"{field_name} must be bounded non-blank text")
    for placeholder in _PLACEHOLDER_PATTERN.findall(value):
        root = re.split(r"[!:.\[]", placeholder, maxsplit=1)[0]
        normalized_root = re.sub(r"[\s_]+", "", root).casefold()
        if normalized_root in _RESERVED_CONTEXT_PLACEHOLDERS:
            raise PromptRegistryError(f"{field_name} contains a reserved context placeholder")
    return value


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    """One immutable behavior version containing trusted instructions only."""

    prompt_id: str
    version: str
    schema_version: str
    system_instructions: str
    task_instructions: str

    def __post_init__(self) -> None:
        _require_prompt_identifier(self.prompt_id, "prompt_id")
        _require_prompt_identifier(self.version, "version")
        _require_prompt_identifier(self.schema_version, "schema_version")
        _require_instructions(self.system_instructions, "system_instructions")
        _require_instructions(self.task_instructions, "task_instructions")


@dataclass(frozen=True, slots=True)
class BoundPrompt:
    """Trusted instructions and untrusted retrieval data kept in separate fields."""

    prompt_id: str
    prompt_version: str
    schema_version: str
    trusted_system_instructions: str
    trusted_task_instructions: str
    blueprint_version: BlueprintVersion
    blueprint_slot: BlueprintSlot
    untrusted_context: ProvenanceContext
    context_trust: ContextTrust = field(default=ContextTrust.UNTRUSTED_DATA, init=False)

    def __post_init__(self) -> None:
        _require_prompt_identifier(self.prompt_id, "prompt_id")
        _require_prompt_identifier(self.prompt_version, "prompt_version")
        _require_prompt_identifier(self.schema_version, "schema_version")
        _require_instructions(
            self.trusted_system_instructions,
            "trusted_system_instructions",
        )
        _require_instructions(
            self.trusted_task_instructions,
            "trusted_task_instructions",
        )
        if not isinstance(self.blueprint_version, BlueprintVersion):
            raise PromptBindingError("blueprint_version must be BlueprintVersion")
        if not isinstance(self.blueprint_slot, BlueprintSlot):
            raise PromptBindingError("blueprint_slot must be BlueprintSlot")
        if not isinstance(self.untrusted_context, ProvenanceContext):
            raise PromptBindingError("untrusted_context must be ProvenanceContext")


class PromptRegistry:
    """An in-memory append-only registry requiring exact prompt versions."""

    def __init__(self, templates: Iterable[PromptTemplate] = ()) -> None:
        self._templates: dict[tuple[str, str], PromptTemplate] = {}
        for template in templates:
            self.register(template)

    @property
    def templates(self) -> tuple[PromptTemplate, ...]:
        return tuple(self._templates[key] for key in sorted(self._templates))

    def register(self, template: PromptTemplate) -> None:
        if not isinstance(template, PromptTemplate):
            raise PromptRegistryError("template must be PromptTemplate")
        key = (template.prompt_id, template.version)
        if key in self._templates:
            raise PromptAlreadyRegisteredError(
                f"prompt {template.prompt_id}@{template.version} is already registered"
            )
        self._templates[key] = template

    def resolve(self, prompt_id: str, version: str) -> PromptTemplate:
        _require_prompt_identifier(prompt_id, "prompt_id")
        _require_prompt_identifier(version, "version")
        try:
            return self._templates[(prompt_id, version)]
        except KeyError as error:
            raise PromptNotFoundError(f"prompt {prompt_id}@{version} is not registered") from error

    def versions(self, prompt_id: str) -> tuple[str, ...]:
        _require_prompt_identifier(prompt_id, "prompt_id")
        return tuple(
            sorted(
                version
                for registered_prompt_id, version in self._templates
                if registered_prompt_id == prompt_id
            )
        )

    def bind(self, request: GenerationRequest) -> BoundPrompt:
        if not isinstance(request, GenerationRequest):
            raise PromptBindingError("request must be GenerationRequest")
        template = self.resolve(
            request.versions.prompt_id,
            request.versions.prompt_version,
        )
        if template.schema_version != request.versions.schema_version:
            raise PromptBindingError("prompt and generation schema versions do not match")
        return BoundPrompt(
            prompt_id=template.prompt_id,
            prompt_version=template.version,
            schema_version=template.schema_version,
            trusted_system_instructions=template.system_instructions,
            trusted_task_instructions=template.task_instructions,
            blueprint_version=request.blueprint_version,
            blueprint_slot=request.blueprint_slot,
            untrusted_context=request.context,
        )

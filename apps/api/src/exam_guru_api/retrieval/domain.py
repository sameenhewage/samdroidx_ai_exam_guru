"""Provider-independent contracts for deterministic, scope-safe retrieval.

Retrieved source text is opaque data.  These contracts deliberately do not
parse, execute, or otherwise interpret its contents.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast
from uuid import UUID

from exam_guru_api.curriculum.domain import LEGACY_UNCLASSIFIED_SUBJECT_ID

MAX_RECORD_CHARACTERS = 100_000
MAX_FINGERPRINT_CHARACTERS = 512


class RetrievalContractError(ValueError):
    """Raised when a retrieval boundary receives a malformed value."""


def _require_uuid(value: object, *, field_name: str) -> None:
    if not isinstance(value, UUID):
        raise RetrievalContractError(f"{field_name} must be a UUID")


def _require_optional_uuid(value: object, *, field_name: str) -> None:
    if value is not None:
        _require_uuid(value, field_name=field_name)


def _require_finite_score(value: object, *, field_name: str) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise RetrievalContractError(f"{field_name} must be a finite number")


def _require_fingerprint(value: object) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > MAX_FINGERPRINT_CHARACTERS
    ):
        raise RetrievalContractError("embedding_config_fingerprint must be non-blank and bounded")


@dataclass(frozen=True, slots=True)
class TaxonomyScope:
    """A selected hierarchy path, from competency to an optional concept."""

    competency_id: UUID
    skill_id: UUID | None = None
    sub_skill_id: UUID | None = None
    learning_concept_id: UUID | None = None

    def __post_init__(self) -> None:
        _require_uuid(self.competency_id, field_name="competency_id")
        _require_optional_uuid(self.skill_id, field_name="skill_id")
        _require_optional_uuid(self.sub_skill_id, field_name="sub_skill_id")
        _require_optional_uuid(self.learning_concept_id, field_name="learning_concept_id")
        if self.sub_skill_id is not None and self.skill_id is None:
            raise RetrievalContractError("sub_skill_id requires skill_id")
        if self.learning_concept_id is not None and self.sub_skill_id is None:
            raise RetrievalContractError("learning_concept_id requires sub_skill_id")

    def allows(self, candidate: TaxonomyScope) -> bool:
        """Return whether ``candidate`` stays within this selected hierarchy path."""

        if not isinstance(candidate, TaxonomyScope):
            return False
        return all(
            selected is None or selected == candidate_value
            for selected, candidate_value in (
                (self.competency_id, candidate.competency_id),
                (self.skill_id, candidate.skill_id),
                (self.sub_skill_id, candidate.sub_skill_id),
                (self.learning_concept_id, candidate.learning_concept_id),
            )
        )


@dataclass(frozen=True, slots=True)
class RetrievalScope:
    """Mandatory hard scope for both a request and a retrievable record.

    A request may select a broader taxonomy ancestor, but grade, exam, medium,
    and curriculum version are always exact-match boundaries.
    """

    grade: int
    exam_id: UUID
    medium_id: UUID
    curriculum_version_id: UUID
    taxonomy: TaxonomyScope
    subject_id: UUID = LEGACY_UNCLASSIFIED_SUBJECT_ID
    unit_ids: tuple[UUID, ...] = ()
    lesson_ids: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.grade, int)
            or isinstance(self.grade, bool)
            or not 1 <= self.grade <= 13
        ):
            raise RetrievalContractError("grade must be an integer between 1 and 13")
        _require_uuid(self.exam_id, field_name="exam_id")
        _require_uuid(self.medium_id, field_name="medium_id")
        _require_uuid(self.subject_id, field_name="subject_id")
        _require_uuid(self.curriculum_version_id, field_name="curriculum_version_id")
        if not isinstance(self.taxonomy, TaxonomyScope):
            raise RetrievalContractError("taxonomy must be a TaxonomyScope")
        for field_name, values in (("unit_ids", self.unit_ids), ("lesson_ids", self.lesson_ids)):
            if not isinstance(values, tuple) or any(
                not isinstance(value, UUID) for value in values
            ):
                raise RetrievalContractError(f"{field_name} must be a tuple of UUID values")
            if len(values) != len(set(values)):
                raise RetrievalContractError(f"{field_name} must not contain duplicates")
        if self.lesson_ids and not self.unit_ids:
            raise RetrievalContractError("lesson_ids require unit_ids")

    def allows(self, candidate: RetrievalScope) -> bool:
        """Apply all hard metadata boundaries before relevance ranking."""

        return (
            isinstance(candidate, RetrievalScope)
            and self.grade == candidate.grade
            and self.exam_id == candidate.exam_id
            and self.medium_id == candidate.medium_id
            and self.subject_id == candidate.subject_id
            and self.curriculum_version_id == candidate.curriculum_version_id
            and self._selection_allows(self.unit_ids, candidate.unit_ids)
            and self._selection_allows(self.lesson_ids, candidate.lesson_ids)
            and self.taxonomy.allows(candidate.taxonomy)
        )

    @staticmethod
    def _selection_allows(selected: tuple[UUID, ...], candidate: tuple[UUID, ...]) -> bool:
        if not selected:
            return True
        return bool(candidate) and set(candidate).issubset(selected)


@dataclass(frozen=True, slots=True)
class RetrievalScopeSet:
    policy_version: str
    scopes: tuple[RetrievalScope, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.policy_version, str)
            or not self.policy_version
            or self.policy_version != self.policy_version.strip()
            or len(self.policy_version) > 128
        ):
            raise RetrievalContractError("policy_version must be non-blank and bounded")
        if (
            not isinstance(self.scopes, tuple)
            or not self.scopes
            or len(self.scopes) > 64
            or any(not isinstance(scope, RetrievalScope) for scope in self.scopes)
        ):
            raise RetrievalContractError("scopes must contain one to 64 RetrievalScope values")
        if len(set(self.scopes)) != len(self.scopes):
            raise RetrievalContractError("scopes must not contain duplicates")
        if len({scope.medium_id for scope in self.scopes}) != 1:
            raise RetrievalContractError("all policy scopes must use the same medium")

    def allows(self, candidate: RetrievalScope) -> bool:
        return isinstance(candidate, RetrievalScope) and any(
            scope.allows(candidate) for scope in self.scopes
        )


RetrievalFilters = RetrievalScope | RetrievalScopeSet


def serialize_retrieval_scope(scope: RetrievalScope) -> dict[str, object]:
    if not isinstance(scope, RetrievalScope):
        raise RetrievalContractError("scope must be a RetrievalScope")
    return {
        "curriculum_version_id": str(scope.curriculum_version_id),
        "exam_id": str(scope.exam_id),
        "grade": scope.grade,
        "lesson_ids": [str(value) for value in scope.lesson_ids],
        "medium_id": str(scope.medium_id),
        "subject_id": str(scope.subject_id),
        "taxonomy": {
            "competency_id": str(scope.taxonomy.competency_id),
            "learning_concept_id": (
                None
                if scope.taxonomy.learning_concept_id is None
                else str(scope.taxonomy.learning_concept_id)
            ),
            "skill_id": None if scope.taxonomy.skill_id is None else str(scope.taxonomy.skill_id),
            "sub_skill_id": (
                None if scope.taxonomy.sub_skill_id is None else str(scope.taxonomy.sub_skill_id)
            ),
        },
        "unit_ids": [str(value) for value in scope.unit_ids],
    }


def serialize_retrieval_filters(filters: RetrievalFilters) -> dict[str, object]:
    if isinstance(filters, RetrievalScope):
        return {"kind": "scope", "scope": serialize_retrieval_scope(filters)}
    if isinstance(filters, RetrievalScopeSet):
        return {
            "kind": "scope_set",
            "policy_version": filters.policy_version,
            "scopes": [serialize_retrieval_scope(scope) for scope in filters.scopes],
        }
    raise RetrievalContractError("filters must be a RetrievalScope or RetrievalScopeSet")


def _snapshot_uuid(value: object, field_name: str) -> UUID:
    if not isinstance(value, str):
        raise RetrievalContractError(f"{field_name} must be UUID text")
    try:
        return UUID(value)
    except ValueError as error:
        raise RetrievalContractError(f"{field_name} must be UUID text") from error


def _snapshot_uuid_tuple(value: object, field_name: str) -> tuple[UUID, ...]:
    if not isinstance(value, list):
        raise RetrievalContractError(f"{field_name} must be a UUID array")
    return tuple(_snapshot_uuid(item, field_name) for item in value)


def deserialize_retrieval_scope(value: object) -> RetrievalScope:
    expected_keys = {
        "curriculum_version_id",
        "exam_id",
        "grade",
        "lesson_ids",
        "medium_id",
        "subject_id",
        "taxonomy",
        "unit_ids",
    }
    if not isinstance(value, Mapping) or set(value) != expected_keys:
        raise RetrievalContractError("retrieval scope snapshot has an invalid shape")
    taxonomy = value["taxonomy"]
    taxonomy_keys = {"competency_id", "learning_concept_id", "skill_id", "sub_skill_id"}
    if not isinstance(taxonomy, Mapping) or set(taxonomy) != taxonomy_keys:
        raise RetrievalContractError("retrieval taxonomy snapshot has an invalid shape")

    def optional_uuid(name: str) -> UUID | None:
        raw = taxonomy[name]
        return None if raw is None else _snapshot_uuid(raw, name)

    return RetrievalScope(
        grade=cast(int, value["grade"]),
        exam_id=_snapshot_uuid(value["exam_id"], "exam_id"),
        medium_id=_snapshot_uuid(value["medium_id"], "medium_id"),
        subject_id=_snapshot_uuid(value["subject_id"], "subject_id"),
        curriculum_version_id=_snapshot_uuid(
            value["curriculum_version_id"], "curriculum_version_id"
        ),
        unit_ids=_snapshot_uuid_tuple(value["unit_ids"], "unit_ids"),
        lesson_ids=_snapshot_uuid_tuple(value["lesson_ids"], "lesson_ids"),
        taxonomy=TaxonomyScope(
            competency_id=_snapshot_uuid(taxonomy["competency_id"], "competency_id"),
            skill_id=optional_uuid("skill_id"),
            sub_skill_id=optional_uuid("sub_skill_id"),
            learning_concept_id=optional_uuid("learning_concept_id"),
        ),
    )


def deserialize_retrieval_filters(value: object) -> RetrievalFilters:
    if not isinstance(value, Mapping):
        raise RetrievalContractError("retrieval filter snapshot must be an object")
    if value.get("kind") == "scope" and set(value) == {"kind", "scope"}:
        return deserialize_retrieval_scope(value["scope"])
    if value.get("kind") == "scope_set" and set(value) == {
        "kind",
        "policy_version",
        "scopes",
    }:
        scopes = value["scopes"]
        if not isinstance(scopes, list):
            raise RetrievalContractError("retrieval scope set snapshot must contain scopes")
        return RetrievalScopeSet(
            policy_version=cast(str, value["policy_version"]),
            scopes=tuple(deserialize_retrieval_scope(scope) for scope in scopes),
        )
    raise RetrievalContractError("retrieval filter snapshot has an invalid shape")


@dataclass(frozen=True, slots=True)
class SourceProvenance:
    """Immutable source location carried through retrieval and context building."""

    source_document_id: UUID
    page_number: int
    source_block_id: UUID | None = None

    def __post_init__(self) -> None:
        _require_uuid(self.source_document_id, field_name="source_document_id")
        if (
            not isinstance(self.page_number, int)
            or isinstance(self.page_number, bool)
            or self.page_number < 1
        ):
            raise RetrievalContractError("page_number must be a positive integer")
        _require_optional_uuid(self.source_block_id, field_name="source_block_id")


@dataclass(frozen=True, slots=True)
class RetrievalRecord:
    """One retrievable source segment; ``text`` remains opaque and untrusted."""

    chunk_id: UUID
    text: str
    scope: RetrievalScope
    provenance: SourceProvenance

    def __post_init__(self) -> None:
        _require_uuid(self.chunk_id, field_name="chunk_id")
        if (
            not isinstance(self.text, str)
            or not self.text.strip()
            or len(self.text) > MAX_RECORD_CHARACTERS
            or any(
                (ord(character) < 32 and character not in "\t\n\r") or 127 <= ord(character) <= 159
                for character in self.text
            )
        ):
            raise RetrievalContractError("record text must be non-blank, bounded, and control-safe")
        if not isinstance(self.scope, RetrievalScope):
            raise RetrievalContractError("record scope must be a RetrievalScope")
        if not isinstance(self.provenance, SourceProvenance):
            raise RetrievalContractError("record provenance must be SourceProvenance")


@dataclass(frozen=True, slots=True)
class LexicalCandidate:
    """A lexical retriever result whose score is used only to establish rank."""

    record: RetrievalRecord
    score: float

    def __post_init__(self) -> None:
        if not isinstance(self.record, RetrievalRecord):
            raise RetrievalContractError("lexical candidate record must be a RetrievalRecord")
        _require_finite_score(self.score, field_name="lexical score")


@dataclass(frozen=True, slots=True)
class VectorCandidate:
    """A vector result bound to one reproducible embedding configuration."""

    record: RetrievalRecord
    score: float
    embedding_config_fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.record, RetrievalRecord):
            raise RetrievalContractError("vector candidate record must be a RetrievalRecord")
        _require_finite_score(self.score, field_name="vector score")
        _require_fingerprint(self.embedding_config_fingerprint)


def validate_embedding_config_fingerprint(value: object) -> str:
    """Validate and return an embedding fingerprint at orchestration boundaries."""

    _require_fingerprint(value)
    return cast(str, value)

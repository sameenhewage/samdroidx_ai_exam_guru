import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

_CODE_PATTERN = re.compile(r"[A-Z0-9]+(?:[._-][A-Z0-9]+)*")


class TaxonomyLevel(StrEnum):
    COMPETENCY = "competency"
    SKILL = "skill"
    SUB_SKILL = "sub_skill"
    LEARNING_CONCEPT = "learning_concept"


class TaxonomyViolation(StrEnum):
    INVALID_CODE = "invalid_code"
    INVALID_TITLE = "invalid_title"
    PARENT_NOT_ALLOWED = "parent_not_allowed"
    PARENT_REQUIRED = "parent_required"
    DUPLICATE_ID = "duplicate_id"
    DUPLICATE_SIBLING_CODE = "duplicate_sibling_code"
    PARENT_NOT_FOUND = "parent_not_found"
    INVALID_PARENT_LEVEL = "invalid_parent_level"
    CROSS_CURRICULUM_PARENT = "cross_curriculum_parent"
    INACTIVE_PARENT = "inactive_parent"


class TaxonomyValidationError(ValueError):
    def __init__(self, violation: TaxonomyViolation, node_id: UUID) -> None:
        self.violation = violation
        self.node_id = node_id
        super().__init__(f"{violation.value}: {node_id}")


@dataclass(frozen=True, slots=True)
class TaxonomyNode:
    id: UUID
    curriculum_version_id: UUID
    level: TaxonomyLevel
    code: str
    title: str
    parent_id: UUID | None = None
    active: bool = True

    def __post_init__(self) -> None:
        if len(self.code) > 64 or _CODE_PATTERN.fullmatch(self.code) is None:
            raise TaxonomyValidationError(TaxonomyViolation.INVALID_CODE, self.id)
        if not self.title or self.title != self.title.strip() or len(self.title) > 255:
            raise TaxonomyValidationError(TaxonomyViolation.INVALID_TITLE, self.id)
        if self.level is TaxonomyLevel.COMPETENCY and self.parent_id is not None:
            raise TaxonomyValidationError(TaxonomyViolation.PARENT_NOT_ALLOWED, self.id)
        if self.level is not TaxonomyLevel.COMPETENCY and self.parent_id is None:
            raise TaxonomyValidationError(TaxonomyViolation.PARENT_REQUIRED, self.id)


_EXPECTED_PARENT_LEVEL = {
    TaxonomyLevel.SKILL: TaxonomyLevel.COMPETENCY,
    TaxonomyLevel.SUB_SKILL: TaxonomyLevel.SKILL,
    TaxonomyLevel.LEARNING_CONCEPT: TaxonomyLevel.SUB_SKILL,
}


def validate_taxonomy(nodes: Iterable[TaxonomyNode]) -> tuple[TaxonomyNode, ...]:
    validated_nodes = tuple(nodes)
    nodes_by_id: dict[UUID, TaxonomyNode] = {}
    sibling_keys: set[tuple[UUID, UUID | None, TaxonomyLevel, str]] = set()

    for node in validated_nodes:
        if node.id in nodes_by_id:
            raise TaxonomyValidationError(TaxonomyViolation.DUPLICATE_ID, node.id)
        nodes_by_id[node.id] = node
        sibling_key = (node.curriculum_version_id, node.parent_id, node.level, node.code)
        if sibling_key in sibling_keys:
            raise TaxonomyValidationError(TaxonomyViolation.DUPLICATE_SIBLING_CODE, node.id)
        sibling_keys.add(sibling_key)

    for node in validated_nodes:
        if node.parent_id is None:
            continue
        parent = nodes_by_id.get(node.parent_id)
        if parent is None:
            raise TaxonomyValidationError(TaxonomyViolation.PARENT_NOT_FOUND, node.id)
        if parent.curriculum_version_id != node.curriculum_version_id:
            raise TaxonomyValidationError(TaxonomyViolation.CROSS_CURRICULUM_PARENT, node.id)
        if parent.level is not _EXPECTED_PARENT_LEVEL[node.level]:
            raise TaxonomyValidationError(TaxonomyViolation.INVALID_PARENT_LEVEL, node.id)
        if node.active and not parent.active:
            raise TaxonomyValidationError(TaxonomyViolation.INACTIVE_PARENT, node.id)

    return validated_nodes

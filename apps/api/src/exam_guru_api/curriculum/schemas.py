from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from exam_guru_api.curriculum.domain import TaxonomyLevel, TaxonomyNode


class TaxonomyNodeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent_id: UUID | None = None
    level: TaxonomyLevel
    code: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=255)
    active: bool = True


class TaxonomyNodeResponse(BaseModel):
    id: UUID
    curriculum_version_id: UUID
    parent_id: UUID | None
    level: TaxonomyLevel
    code: str
    title: str
    active: bool

    @classmethod
    def from_domain(cls, node: TaxonomyNode) -> "TaxonomyNodeResponse":
        return cls(
            id=node.id,
            curriculum_version_id=node.curriculum_version_id,
            parent_id=node.parent_id,
            level=node.level,
            code=node.code,
            title=node.title,
            active=node.active,
        )

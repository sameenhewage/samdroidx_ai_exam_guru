from datetime import datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from exam_guru_api.curriculum.domain import LEGACY_UNCLASSIFIED_SUBJECT_ID

CODE_PATTERN = r"^[A-Z0-9]+(?:[._-][A-Z0-9]+)*$"
MEDIUM_CODE_PATTERN = r"^[a-z][a-z0-9-]{1,15}$"
CleanName = Annotated[str, StringConstraints(min_length=1, max_length=255)]


class _StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExamConfigurationCreate(_StrictRequest):
    code: str = Field(min_length=1, max_length=32, pattern=CODE_PATTERN)
    name: CleanName
    grade: int = Field(strict=True, ge=1, le=13)


class ExamConfigurationUpdate(_StrictRequest):
    name: CleanName


class ExamConfigurationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str
    grade: int
    active: bool
    created_at: datetime
    updated_at: datetime


class MediumCreate(_StrictRequest):
    code: str = Field(min_length=2, max_length=16, pattern=MEDIUM_CODE_PATTERN)
    name: CleanName


class MediumUpdate(_StrictRequest):
    name: CleanName


class MediumResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str
    active: bool
    created_at: datetime
    updated_at: datetime


class SubjectCreate(_StrictRequest):
    code: str = Field(min_length=1, max_length=64, pattern=CODE_PATTERN)
    name: CleanName


class SubjectUpdate(_StrictRequest):
    name: CleanName


class SubjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str
    active: bool
    created_at: datetime
    updated_at: datetime


class CurriculumVersionCreate(_StrictRequest):
    exam_configuration_id: UUID
    medium_id: UUID
    subject_id: UUID = LEGACY_UNCLASSIFIED_SUBJECT_ID
    code: str = Field(min_length=1, max_length=64, pattern=CODE_PATTERN)
    title: CleanName


class CurriculumVersionUpdate(_StrictRequest):
    title: CleanName


class CurriculumVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    exam_configuration_id: UUID
    medium_id: UUID
    subject_id: UUID
    code: str
    title: str
    active: bool
    created_at: datetime
    updated_at: datetime


class CurriculumUnitCreate(_StrictRequest):
    code: str = Field(min_length=1, max_length=64, pattern=CODE_PATTERN)
    title: CleanName
    ordinal: int = Field(strict=True, ge=1, le=10_000)


class CurriculumUnitUpdate(_StrictRequest):
    title: CleanName


class CurriculumUnitResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    curriculum_version_id: UUID
    code: str
    title: str
    ordinal: int
    active: bool
    created_at: datetime
    updated_at: datetime


class CurriculumLessonCreate(_StrictRequest):
    unit_id: UUID
    code: str = Field(min_length=1, max_length=64, pattern=CODE_PATTERN)
    title: CleanName
    ordinal: int = Field(strict=True, ge=1, le=10_000)
    taxonomy_node_ids: tuple[UUID, ...] = Field(default=(), max_length=100)

    @model_validator(mode="after")
    def taxonomy_nodes_are_unique(self) -> Self:
        if len(set(self.taxonomy_node_ids)) != len(self.taxonomy_node_ids):
            raise ValueError("taxonomy_node_ids must be unique")
        return self


class CurriculumLessonUpdate(_StrictRequest):
    title: CleanName


class CurriculumLessonTaxonomyUpdate(_StrictRequest):
    taxonomy_node_ids: tuple[UUID, ...] = Field(max_length=100)

    @model_validator(mode="after")
    def taxonomy_nodes_are_unique(self) -> Self:
        if len(set(self.taxonomy_node_ids)) != len(self.taxonomy_node_ids):
            raise ValueError("taxonomy_node_ids must be unique")
        return self


class CurriculumLessonResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    curriculum_version_id: UUID
    unit_id: UUID
    code: str
    title: str
    ordinal: int
    active: bool
    taxonomy_node_ids: tuple[UUID, ...] = ()
    created_at: datetime
    updated_at: datetime

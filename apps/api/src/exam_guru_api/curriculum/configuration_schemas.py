from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

CODE_PATTERN = r"^[A-Z0-9]+(?:[._-][A-Z0-9]+)*$"
MEDIUM_CODE_PATTERN = r"^[a-z][a-z0-9-]{1,15}$"


class ExamConfigurationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=32, pattern=CODE_PATTERN)
    name: str = Field(min_length=1, max_length=255)
    grade: Literal[5]


class ExamConfigurationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)


class ExamConfigurationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str
    grade: int
    active: bool
    created_at: datetime
    updated_at: datetime


class MediumCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=2, max_length=16, pattern=MEDIUM_CODE_PATTERN)
    name: str = Field(min_length=1, max_length=255)


class MediumUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)


class MediumResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str
    active: bool
    created_at: datetime
    updated_at: datetime


class CurriculumVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exam_configuration_id: UUID
    medium_id: UUID
    code: str = Field(min_length=1, max_length=64, pattern=CODE_PATTERN)
    title: str = Field(min_length=1, max_length=255)


class CurriculumVersionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=255)


class CurriculumVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    exam_configuration_id: UUID
    medium_id: UUID
    code: str
    title: str
    active: bool
    created_at: datetime
    updated_at: datetime

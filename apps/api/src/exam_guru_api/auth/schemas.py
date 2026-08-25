from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from exam_guru_api.auth.domain import AdminRole


class AuthSessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    subject_id: UUID
    roles: list[AdminRole]


class AuthenticationErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: Literal["authentication_required", "invalid_access_token"]


class AuthenticationErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    detail: AuthenticationErrorDetail


class IdentityProviderUnavailableDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: Literal["identity_provider_unavailable"]


class IdentityProviderUnavailableResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    detail: IdentityProviderUnavailableDetail


class AdminAuditEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    actor_id: UUID
    action: str
    resource_type: str
    resource_id: UUID
    payload: dict[str, Any]
    created_at: datetime

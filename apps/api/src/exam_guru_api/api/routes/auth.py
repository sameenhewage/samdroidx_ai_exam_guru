from typing import Annotated

from fastapi import APIRouter, Depends, status

from exam_guru_api.auth.api import get_current_principal
from exam_guru_api.auth.domain import Principal
from exam_guru_api.auth.schemas import (
    AuthenticationErrorResponse,
    AuthSessionResponse,
    IdentityProviderUnavailableResponse,
)

router = APIRouter()


@router.get(
    "/session",
    operation_id="get_auth_session",
    response_model=AuthSessionResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "description": "Bearer access token is missing or invalid",
            "model": AuthenticationErrorResponse,
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Configured identity provider is unavailable",
            "model": IdentityProviderUnavailableResponse,
        },
    },
)
async def get_auth_session(
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AuthSessionResponse:
    return AuthSessionResponse(
        subject_id=principal.subject_id,
        roles=sorted(principal.roles, key=lambda role: role.value),
    )

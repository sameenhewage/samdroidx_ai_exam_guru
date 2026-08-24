"""Authorized, read-only RAG retrieval exploration endpoint."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from exam_guru_api.api.dependencies import get_retrieval_explorer_service
from exam_guru_api.api.schemas import ApiErrorResponse
from exam_guru_api.auth.api import require_permission
from exam_guru_api.auth.domain import Permission, Principal
from exam_guru_api.retrieval.domain import RetrievalContractError
from exam_guru_api.retrieval.embeddings import EmbeddingProviderUnavailableError
from exam_guru_api.retrieval.explorer import (
    EmbeddingConfigurationNotFoundError,
    RetrievalExplorerService,
    RetrievalScopeNotFoundError,
)
from exam_guru_api.retrieval.schemas import RetrievalExploreRequest, RetrievalExploreResponse

router = APIRouter()
RetrievalPrincipal = Annotated[
    Principal,
    Depends(require_permission(Permission.RETRIEVAL_READ)),
]
RetrievalExplorer = Annotated[
    RetrievalExplorerService,
    Depends(get_retrieval_explorer_service),
]


@router.post(
    "/retrieval/explore",
    operation_id="explore_retrieval",
    response_model=RetrievalExploreResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "Embedding configuration or exact retrieval scope not found",
            "model": ApiErrorResponse,
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "Malformed or internally inconsistent retrieval request",
            "model": ApiErrorResponse,
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Configured embedding provider is unavailable",
            "model": ApiErrorResponse,
        },
    },
    summary="Explore hard-scoped hybrid retrieval evidence",
)
async def explore_retrieval(
    request: RetrievalExploreRequest,
    principal: RetrievalPrincipal,
    explorer: RetrievalExplorer,
) -> RetrievalExploreResponse:
    del principal
    try:
        result = await explorer.explore(
            query=request.query,
            scope=request.scope.to_domain(),
            embedding_config=request.embedding_config.to_domain(),
            limits=request.limits.to_domain(),
        )
    except EmbeddingConfigurationNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "embedding_configuration_not_found"},
        ) from error
    except RetrievalScopeNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "retrieval_scope_not_found"},
        ) from error
    except EmbeddingProviderUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "embedding_provider_unavailable"},
        ) from error
    except RetrievalContractError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "invalid_retrieval_request"},
        ) from error
    return RetrievalExploreResponse.from_domain(result)

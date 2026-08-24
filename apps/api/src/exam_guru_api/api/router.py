from fastapi import APIRouter

from exam_guru_api.api.routes.audit import router as audit_router
from exam_guru_api.api.routes.configuration import router as configuration_router
from exam_guru_api.api.routes.documents import router as document_router
from exam_guru_api.api.routes.health import router as health_router
from exam_guru_api.api.routes.knowledge import router as knowledge_router
from exam_guru_api.api.routes.taxonomy import router as taxonomy_router

api_router = APIRouter()
api_router.include_router(health_router, prefix="/health", tags=["health"])
api_router.include_router(audit_router, prefix="/admin", tags=["admin-audit"])
api_router.include_router(configuration_router, prefix="/admin", tags=["admin-configuration"])
api_router.include_router(document_router, prefix="/admin", tags=["admin-documents"])
api_router.include_router(
    knowledge_router,
    prefix="/admin/curricula",
    tags=["admin-knowledge"],
)
api_router.include_router(
    taxonomy_router,
    prefix="/admin/curricula",
    tags=["admin-taxonomy"],
)

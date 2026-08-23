from fastapi import APIRouter

from exam_guru_api.api.routes.health import router as health_router
from exam_guru_api.api.routes.taxonomy import router as taxonomy_router

api_router = APIRouter()
api_router.include_router(health_router, prefix="/health", tags=["health"])
api_router.include_router(
    taxonomy_router,
    prefix="/admin/curricula",
    tags=["admin-taxonomy"],
)

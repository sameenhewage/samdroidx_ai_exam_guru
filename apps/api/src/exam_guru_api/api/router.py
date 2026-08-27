from fastapi import APIRouter

from exam_guru_api.api.routes.analytics import router as analytics_router
from exam_guru_api.api.routes.audit import router as audit_router
from exam_guru_api.api.routes.auth import router as auth_router
from exam_guru_api.api.routes.blueprints import router as blueprint_router
from exam_guru_api.api.routes.configuration import router as configuration_router
from exam_guru_api.api.routes.documents import router as document_router
from exam_guru_api.api.routes.embedding_jobs import router as embedding_job_router
from exam_guru_api.api.routes.generation import router as generation_router
from exam_guru_api.api.routes.health import router as health_router
from exam_guru_api.api.routes.knowledge import router as knowledge_router
from exam_guru_api.api.routes.operations import router as operations_router
from exam_guru_api.api.routes.papers import router as paper_router
from exam_guru_api.api.routes.retrieval import router as retrieval_router
from exam_guru_api.api.routes.review_candidates import router as review_candidate_router
from exam_guru_api.api.routes.review_papers import router as review_paper_router
from exam_guru_api.api.routes.subject_quality import router as subject_quality_router
from exam_guru_api.api.routes.taxonomy import router as taxonomy_router
from exam_guru_api.api.routes.teacher_papers import router as teacher_paper_router
from exam_guru_api.api.routes.validation import router as validation_router

api_router = APIRouter()
api_router.include_router(health_router, prefix="/health", tags=["health"])
api_router.include_router(auth_router, prefix="/auth", tags=["authentication"])
api_router.include_router(audit_router, prefix="/admin", tags=["admin-audit"])
api_router.include_router(operations_router, prefix="/admin", tags=["admin-operations"])
api_router.include_router(configuration_router, prefix="/admin", tags=["admin-configuration"])
api_router.include_router(document_router, prefix="/admin", tags=["admin-documents"])
api_router.include_router(
    analytics_router,
    prefix="/admin/curricula",
    tags=["admin-analytics"],
)
api_router.include_router(
    blueprint_router,
    prefix="/admin/curricula",
    tags=["admin-blueprints"],
)
api_router.include_router(retrieval_router, prefix="/admin", tags=["admin-retrieval"])
api_router.include_router(
    teacher_paper_router,
    prefix="/admin/paper-generation",
    tags=["teacher-paper-generation"],
)
api_router.include_router(
    review_paper_router,
    prefix="/admin/review-papers",
    tags=["teacher-paper-review"],
)
api_router.include_router(
    subject_quality_router,
    prefix="/admin/subject-quality",
    tags=["private-studio-subject-quality"],
)
api_router.include_router(
    embedding_job_router,
    prefix="/admin/curricula",
    tags=["admin-embedding-jobs"],
)
api_router.include_router(
    generation_router,
    prefix="/admin/curricula",
    tags=["admin-generation"],
)
api_router.include_router(
    validation_router,
    prefix="/admin/curricula",
    tags=["admin-validation"],
)
api_router.include_router(
    review_candidate_router,
    prefix="/admin/curricula",
    tags=["admin-review-candidates"],
)
api_router.include_router(
    paper_router,
    prefix="/admin/curricula",
    tags=["admin-papers"],
)
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

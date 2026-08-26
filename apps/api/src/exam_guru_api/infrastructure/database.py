from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def get_metadata() -> MetaData:
    from exam_guru_api.analytics import models as analytics_models
    from exam_guru_api.auth import models as auth_models
    from exam_guru_api.blueprints import models as blueprint_models
    from exam_guru_api.curriculum import models as curriculum_models
    from exam_guru_api.documents import models as document_models
    from exam_guru_api.generation import models as generation_models
    from exam_guru_api.knowledge import models as knowledge_models
    from exam_guru_api.papers import models as paper_models
    from exam_guru_api.papers import publication_models as paper_publication_models
    from exam_guru_api.storage_reconciliation import models as storage_reconciliation_models
    from exam_guru_api.teacher_papers import models as teacher_paper_models
    from exam_guru_api.validation import models as validation_models

    _ = (
        analytics_models.AnalyticsRunModel,
        auth_models.AdminAuditEventModel,
        blueprint_models.PaperBlueprintModel,
        document_models.SourceDocumentModel,
        generation_models.GenerationRunModel,
        generation_models.GenerationAttemptModel,
        generation_models.GenerationJobModel,
        knowledge_models.KnowledgeEmbeddingModel,
        knowledge_models.EmbeddingJobModel,
        paper_models.QuestionCandidateModel,
        paper_models.QuestionCandidateRevisionModel,
        paper_models.CandidateReviewEventModel,
        paper_publication_models.PracticePaperModel,
        paper_publication_models.PaperDraftVersionModel,
        paper_publication_models.PaperDraftCandidateModel,
        paper_publication_models.PublishedPaperVersionModel,
        paper_publication_models.PaperArchiveEventModel,
        storage_reconciliation_models.StorageReconciliationStateModel,
        storage_reconciliation_models.StorageReconciliationRunModel,
        storage_reconciliation_models.StorageOrphanFindingModel,
        teacher_paper_models.TeacherPaperJobModel,
        teacher_paper_models.TeacherPaperSlotModel,
        teacher_paper_models.TeacherPaperSlotRunModel,
        validation_models.ValidationRunModel,
        validation_models.ValidationFindingModel,
    )
    return curriculum_models.TaxonomyNodeModel.metadata

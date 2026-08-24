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
    from exam_guru_api.knowledge import models as knowledge_models

    _ = (
        analytics_models.AnalyticsRunModel,
        auth_models.AdminAuditEventModel,
        blueprint_models.PaperBlueprintModel,
        document_models.SourceDocumentModel,
        knowledge_models.KnowledgeEmbeddingModel,
    )
    return curriculum_models.TaxonomyNodeModel.metadata

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def get_metadata() -> MetaData:
    from exam_guru_api.auth import models as auth_models
    from exam_guru_api.curriculum import models as curriculum_models

    _ = auth_models.AdminAuditEventModel
    return curriculum_models.TaxonomyNodeModel.metadata

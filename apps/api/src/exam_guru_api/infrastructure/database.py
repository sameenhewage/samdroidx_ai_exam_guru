from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def get_metadata() -> MetaData:
    from exam_guru_api.curriculum import models

    return models.TaxonomyNodeModel.metadata

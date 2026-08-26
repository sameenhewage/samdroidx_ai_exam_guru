from datetime import datetime
from typing import Self
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    Uuid,
    func,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from exam_guru_api.curriculum.domain import (
    LEGACY_UNCLASSIFIED_SUBJECT_ID,
    TaxonomyLevel,
    TaxonomyNode,
    TaxonomyReviewState,
)
from exam_guru_api.infrastructure.database import Base

_TAXONOMY_LEVELS_SQL = ", ".join(f"'{level.value}'" for level in TaxonomyLevel)
_TAXONOMY_REVIEW_STATES_SQL = ", ".join(f"'{state.value}'" for state in TaxonomyReviewState)


class AuditColumns:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    created_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    updated_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)


class ExamConfigurationModel(AuditColumns, Base):
    __tablename__ = "exam_configurations"
    __table_args__ = (
        CheckConstraint(
            "grade BETWEEN 1 AND 13",
            name="ck_exam_configurations_grade_range",
        ),
        CheckConstraint("code ~ '^[A-Z0-9]+([._-][A-Z0-9]+)*$'", name="ck_exam_code"),
        CheckConstraint("name = btrim(name) AND length(name) > 0", name="ck_exam_name"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    grade: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )


class MediumModel(AuditColumns, Base):
    __tablename__ = "media"
    __table_args__ = (
        CheckConstraint("code ~ '^[a-z][a-z0-9-]{1,15}$'", name="ck_medium_code"),
        CheckConstraint("name = btrim(name) AND length(name) > 0", name="ck_medium_name"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    code: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )


class SubjectModel(AuditColumns, Base):
    __tablename__ = "subjects"
    __table_args__ = (
        CheckConstraint(
            "code ~ '^[A-Z0-9]+([._-][A-Z0-9]+)*$'",
            name="ck_subject_code",
        ),
        CheckConstraint("name = btrim(name) AND length(name) > 0", name="ck_subject_name"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )


class CurriculumVersionModel(AuditColumns, Base):
    __tablename__ = "curriculum_versions"
    __table_args__ = (
        UniqueConstraint(
            "exam_configuration_id",
            "medium_id",
            "subject_id",
            "code",
            name="uq_curriculum_version_scope_code",
        ),
        CheckConstraint(
            "code ~ '^[A-Z0-9]+([._-][A-Z0-9]+)*$'",
            name="ck_curriculum_code",
        ),
        CheckConstraint(
            "title = btrim(title) AND length(title) > 0",
            name="ck_curriculum_title",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    exam_configuration_id: Mapped[UUID] = mapped_column(
        ForeignKey("exam_configurations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    medium_id: Mapped[UUID] = mapped_column(
        ForeignKey("media.id", ondelete="RESTRICT"),
        nullable=False,
    )
    subject_id: Mapped[UUID] = mapped_column(
        ForeignKey("subjects.id", ondelete="RESTRICT"),
        nullable=False,
        default=LEGACY_UNCLASSIFIED_SUBJECT_ID,
        server_default=str(LEGACY_UNCLASSIFIED_SUBJECT_ID),
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )


class CurriculumUnitModel(AuditColumns, Base):
    __tablename__ = "curriculum_units"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "curriculum_version_id",
            name="uq_curriculum_units_id_curriculum",
        ),
        UniqueConstraint(
            "curriculum_version_id",
            "code",
            name="uq_curriculum_units_scope_code",
        ),
        UniqueConstraint(
            "curriculum_version_id",
            "ordinal",
            name="uq_curriculum_units_scope_ordinal",
        ),
        CheckConstraint(
            "code ~ '^[A-Z0-9]+([._-][A-Z0-9]+)*$'",
            name="ck_curriculum_units_code",
        ),
        CheckConstraint(
            "title = btrim(title) AND length(title) > 0",
            name="ck_curriculum_units_title",
        ),
        CheckConstraint(
            "ordinal BETWEEN 1 AND 10000",
            name="ck_curriculum_units_ordinal",
        ),
        Index(
            "ix_curriculum_units_curriculum_active",
            "curriculum_version_id",
            "active",
            "ordinal",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    curriculum_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("curriculum_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )


class CurriculumLessonModel(AuditColumns, Base):
    __tablename__ = "curriculum_lessons"
    __table_args__ = (
        ForeignKeyConstraint(
            ["unit_id", "curriculum_version_id"],
            ["curriculum_units.id", "curriculum_units.curriculum_version_id"],
            name="fk_curriculum_lessons_unit_curriculum",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "id",
            "unit_id",
            "curriculum_version_id",
            name="uq_curriculum_lessons_identity_scope",
        ),
        UniqueConstraint("unit_id", "code", name="uq_curriculum_lessons_unit_code"),
        UniqueConstraint(
            "unit_id",
            "ordinal",
            name="uq_curriculum_lessons_unit_ordinal",
        ),
        CheckConstraint(
            "code ~ '^[A-Z0-9]+([._-][A-Z0-9]+)*$'",
            name="ck_curriculum_lessons_code",
        ),
        CheckConstraint(
            "title = btrim(title) AND length(title) > 0",
            name="ck_curriculum_lessons_title",
        ),
        CheckConstraint(
            "ordinal BETWEEN 1 AND 10000",
            name="ck_curriculum_lessons_ordinal",
        ),
        Index(
            "ix_curriculum_lessons_curriculum_unit_active",
            "curriculum_version_id",
            "unit_id",
            "active",
            "ordinal",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    curriculum_version_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    unit_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )


class TaxonomyNodeModel(AuditColumns, Base):
    __tablename__ = "taxonomy_nodes"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "curriculum_version_id",
            name="uq_taxonomy_node_id_curriculum",
        ),
        ForeignKeyConstraint(
            ["parent_id", "curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_taxonomy_nodes_parent_curriculum",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"level IN ({_TAXONOMY_LEVELS_SQL})",
            name="ck_taxonomy_node_level",
        ),
        CheckConstraint(
            f"review_state IN ({_TAXONOMY_REVIEW_STATES_SQL})",
            name="ck_taxonomy_node_review_state",
        ),
        CheckConstraint(
            "(review_state = 'deprecated' AND NOT active) OR "
            "(review_state IN ('draft', 'reviewed') AND active)",
            name="ck_taxonomy_node_review_state_active",
        ),
        CheckConstraint(
            "(level = 'competency' AND parent_id IS NULL) OR "
            "(level <> 'competency' AND parent_id IS NOT NULL)",
            name="ck_taxonomy_node_parent_shape",
        ),
        CheckConstraint(
            "code ~ '^[A-Z0-9]+([._-][A-Z0-9]+)*$'",
            name="ck_taxonomy_node_code",
        ),
        CheckConstraint(
            "title = btrim(title) AND length(title) > 0",
            name="ck_taxonomy_node_title",
        ),
        Index(
            "uq_taxonomy_node_sibling_code",
            "curriculum_version_id",
            "parent_id",
            "level",
            "code",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    curriculum_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("curriculum_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    parent_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    level: Mapped[TaxonomyLevel] = mapped_column(
        Enum(
            TaxonomyLevel,
            name="taxonomy_level",
            native_enum=False,
            create_constraint=False,
            values_callable=lambda levels: [level.value for level in levels],
            length=32,
        ),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    review_state: Mapped[TaxonomyReviewState] = mapped_column(
        Enum(
            TaxonomyReviewState,
            name="taxonomy_review_state",
            native_enum=False,
            create_constraint=False,
            values_callable=lambda states: [state.value for state in states],
            length=32,
        ),
        nullable=False,
        default=TaxonomyReviewState.DRAFT,
        server_default=TaxonomyReviewState.DRAFT.value,
    )

    @classmethod
    def from_domain(cls, node: TaxonomyNode, actor_id: UUID) -> Self:
        return cls(
            id=node.id,
            curriculum_version_id=node.curriculum_version_id,
            parent_id=node.parent_id,
            level=node.level,
            code=node.code,
            title=node.title,
            active=node.active,
            review_state=node.review_state,
            created_by=actor_id,
            updated_by=actor_id,
        )

    def to_domain(self) -> TaxonomyNode:
        return TaxonomyNode(
            id=self.id,
            curriculum_version_id=self.curriculum_version_id,
            level=self.level,
            code=self.code,
            title=self.title,
            parent_id=self.parent_id,
            active=self.active,
            review_state=self.review_state,
        )


class CurriculumLessonTaxonomyMappingModel(Base):
    __tablename__ = "curriculum_lesson_taxonomy_mappings"
    __table_args__ = (
        PrimaryKeyConstraint(
            "lesson_id",
            "taxonomy_node_id",
            name="pk_curriculum_lesson_taxonomy_mappings",
        ),
        ForeignKeyConstraint(
            ["lesson_id", "unit_id", "curriculum_version_id"],
            [
                "curriculum_lessons.id",
                "curriculum_lessons.unit_id",
                "curriculum_lessons.curriculum_version_id",
            ],
            name="fk_lesson_taxonomy_mapping_lesson_scope",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["taxonomy_node_id", "curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_lesson_taxonomy_mapping_node_scope",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_lesson_taxonomy_mappings_node",
            "curriculum_version_id",
            "taxonomy_node_id",
        ),
    )

    lesson_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    curriculum_version_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    unit_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    taxonomy_node_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    created_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)

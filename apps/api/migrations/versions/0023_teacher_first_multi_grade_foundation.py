from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_teacher_first_multi_grade_foundation"
down_revision: str | None = "0022_provider_job_retry_depth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_UNCLASSIFIED_SUBJECT_ID = "00000000-0000-5000-8000-000000000023"
MIGRATION_ACTOR_ID = "00000000-0000-5000-8000-000000000022"


def _audit_columns() -> tuple[sa.Column, ...]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
    )


def _create_knowledge_reference_function(*, include_scope: bool) -> None:
    scope_declarations = (
        """
            document_active_for_ai boolean;
            document_unit uuid;
            document_lesson uuid;
    """
        if include_scope
        else ""
    )
    scope_select = ", active_for_ai, unit_id, lesson_id" if include_scope else ""
    scope_into = ", document_active_for_ai, document_unit, document_lesson" if include_scope else ""
    active_check = (
        """
            IF NOT document_active_for_ai THEN
                RAISE EXCEPTION 'knowledge imports require a source active for AI use'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.unit_id IS DISTINCT FROM document_unit
                OR NEW.lesson_id IS DISTINCT FROM document_lesson
            THEN
                RAISE EXCEPTION 'knowledge scope must inherit its source unit and lesson'
                    USING ERRCODE = '23514';
            END IF;
    """
        if include_scope
        else ""
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION validate_knowledge_record_references()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            document_curriculum uuid;
            document_status text;
            document_type text;
            document_year integer;
            document_paper_code text;
            {scope_declarations}
        BEGIN
            SELECT
                curriculum_version_id,
                extraction_status,
                source_documents.document_type,
                year,
                paper_code
                {scope_select}
            INTO
                document_curriculum,
                document_status,
                document_type,
                document_year,
                document_paper_code
                {scope_into}
            FROM source_documents
            WHERE id = NEW.source_document_id;

            IF NOT FOUND OR document_curriculum IS DISTINCT FROM NEW.curriculum_version_id THEN
                RAISE EXCEPTION 'knowledge provenance must use its source curriculum'
                    USING ERRCODE = '23514';
            END IF;

            IF document_status <> 'trusted' THEN
                RAISE EXCEPTION 'knowledge imports require a trusted source document'
                    USING ERRCODE = '23514';
            END IF;

            {active_check}

            IF TG_TABLE_NAME = 'historical_questions' AND (
                document_type <> 'past_paper'
                OR document_year IS DISTINCT FROM (to_jsonb(NEW)->>'year')::integer
                OR document_paper_code IS DISTINCT FROM to_jsonb(NEW)->>'paper_code'
            ) THEN
                RAISE EXCEPTION 'historical question metadata must match its past paper source'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.source_block_id IS NOT NULL AND NOT EXISTS (
                SELECT 1
                FROM extracted_blocks
                WHERE id = NEW.source_block_id
                  AND source_document_id = NEW.source_document_id
                  AND page_number = NEW.page_number
            ) THEN
                RAISE EXCEPTION 'knowledge source block must belong to its source page'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.competency_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM taxonomy_nodes
                WHERE id = NEW.competency_id
                  AND curriculum_version_id = NEW.curriculum_version_id
                  AND level = 'competency'
            ) THEN
                RAISE EXCEPTION 'knowledge competency classification is invalid'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.skill_id IS NOT NULL AND (
                NEW.competency_id IS NULL OR NOT EXISTS (
                    SELECT 1 FROM taxonomy_nodes
                    WHERE id = NEW.skill_id
                      AND curriculum_version_id = NEW.curriculum_version_id
                      AND level = 'skill'
                      AND parent_id = NEW.competency_id
                )
            ) THEN
                RAISE EXCEPTION 'knowledge skill classification is invalid'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.sub_skill_id IS NOT NULL AND (
                NEW.skill_id IS NULL OR NOT EXISTS (
                    SELECT 1 FROM taxonomy_nodes
                    WHERE id = NEW.sub_skill_id
                      AND curriculum_version_id = NEW.curriculum_version_id
                      AND level = 'sub_skill'
                      AND parent_id = NEW.skill_id
                )
            ) THEN
                RAISE EXCEPTION 'knowledge sub-skill classification is invalid'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.learning_concept_id IS NOT NULL AND (
                NEW.sub_skill_id IS NULL OR NOT EXISTS (
                    SELECT 1 FROM taxonomy_nodes
                    WHERE id = NEW.learning_concept_id
                      AND curriculum_version_id = NEW.curriculum_version_id
                      AND level = 'learning_concept'
                      AND parent_id = NEW.sub_skill_id
                )
            ) THEN
                RAISE EXCEPTION 'knowledge learning-concept classification is invalid'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.review_state = 'reviewed' THEN
                IF document_status <> 'trusted'
                    OR NEW.source_block_id IS NULL
                    OR NEW.competency_id IS NULL
                THEN
                    RAISE EXCEPTION
                        'reviewed knowledge requires trusted block and taxonomy provenance'
                        USING ERRCODE = '23514';
                END IF;

                IF EXISTS (
                    SELECT 1 FROM taxonomy_nodes
                    WHERE id IN (
                        NEW.competency_id,
                        NEW.skill_id,
                        NEW.sub_skill_id,
                        NEW.learning_concept_id
                    )
                      AND (NOT active OR review_state <> 'reviewed')
                ) THEN
                    RAISE EXCEPTION 'reviewed knowledge requires reviewed active taxonomy'
                        USING ERRCODE = '23514';
                END IF;
            END IF;

            RETURN NEW;
        END;
        $$
        """  # noqa: S608 - only migration-owned SQL fragments are interpolated
    )


def _create_knowledge_lifecycle_functions(*, include_scope: bool) -> None:
    question_scope = (
        """
                OR NEW.unit_id IS DISTINCT FROM OLD.unit_id
                OR NEW.lesson_id IS DISTINCT FROM OLD.lesson_id
    """
        if include_scope
        else ""
    )
    chunk_scope = question_scope
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION enforce_historical_question_lifecycle()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.id <> OLD.id
                OR NEW.curriculum_version_id <> OLD.curriculum_version_id
                OR NEW.source_document_id <> OLD.source_document_id
                OR NEW.page_number <> OLD.page_number
                OR NEW.source_block_id IS DISTINCT FROM OLD.source_block_id
                OR NEW.year <> OLD.year
                OR NEW.paper_code <> OLD.paper_code
                OR NEW.question_number <> OLD.question_number
                OR NEW.media_references IS DISTINCT FROM OLD.media_references
                OR NEW.options IS DISTINCT FROM OLD.options
                OR NEW.answer IS DISTINCT FROM OLD.answer
                OR NEW.marking_guidance IS DISTINCT FROM OLD.marking_guidance
                OR NEW.marking_data IS DISTINCT FROM OLD.marking_data
                OR NEW.question_archetype IS DISTINCT FROM OLD.question_archetype
                OR NEW.difficulty_label IS DISTINCT FROM OLD.difficulty_label
                OR NEW.difficulty_confidence IS DISTINCT FROM OLD.difficulty_confidence
                OR NEW.difficulty_source IS DISTINCT FROM OLD.difficulty_source
                {question_scope}
                OR NEW.created_at <> OLD.created_at
                OR NEW.created_by <> OLD.created_by
            THEN
                RAISE EXCEPTION 'historical question provenance and source metadata are immutable'
                    USING ERRCODE = '23514';
            END IF;

            IF OLD.review_state IN ('reviewed', 'rejected') THEN
                RAISE EXCEPTION 'final historical questions are immutable'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.version <> OLD.version + 1 THEN
                RAISE EXCEPTION 'historical question version must increment by one'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.review_state <> OLD.review_state AND NOT (
                (OLD.review_state = 'draft' AND NEW.review_state = 'in_review')
                OR (OLD.review_state = 'in_review'
                    AND NEW.review_state IN ('reviewed', 'rejected'))
            ) THEN
                RAISE EXCEPTION 'invalid historical question review transition'
                    USING ERRCODE = '23514';
            END IF;

            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION enforce_knowledge_chunk_lifecycle()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.id <> OLD.id
                OR NEW.curriculum_version_id <> OLD.curriculum_version_id
                OR NEW.source_document_id <> OLD.source_document_id
                OR NEW.page_number <> OLD.page_number
                OR NEW.source_block_id IS DISTINCT FROM OLD.source_block_id
                OR NEW.sequence <> OLD.sequence
                {chunk_scope}
                OR NEW.created_at <> OLD.created_at
                OR NEW.created_by <> OLD.created_by
            THEN
                RAISE EXCEPTION 'knowledge chunk provenance is immutable'
                    USING ERRCODE = '23514';
            END IF;

            IF OLD.review_state IN ('reviewed', 'rejected') THEN
                RAISE EXCEPTION 'final knowledge chunks are immutable'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.version <> OLD.version + 1 THEN
                RAISE EXCEPTION 'knowledge chunk version must increment by one'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.review_state <> OLD.review_state AND NOT (
                (OLD.review_state = 'draft' AND NEW.review_state = 'in_review')
                OR (OLD.review_state = 'in_review'
                    AND NEW.review_state IN ('reviewed', 'rejected'))
            ) THEN
                RAISE EXCEPTION 'invalid knowledge chunk review transition'
                    USING ERRCODE = '23514';
            END IF;

            RETURN NEW;
        END;
        $$
        """
    )


def _create_embedding_integrity_function(*, require_active_source: bool) -> None:
    declaration = (
        "target_source_document_id uuid; source_active_for_ai boolean;"
        if require_active_source
        else ""
    )
    question_select = (
        "SELECT review_state, text, source_document_id INTO target_review_state, target_text, "
        "target_source_document_id"
        if require_active_source
        else "SELECT review_state, text INTO target_review_state, target_text"
    )
    chunk_select = question_select
    active_check = (
        """
            SELECT active_for_ai INTO source_active_for_ai
            FROM source_documents
            WHERE id = target_source_document_id;
            IF source_active_for_ai IS DISTINCT FROM TRUE THEN
                RAISE EXCEPTION 'embeddings require a source active for AI use'
                    USING ERRCODE = '23514';
            END IF;
    """
        if require_active_source
        else ""
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION enforce_knowledge_embedding_integrity()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            target_review_state text;
            target_text text;
            {declaration}
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'knowledge embeddings are immutable'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.historical_question_id IS NOT NULL THEN
                {question_select}
                FROM historical_questions
                WHERE id = NEW.historical_question_id;
            ELSE
                {chunk_select}
                FROM knowledge_chunks
                WHERE id = NEW.knowledge_chunk_id;
            END IF;

            IF target_review_state <> 'reviewed' THEN
                RAISE EXCEPTION 'embeddings require reviewed knowledge'
                    USING ERRCODE = '23514';
            END IF;

            {active_check}

            IF encode(sha256(convert_to(target_text, 'UTF8')), 'hex')
                <> NEW.source_text_sha256
            THEN
                RAISE EXCEPTION 'embedding source hash does not match reviewed knowledge'
                    USING ERRCODE = '23514';
            END IF;

            RETURN NEW;
        END;
        $$
        """
    )


def upgrade() -> None:
    op.drop_constraint(
        "ck_exam_configurations_grade_five",
        "exam_configurations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exam_configurations_grade_range",
        "exam_configurations",
        "grade BETWEEN 1 AND 13",
    )

    op.create_table(
        "subjects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_audit_columns(),
        sa.CheckConstraint(
            "code ~ '^[A-Z0-9]+([._-][A-Z0-9]+)*$'",
            name="ck_subject_code",
        ),
        sa.CheckConstraint(
            "name = btrim(name) AND length(name) > 0",
            name="ck_subject_name",
        ),
        sa.UniqueConstraint("code", name="uq_subjects_code"),
    )
    op.execute(
        f"""
        INSERT INTO subjects (
            id, code, name, active, created_by, updated_by
        ) VALUES (
            '{LEGACY_UNCLASSIFIED_SUBJECT_ID}'::uuid,
            'LEGACY_UNCLASSIFIED',
            'Legacy unclassified subject',
            TRUE,
            '{MIGRATION_ACTOR_ID}'::uuid,
            '{MIGRATION_ACTOR_ID}'::uuid
        )
        """  # noqa: S608 - deterministic migration constants only
    )

    op.add_column(
        "curriculum_versions",
        sa.Column(
            "subject_id",
            sa.Uuid(),
            nullable=False,
            server_default=LEGACY_UNCLASSIFIED_SUBJECT_ID,
        ),
    )
    op.create_foreign_key(
        "fk_curriculum_versions_subject",
        "curriculum_versions",
        "subjects",
        ["subject_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        "uq_curriculum_version_scope_code",
        "curriculum_versions",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_curriculum_version_scope_code",
        "curriculum_versions",
        ["exam_configuration_id", "medium_id", "subject_id", "code"],
    )

    op.create_table(
        "curriculum_units",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("curriculum_version_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_audit_columns(),
        sa.ForeignKeyConstraint(
            ["curriculum_version_id"],
            ["curriculum_versions.id"],
            name="fk_curriculum_units_curriculum",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "id",
            "curriculum_version_id",
            name="uq_curriculum_units_id_curriculum",
        ),
        sa.UniqueConstraint(
            "curriculum_version_id",
            "code",
            name="uq_curriculum_units_scope_code",
        ),
        sa.UniqueConstraint(
            "curriculum_version_id",
            "ordinal",
            name="uq_curriculum_units_scope_ordinal",
        ),
        sa.CheckConstraint(
            "code ~ '^[A-Z0-9]+([._-][A-Z0-9]+)*$'",
            name="ck_curriculum_units_code",
        ),
        sa.CheckConstraint(
            "title = btrim(title) AND length(title) > 0",
            name="ck_curriculum_units_title",
        ),
        sa.CheckConstraint(
            "ordinal BETWEEN 1 AND 10000",
            name="ck_curriculum_units_ordinal",
        ),
    )
    op.create_index(
        "ix_curriculum_units_curriculum_active",
        "curriculum_units",
        ["curriculum_version_id", "active", "ordinal"],
    )

    op.create_table(
        "curriculum_lessons",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("curriculum_version_id", sa.Uuid(), nullable=False),
        sa.Column("unit_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_audit_columns(),
        sa.ForeignKeyConstraint(
            ["unit_id", "curriculum_version_id"],
            ["curriculum_units.id", "curriculum_units.curriculum_version_id"],
            name="fk_curriculum_lessons_unit_curriculum",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "id",
            "unit_id",
            "curriculum_version_id",
            name="uq_curriculum_lessons_identity_scope",
        ),
        sa.UniqueConstraint(
            "unit_id",
            "code",
            name="uq_curriculum_lessons_unit_code",
        ),
        sa.UniqueConstraint(
            "unit_id",
            "ordinal",
            name="uq_curriculum_lessons_unit_ordinal",
        ),
        sa.CheckConstraint(
            "code ~ '^[A-Z0-9]+([._-][A-Z0-9]+)*$'",
            name="ck_curriculum_lessons_code",
        ),
        sa.CheckConstraint(
            "title = btrim(title) AND length(title) > 0",
            name="ck_curriculum_lessons_title",
        ),
        sa.CheckConstraint(
            "ordinal BETWEEN 1 AND 10000",
            name="ck_curriculum_lessons_ordinal",
        ),
    )
    op.create_index(
        "ix_curriculum_lessons_curriculum_unit_active",
        "curriculum_lessons",
        ["curriculum_version_id", "unit_id", "active", "ordinal"],
    )

    op.create_table(
        "curriculum_lesson_taxonomy_mappings",
        sa.Column("lesson_id", sa.Uuid(), nullable=False),
        sa.Column("curriculum_version_id", sa.Uuid(), nullable=False),
        sa.Column("unit_id", sa.Uuid(), nullable=False),
        sa.Column("taxonomy_node_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["lesson_id", "unit_id", "curriculum_version_id"],
            [
                "curriculum_lessons.id",
                "curriculum_lessons.unit_id",
                "curriculum_lessons.curriculum_version_id",
            ],
            name="fk_lesson_taxonomy_mapping_lesson_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["taxonomy_node_id", "curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_lesson_taxonomy_mapping_node_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "lesson_id",
            "taxonomy_node_id",
            name="pk_curriculum_lesson_taxonomy_mappings",
        ),
    )
    op.create_index(
        "ix_lesson_taxonomy_mappings_node",
        "curriculum_lesson_taxonomy_mappings",
        ["curriculum_version_id", "taxonomy_node_id"],
    )

    op.execute(
        """
        CREATE FUNCTION enforce_curriculum_learning_scope()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            parent_active boolean;
        BEGIN
            IF TG_TABLE_NAME = 'curriculum_units' THEN
                IF TG_OP = 'UPDATE' AND (
                    NEW.id <> OLD.id
                    OR NEW.curriculum_version_id <> OLD.curriculum_version_id
                    OR NEW.code <> OLD.code
                    OR NEW.ordinal <> OLD.ordinal
                ) THEN
                    RAISE EXCEPTION 'curriculum unit identity and order are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF TG_OP = 'UPDATE' AND OLD.active AND NOT NEW.active AND EXISTS (
                    SELECT 1 FROM curriculum_lessons
                    WHERE unit_id = NEW.id
                      AND curriculum_version_id = NEW.curriculum_version_id
                      AND active
                ) THEN
                    RAISE EXCEPTION 'curriculum unit with active lessons cannot be deactivated'
                        USING ERRCODE = '23514';
                END IF;
            ELSE
                SELECT active INTO parent_active
                FROM curriculum_units
                WHERE id = NEW.unit_id
                  AND curriculum_version_id = NEW.curriculum_version_id;
                IF NEW.active AND parent_active IS DISTINCT FROM TRUE THEN
                    RAISE EXCEPTION 'active curriculum lesson requires an active unit'
                        USING ERRCODE = '23514';
                END IF;
                IF TG_OP = 'UPDATE' AND (
                    NEW.id <> OLD.id
                    OR NEW.curriculum_version_id <> OLD.curriculum_version_id
                    OR NEW.unit_id <> OLD.unit_id
                    OR NEW.code <> OLD.code
                    OR NEW.ordinal <> OLD.ordinal
                ) THEN
                    RAISE EXCEPTION 'curriculum lesson identity and order are immutable'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_curriculum_unit_scope_trigger
        BEFORE INSERT OR UPDATE ON curriculum_units
        FOR EACH ROW EXECUTE FUNCTION enforce_curriculum_learning_scope()
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_curriculum_lesson_scope_trigger
        BEFORE INSERT OR UPDATE ON curriculum_lessons
        FOR EACH ROW EXECUTE FUNCTION enforce_curriculum_learning_scope()
        """
    )

    for column in (
        sa.Column("unit_id", sa.Uuid(), nullable=True),
        sa.Column("lesson_id", sa.Uuid(), nullable=True),
        sa.Column("active_for_ai", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("removal_reason", sa.String(512), nullable=True),
        sa.Column("removed_by", sa.Uuid(), nullable=True),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_scope_version", sa.Integer(), nullable=False, server_default="0"),
    ):
        op.add_column("source_documents", column)
    op.create_check_constraint(
        "ck_source_documents_learning_scope_shape",
        "source_documents",
        "(unit_id IS NULL AND lesson_id IS NULL) OR "
        "(curriculum_version_id IS NOT NULL AND unit_id IS NOT NULL AND "
        "(lesson_id IS NULL OR lesson_id IS NOT NULL))",
    )
    op.create_check_constraint(
        "ck_source_documents_lesson_requires_unit",
        "source_documents",
        "lesson_id IS NULL OR unit_id IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_source_documents_ai_use_state",
        "source_documents",
        "(active_for_ai AND removal_reason IS NULL AND removed_by IS NULL AND removed_at IS NULL) "
        "OR (NOT active_for_ai AND removal_reason IS NOT NULL AND removed_by IS NOT NULL "
        "AND removed_at IS NOT NULL AND removal_reason = btrim(removal_reason) "
        "AND char_length(removal_reason) BETWEEN 1 AND 512)",
    )
    op.create_check_constraint(
        "ck_source_documents_metadata_scope_version",
        "source_documents",
        "metadata_scope_version >= 0",
    )
    op.create_foreign_key(
        "fk_source_documents_unit_curriculum",
        "source_documents",
        "curriculum_units",
        ["unit_id", "curriculum_version_id"],
        ["id", "curriculum_version_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_source_documents_lesson_scope",
        "source_documents",
        "curriculum_lessons",
        ["lesson_id", "unit_id", "curriculum_version_id"],
        ["id", "unit_id", "curriculum_version_id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_source_documents_ai_scope",
        "source_documents",
        ["active_for_ai", "curriculum_version_id", "unit_id", "lesson_id"],
    )

    for table_name in ("historical_questions", "knowledge_chunks"):
        op.add_column(table_name, sa.Column("unit_id", sa.Uuid(), nullable=True))
        op.add_column(table_name, sa.Column("lesson_id", sa.Uuid(), nullable=True))
        op.create_check_constraint(
            f"ck_{table_name}_lesson_requires_unit",
            table_name,
            "lesson_id IS NULL OR unit_id IS NOT NULL",
        )
        op.create_foreign_key(
            f"fk_{table_name}_unit_curriculum",
            table_name,
            "curriculum_units",
            ["unit_id", "curriculum_version_id"],
            ["id", "curriculum_version_id"],
            ondelete="RESTRICT",
        )
        op.create_foreign_key(
            f"fk_{table_name}_lesson_scope",
            table_name,
            "curriculum_lessons",
            ["lesson_id", "unit_id", "curriculum_version_id"],
            ["id", "unit_id", "curriculum_version_id"],
            ondelete="RESTRICT",
        )
        op.create_index(
            f"ix_{table_name}_learning_scope",
            table_name,
            ["curriculum_version_id", "unit_id", "lesson_id"],
        )

    op.execute(
        """
        CREATE FUNCTION enforce_source_document_use_and_scope()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            protected_change boolean;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.metadata_scope_version <> 0 THEN
                    RAISE EXCEPTION 'source document metadata scope version must start at zero'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;

            protected_change :=
                NEW.curriculum_version_id IS DISTINCT FROM OLD.curriculum_version_id
                OR NEW.unit_id IS DISTINCT FROM OLD.unit_id
                OR NEW.lesson_id IS DISTINCT FROM OLD.lesson_id
                OR NEW.active_for_ai IS DISTINCT FROM OLD.active_for_ai
                OR NEW.removal_reason IS DISTINCT FROM OLD.removal_reason
                OR NEW.removed_by IS DISTINCT FROM OLD.removed_by
                OR NEW.removed_at IS DISTINCT FROM OLD.removed_at;

            IF protected_change
                AND NEW.metadata_scope_version <> OLD.metadata_scope_version + 1
            THEN
                RAISE EXCEPTION 'source document metadata scope version must increment by one'
                    USING ERRCODE = '23514';
            END IF;
            IF NOT protected_change
                AND NEW.metadata_scope_version IS DISTINCT FROM OLD.metadata_scope_version
            THEN
                RAISE EXCEPTION 'source document metadata scope version cannot change alone'
                    USING ERRCODE = '23514';
            END IF;

            IF (
                NEW.curriculum_version_id IS DISTINCT FROM OLD.curriculum_version_id
                OR NEW.unit_id IS DISTINCT FROM OLD.unit_id
                OR NEW.lesson_id IS DISTINCT FROM OLD.lesson_id
            ) AND (
                OLD.extraction_status = 'trusted'
                OR EXISTS (
                    SELECT 1 FROM historical_questions
                    WHERE source_document_id = OLD.id
                )
                OR EXISTS (
                    SELECT 1 FROM knowledge_chunks
                    WHERE source_document_id = OLD.id
                )
            ) THEN
                RAISE EXCEPTION 'trusted or imported source scope is immutable; remove it from use'
                    USING ERRCODE = '23514';
            END IF;

            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_source_document_use_and_scope_trigger
        BEFORE INSERT OR UPDATE ON source_documents
        FOR EACH ROW EXECUTE FUNCTION enforce_source_document_use_and_scope()
        """
    )

    _create_knowledge_reference_function(include_scope=True)
    _create_knowledge_lifecycle_functions(include_scope=True)
    _create_embedding_integrity_function(require_active_source=True)


def downgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM exam_configurations WHERE grade <> 5) THEN
                RAISE EXCEPTION 'cannot downgrade: non-Grade-5 exam configurations exist'
                    USING ERRCODE = '23514';
            END IF;
            IF EXISTS (
                SELECT 1 FROM curriculum_versions
                WHERE subject_id <> '{LEGACY_UNCLASSIFIED_SUBJECT_ID}'::uuid
            ) THEN
                RAISE EXCEPTION 'cannot downgrade: subject-classified curricula exist'
                    USING ERRCODE = '23514';
            END IF;
            IF EXISTS (
                SELECT 1 FROM subjects
                WHERE id <> '{LEGACY_UNCLASSIFIED_SUBJECT_ID}'::uuid
            ) THEN
                RAISE EXCEPTION 'cannot downgrade: non-legacy subjects exist'
                    USING ERRCODE = '23514';
            END IF;
            IF EXISTS (SELECT 1 FROM curriculum_units)
                OR EXISTS (SELECT 1 FROM curriculum_lessons)
                OR EXISTS (SELECT 1 FROM curriculum_lesson_taxonomy_mappings)
            THEN
                RAISE EXCEPTION 'cannot downgrade: normalized curriculum scope data exists'
                    USING ERRCODE = '23514';
            END IF;
            IF EXISTS (
                SELECT 1 FROM source_documents
                WHERE unit_id IS NOT NULL
                   OR lesson_id IS NOT NULL
                   OR NOT active_for_ai
                   OR metadata_scope_version <> 0
            ) THEN
                RAISE EXCEPTION 'cannot downgrade: source use-state or learning scope data exists'
                    USING ERRCODE = '23514';
            END IF;
            IF EXISTS (
                SELECT 1 FROM historical_questions
                WHERE unit_id IS NOT NULL OR lesson_id IS NOT NULL
            ) OR EXISTS (
                SELECT 1 FROM knowledge_chunks
                WHERE unit_id IS NOT NULL OR lesson_id IS NOT NULL
            ) THEN
                RAISE EXCEPTION 'cannot downgrade: scoped knowledge data exists'
                    USING ERRCODE = '23514';
            END IF;
        END;
        $$
        """  # noqa: S608 - deterministic migration guard constants only
    )

    op.execute("DROP TRIGGER enforce_source_document_use_and_scope_trigger ON source_documents")
    op.execute("DROP FUNCTION enforce_source_document_use_and_scope()")
    op.execute("DROP TRIGGER enforce_curriculum_lesson_scope_trigger ON curriculum_lessons")
    op.execute("DROP TRIGGER enforce_curriculum_unit_scope_trigger ON curriculum_units")
    op.execute("DROP FUNCTION enforce_curriculum_learning_scope()")

    _create_embedding_integrity_function(require_active_source=False)
    _create_knowledge_lifecycle_functions(include_scope=False)
    _create_knowledge_reference_function(include_scope=False)

    for table_name in ("knowledge_chunks", "historical_questions"):
        op.drop_index(f"ix_{table_name}_learning_scope", table_name=table_name)
        op.drop_constraint(f"fk_{table_name}_lesson_scope", table_name, type_="foreignkey")
        op.drop_constraint(
            f"fk_{table_name}_unit_curriculum",
            table_name,
            type_="foreignkey",
        )
        op.drop_constraint(
            f"ck_{table_name}_lesson_requires_unit",
            table_name,
            type_="check",
        )
        op.drop_column(table_name, "lesson_id")
        op.drop_column(table_name, "unit_id")

    op.drop_index("ix_source_documents_ai_scope", table_name="source_documents")
    op.drop_constraint(
        "fk_source_documents_lesson_scope",
        "source_documents",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_source_documents_unit_curriculum",
        "source_documents",
        type_="foreignkey",
    )
    for constraint_name in (
        "ck_source_documents_metadata_scope_version",
        "ck_source_documents_ai_use_state",
        "ck_source_documents_lesson_requires_unit",
        "ck_source_documents_learning_scope_shape",
    ):
        op.drop_constraint(constraint_name, "source_documents", type_="check")
    for column_name in (
        "metadata_scope_version",
        "removed_at",
        "removed_by",
        "removal_reason",
        "active_for_ai",
        "lesson_id",
        "unit_id",
    ):
        op.drop_column("source_documents", column_name)

    op.drop_index(
        "ix_lesson_taxonomy_mappings_node",
        table_name="curriculum_lesson_taxonomy_mappings",
    )
    op.drop_table("curriculum_lesson_taxonomy_mappings")
    op.drop_index(
        "ix_curriculum_lessons_curriculum_unit_active",
        table_name="curriculum_lessons",
    )
    op.drop_table("curriculum_lessons")
    op.drop_index(
        "ix_curriculum_units_curriculum_active",
        table_name="curriculum_units",
    )
    op.drop_table("curriculum_units")

    op.drop_constraint(
        "uq_curriculum_version_scope_code",
        "curriculum_versions",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_curriculum_version_scope_code",
        "curriculum_versions",
        ["exam_configuration_id", "medium_id", "code"],
    )
    op.drop_constraint(
        "fk_curriculum_versions_subject",
        "curriculum_versions",
        type_="foreignkey",
    )
    op.drop_column("curriculum_versions", "subject_id")
    op.drop_table("subjects")

    op.drop_constraint(
        "ck_exam_configurations_grade_range",
        "exam_configurations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_exam_configurations_grade_five",
        "exam_configurations",
        "grade = 5",
    )

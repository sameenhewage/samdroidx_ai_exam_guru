from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0007_knowledge_foundation"
down_revision: str | None = "0006_extraction_persistence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REVIEW_STATES_SQL = "'draft', 'in_review', 'reviewed', 'rejected'"
_QUESTION_TYPES_SQL = "'multiple_choice', 'short_answer', 'structured'"
_CHUNK_TYPES_SQL = (
    "'competency_section', 'learning_outcome', 'explanation', "
    "'example', 'practice_question', 'key_term'"
)


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


def _review_state() -> sa.Enum:
    return sa.Enum(
        "draft",
        "in_review",
        "reviewed",
        "rejected",
        name="knowledge_review_state",
        native_enum=False,
        create_constraint=False,
        length=32,
    )


def upgrade() -> None:
    op.create_table(
        "historical_questions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("curriculum_version_id", sa.Uuid(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("paper_code", sa.String(64), nullable=False),
        sa.Column("question_number", sa.String(64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "question_type",
            sa.Enum(
                "multiple_choice",
                "short_answer",
                "structured",
                name="knowledge_question_type",
                native_enum=False,
                create_constraint=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("marks", sa.Integer(), nullable=False),
        sa.Column("source_document_id", sa.Uuid(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("source_block_id", sa.Uuid(), nullable=True),
        sa.Column("review_state", _review_state(), nullable=False, server_default="draft"),
        sa.Column("competency_id", sa.Uuid(), nullable=True),
        sa.Column("skill_id", sa.Uuid(), nullable=True),
        sa.Column("sub_skill_id", sa.Uuid(), nullable=True),
        sa.Column("learning_concept_id", sa.Uuid(), nullable=True),
        *_audit_columns(),
        sa.ForeignKeyConstraint(
            ["curriculum_version_id"],
            ["curriculum_versions.id"],
            name="fk_historical_questions_curriculum_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id", "page_number"],
            ["source_pages.source_document_id", "source_pages.page_number"],
            name="fk_historical_questions_source_page",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_block_id"],
            ["extracted_blocks.id"],
            name="fk_historical_questions_source_block_id_extracted_blocks",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["competency_id", "curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_historical_questions_competency_curriculum",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["skill_id", "curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_historical_questions_skill_curriculum",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["sub_skill_id", "curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_historical_questions_sub_skill_curriculum",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["learning_concept_id", "curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_historical_questions_learning_concept_curriculum",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "source_document_id",
            "question_number",
            name="uq_historical_questions_source_question",
        ),
        sa.CheckConstraint(
            f"question_type IN ({_QUESTION_TYPES_SQL})",
            name="ck_historical_questions_question_type",
        ),
        sa.CheckConstraint(
            f"review_state IN ({_REVIEW_STATES_SQL})",
            name="ck_historical_questions_review_state",
        ),
        sa.CheckConstraint(
            "year BETWEEN 1900 AND 2100",
            name="ck_historical_questions_year",
        ),
        sa.CheckConstraint(
            "paper_code = btrim(paper_code) AND length(paper_code) > 0",
            name="ck_historical_questions_paper_code",
        ),
        sa.CheckConstraint(
            "question_number = btrim(question_number) AND length(question_number) > 0",
            name="ck_historical_questions_question_number",
        ),
        sa.CheckConstraint(
            "length(btrim(text)) > 0",
            name="ck_historical_questions_text",
        ),
        sa.CheckConstraint("marks > 0", name="ck_historical_questions_marks"),
        sa.CheckConstraint(
            "page_number > 0",
            name="ck_historical_questions_page_number",
        ),
        sa.CheckConstraint(
            "review_state <> 'reviewed' OR "
            "(source_block_id IS NOT NULL AND competency_id IS NOT NULL)",
            name="ck_historical_questions_reviewed_references",
        ),
    )
    op.create_index(
        "ix_historical_questions_curriculum_review",
        "historical_questions",
        ["curriculum_version_id", "review_state"],
    )
    op.create_index(
        "ix_historical_questions_competency",
        "historical_questions",
        ["competency_id"],
    )

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("curriculum_version_id", sa.Uuid(), nullable=False),
        sa.Column(
            "chunk_type",
            sa.Enum(
                "competency_section",
                "learning_outcome",
                "explanation",
                "example",
                "practice_question",
                "key_term",
                name="knowledge_chunk_type",
                native_enum=False,
                create_constraint=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("educational_boundary", sa.String(512), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("source_document_id", sa.Uuid(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("source_block_id", sa.Uuid(), nullable=True),
        sa.Column("review_state", _review_state(), nullable=False, server_default="draft"),
        sa.Column("competency_id", sa.Uuid(), nullable=True),
        sa.Column("skill_id", sa.Uuid(), nullable=True),
        sa.Column("sub_skill_id", sa.Uuid(), nullable=True),
        sa.Column("learning_concept_id", sa.Uuid(), nullable=True),
        *_audit_columns(),
        sa.ForeignKeyConstraint(
            ["curriculum_version_id"],
            ["curriculum_versions.id"],
            name="fk_knowledge_chunks_curriculum_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id", "page_number"],
            ["source_pages.source_document_id", "source_pages.page_number"],
            name="fk_knowledge_chunks_source_page",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_block_id"],
            ["extracted_blocks.id"],
            name="fk_knowledge_chunks_source_block_id_extracted_blocks",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["competency_id", "curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_knowledge_chunks_competency_curriculum",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["skill_id", "curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_knowledge_chunks_skill_curriculum",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["sub_skill_id", "curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_knowledge_chunks_sub_skill_curriculum",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["learning_concept_id", "curriculum_version_id"],
            ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
            name="fk_knowledge_chunks_learning_concept_curriculum",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "source_document_id",
            "sequence",
            name="uq_knowledge_chunks_source_sequence",
        ),
        sa.CheckConstraint(
            f"chunk_type IN ({_CHUNK_TYPES_SQL})",
            name="ck_knowledge_chunks_chunk_type",
        ),
        sa.CheckConstraint(
            f"review_state IN ({_REVIEW_STATES_SQL})",
            name="ck_knowledge_chunks_review_state",
        ),
        sa.CheckConstraint("length(btrim(text)) > 0", name="ck_knowledge_chunks_text"),
        sa.CheckConstraint(
            "educational_boundary = btrim(educational_boundary) "
            "AND length(educational_boundary) > 0",
            name="ck_knowledge_chunks_educational_boundary",
        ),
        sa.CheckConstraint("sequence >= 0", name="ck_knowledge_chunks_sequence"),
        sa.CheckConstraint("page_number > 0", name="ck_knowledge_chunks_page_number"),
        sa.CheckConstraint(
            "review_state <> 'reviewed' OR "
            "(source_block_id IS NOT NULL AND competency_id IS NOT NULL)",
            name="ck_knowledge_chunks_reviewed_references",
        ),
    )
    op.create_index(
        "ix_knowledge_chunks_curriculum_review",
        "knowledge_chunks",
        ["curriculum_version_id", "review_state"],
    )
    op.create_index(
        "ix_knowledge_chunks_competency",
        "knowledge_chunks",
        ["competency_id"],
    )

    op.create_table(
        "embedding_configurations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("config_fingerprint", sa.String(128), nullable=False),
        *_audit_columns(),
        sa.UniqueConstraint(
            "provider",
            "model",
            "version",
            "config_fingerprint",
            name="uq_embedding_configurations_space",
        ),
        sa.UniqueConstraint(
            "id",
            "dimension",
            name="uq_embedding_configurations_id_dimension",
        ),
        sa.CheckConstraint(
            "provider = btrim(provider) AND length(provider) > 0",
            name="ck_embedding_configurations_provider",
        ),
        sa.CheckConstraint(
            "model = btrim(model) AND length(model) > 0",
            name="ck_embedding_configurations_model",
        ),
        sa.CheckConstraint(
            "version = btrim(version) AND length(version) > 0",
            name="ck_embedding_configurations_version",
        ),
        sa.CheckConstraint(
            "config_fingerprint = btrim(config_fingerprint) AND length(config_fingerprint) > 0",
            name="ck_embedding_configurations_fingerprint",
        ),
        sa.CheckConstraint(
            "dimension BETWEEN 1 AND 4096",
            name="ck_embedding_configurations_dimension",
        ),
    )

    op.create_table(
        "knowledge_embeddings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("historical_question_id", sa.Uuid(), nullable=True),
        sa.Column("knowledge_chunk_id", sa.Uuid(), nullable=True),
        sa.Column("embedding_configuration_id", sa.Uuid(), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.Column("source_text_sha256", sa.String(64), nullable=False),
        sa.Column("embedding", Vector(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["historical_question_id"],
            ["historical_questions.id"],
            name="fk_knowledge_embeddings_question",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_chunk_id"],
            ["knowledge_chunks.id"],
            name="fk_knowledge_embeddings_chunk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["embedding_configuration_id", "embedding_dimension"],
            ["embedding_configurations.id", "embedding_configurations.dimension"],
            name="fk_knowledge_embeddings_configuration_dimension",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "(historical_question_id IS NOT NULL AND knowledge_chunk_id IS NULL) OR "
            "(historical_question_id IS NULL AND knowledge_chunk_id IS NOT NULL)",
            name="ck_knowledge_embeddings_single_target",
        ),
        sa.CheckConstraint(
            "embedding_dimension BETWEEN 1 AND 4096",
            name="ck_knowledge_embeddings_dimension",
        ),
        sa.CheckConstraint(
            "vector_dims(embedding) = embedding_dimension",
            name="ck_knowledge_embeddings_vector_dimension",
        ),
        sa.CheckConstraint(
            "source_text_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_knowledge_embeddings_source_text_sha256",
        ),
    )
    op.create_index(
        "uq_knowledge_embeddings_question_configuration",
        "knowledge_embeddings",
        ["historical_question_id", "embedding_configuration_id"],
        unique=True,
        postgresql_where=sa.text("historical_question_id IS NOT NULL"),
    )
    op.create_index(
        "uq_knowledge_embeddings_chunk_configuration",
        "knowledge_embeddings",
        ["knowledge_chunk_id", "embedding_configuration_id"],
        unique=True,
        postgresql_where=sa.text("knowledge_chunk_id IS NOT NULL"),
    )
    op.create_index(
        "ix_knowledge_embeddings_configuration",
        "knowledge_embeddings",
        ["embedding_configuration_id"],
    )

    op.execute(
        """
        CREATE FUNCTION validate_knowledge_record_references()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            document_curriculum uuid;
            document_status text;
            document_type text;
            document_year integer;
            document_paper_code text;
        BEGIN
            SELECT
                curriculum_version_id,
                extraction_status,
                source_documents.document_type,
                year,
                paper_code
            INTO
                document_curriculum,
                document_status,
                document_type,
                document_year,
                document_paper_code
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
        """
    )
    op.execute(
        """
        CREATE TRIGGER validate_historical_question_references_trigger
        BEFORE INSERT OR UPDATE ON historical_questions
        FOR EACH ROW EXECUTE FUNCTION validate_knowledge_record_references()
        """
    )
    op.execute(
        """
        CREATE TRIGGER validate_knowledge_chunk_references_trigger
        BEFORE INSERT OR UPDATE ON knowledge_chunks
        FOR EACH ROW EXECUTE FUNCTION validate_knowledge_record_references()
        """
    )

    op.execute(
        """
        CREATE FUNCTION enforce_historical_question_lifecycle()
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
            THEN
                RAISE EXCEPTION 'historical question provenance is immutable'
                    USING ERRCODE = '23514';
            END IF;

            IF OLD.review_state IN ('reviewed', 'rejected') THEN
                RAISE EXCEPTION 'final historical questions are immutable'
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
        """
        CREATE TRIGGER enforce_historical_question_lifecycle_trigger
        BEFORE UPDATE ON historical_questions
        FOR EACH ROW EXECUTE FUNCTION enforce_historical_question_lifecycle()
        """
    )

    op.execute(
        """
        CREATE FUNCTION enforce_knowledge_chunk_lifecycle()
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
            THEN
                RAISE EXCEPTION 'knowledge chunk provenance is immutable'
                    USING ERRCODE = '23514';
            END IF;

            IF OLD.review_state IN ('reviewed', 'rejected') THEN
                RAISE EXCEPTION 'final knowledge chunks are immutable'
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
    op.execute(
        """
        CREATE TRIGGER enforce_knowledge_chunk_lifecycle_trigger
        BEFORE UPDATE ON knowledge_chunks
        FOR EACH ROW EXECUTE FUNCTION enforce_knowledge_chunk_lifecycle()
        """
    )

    op.execute(
        """
        CREATE FUNCTION enforce_embedding_configuration_immutable()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'embedding configurations are immutable'
                USING ERRCODE = '23514';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_embedding_configuration_immutable_trigger
        BEFORE UPDATE OR DELETE ON embedding_configurations
        FOR EACH ROW EXECUTE FUNCTION enforce_embedding_configuration_immutable()
        """
    )

    op.execute(
        """
        CREATE FUNCTION enforce_knowledge_embedding_integrity()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            target_review_state text;
            target_text text;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'knowledge embeddings are immutable'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.historical_question_id IS NOT NULL THEN
                SELECT review_state, text
                INTO target_review_state, target_text
                FROM historical_questions
                WHERE id = NEW.historical_question_id;
            ELSE
                SELECT review_state, text
                INTO target_review_state, target_text
                FROM knowledge_chunks
                WHERE id = NEW.knowledge_chunk_id;
            END IF;

            IF target_review_state <> 'reviewed' THEN
                RAISE EXCEPTION 'embeddings require reviewed knowledge'
                    USING ERRCODE = '23514';
            END IF;

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
    op.execute(
        """
        CREATE TRIGGER enforce_knowledge_embedding_integrity_trigger
        BEFORE INSERT OR UPDATE OR DELETE ON knowledge_embeddings
        FOR EACH ROW EXECUTE FUNCTION enforce_knowledge_embedding_integrity()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER enforce_knowledge_embedding_integrity_trigger ON knowledge_embeddings")
    op.execute("DROP FUNCTION enforce_knowledge_embedding_integrity()")
    op.execute(
        "DROP TRIGGER enforce_embedding_configuration_immutable_trigger ON embedding_configurations"
    )
    op.execute("DROP FUNCTION enforce_embedding_configuration_immutable()")
    op.execute("DROP TRIGGER enforce_knowledge_chunk_lifecycle_trigger ON knowledge_chunks")
    op.execute("DROP FUNCTION enforce_knowledge_chunk_lifecycle()")
    op.execute("DROP TRIGGER enforce_historical_question_lifecycle_trigger ON historical_questions")
    op.execute("DROP FUNCTION enforce_historical_question_lifecycle()")
    op.execute("DROP TRIGGER validate_knowledge_chunk_references_trigger ON knowledge_chunks")
    op.execute(
        "DROP TRIGGER validate_historical_question_references_trigger ON historical_questions"
    )
    op.execute("DROP FUNCTION validate_knowledge_record_references()")

    op.drop_index(
        "ix_knowledge_embeddings_configuration",
        table_name="knowledge_embeddings",
    )
    op.drop_index(
        "uq_knowledge_embeddings_chunk_configuration",
        table_name="knowledge_embeddings",
    )
    op.drop_index(
        "uq_knowledge_embeddings_question_configuration",
        table_name="knowledge_embeddings",
    )
    op.drop_table("knowledge_embeddings")
    op.drop_table("embedding_configurations")
    op.drop_index("ix_knowledge_chunks_competency", table_name="knowledge_chunks")
    op.drop_index(
        "ix_knowledge_chunks_curriculum_review",
        table_name="knowledge_chunks",
    )
    op.drop_table("knowledge_chunks")
    op.drop_index(
        "ix_historical_questions_competency",
        table_name="historical_questions",
    )
    op.drop_index(
        "ix_historical_questions_curriculum_review",
        table_name="historical_questions",
    )
    op.drop_table("historical_questions")

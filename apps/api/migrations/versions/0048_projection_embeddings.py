import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0048_projection_embeddings"
down_revision = "0047_knowledge_unit_review"
branch_labels = None
depends_on = None


def _job_constraints(*, projections: bool) -> None:
    op.drop_constraint("ck_embedding_jobs_record_ids", "embedding_jobs", type_="check")
    op.drop_constraint("ck_embedding_jobs_counts", "embedding_jobs", type_="check")
    extra_count = " + jsonb_array_length(knowledge_projection_ids)" if projections else ""
    extra_valid = (
        "embedding_job_uuid_array_valid(knowledge_projection_ids,100) AND " if projections else ""
    )
    op.create_check_constraint(
        "ck_embedding_jobs_record_ids",
        "embedding_jobs",
        "embedding_job_uuid_array_valid(historical_question_ids,100) AND "
        "embedding_job_uuid_array_valid(knowledge_chunk_ids,100) AND "
        + extra_valid
        + "jsonb_array_length(historical_question_ids)+jsonb_array_length(knowledge_chunk_ids)"
        + extra_count
        + " BETWEEN 1 AND 100",
    )
    op.create_check_constraint(
        "ck_embedding_jobs_counts",
        "embedding_jobs",
        "requested_count=jsonb_array_length(historical_question_ids)+jsonb_array_length(knowledge_chunk_ids)"
        + extra_count
        + " AND requested_count BETWEEN 1 AND 100 AND embedded_count BETWEEN 0 AND requested_count "
        "AND deduplicated_count BETWEEN 0 AND requested_count "
        "AND embedded_count+deduplicated_count<=requested_count",
    )


def upgrade() -> None:
    op.add_column(
        "knowledge_embeddings", sa.Column("knowledge_projection_id", sa.Uuid(), nullable=True)
    )
    op.create_foreign_key(
        "fk_knowledge_embeddings_projection",
        "knowledge_embeddings",
        "knowledge_projections",
        ["knowledge_projection_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        "ck_knowledge_embeddings_single_target", "knowledge_embeddings", type_="check"
    )
    op.create_check_constraint(
        "ck_knowledge_embeddings_single_target",
        "knowledge_embeddings",
        "num_nonnulls(historical_question_id,knowledge_chunk_id,knowledge_projection_id)=1",
    )
    op.create_index(
        "uq_knowledge_embeddings_projection_configuration",
        "knowledge_embeddings",
        ["knowledge_projection_id", "embedding_configuration_id"],
        unique=True,
        postgresql_where=sa.text("knowledge_projection_id IS NOT NULL"),
    )
    op.add_column(
        "embedding_jobs",
        sa.Column(
            "knowledge_projection_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    _job_constraints(projections=True)
    op.execute("""
        CREATE FUNCTION public.lock_projection_embedding_source(
            identifier uuid, expected_review uuid DEFAULT NULL)
        RETURNS boolean LANGUAGE plpgsql AS $$
        DECLARE unit public.knowledge_units%ROWTYPE; review public.knowledge_unit_reviews%ROWTYPE;
        BEGIN
            SELECT u.* INTO unit FROM public.knowledge_units u
                JOIN public.knowledge_projections p ON p.unit_id=u.id WHERE p.id=identifier;
            IF NOT FOUND THEN RETURN false; END IF;
            PERFORM public.lock_knowledge_unit_source(unit.document_id);
            SELECT * INTO review FROM public.knowledge_unit_reviews
                WHERE unit_id=unit.id ORDER BY version DESC LIMIT 1;
            IF review.id IS NULL OR (expected_review IS NOT NULL AND review.id<>expected_review)
                THEN RETURN false; END IF;
            PERFORM public.lock_knowledge_review_taxonomy(review.curriculum_unit_id,
                review.lesson_id,review.competency_id,review.skill_id,review.sub_skill_id,
                review.learning_concept_id);
            RETURN public.knowledge_projection_is_eligible(identifier);
        END; $$;
    """)
    op.execute("DROP TRIGGER aa_verified_embedding_lineage ON public.knowledge_embeddings")
    op.execute("""
        CREATE TRIGGER aa_verified_embedding_lineage BEFORE INSERT ON public.knowledge_embeddings
            FOR EACH ROW WHEN (NEW.knowledge_projection_id IS NULL)
            EXECUTE FUNCTION public.enforce_verified_embedding_lineage();
    """)
    op.execute(
        "DROP TRIGGER enforce_knowledge_embedding_integrity_trigger ON public.knowledge_embeddings"
    )
    op.execute("""
        CREATE TRIGGER enforce_knowledge_embedding_immutable_trigger
            BEFORE UPDATE OR DELETE ON public.knowledge_embeddings
            FOR EACH ROW EXECUTE FUNCTION public.enforce_knowledge_embedding_integrity();
    """)
    op.execute("""
        CREATE TRIGGER enforce_knowledge_embedding_integrity_trigger
            BEFORE INSERT ON public.knowledge_embeddings
            FOR EACH ROW WHEN (NEW.knowledge_projection_id IS NULL)
            EXECUTE FUNCTION public.enforce_knowledge_embedding_integrity();
    """)
    op.execute("""
        CREATE FUNCTION public.enforce_projection_embedding_lineage() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE projection public.knowledge_projections%ROWTYPE;
        BEGIN
            IF TG_OP='TRUNCATE' THEN
                IF EXISTS (SELECT 1 FROM public.knowledge_embeddings
                    WHERE knowledge_projection_id IS NOT NULL)
                THEN RAISE EXCEPTION 'projection embedding history cannot be truncated'
                    USING ERRCODE='23514'; END IF;
                RETURN NULL;
            END IF;
            IF NOT public.lock_projection_embedding_source(NEW.knowledge_projection_id) THEN
                RAISE EXCEPTION 'embedding requires current reviewed projection lineage'
                    USING ERRCODE='23514';
            END IF;
            SELECT * INTO projection FROM public.knowledge_projections
                WHERE id=NEW.knowledge_projection_id;
            IF projection.id IS NULL OR NEW.historical_question_id IS NOT NULL
                OR NEW.knowledge_chunk_id IS NOT NULL
                OR NEW.source_text_sha256 IS DISTINCT FROM projection.text_sha256
            THEN RAISE EXCEPTION 'embedding hash differs from its exact projection'
                USING ERRCODE='23514'; END IF;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE TRIGGER aa_verified_projection_embedding_lineage
            BEFORE INSERT ON public.knowledge_embeddings
            FOR EACH ROW WHEN (NEW.knowledge_projection_id IS NOT NULL)
            EXECUTE FUNCTION public.enforce_projection_embedding_lineage();
    """)
    op.execute("""
        CREATE TRIGGER projection_embedding_no_truncate
            BEFORE TRUNCATE ON public.knowledge_embeddings
            FOR EACH STATEMENT EXECUTE FUNCTION public.enforce_projection_embedding_lineage();
    """)
    op.execute("""
        CREATE FUNCTION public.guard_projection_embedding_job() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE identifier uuid;
        BEGIN
            IF TG_OP='UPDATE' THEN
                IF NEW.knowledge_projection_ids IS DISTINCT FROM OLD.knowledge_projection_ids THEN
                    RAISE EXCEPTION 'embedding projection request identity is immutable'
                        USING ERRCODE='23514';
                END IF;
                RETURN NEW;
            END IF;
            IF NOT public.embedding_job_uuid_array_valid(NEW.knowledge_projection_ids,100) THEN
                RAISE EXCEPTION 'projection embedding targets must be bounded UUIDs'
                    USING ERRCODE='23514';
            END IF;
            FOR identifier IN SELECT value::uuid
                FROM jsonb_array_elements_text(NEW.knowledge_projection_ids) LOOP
                IF NOT EXISTS (SELECT 1 FROM public.knowledge_projections p
                    JOIN public.knowledge_units u ON u.id=p.unit_id
                    WHERE p.id=identifier AND u.curriculum_version_id=NEW.curriculum_version_id
                        AND public.knowledge_projection_is_eligible(p.id))
                THEN RAISE EXCEPTION 'embedding job requires current reviewed projection scope'
                    USING ERRCODE='23514'; END IF;
            END LOOP;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE TRIGGER guard_projection_embedding_job_trigger
            BEFORE INSERT OR UPDATE ON public.embedding_jobs
            FOR EACH ROW EXECUTE FUNCTION public.guard_projection_embedding_job();
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM public.knowledge_embeddings
                    WHERE knowledge_projection_id IS NOT NULL)
                OR EXISTS (SELECT 1 FROM public.embedding_jobs
                    WHERE jsonb_array_length(knowledge_projection_ids)>0)
            THEN RAISE EXCEPTION 'cannot discard projection embedding history'
                USING ERRCODE='23514'; END IF;
        END; $$;
    """)
    op.execute("DROP TRIGGER guard_projection_embedding_job_trigger ON public.embedding_jobs")
    op.execute("DROP FUNCTION public.guard_projection_embedding_job()")
    op.execute(
        "DROP TRIGGER aa_verified_projection_embedding_lineage ON public.knowledge_embeddings"
    )
    op.execute("DROP TRIGGER projection_embedding_no_truncate ON public.knowledge_embeddings")
    op.execute("DROP FUNCTION public.enforce_projection_embedding_lineage()")
    op.execute("DROP FUNCTION public.lock_projection_embedding_source(uuid,uuid)")
    op.execute(
        "DROP TRIGGER enforce_knowledge_embedding_immutable_trigger ON public.knowledge_embeddings"
    )
    op.execute(
        "DROP TRIGGER enforce_knowledge_embedding_integrity_trigger ON public.knowledge_embeddings"
    )
    op.execute("""
        CREATE TRIGGER enforce_knowledge_embedding_integrity_trigger
            BEFORE INSERT OR UPDATE OR DELETE ON public.knowledge_embeddings
            FOR EACH ROW EXECUTE FUNCTION public.enforce_knowledge_embedding_integrity();
    """)
    op.execute("DROP TRIGGER aa_verified_embedding_lineage ON public.knowledge_embeddings")
    op.execute("""
        CREATE TRIGGER aa_verified_embedding_lineage BEFORE INSERT ON public.knowledge_embeddings
            FOR EACH ROW EXECUTE FUNCTION public.enforce_verified_embedding_lineage();
    """)
    _job_constraints(projections=False)
    op.drop_column("embedding_jobs", "knowledge_projection_ids")
    op.drop_index(
        "uq_knowledge_embeddings_projection_configuration", table_name="knowledge_embeddings"
    )
    op.drop_constraint(
        "fk_knowledge_embeddings_projection", "knowledge_embeddings", type_="foreignkey"
    )
    op.drop_constraint(
        "ck_knowledge_embeddings_single_target", "knowledge_embeddings", type_="check"
    )
    op.drop_column("knowledge_embeddings", "knowledge_projection_id")
    op.create_check_constraint(
        "ck_knowledge_embeddings_single_target",
        "knowledge_embeddings",
        "(historical_question_id IS NOT NULL AND knowledge_chunk_id IS NULL) OR "
        "(historical_question_id IS NULL AND knowledge_chunk_id IS NOT NULL)",
    )

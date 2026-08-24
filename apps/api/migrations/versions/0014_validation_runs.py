from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0014_validation_runs"
down_revision: str | None = "0013_generation_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MAX_INPUT_SNAPSHOT_BYTES = 8_388_608
_MAX_EVIDENCE_BYTES = 196_608
_FINGERPRINT_SQL = "^[0-9a-f]{64}$"


def upgrade() -> None:
    _create_validation_json_functions()
    op.create_table(
        "validation_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("curriculum_version_id", sa.Uuid(), nullable=False),
        sa.Column("generation_run_id", sa.Uuid(), nullable=False),
        sa.Column("generation_attempt_id", sa.Uuid(), nullable=False),
        sa.Column("pipeline_version", sa.String(128), nullable=False),
        sa.Column("pipeline_fingerprint", sa.String(64), nullable=False),
        sa.Column("input_schema_version", sa.String(128), nullable=False),
        sa.Column("report_schema_version", sa.String(128), nullable=False),
        sa.Column("generation_result_fingerprint", sa.String(64), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("candidate_fingerprint", sa.String(64), nullable=False),
        sa.Column("report_fingerprint", sa.String(64), nullable=False),
        sa.Column("overall_status", sa.String(8), nullable=False),
        sa.Column("input_snapshot", JSONB(), nullable=False),
        sa.Column("validator_lineage", JSONB(), nullable=False),
        sa.Column("limitations", JSONB(), nullable=False),
        sa.Column("finding_count", sa.Integer(), nullable=False),
        sa.Column("validator_count", sa.Integer(), nullable=False),
        sa.Column("grounding_source_count", sa.Integer(), nullable=False),
        sa.Column("duplicate_reference_count", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["curriculum_version_id"],
            ["curriculum_versions.id"],
            name="fk_validation_runs_curriculum_version_id_curriculum_versions",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["generation_run_id", "curriculum_version_id"],
            ["generation_runs.id", "generation_runs.curriculum_version_id"],
            name="fk_validation_runs_generation_curriculum",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["generation_attempt_id", "generation_run_id"],
            ["generation_attempts.id", "generation_attempts.generation_run_id"],
            name="fk_validation_runs_generation_attempt",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "generation_run_id",
            "pipeline_version",
            name="uq_validation_runs_generation_pipeline",
        ),
        sa.UniqueConstraint(
            "input_fingerprint",
            "pipeline_version",
            name="uq_validation_runs_input_pipeline",
        ),
        sa.UniqueConstraint(
            "id",
            "curriculum_version_id",
            name="uq_validation_runs_id_curriculum",
        ),
        *(
            sa.CheckConstraint(
                f"{column_name} ~ '{_FINGERPRINT_SQL}'",
                name=f"ck_validation_runs_{column_name}",
            )
            for column_name in (
                "pipeline_fingerprint",
                "generation_result_fingerprint",
                "input_fingerprint",
                "candidate_fingerprint",
                "report_fingerprint",
            )
        ),
        *(
            sa.CheckConstraint(
                f"{column_name} = btrim({column_name}) AND length({column_name}) BETWEEN 1 AND 128",
                name=f"ck_validation_runs_{column_name}",
            )
            for column_name in (
                "pipeline_version",
                "input_schema_version",
                "report_schema_version",
            )
        ),
        sa.CheckConstraint(
            "overall_status IN ('pass', 'warn', 'fail')",
            name="ck_validation_runs_overall_status",
        ),
        sa.CheckConstraint(
            "finding_count BETWEEN 1 AND 256 AND validator_count BETWEEN 1 AND 32 AND "
            "validator_count <= finding_count AND grounding_source_count BETWEEN 1 AND 16 AND "
            "duplicate_reference_count BETWEEN 0 AND 256",
            name="ck_validation_runs_counts",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(input_snapshot) = 'object' AND "
            "input_snapshot ?& ARRAY['schema_version', 'trust', 'generation', 'candidate', "
            "'candidate_fingerprint', 'input_fingerprint', 'blueprint', 'grounding_sources', "
            "'duplicate_references'] AND "
            "input_snapshot->>'trust' = 'server_reconstructed' AND "
            "input_snapshot->>'schema_version' = input_schema_version AND "
            "input_snapshot->>'candidate_fingerprint' = candidate_fingerprint AND "
            "input_snapshot->>'input_fingerprint' = input_fingerprint AND "
            "jsonb_typeof(input_snapshot->'generation') = 'object' AND "
            "input_snapshot->'generation' ?& ARRAY['generation_run_id', "
            "'generation_attempt_id', 'generation_result_fingerprint'] AND "
            "input_snapshot->'generation'->>'generation_run_id' = generation_run_id::text AND "
            "input_snapshot->'generation'->>'generation_attempt_id' = "
            "generation_attempt_id::text AND "
            "input_snapshot->'generation'->>'generation_result_fingerprint' = "
            "generation_result_fingerprint AND "
            "jsonb_typeof(input_snapshot->'candidate') = 'object' AND "
            "jsonb_typeof(input_snapshot->'blueprint') = 'object' AND "
            "jsonb_typeof(input_snapshot->'grounding_sources') = 'array' AND "
            "jsonb_array_length(input_snapshot->'grounding_sources') = "
            "grounding_source_count AND "
            "jsonb_typeof(input_snapshot->'duplicate_references') = 'array' AND "
            "jsonb_array_length(input_snapshot->'duplicate_references') = "
            "duplicate_reference_count AND "
            f"pg_column_size(input_snapshot) <= {_MAX_INPUT_SNAPSHOT_BYTES}",
            name="ck_validation_runs_input_snapshot",
        ),
        sa.CheckConstraint(
            "validation_lineage_valid(validator_lineage, validator_count)",
            name="ck_validation_runs_validator_lineage",
        ),
        sa.CheckConstraint(
            "validation_text_array_valid(limitations, 1, 16, 2048)",
            name="ck_validation_runs_limitations",
        ),
    )
    op.create_index(
        "ix_validation_runs_curriculum_created",
        "validation_runs",
        ["curriculum_version_id", "created_at", "id"],
    )
    op.create_index(
        "ix_validation_runs_generation_created",
        "validation_runs",
        ["generation_run_id", "created_at", "id"],
    )
    op.create_index(
        "ix_validation_runs_status_created",
        "validation_runs",
        ["overall_status", "created_at", "id"],
    )

    op.create_table(
        "validation_findings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("validation_run_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("validator_id", sa.String(128), nullable=False),
        sa.Column("validator_version", sa.String(128), nullable=False),
        sa.Column("code", sa.String(128), nullable=False),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("message", sa.String(1024), nullable=False),
        sa.Column("evidence", JSONB(), nullable=False),
        sa.Column("evidence_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["validation_run_id"],
            ["validation_runs.id"],
            name="fk_validation_findings_validation_run_id_validation_runs",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "validation_run_id",
            "ordinal",
            name="uq_validation_findings_run_ordinal",
        ),
        sa.UniqueConstraint(
            "validation_run_id",
            "validator_id",
            "code",
            name="uq_validation_findings_run_validator_code",
        ),
        sa.CheckConstraint(
            "ordinal BETWEEN 0 AND 255",
            name="ck_validation_findings_ordinal",
        ),
        sa.CheckConstraint(
            "validator_id = btrim(validator_id) AND length(validator_id) BETWEEN 1 AND 128 AND "
            "validator_id ~ '^[a-z0-9][a-z0-9-]*$'",
            name="ck_validation_findings_validator_id",
        ),
        sa.CheckConstraint(
            "validator_version = btrim(validator_version) AND "
            "length(validator_version) BETWEEN 1 AND 128",
            name="ck_validation_findings_validator_version",
        ),
        sa.CheckConstraint(
            "code = btrim(code) AND length(code) BETWEEN 1 AND 128 AND "
            "code ~ '^[a-z][a-z0-9]*([._-][a-z0-9]+)*$'",
            name="ck_validation_findings_code",
        ),
        sa.CheckConstraint(
            "status IN ('pass', 'warn', 'fail')",
            name="ck_validation_findings_status",
        ),
        sa.CheckConstraint(
            "message = btrim(message) AND char_length(message) BETWEEN 1 AND 1024",
            name="ck_validation_findings_message",
        ),
        sa.CheckConstraint(
            "validation_evidence_valid(evidence, evidence_count) AND "
            f"pg_column_size(evidence) <= {_MAX_EVIDENCE_BYTES}",
            name="ck_validation_findings_evidence",
        ),
    )
    op.create_index(
        "ix_validation_findings_run_ordinal",
        "validation_findings",
        ["validation_run_id", "ordinal"],
    )
    op.create_index(
        "ix_validation_findings_code_status",
        "validation_findings",
        ["code", "status"],
    )
    _create_validation_triggers()


def _create_validation_json_functions() -> None:
    op.execute(
        """
        CREATE FUNCTION validation_text_array_valid(
            candidate jsonb,
            minimum_items integer,
            maximum_items integer,
            maximum_characters integer
        )
        RETURNS boolean
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        AS $$
        DECLARE
            item jsonb;
        BEGIN
            IF jsonb_typeof(candidate) <> 'array'
                OR jsonb_array_length(candidate) NOT BETWEEN minimum_items AND maximum_items
            THEN
                RETURN FALSE;
            END IF;
            FOR item IN SELECT value FROM jsonb_array_elements(candidate)
            LOOP
                IF jsonb_typeof(item) <> 'string'
                    OR char_length(item #>> '{}') NOT BETWEEN 1 AND maximum_characters
                    OR btrim(item #>> '{}') = ''
                THEN
                    RETURN FALSE;
                END IF;
            END LOOP;
            RETURN TRUE;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION validation_lineage_valid(candidate jsonb, expected_count integer)
        RETURNS boolean
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        AS $$
        DECLARE
            item jsonb;
            distinct_count integer;
        BEGIN
            IF jsonb_typeof(candidate) <> 'array'
                OR jsonb_array_length(candidate) <> expected_count
                OR expected_count NOT BETWEEN 1 AND 32
            THEN
                RETURN FALSE;
            END IF;
            FOR item IN SELECT value FROM jsonb_array_elements(candidate)
            LOOP
                IF jsonb_typeof(item) <> 'object'
                    OR NOT item ?& ARRAY['validator_id', 'validator_version']
                    OR item - ARRAY['validator_id', 'validator_version'] <> '{}'::jsonb
                    OR jsonb_typeof(item->'validator_id') <> 'string'
                    OR jsonb_typeof(item->'validator_version') <> 'string'
                    OR item->>'validator_id' !~ '^[a-z0-9][a-z0-9-]*$'
                    OR char_length(item->>'validator_id') NOT BETWEEN 1 AND 128
                    OR char_length(item->>'validator_version') NOT BETWEEN 1 AND 128
                    OR btrim(item->>'validator_version') <> item->>'validator_version'
                THEN
                    RETURN FALSE;
                END IF;
            END LOOP;
            SELECT count(*) INTO distinct_count
            FROM (
                SELECT DISTINCT value->>'validator_id', value->>'validator_version'
                FROM jsonb_array_elements(candidate)
            ) AS identities;
            RETURN distinct_count = expected_count;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION validation_evidence_valid(candidate jsonb, expected_count integer)
        RETURNS boolean
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        AS $$
        DECLARE
            item jsonb;
            distinct_count integer;
        BEGIN
            IF jsonb_typeof(candidate) <> 'array'
                OR jsonb_array_length(candidate) <> expected_count
                OR expected_count NOT BETWEEN 1 AND 64
            THEN
                RETURN FALSE;
            END IF;
            FOR item IN SELECT value FROM jsonb_array_elements(candidate)
            LOOP
                IF jsonb_typeof(item) <> 'object'
                    OR NOT item ?& ARRAY['location', 'expected', 'observed']
                    OR item - ARRAY['location', 'expected', 'observed'] <> '{}'::jsonb
                    OR jsonb_typeof(item->'location') <> 'string'
                    OR jsonb_typeof(item->'expected') <> 'string'
                    OR jsonb_typeof(item->'observed') <> 'string'
                    OR char_length(item->>'location') NOT BETWEEN 1 AND 512
                    OR char_length(item->>'expected') NOT BETWEEN 1 AND 1024
                    OR char_length(item->>'observed') NOT BETWEEN 1 AND 1024
                    OR btrim(item->>'location') = ''
                    OR btrim(item->>'expected') = ''
                    OR btrim(item->>'observed') = ''
                THEN
                    RETURN FALSE;
                END IF;
            END LOOP;
            SELECT count(*) INTO distinct_count
            FROM (SELECT DISTINCT value FROM jsonb_array_elements(candidate)) AS evidence_items;
            RETURN distinct_count = expected_count;
        END;
        $$
        """
    )


def _create_validation_triggers() -> None:
    op.execute(
        """
        CREATE FUNCTION enforce_validation_run_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            generation_status text;
            persisted_result_attempt_id uuid;
            generation_disposition text;
        BEGIN
            SELECT status, generation_runs.result_attempt_id, disposition
            INTO generation_status, persisted_result_attempt_id, generation_disposition
            FROM generation_runs
            WHERE id = NEW.generation_run_id
                AND curriculum_version_id = NEW.curriculum_version_id;
            IF generation_status IS DISTINCT FROM 'succeeded'
                OR persisted_result_attempt_id IS DISTINCT FROM NEW.generation_attempt_id
                OR generation_disposition IS DISTINCT FROM 'requires_validation'
            THEN
                RAISE EXCEPTION 'validation requires the exact succeeded generation result'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_validation_run_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'validation runs are immutable and append-only'
                USING ERRCODE = '23514';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_validation_finding_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            expected_findings integer;
            current_findings integer;
            lineage jsonb;
        BEGIN
            SELECT finding_count, validator_lineage
            INTO expected_findings, lineage
            FROM validation_runs
            WHERE id = NEW.validation_run_id
            FOR UPDATE;
            IF expected_findings IS NULL THEN
                RAISE EXCEPTION 'validation finding run does not exist'
                    USING ERRCODE = '23503';
            END IF;
            SELECT count(*) INTO current_findings
            FROM validation_findings
            WHERE validation_run_id = NEW.validation_run_id;
            IF current_findings >= expected_findings OR NEW.ordinal <> current_findings THEN
                RAISE EXCEPTION 'validation findings must be complete, bounded, and contiguous'
                    USING ERRCODE = '23514';
            END IF;
            IF NOT EXISTS (
                SELECT 1
                FROM jsonb_array_elements(lineage) AS component
                WHERE component->>'validator_id' = NEW.validator_id
                    AND component->>'validator_version' = NEW.validator_version
            ) THEN
                RAISE EXCEPTION 'validation finding identity is outside pipeline lineage'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_validation_report_complete()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            target_run_id uuid;
            expected_findings integer;
            expected_validators integer;
            expected_status text;
            actual_findings integer;
            actual_validators integer;
            actual_rank integer;
            expected_rank integer;
        BEGIN
            IF TG_TABLE_NAME = 'validation_runs' THEN
                target_run_id := NEW.id;
            ELSE
                target_run_id := NEW.validation_run_id;
            END IF;
            SELECT finding_count, validator_count, overall_status
            INTO expected_findings, expected_validators, expected_status
            FROM validation_runs
            WHERE id = target_run_id;
            IF expected_findings IS NULL THEN
                RETURN NEW;
            END IF;
            SELECT
                count(*),
                count(DISTINCT validator_id),
                coalesce(max(CASE status WHEN 'fail' THEN 2 WHEN 'warn' THEN 1 ELSE 0 END), -1)
            INTO actual_findings, actual_validators, actual_rank
            FROM validation_findings
            WHERE validation_run_id = target_run_id;
            expected_rank := CASE expected_status WHEN 'fail' THEN 2 WHEN 'warn' THEN 1 ELSE 0 END;
            IF actual_findings <> expected_findings
                OR actual_validators <> expected_validators
                OR actual_rank <> expected_rank
            THEN
                RAISE EXCEPTION 'validation report findings are incomplete or inconsistent'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_validation_run_insert_trigger
        BEFORE INSERT ON validation_runs
        FOR EACH ROW EXECUTE FUNCTION enforce_validation_run_insert()
        """
    )
    op.execute(
        """
        CREATE TRIGGER reject_validation_run_mutation_trigger
        BEFORE UPDATE OR DELETE ON validation_runs
        FOR EACH ROW EXECUTE FUNCTION reject_validation_run_mutation()
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_validation_finding_insert_trigger
        BEFORE INSERT ON validation_findings
        FOR EACH ROW EXECUTE FUNCTION enforce_validation_finding_insert()
        """
    )
    op.execute(
        """
        CREATE TRIGGER reject_validation_finding_mutation_trigger
        BEFORE UPDATE OR DELETE ON validation_findings
        FOR EACH ROW EXECUTE FUNCTION reject_validation_run_mutation()
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER enforce_validation_run_complete_trigger
        AFTER INSERT ON validation_runs
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION enforce_validation_report_complete()
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER enforce_validation_finding_complete_trigger
        AFTER INSERT ON validation_findings
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION enforce_validation_report_complete()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER enforce_validation_finding_complete_trigger ON validation_findings")
    op.execute("DROP TRIGGER enforce_validation_run_complete_trigger ON validation_runs")
    op.execute("DROP TRIGGER reject_validation_finding_mutation_trigger ON validation_findings")
    op.execute("DROP TRIGGER enforce_validation_finding_insert_trigger ON validation_findings")
    op.execute("DROP TRIGGER reject_validation_run_mutation_trigger ON validation_runs")
    op.execute("DROP TRIGGER enforce_validation_run_insert_trigger ON validation_runs")
    op.execute("DROP FUNCTION enforce_validation_report_complete()")
    op.execute("DROP FUNCTION enforce_validation_finding_insert()")
    op.execute("DROP FUNCTION reject_validation_run_mutation()")
    op.execute("DROP FUNCTION enforce_validation_run_insert()")
    op.drop_index("ix_validation_findings_code_status", table_name="validation_findings")
    op.drop_index("ix_validation_findings_run_ordinal", table_name="validation_findings")
    op.drop_table("validation_findings")
    op.drop_index("ix_validation_runs_status_created", table_name="validation_runs")
    op.drop_index("ix_validation_runs_generation_created", table_name="validation_runs")
    op.drop_index("ix_validation_runs_curriculum_created", table_name="validation_runs")
    op.drop_table("validation_runs")
    op.execute("DROP FUNCTION validation_evidence_valid(jsonb, integer)")
    op.execute("DROP FUNCTION validation_lineage_valid(jsonb, integer)")
    op.execute("DROP FUNCTION validation_text_array_valid(jsonb, integer, integer, integer)")

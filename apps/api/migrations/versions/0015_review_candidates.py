from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0015_review_candidates"
down_revision: str | None = "0014_validation_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MAX_REVISIONS = 32
_MAX_CANDIDATE_VERSION = _MAX_REVISIONS + 3
_MAX_CONTENT_BYTES = 131_072
_MAX_LINEAGE_BYTES = 131_072
_MAX_EVIDENCE_BYTES = 32_768
_MAX_REASON_CHARACTERS = 1_024


def upgrade() -> None:
    _create_json_validation_functions()
    op.create_unique_constraint(
        "uq_validation_runs_candidate_lineage",
        "validation_runs",
        ["id", "generation_run_id", "generation_attempt_id", "curriculum_version_id"],
    )
    _create_candidate_tables()
    _create_candidate_triggers()


def _create_json_validation_functions() -> None:
    op.execute(
        """
        CREATE FUNCTION review_candidate_content_valid(candidate jsonb)
        RETURNS boolean
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        AS $$
        DECLARE
            item jsonb;
            option_ids text[] := ARRAY[]::text[];
            option_id text;
            answer_value text;
        BEGIN
            IF jsonb_typeof(candidate) <> 'object'
                OR NOT candidate ?& ARRAY[
                    'question_type', 'stem', 'options', 'answer', 'explanation', 'marks',
                    'marking_guide'
                ]
                OR candidate - ARRAY[
                    'question_type', 'stem', 'options', 'answer', 'explanation', 'marks',
                    'marking_guide'
                ] <> '{}'::jsonb
                OR jsonb_typeof(candidate->'question_type') <> 'string'
                OR candidate->>'question_type' NOT IN (
                    'multiple_choice', 'short_answer', 'structured', 'structured_response'
                )
                OR jsonb_typeof(candidate->'stem') <> 'string'
                OR candidate->>'stem' <> btrim(candidate->>'stem')
                OR char_length(candidate->>'stem') NOT BETWEEN 1 AND 32768
                OR jsonb_typeof(candidate->'answer') <> 'string'
                OR candidate->>'answer' <> btrim(candidate->>'answer')
                OR char_length(candidate->>'answer') NOT BETWEEN 1 AND 32768
                OR jsonb_typeof(candidate->'explanation') <> 'string'
                OR candidate->>'explanation' <> btrim(candidate->>'explanation')
                OR char_length(candidate->>'explanation') NOT BETWEEN 1 AND 32768
                OR jsonb_typeof(candidate->'marks') <> 'number'
                OR candidate->>'marks' !~ '^[0-9]+$'
                OR (candidate->>'marks')::integer NOT BETWEEN 1 AND 100
                OR jsonb_typeof(candidate->'options') <> 'array'
                OR jsonb_array_length(candidate->'options') > 16
                OR jsonb_typeof(candidate->'marking_guide') <> 'array'
                OR jsonb_array_length(candidate->'marking_guide') NOT BETWEEN 1 AND 64
            THEN
                RETURN FALSE;
            END IF;

            FOR item IN SELECT value FROM jsonb_array_elements(candidate->'options')
            LOOP
                IF jsonb_typeof(item) <> 'object'
                    OR NOT item ?& ARRAY['option_id', 'text']
                    OR item - ARRAY['option_id', 'text'] <> '{}'::jsonb
                    OR jsonb_typeof(item->'option_id') <> 'string'
                    OR jsonb_typeof(item->'text') <> 'string'
                    OR item->>'option_id' <> btrim(item->>'option_id')
                    OR char_length(item->>'option_id') NOT BETWEEN 1 AND 128
                    OR item->>'text' <> btrim(item->>'text')
                    OR char_length(item->>'text') NOT BETWEEN 1 AND 8192
                THEN
                    RETURN FALSE;
                END IF;
                option_id := item->>'option_id';
                IF option_id = ANY(option_ids) THEN
                    RETURN FALSE;
                END IF;
                option_ids := array_append(option_ids, option_id);
            END LOOP;

            FOR item IN SELECT value FROM jsonb_array_elements(candidate->'marking_guide')
            LOOP
                IF jsonb_typeof(item) <> 'string'
                    OR item #>> '{}' <> btrim(item #>> '{}')
                    OR char_length(item #>> '{}') NOT BETWEEN 1 AND 8192
                THEN
                    RETURN FALSE;
                END IF;
            END LOOP;

            IF candidate->>'question_type' = 'multiple_choice' THEN
                answer_value := candidate->>'answer';
                IF array_length(option_ids, 1) IS NULL
                    OR array_length(option_ids, 1) NOT BETWEEN 2 AND 16
                    OR NOT answer_value = ANY(option_ids)
                THEN
                    RETURN FALSE;
                END IF;
            END IF;
            RETURN TRUE;
        EXCEPTION WHEN numeric_value_out_of_range THEN
            RETURN FALSE;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION review_candidate_lineage_valid(
            candidate jsonb,
            expected_generation_id uuid,
            expected_attempt_id uuid,
            expected_paper_blueprint_id uuid,
            expected_blueprint_id text,
            expected_blueprint_version text,
            expected_slot_id text
        )
        RETURNS boolean
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        AS $$
        DECLARE
            item jsonb;
            seen jsonb[] := ARRAY[]::jsonb[];
        BEGIN
            IF jsonb_typeof(candidate) <> 'object'
                OR NOT candidate ?& ARRAY[
                    'generation_id', 'generation_attempt_id', 'paper_blueprint_id',
                    'blueprint_id', 'blueprint_version', 'blueprint_slot_id', 'prompt_version',
                    'provider', 'model_version', 'retrieval_version', 'schema_version', 'provenance'
                ]
                OR candidate - ARRAY[
                    'generation_id', 'generation_attempt_id', 'paper_blueprint_id',
                    'blueprint_id', 'blueprint_version', 'blueprint_slot_id', 'prompt_version',
                    'provider', 'model_version', 'retrieval_version', 'schema_version', 'provenance'
                ] <> '{}'::jsonb
                OR jsonb_typeof(candidate->'generation_id') <> 'string'
                OR candidate->>'generation_id' <> expected_generation_id::text
                OR jsonb_typeof(candidate->'generation_attempt_id') <> 'string'
                OR candidate->>'generation_attempt_id' <> expected_attempt_id::text
                OR jsonb_typeof(candidate->'paper_blueprint_id') <> 'string'
                OR candidate->>'paper_blueprint_id' <> expected_paper_blueprint_id::text
                OR jsonb_typeof(candidate->'blueprint_id') <> 'string'
                OR candidate->>'blueprint_id' <> expected_blueprint_id
                OR jsonb_typeof(candidate->'blueprint_version') <> 'string'
                OR candidate->>'blueprint_version' <> expected_blueprint_version
                OR jsonb_typeof(candidate->'blueprint_slot_id') <> 'string'
                OR candidate->>'blueprint_slot_id' <> expected_slot_id
                OR jsonb_typeof(candidate->'provenance') <> 'array'
                OR jsonb_array_length(candidate->'provenance') NOT BETWEEN 1 AND 16
            THEN
                RETURN FALSE;
            END IF;

            FOREACH item IN ARRAY ARRAY[
                candidate->'prompt_version', candidate->'provider', candidate->'model_version',
                candidate->'retrieval_version', candidate->'schema_version'
            ]
            LOOP
                IF jsonb_typeof(item) <> 'string'
                    OR item #>> '{}' <> btrim(item #>> '{}')
                    OR char_length(item #>> '{}') NOT BETWEEN 1 AND 128
                THEN
                    RETURN FALSE;
                END IF;
            END LOOP;

            FOR item IN SELECT value FROM jsonb_array_elements(candidate->'provenance')
            LOOP
                IF jsonb_typeof(item) <> 'object'
                    OR NOT item ?& ARRAY[
                        'source_document_id', 'source_version', 'page_number', 'chunk_id'
                    ]
                    OR item - ARRAY[
                        'source_document_id', 'source_version', 'page_number', 'chunk_id'
                    ] <> '{}'::jsonb
                    OR jsonb_typeof(item->'source_document_id') <> 'string'
                    OR jsonb_typeof(item->'source_version') <> 'string'
                    OR jsonb_typeof(item->'page_number') <> 'number'
                    OR jsonb_typeof(item->'chunk_id') <> 'string'
                    OR item->>'source_document_id' <> btrim(item->>'source_document_id')
                    OR char_length(item->>'source_document_id') NOT BETWEEN 1 AND 256
                    OR item->>'source_version' <> btrim(item->>'source_version')
                    OR char_length(item->>'source_version') NOT BETWEEN 1 AND 256
                    OR item->>'page_number' !~ '^[0-9]+$'
                    OR (item->>'page_number')::integer NOT BETWEEN 1 AND 1000000
                    OR item->>'chunk_id' <> btrim(item->>'chunk_id')
                    OR char_length(item->>'chunk_id') NOT BETWEEN 1 AND 256
                    OR item = ANY(seen)
                THEN
                    RETURN FALSE;
                END IF;
                seen := array_append(seen, item);
            END LOOP;
            RETURN TRUE;
        EXCEPTION WHEN numeric_value_out_of_range THEN
            RETURN FALSE;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION review_candidate_evidence_valid(candidate jsonb, expected_run_id uuid)
        RETURNS boolean
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        AS $$
        DECLARE
            item jsonb;
            seen text[] := ARRAY[]::text[];
            reference text;
        BEGIN
            IF jsonb_typeof(candidate) <> 'object'
                OR NOT candidate ?& ARRAY[
                    'validation_run_id', 'validator_version', 'finding_refs', 'passed',
                    'validated_revision'
                ]
                OR candidate - ARRAY[
                    'validation_run_id', 'validator_version', 'finding_refs', 'passed',
                    'validated_revision'
                ] <> '{}'::jsonb
                OR jsonb_typeof(candidate->'validation_run_id') <> 'string'
                OR candidate->>'validation_run_id' <> expected_run_id::text
                OR jsonb_typeof(candidate->'validator_version') <> 'string'
                OR candidate->>'validator_version' <> btrim(candidate->>'validator_version')
                OR char_length(candidate->>'validator_version') NOT BETWEEN 1 AND 257
                OR jsonb_typeof(candidate->'finding_refs') <> 'array'
                OR jsonb_array_length(candidate->'finding_refs') NOT BETWEEN 1 AND 256
                OR candidate->'passed' <> 'true'::jsonb
                OR candidate->'validated_revision' <> '1'::jsonb
            THEN
                RETURN FALSE;
            END IF;
            FOR item IN SELECT value FROM jsonb_array_elements(candidate->'finding_refs')
            LOOP
                IF jsonb_typeof(item) <> 'string'
                    OR item #>> '{}' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-'
                        '[0-9a-f]{4}-[0-9a-f]{12}$'
                THEN
                    RETURN FALSE;
                END IF;
                reference := item #>> '{}';
                IF reference = ANY(seen) THEN
                    RETURN FALSE;
                END IF;
                seen := array_append(seen, reference);
            END LOOP;
            RETURN TRUE;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION review_candidate_initial_content_matches(content jsonb, generated jsonb)
        RETURNS boolean
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        AS $$
        DECLARE
            item jsonb;
            criterion jsonb;
            ordinal integer := 0;
            parsed jsonb;
        BEGIN
            IF content->>'question_type' IS DISTINCT FROM generated->>'question_type'
                OR content->>'stem' IS DISTINCT FROM generated->>'stem'
                OR content->'options' IS DISTINCT FROM generated->'options'
                OR content->>'explanation' IS DISTINCT FROM generated->'answer'->>'explanation'
                OR content->'marks' IS DISTINCT FROM generated->'marking'->'total_marks'
                OR jsonb_typeof(generated->'answer') <> 'object'
                OR jsonb_typeof(generated->'marking') <> 'object'
                OR jsonb_typeof(generated->'marking'->'criteria') <> 'array'
                OR jsonb_array_length(content->'marking_guide') <>
                    jsonb_array_length(generated->'marking'->'criteria')
            THEN
                RETURN FALSE;
            END IF;
            IF jsonb_typeof(generated->'answer'->'correct_option_id') = 'string' THEN
                IF content->>'answer' IS DISTINCT FROM
                    generated->'answer'->>'correct_option_id'
                THEN
                    RETURN FALSE;
                END IF;
            ELSIF generated->'answer'->'correct_option_id' = 'null'::jsonb THEN
                BEGIN
                    parsed := (content->>'answer')::jsonb;
                EXCEPTION WHEN others THEN
                    RETURN FALSE;
                END;
                IF parsed IS DISTINCT FROM generated->'answer'->'accepted_responses' THEN
                    RETURN FALSE;
                END IF;
            ELSE
                RETURN FALSE;
            END IF;

            FOR item IN SELECT value FROM jsonb_array_elements(content->'marking_guide')
            LOOP
                criterion := generated->'marking'->'criteria'->ordinal;
                BEGIN
                    parsed := (item #>> '{}')::jsonb;
                EXCEPTION WHEN others THEN
                    RETURN FALSE;
                END;
                IF parsed IS DISTINCT FROM criterion THEN
                    RETURN FALSE;
                END IF;
                ordinal := ordinal + 1;
            END LOOP;
            RETURN TRUE;
        END;
        $$
        """
    )


def _create_candidate_tables() -> None:
    op.create_table(
        "question_candidates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("curriculum_version_id", sa.Uuid(), nullable=False),
        sa.Column("generation_run_id", sa.Uuid(), nullable=False),
        sa.Column("generation_attempt_id", sa.Uuid(), nullable=False),
        sa.Column("validation_run_id", sa.Uuid(), nullable=False),
        sa.Column("paper_blueprint_id", sa.Uuid(), nullable=False),
        sa.Column("blueprint_id", sa.String(128), nullable=False),
        sa.Column("blueprint_version", sa.String(128), nullable=False),
        sa.Column("blueprint_slot_id", sa.String(128), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("current_revision", sa.Integer(), nullable=False),
        sa.Column("generation_lineage", JSONB(), nullable=False),
        sa.Column("validation_evidence", JSONB(), nullable=False),
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
            name="fk_question_candidates_curriculum_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["generation_run_id", "curriculum_version_id"],
            ["generation_runs.id", "generation_runs.curriculum_version_id"],
            name="fk_question_candidates_generation_curriculum",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["generation_attempt_id", "generation_run_id"],
            ["generation_attempts.id", "generation_attempts.generation_run_id"],
            name="fk_question_candidates_generation_attempt",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "validation_run_id",
                "generation_run_id",
                "generation_attempt_id",
                "curriculum_version_id",
            ],
            [
                "validation_runs.id",
                "validation_runs.generation_run_id",
                "validation_runs.generation_attempt_id",
                "validation_runs.curriculum_version_id",
            ],
            name="fk_question_candidates_validation_lineage",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["paper_blueprint_id", "curriculum_version_id"],
            ["paper_blueprints.id", "paper_blueprints.curriculum_version_id"],
            name="fk_question_candidates_blueprint_curriculum",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("generation_run_id", name="uq_question_candidates_generation_run"),
        sa.UniqueConstraint("validation_run_id", name="uq_question_candidates_validation_run"),
        sa.CheckConstraint(
            "id = generation_run_id", name="ck_question_candidates_deterministic_id"
        ),
        sa.CheckConstraint(
            "blueprint_id = btrim(blueprint_id) AND length(blueprint_id) BETWEEN 1 AND 128 AND "
            "blueprint_version = btrim(blueprint_version) AND "
            "length(blueprint_version) BETWEEN 1 AND 128 AND "
            "blueprint_slot_id = btrim(blueprint_slot_id) AND "
            "length(blueprint_slot_id) BETWEEN 1 AND 128",
            name="ck_question_candidates_blueprint_values",
        ),
        sa.CheckConstraint(
            f"current_revision BETWEEN 1 AND {_MAX_REVISIONS} AND "
            "((state = 'validated' AND version = 2 AND current_revision = 1) OR "
            "(state = 'in_review' AND version = current_revision + 2) OR "
            "(state IN ('approved', 'rejected') AND version = current_revision + 3))",
            name="ck_question_candidates_state_version_revision",
        ),
        sa.CheckConstraint(
            "review_candidate_lineage_valid(generation_lineage, id, generation_attempt_id, "
            "paper_blueprint_id, blueprint_id, blueprint_version, blueprint_slot_id) AND "
            f"pg_column_size(generation_lineage) <= {_MAX_LINEAGE_BYTES}",
            name="ck_question_candidates_generation_lineage",
        ),
        sa.CheckConstraint(
            "review_candidate_evidence_valid(validation_evidence, validation_run_id) AND "
            f"pg_column_size(validation_evidence) <= {_MAX_EVIDENCE_BYTES}",
            name="ck_question_candidates_validation_evidence",
        ),
    )
    op.create_index(
        "ix_question_candidates_curriculum_state_created",
        "question_candidates",
        ["curriculum_version_id", "state", "created_at", "id"],
    )
    op.create_index(
        "ix_question_candidates_curriculum_blueprint_slot",
        "question_candidates",
        [
            "curriculum_version_id",
            "paper_blueprint_id",
            "blueprint_slot_id",
            "created_at",
            "id",
        ],
    )

    op.create_table(
        "question_candidate_revisions",
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("candidate_version", sa.Integer(), nullable=False),
        sa.Column("content", JSONB(), nullable=False),
        sa.Column("reviewer_id", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.String(_MAX_REASON_CHARACTERS), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("candidate_id", "revision", name="pk_question_candidate_revisions"),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["question_candidates.id"],
            name="fk_candidate_revisions_candidate",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            f"revision BETWEEN 1 AND {_MAX_REVISIONS} AND "
            "((revision = 1 AND candidate_version = 2 AND reviewer_id IS NULL AND "
            "reason IS NULL) OR (revision >= 2 AND candidate_version = revision + 2 AND "
            "reviewer_id IS NOT NULL AND reason IS NOT NULL))",
            name="ck_question_candidate_revisions_identity",
        ),
        sa.CheckConstraint(
            "reason IS NULL OR (reason = btrim(reason) AND "
            f"char_length(reason) BETWEEN 1 AND {_MAX_REASON_CHARACTERS})",
            name="ck_question_candidate_revisions_reason",
        ),
        sa.CheckConstraint(
            "review_candidate_content_valid(content) AND "
            f"pg_column_size(content) <= {_MAX_CONTENT_BYTES}",
            name="ck_question_candidate_revisions_content",
        ),
    )
    op.create_index(
        "ix_question_candidate_revisions_candidate_revision",
        "question_candidate_revisions",
        ["candidate_id", "revision"],
    )

    op.create_table(
        "candidate_review_events",
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_version", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("reviewer_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(_MAX_REASON_CHARACTERS), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint(
            "candidate_id", "candidate_version", name="pk_candidate_review_events"
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["question_candidates.id"],
            name="fk_candidate_review_events_candidate_id_question_candidates",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            f"candidate_version BETWEEN 3 AND {_MAX_CANDIDATE_VERSION} AND "
            f"revision BETWEEN 1 AND {_MAX_REVISIONS}",
            name="ck_candidate_review_events_bounds",
        ),
        sa.CheckConstraint(
            "(action = 'started' AND candidate_version = 3 AND revision = 1 AND "
            "reason IS NULL) OR (action = 'edited' AND revision >= 2 AND "
            "candidate_version = revision + 2 AND reason IS NOT NULL) OR "
            "(action = 'approved' AND candidate_version = revision + 3) OR "
            "(action = 'rejected' AND candidate_version = revision + 3 AND "
            "reason IS NOT NULL)",
            name="ck_candidate_review_events_action",
        ),
        sa.CheckConstraint(
            "reason IS NULL OR (reason = btrim(reason) AND "
            f"char_length(reason) BETWEEN 1 AND {_MAX_REASON_CHARACTERS})",
            name="ck_candidate_review_events_reason",
        ),
    )
    op.create_index(
        "ix_candidate_review_events_candidate_version",
        "candidate_review_events",
        ["candidate_id", "candidate_version"],
    )
    op.create_index(
        "ix_candidate_review_events_reviewer_created",
        "candidate_review_events",
        ["reviewer_id", "created_at"],
    )


def _create_candidate_triggers() -> None:
    op.execute(
        """
        CREATE FUNCTION enforce_question_candidate_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            validation_row validation_runs%ROWTYPE;
            generation_row generation_runs%ROWTYPE;
            blueprint_row paper_blueprints%ROWTYPE;
            expected_provenance jsonb;
            referenced_finding_count integer;
        BEGIN
            IF NEW.state <> 'validated' OR NEW.version <> 2 OR NEW.current_revision <> 1
                OR NEW.id IS DISTINCT FROM NEW.generation_run_id
            THEN
                RAISE EXCEPTION 'question candidates must begin validated at v2/revision1'
                    USING ERRCODE = '23514';
            END IF;

            SELECT * INTO validation_row
            FROM validation_runs
            WHERE id = NEW.validation_run_id
                AND generation_run_id = NEW.generation_run_id
                AND generation_attempt_id = NEW.generation_attempt_id
                AND curriculum_version_id = NEW.curriculum_version_id;
            IF NOT FOUND OR validation_row.overall_status IS DISTINCT FROM 'pass' THEN
                RAISE EXCEPTION 'question candidate requires an exact passing validation run'
                    USING ERRCODE = '23514';
            END IF;

            SELECT * INTO generation_row
            FROM generation_runs
            WHERE id = NEW.generation_run_id
                AND curriculum_version_id = NEW.curriculum_version_id;
            IF NOT FOUND
                OR generation_row.status IS DISTINCT FROM 'succeeded'
                OR generation_row.disposition IS DISTINCT FROM 'requires_validation'
                OR generation_row.result_attempt_id IS DISTINCT FROM NEW.generation_attempt_id
                OR generation_row.paper_blueprint_id IS DISTINCT FROM NEW.paper_blueprint_id
                OR generation_row.blueprint_version IS DISTINCT FROM NEW.blueprint_version
                OR generation_row.slot_id IS DISTINCT FROM NEW.blueprint_slot_id
                OR generation_row.candidate IS NULL
            THEN
                RAISE EXCEPTION 'question candidate generation lineage is inconsistent'
                    USING ERRCODE = '23514';
            END IF;

            SELECT * INTO blueprint_row
            FROM paper_blueprints
            WHERE id = NEW.paper_blueprint_id
                AND curriculum_version_id = NEW.curriculum_version_id;
            IF NOT FOUND
                OR blueprint_row.blueprint_id IS DISTINCT FROM NEW.blueprint_id
                OR generation_row.blueprint_snapshot IS DISTINCT FROM blueprint_row.blueprint
                OR generation_row.blueprint_snapshot->'version'->>'blueprint_id'
                    IS DISTINCT FROM NEW.blueprint_id
                OR NOT EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements(blueprint_row.blueprint->'slots') AS slot
                    WHERE slot->>'slot_id' = NEW.blueprint_slot_id
                        AND slot = generation_row.blueprint_slot_snapshot
                )
            THEN
                RAISE EXCEPTION 'question candidate blueprint and slot lineage is inconsistent'
                    USING ERRCODE = '23514';
            END IF;

            IF validation_row.input_snapshot->'generation'->>'generation_run_id'
                    IS DISTINCT FROM NEW.generation_run_id::text
                OR validation_row.input_snapshot->'generation'->>'generation_attempt_id'
                    IS DISTINCT FROM NEW.generation_attempt_id::text
                OR validation_row.input_snapshot->'generation'->>'paper_blueprint_id'
                    IS DISTINCT FROM NEW.paper_blueprint_id::text
                OR validation_row.input_snapshot->'generation'->>'blueprint_version'
                    IS DISTINCT FROM NEW.blueprint_version
                OR validation_row.input_snapshot->'generation'->>'generation_result_fingerprint'
                    IS DISTINCT FROM validation_row.generation_result_fingerprint
                OR validation_row.input_snapshot->'blueprint'->>'slot_id'
                    IS DISTINCT FROM NEW.blueprint_slot_id
            THEN
                RAISE EXCEPTION 'question candidate validation lineage is inconsistent'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.generation_lineage->>'prompt_version'
                    IS DISTINCT FROM generation_row.prompt_version
                OR NEW.generation_lineage->>'provider' IS DISTINCT FROM generation_row.provider
                OR NEW.generation_lineage->>'model_version'
                    IS DISTINCT FROM generation_row.model_version
                OR NEW.generation_lineage->>'retrieval_version'
                    IS DISTINCT FROM generation_row.retrieval_version
                OR NEW.generation_lineage->>'schema_version'
                    IS DISTINCT FROM generation_row.schema_version
            THEN
                RAISE EXCEPTION 'question candidate generation versions are inconsistent'
                    USING ERRCODE = '23514';
            END IF;

            SELECT jsonb_agg(
                jsonb_build_object(
                    'source_document_id', item->'provenance'->>'source_document_id',
                    'source_version', item->'provenance'->>'source_version',
                    'page_number', (item->'provenance'->>'page_number')::integer,
                    'chunk_id', item->'provenance'->>'chunk_id'
                ) ORDER BY ordinal
            ) INTO expected_provenance
            FROM jsonb_array_elements(generation_row.context_snapshot->'items')
                WITH ORDINALITY AS context_item(item, ordinal);
            IF NEW.generation_lineage->'provenance' IS DISTINCT FROM expected_provenance THEN
                RAISE EXCEPTION 'question candidate provenance is inconsistent'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.validation_evidence->>'validator_version' IS DISTINCT FROM
                    validation_row.pipeline_version || '/' || validation_row.report_schema_version
                OR jsonb_array_length(NEW.validation_evidence->'finding_refs')
                    <> validation_row.finding_count
            THEN
                RAISE EXCEPTION 'question candidate validation evidence is inconsistent'
                    USING ERRCODE = '23514';
            END IF;
            SELECT count(*) INTO referenced_finding_count
            FROM validation_findings
            WHERE validation_run_id = NEW.validation_run_id
                AND NEW.validation_evidence->'finding_refs' ? id::text;
            IF referenced_finding_count <> validation_row.finding_count THEN
                RAISE EXCEPTION 'question candidate finding references are incomplete'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_question_candidate_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.id IS DISTINCT FROM OLD.id
                OR NEW.curriculum_version_id IS DISTINCT FROM OLD.curriculum_version_id
                OR NEW.generation_run_id IS DISTINCT FROM OLD.generation_run_id
                OR NEW.generation_attempt_id IS DISTINCT FROM OLD.generation_attempt_id
                OR NEW.validation_run_id IS DISTINCT FROM OLD.validation_run_id
                OR NEW.paper_blueprint_id IS DISTINCT FROM OLD.paper_blueprint_id
                OR NEW.blueprint_id IS DISTINCT FROM OLD.blueprint_id
                OR NEW.blueprint_version IS DISTINCT FROM OLD.blueprint_version
                OR NEW.blueprint_slot_id IS DISTINCT FROM OLD.blueprint_slot_id
                OR NEW.generation_lineage IS DISTINCT FROM OLD.generation_lineage
                OR NEW.validation_evidence IS DISTINCT FROM OLD.validation_evidence
                OR NEW.created_by IS DISTINCT FROM OLD.created_by
                OR NEW.created_at IS DISTINCT FROM OLD.created_at
            THEN
                RAISE EXCEPTION 'question candidate upstream lineage is immutable'
                    USING ERRCODE = '23514';
            END IF;
            IF OLD.state IN ('approved', 'rejected') THEN
                RAISE EXCEPTION 'terminal question candidates are immutable'
                    USING ERRCODE = '23514';
            END IF;
            IF NOT (
                OLD.state = 'validated' AND OLD.version = 2 AND OLD.current_revision = 1
                    AND NEW.state = 'in_review' AND NEW.version = 3
                    AND NEW.current_revision = 1
                OR OLD.state = 'in_review' AND NEW.state = 'in_review'
                    AND NEW.version = OLD.version + 1
                    AND NEW.current_revision = OLD.current_revision + 1
                OR OLD.state = 'in_review' AND NEW.state IN ('approved', 'rejected')
                    AND NEW.version = OLD.version + 1
                    AND NEW.current_revision = OLD.current_revision
            ) THEN
                RAISE EXCEPTION 'invalid question candidate transition'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_question_candidate_delete()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'question candidates are durable and cannot be deleted'
                USING ERRCODE = '23514';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_question_candidate_insert_trigger
        BEFORE INSERT ON question_candidates
        FOR EACH ROW EXECUTE FUNCTION enforce_question_candidate_insert()
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_question_candidate_update_trigger
        BEFORE UPDATE ON question_candidates
        FOR EACH ROW EXECUTE FUNCTION enforce_question_candidate_update()
        """
    )
    op.execute(
        """
        CREATE TRIGGER reject_question_candidate_delete_trigger
        BEFORE DELETE ON question_candidates
        FOR EACH ROW EXECUTE FUNCTION reject_question_candidate_delete()
        """
    )

    op.execute(
        """
        CREATE FUNCTION enforce_question_candidate_revision_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            aggregate question_candidates%ROWTYPE;
            existing_count integer;
            generated_content jsonb;
            original_content jsonb;
        BEGIN
            SELECT * INTO aggregate
            FROM question_candidates WHERE id = NEW.candidate_id FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'question candidate revision aggregate does not exist'
                    USING ERRCODE = '23503';
            END IF;
            SELECT count(*) INTO existing_count
            FROM question_candidate_revisions WHERE candidate_id = NEW.candidate_id;
            IF NEW.revision <> existing_count + 1 THEN
                RAISE EXCEPTION 'question candidate revisions must be contiguous'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.revision = 1 THEN
                IF aggregate.state <> 'validated' OR aggregate.version <> 2
                    OR aggregate.current_revision <> 1 OR NEW.candidate_version <> 2
                THEN
                    RAISE EXCEPTION 'initial candidate revision requires validated v2 aggregate'
                        USING ERRCODE = '23514';
                END IF;
                SELECT candidate INTO generated_content
                FROM generation_runs WHERE id = aggregate.generation_run_id;
                IF generated_content IS NULL
                    OR NOT review_candidate_initial_content_matches(NEW.content, generated_content)
                THEN
                    RAISE EXCEPTION 'initial candidate revision differs from generation result'
                        USING ERRCODE = '23514';
                END IF;
            ELSE
                IF aggregate.state <> 'in_review'
                    OR aggregate.version <> NEW.candidate_version
                    OR aggregate.current_revision <> NEW.revision
                THEN
                    RAISE EXCEPTION 'review edit revision does not match aggregate CAS state'
                        USING ERRCODE = '23514';
                END IF;
                SELECT content INTO original_content
                FROM question_candidate_revisions
                WHERE candidate_id = NEW.candidate_id AND revision = 1;
                IF original_content IS NULL
                    OR NEW.content->>'question_type' IS DISTINCT FROM
                        original_content->>'question_type'
                    OR NEW.content->'marks' IS DISTINCT FROM original_content->'marks'
                THEN
                    RAISE EXCEPTION 'review edit cannot change generated type or marks'
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
        CREATE FUNCTION enforce_candidate_review_event_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            aggregate question_candidates%ROWTYPE;
            existing_count integer;
            revision_reviewer uuid;
            revision_reason text;
        BEGIN
            SELECT * INTO aggregate
            FROM question_candidates WHERE id = NEW.candidate_id FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'candidate review event aggregate does not exist'
                    USING ERRCODE = '23503';
            END IF;
            SELECT count(*) INTO existing_count
            FROM candidate_review_events WHERE candidate_id = NEW.candidate_id;
            IF NEW.candidate_version <> existing_count + 3
                OR NEW.candidate_version <> aggregate.version
            THEN
                RAISE EXCEPTION 'candidate review events must be contiguous and match CAS state'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.action = 'started' THEN
                IF aggregate.state <> 'in_review' OR aggregate.version <> 3
                    OR aggregate.current_revision <> 1 OR existing_count <> 0
                THEN
                    RAISE EXCEPTION 'review start event does not match aggregate state'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF NEW.action = 'edited' THEN
                IF aggregate.state <> 'in_review'
                    OR aggregate.current_revision <> NEW.revision
                THEN
                    RAISE EXCEPTION 'review edit event does not match aggregate state'
                        USING ERRCODE = '23514';
                END IF;
                SELECT reviewer_id, reason INTO revision_reviewer, revision_reason
                FROM question_candidate_revisions
                WHERE candidate_id = NEW.candidate_id AND revision = NEW.revision;
                IF revision_reviewer IS DISTINCT FROM NEW.reviewer_id
                    OR revision_reason IS DISTINCT FROM NEW.reason
                THEN
                    RAISE EXCEPTION 'review edit event does not match its revision'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF NEW.action = 'approved' THEN
                IF aggregate.state <> 'approved'
                    OR aggregate.current_revision <> NEW.revision
                THEN
                    RAISE EXCEPTION 'approval event does not match aggregate state'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF NEW.action = 'rejected' THEN
                IF aggregate.state <> 'rejected'
                    OR aggregate.current_revision <> NEW.revision
                THEN
                    RAISE EXCEPTION 'rejection event does not match aggregate state'
                        USING ERRCODE = '23514';
                END IF;
            ELSE
                RAISE EXCEPTION 'candidate review action is invalid' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_question_candidate_history_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'question candidate revisions and events are append-only'
                USING ERRCODE = '23514';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_question_candidate_revision_insert_trigger
        BEFORE INSERT ON question_candidate_revisions
        FOR EACH ROW EXECUTE FUNCTION enforce_question_candidate_revision_insert()
        """
    )
    op.execute(
        """
        CREATE TRIGGER reject_question_candidate_revision_mutation_trigger
        BEFORE UPDATE OR DELETE ON question_candidate_revisions
        FOR EACH ROW EXECUTE FUNCTION reject_question_candidate_history_mutation()
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_candidate_review_event_insert_trigger
        BEFORE INSERT ON candidate_review_events
        FOR EACH ROW EXECUTE FUNCTION enforce_candidate_review_event_insert()
        """
    )
    op.execute(
        """
        CREATE TRIGGER reject_candidate_review_event_mutation_trigger
        BEFORE UPDATE OR DELETE ON candidate_review_events
        FOR EACH ROW EXECUTE FUNCTION reject_question_candidate_history_mutation()
        """
    )

    op.execute(
        """
        CREATE FUNCTION enforce_question_candidate_complete()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            target_id uuid;
            aggregate question_candidates%ROWTYPE;
            revision_count integer;
            minimum_revision integer;
            maximum_revision integer;
            event_count integer;
            minimum_version integer;
            maximum_version integer;
            first_action text;
            final_action text;
        BEGIN
            IF TG_TABLE_NAME = 'question_candidates' THEN
                target_id := NEW.id;
            ELSE
                target_id := NEW.candidate_id;
            END IF;
            SELECT * INTO aggregate FROM question_candidates WHERE id = target_id;
            IF NOT FOUND THEN
                RETURN NEW;
            END IF;
            SELECT count(*), min(revision), max(revision)
            INTO revision_count, minimum_revision, maximum_revision
            FROM question_candidate_revisions WHERE candidate_id = target_id;
            IF revision_count <> aggregate.current_revision
                OR minimum_revision IS DISTINCT FROM 1
                OR maximum_revision IS DISTINCT FROM aggregate.current_revision
            THEN
                RAISE EXCEPTION 'question candidate revisions are incomplete'
                    USING ERRCODE = '23514';
            END IF;

            SELECT count(*), min(candidate_version), max(candidate_version)
            INTO event_count, minimum_version, maximum_version
            FROM candidate_review_events WHERE candidate_id = target_id;
            IF aggregate.state = 'validated' THEN
                IF event_count <> 0 OR aggregate.version <> 2 OR aggregate.current_revision <> 1
                THEN
                    RAISE EXCEPTION 'validated candidate cannot contain review events'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;
            IF event_count <> aggregate.version - 2
                OR minimum_version IS DISTINCT FROM 3
                OR maximum_version IS DISTINCT FROM aggregate.version
            THEN
                RAISE EXCEPTION 'candidate review events are incomplete'
                    USING ERRCODE = '23514';
            END IF;
            SELECT action INTO first_action FROM candidate_review_events
            WHERE candidate_id = target_id ORDER BY candidate_version LIMIT 1;
            SELECT action INTO final_action FROM candidate_review_events
            WHERE candidate_id = target_id ORDER BY candidate_version DESC LIMIT 1;
            IF first_action IS DISTINCT FROM 'started' THEN
                RAISE EXCEPTION 'candidate review must begin with a start event'
                    USING ERRCODE = '23514';
            END IF;
            IF aggregate.state = 'in_review' AND final_action NOT IN ('started', 'edited') THEN
                RAISE EXCEPTION 'in-review candidate has a terminal event'
                    USING ERRCODE = '23514';
            ELSIF aggregate.state = 'approved' AND final_action IS DISTINCT FROM 'approved' THEN
                RAISE EXCEPTION 'approved candidate lacks its exact terminal event'
                    USING ERRCODE = '23514';
            ELSIF aggregate.state = 'rejected' AND final_action IS DISTINCT FROM 'rejected' THEN
                RAISE EXCEPTION 'rejected candidate lacks its exact terminal event'
                    USING ERRCODE = '23514';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM candidate_review_events AS event
                LEFT JOIN question_candidate_revisions AS revision
                    ON revision.candidate_id = event.candidate_id
                    AND revision.revision = event.revision
                WHERE event.candidate_id = target_id
                    AND event.action = 'edited'
                    AND (
                        revision.candidate_id IS NULL
                        OR revision.candidate_version <> event.candidate_version
                        OR revision.reviewer_id IS DISTINCT FROM event.reviewer_id
                        OR revision.reason IS DISTINCT FROM event.reason
                    )
            ) OR EXISTS (
                SELECT 1
                FROM question_candidate_revisions AS revision
                LEFT JOIN candidate_review_events AS event
                    ON event.candidate_id = revision.candidate_id
                    AND event.revision = revision.revision
                    AND event.action = 'edited'
                WHERE revision.candidate_id = target_id
                    AND revision.revision > 1
                    AND event.candidate_id IS NULL
            ) THEN
                RAISE EXCEPTION 'review edit revisions and events are inconsistent'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    for table_name, operation in (
        ("question_candidates", "INSERT OR UPDATE"),
        ("question_candidate_revisions", "INSERT"),
        ("candidate_review_events", "INSERT"),
    ):
        op.execute(
            f"""
            CREATE CONSTRAINT TRIGGER enforce_{table_name}_complete_trigger
            AFTER {operation} ON {table_name}
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION enforce_question_candidate_complete()
            """
        )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER enforce_candidate_review_events_complete_trigger ON candidate_review_events"
    )
    op.execute(
        "DROP TRIGGER enforce_question_candidate_revisions_complete_trigger "
        "ON question_candidate_revisions"
    )
    op.execute("DROP TRIGGER enforce_question_candidates_complete_trigger ON question_candidates")
    op.execute("DROP FUNCTION enforce_question_candidate_complete()")
    op.execute(
        "DROP TRIGGER reject_candidate_review_event_mutation_trigger ON candidate_review_events"
    )
    op.execute(
        "DROP TRIGGER enforce_candidate_review_event_insert_trigger ON candidate_review_events"
    )
    op.execute(
        "DROP TRIGGER reject_question_candidate_revision_mutation_trigger "
        "ON question_candidate_revisions"
    )
    op.execute(
        "DROP TRIGGER enforce_question_candidate_revision_insert_trigger "
        "ON question_candidate_revisions"
    )
    op.execute("DROP FUNCTION reject_question_candidate_history_mutation()")
    op.execute("DROP FUNCTION enforce_candidate_review_event_insert()")
    op.execute("DROP FUNCTION enforce_question_candidate_revision_insert()")
    op.execute("DROP TRIGGER reject_question_candidate_delete_trigger ON question_candidates")
    op.execute("DROP TRIGGER enforce_question_candidate_update_trigger ON question_candidates")
    op.execute("DROP TRIGGER enforce_question_candidate_insert_trigger ON question_candidates")
    op.execute("DROP FUNCTION reject_question_candidate_delete()")
    op.execute("DROP FUNCTION enforce_question_candidate_update()")
    op.execute("DROP FUNCTION enforce_question_candidate_insert()")
    op.drop_index(
        "ix_candidate_review_events_reviewer_created",
        table_name="candidate_review_events",
    )
    op.drop_index(
        "ix_candidate_review_events_candidate_version",
        table_name="candidate_review_events",
    )
    op.drop_table("candidate_review_events")
    op.drop_index(
        "ix_question_candidate_revisions_candidate_revision",
        table_name="question_candidate_revisions",
    )
    op.drop_table("question_candidate_revisions")
    op.drop_index(
        "ix_question_candidates_curriculum_blueprint_slot", table_name="question_candidates"
    )
    op.drop_index(
        "ix_question_candidates_curriculum_state_created", table_name="question_candidates"
    )
    op.drop_table("question_candidates")
    op.drop_constraint("uq_validation_runs_candidate_lineage", "validation_runs", type_="unique")
    op.execute("DROP FUNCTION review_candidate_initial_content_matches(jsonb, jsonb)")
    op.execute("DROP FUNCTION review_candidate_evidence_valid(jsonb, uuid)")
    op.execute(
        "DROP FUNCTION review_candidate_lineage_valid(jsonb, uuid, uuid, uuid, text, text, text)"
    )
    op.execute("DROP FUNCTION review_candidate_content_valid(jsonb)")

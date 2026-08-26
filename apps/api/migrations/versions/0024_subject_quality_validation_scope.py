from collections.abc import Sequence

from alembic import op

revision: str = "0024_subject_quality_validation_scope"
down_revision: str | None = "0023_teacher_first_multi_grade_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MAX_INPUT_SNAPSHOT_BYTES = 8_388_608

_BASE_INPUT_CHECK = """
jsonb_typeof(input_snapshot) = 'object' AND
input_snapshot ?& ARRAY[
    'schema_version', 'trust', 'generation', 'candidate', 'candidate_fingerprint',
    'input_fingerprint', 'blueprint', 'grounding_sources', 'duplicate_references'
] AND
input_snapshot->>'trust' = 'server_reconstructed' AND
input_snapshot->>'schema_version' = input_schema_version AND
input_snapshot->>'candidate_fingerprint' = candidate_fingerprint AND
input_snapshot->>'input_fingerprint' = input_fingerprint AND
jsonb_typeof(input_snapshot->'generation') = 'object' AND
input_snapshot->'generation' ?& ARRAY[
    'generation_run_id', 'generation_attempt_id', 'generation_result_fingerprint'
] AND
input_snapshot->'generation'->>'generation_run_id' = generation_run_id::text AND
input_snapshot->'generation'->>'generation_attempt_id' = generation_attempt_id::text AND
input_snapshot->'generation'->>'generation_result_fingerprint' =
generation_result_fingerprint AND
jsonb_typeof(input_snapshot->'candidate') = 'object' AND
jsonb_typeof(input_snapshot->'blueprint') = 'object' AND
jsonb_typeof(input_snapshot->'grounding_sources') = 'array' AND
jsonb_array_length(input_snapshot->'grounding_sources') = grounding_source_count AND
jsonb_typeof(input_snapshot->'duplicate_references') = 'array' AND
jsonb_array_length(input_snapshot->'duplicate_references') = duplicate_reference_count AND
pg_column_size(input_snapshot) <= 8388608
"""

_SUBJECT_V3_CHECK = """
(
    input_schema_version <> 'question-validation-input.v3' OR (
        input_snapshot ?& ARRAY[
            'subject_scope', 'generated_scope', 'context_scope_bindings'
        ] AND
        jsonb_typeof(input_snapshot->'subject_scope') = 'object' AND
        input_snapshot->'subject_scope' ?& ARRAY[
            'trust', 'grade', 'medium', 'subject_id', 'subject_code',
            'curriculum_version_id', 'unit_ids', 'lesson_ids'
        ] AND
        input_snapshot->'subject_scope'->>'trust' = 'server_owned' AND
        input_snapshot->'subject_scope'->>'curriculum_version_id' =
        curriculum_version_id::text AND
        jsonb_typeof(input_snapshot->'generated_scope') = 'object' AND
        jsonb_typeof(input_snapshot->'context_scope_bindings') = 'array'
    )
)
"""


def _replace_candidate_insert_gate(*, allow_warnings: bool) -> None:
    status_check = (
        "validation_row.overall_status = 'fail'"
        if allow_warnings
        else "validation_row.overall_status IS DISTINCT FROM 'pass'"
    )
    requirement = (
        "a non-failing validation run" if allow_warnings else "an exact passing validation run"
    )
    function_sql = """
        CREATE OR REPLACE FUNCTION enforce_question_candidate_insert()
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
            IF NOT FOUND OR {status_check} THEN
                RAISE EXCEPTION 'question candidate requires {requirement}'
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
    op.execute(
        function_sql.replace("{status_check}", status_check).replace("{requirement}", requirement)
    )


def upgrade() -> None:
    op.drop_constraint(
        "ck_validation_runs_input_snapshot",
        "validation_runs",
        type_="check",
    )
    op.create_check_constraint(
        "ck_validation_runs_input_snapshot",
        "validation_runs",
        f"({_BASE_INPUT_CHECK}) AND ({_SUBJECT_V3_CHECK})",
    )
    _replace_candidate_insert_gate(allow_warnings=True)


def downgrade() -> None:
    _replace_candidate_insert_gate(allow_warnings=False)
    op.drop_constraint(
        "ck_validation_runs_input_snapshot",
        "validation_runs",
        type_="check",
    )
    op.create_check_constraint(
        "ck_validation_runs_input_snapshot",
        "validation_runs",
        _BASE_INPUT_CHECK,
    )

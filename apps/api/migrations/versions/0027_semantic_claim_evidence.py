from collections.abc import Sequence

from alembic import op

revision: str = "0027_semantic_claim_evidence"
down_revision: str | None = "0026_subject_quality_feedback"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_semantic_validation_functions() -> None:
    op.execute(
        """
        CREATE FUNCTION semantic_verification_lineage_valid(candidate jsonb)
        RETURNS boolean
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        AS $$
        DECLARE
            field_name text;
            field_value text;
        BEGIN
            IF jsonb_typeof(candidate) <> 'object'
                OR NOT candidate ?& ARRAY[
                    'verifier_id', 'verifier_version', 'prompt_version', 'provider',
                    'provider_version', 'model', 'model_version', 'pricing_version'
                ]
                OR candidate - ARRAY[
                    'verifier_id', 'verifier_version', 'prompt_version', 'provider',
                    'provider_version', 'model', 'model_version', 'pricing_version'
                ] <> '{}'::jsonb
            THEN
                RETURN FALSE;
            END IF;
            FOREACH field_name IN ARRAY ARRAY[
                'verifier_id', 'verifier_version', 'prompt_version', 'provider',
                'provider_version', 'model', 'model_version', 'pricing_version'
            ]
            LOOP
                field_value := candidate->>field_name;
                IF jsonb_typeof(candidate->field_name) <> 'string'
                    OR char_length(field_value) NOT BETWEEN 1 AND 128
                    OR field_value <> btrim(field_value)
                    OR field_value ~ '[[:space:]]'
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
        CREATE FUNCTION semantic_verification_accounting_valid(candidate jsonb)
        RETURNS boolean
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        AS $$
        DECLARE
            input_tokens bigint;
            output_tokens bigint;
            total_tokens bigint;
            cost_microusd bigint;
            latency_ms bigint;
        BEGIN
            IF jsonb_typeof(candidate) <> 'object'
                OR NOT candidate ?& ARRAY[
                    'input_tokens', 'output_tokens', 'total_tokens',
                    'cost_microusd', 'latency_ms'
                ]
                OR candidate - ARRAY[
                    'input_tokens', 'output_tokens', 'total_tokens',
                    'cost_microusd', 'latency_ms'
                ] <> '{}'::jsonb
                OR EXISTS (
                    SELECT 1
                    FROM unnest(ARRAY[
                        'input_tokens', 'output_tokens', 'total_tokens',
                        'cost_microusd', 'latency_ms'
                    ]) AS field_name
                    WHERE jsonb_typeof(candidate->field_name) <> 'number'
                        OR candidate->>field_name !~ '^[0-9]+$'
                )
            THEN
                RETURN FALSE;
            END IF;
            input_tokens := (candidate->>'input_tokens')::bigint;
            output_tokens := (candidate->>'output_tokens')::bigint;
            total_tokens := (candidate->>'total_tokens')::bigint;
            cost_microusd := (candidate->>'cost_microusd')::bigint;
            latency_ms := (candidate->>'latency_ms')::bigint;
            RETURN input_tokens BETWEEN 0 AND 10000000
                AND output_tokens BETWEEN 0 AND 10000000
                AND total_tokens BETWEEN 0 AND 10000000
                AND total_tokens = input_tokens + output_tokens
                AND cost_microusd BETWEEN 0 AND 100000000000
                AND latency_ms BETWEEN 0 AND 120000;
        EXCEPTION
            WHEN numeric_value_out_of_range OR invalid_text_representation THEN
                RETURN FALSE;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION semantic_verification_claims_valid(
            candidate jsonb,
            overall_status text
        )
        RETURNS boolean
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        AS $$
        DECLARE
            item jsonb;
            reference jsonb;
            claim_count integer;
            distinct_claim_count integer;
            reference_count integer;
            distinct_reference_count integer;
            expected_status text;
        BEGIN
            IF jsonb_typeof(candidate) <> 'array'
                OR jsonb_array_length(candidate) NOT BETWEEN 0 AND 32
            THEN
                RETURN FALSE;
            END IF;
            claim_count := jsonb_array_length(candidate);
            IF overall_status <> 'unavailable' AND claim_count = 0 THEN
                RETURN FALSE;
            END IF;
            FOR item IN SELECT value FROM jsonb_array_elements(candidate)
            LOOP
                IF jsonb_typeof(item) <> 'object'
                    OR NOT item ?& ARRAY[
                        'claim_id', 'claim_type', 'location', 'status', 'summary', 'evidence_refs'
                    ]
                    OR item - ARRAY[
                        'claim_id', 'claim_type', 'location', 'status', 'summary', 'evidence_refs'
                    ] <> '{}'::jsonb
                    OR jsonb_typeof(item->'claim_id') <> 'string'
                    OR item->>'claim_id' !~ '^[a-z0-9][a-z0-9-]*$'
                    OR char_length(item->>'claim_id') NOT BETWEEN 1 AND 128
                    OR jsonb_typeof(item->'claim_type') <> 'string'
                    OR item->>'claim_type' NOT IN ('answer', 'explanation', 'marking')
                    OR jsonb_typeof(item->'location') <> 'string'
                    OR item->>'location' NOT LIKE '$.candidate.%'
                    OR char_length(item->>'location') NOT BETWEEN 13 AND 512
                    OR jsonb_typeof(item->'status') <> 'string'
                    OR item->>'status' NOT IN (
                        'supported', 'contradicted', 'insufficient_evidence', 'unavailable'
                    )
                    OR jsonb_typeof(item->'summary') <> 'string'
                    OR char_length(item->>'summary') NOT BETWEEN 1 AND 512
                    OR item->>'summary' <> btrim(item->>'summary')
                    OR jsonb_typeof(item->'evidence_refs') <> 'array'
                    OR jsonb_array_length(item->'evidence_refs') NOT BETWEEN 0 AND 32
                THEN
                    RETURN FALSE;
                END IF;
                IF overall_status = 'unavailable' AND item->>'status' <> 'unavailable' THEN
                    RETURN FALSE;
                ELSIF overall_status <> 'unavailable' AND item->>'status' = 'unavailable' THEN
                    RETURN FALSE;
                END IF;
                reference_count := jsonb_array_length(item->'evidence_refs');
                IF item->>'status' IN ('supported', 'contradicted') AND reference_count = 0 THEN
                    RETURN FALSE;
                END IF;
                FOR reference IN
                    SELECT value FROM jsonb_array_elements(item->'evidence_refs')
                LOOP
                    IF jsonb_typeof(reference) <> 'object'
                        OR NOT reference ?& ARRAY[
                            'context_id', 'source_document_id', 'page_number'
                        ]
                        OR reference - ARRAY[
                            'context_id', 'source_document_id', 'page_number'
                        ] <> '{}'::jsonb
                        OR jsonb_typeof(reference->'context_id') <> 'string'
                        OR char_length(reference->>'context_id') NOT BETWEEN 1 AND 256
                        OR btrim(reference->>'context_id') = ''
                        OR jsonb_typeof(reference->'source_document_id') <> 'string'
                        OR char_length(reference->>'source_document_id') NOT BETWEEN 1 AND 256
                        OR btrim(reference->>'source_document_id') = ''
                        OR jsonb_typeof(reference->'page_number') <> 'number'
                        OR reference->>'page_number' !~ '^[0-9]+$'
                        OR (reference->>'page_number')::bigint NOT BETWEEN 1 AND 1000000
                    THEN
                        RETURN FALSE;
                    END IF;
                END LOOP;
                SELECT count(DISTINCT value)
                INTO distinct_reference_count
                FROM jsonb_array_elements(item->'evidence_refs');
                IF distinct_reference_count <> reference_count THEN
                    RETURN FALSE;
                END IF;
            END LOOP;
            SELECT count(DISTINCT value->>'claim_id')
            INTO distinct_claim_count
            FROM jsonb_array_elements(candidate);
            IF distinct_claim_count <> claim_count THEN
                RETURN FALSE;
            END IF;
            IF overall_status = 'unavailable' THEN
                RETURN TRUE;
            END IF;
            SELECT CASE
                WHEN bool_or(value->>'status' = 'contradicted') THEN 'contradicted'
                WHEN bool_or(value->>'status' = 'insufficient_evidence')
                    THEN 'insufficient_evidence'
                ELSE 'supported'
            END
            INTO expected_status
            FROM jsonb_array_elements(candidate);
            RETURN expected_status = overall_status;
        EXCEPTION
            WHEN numeric_value_out_of_range OR invalid_text_representation THEN
                RETURN FALSE;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION semantic_verification_details_valid(candidate jsonb)
        RETURNS boolean
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        AS $$
        DECLARE
            attempted boolean;
            status text;
            failure_code text;
        BEGIN
            IF jsonb_typeof(candidate) <> 'object'
                OR NOT candidate ?& ARRAY[
                    'schema_version', 'decomposition_version', 'call_attempted',
                    'failure_code', 'status', 'summary', 'claims', 'lineage', 'accounting'
                ]
                OR candidate - ARRAY[
                    'schema_version', 'decomposition_version', 'call_attempted',
                    'failure_code', 'status', 'summary', 'claims', 'lineage', 'accounting'
                ] <> '{}'::jsonb
                OR candidate->>'schema_version' <> 'semantic-verification.v1'
                OR candidate->>'decomposition_version' <> 'deterministic-factual-claims.v1'
                OR jsonb_typeof(candidate->'call_attempted') <> 'boolean'
                OR jsonb_typeof(candidate->'status') <> 'string'
                OR candidate->>'status' NOT IN (
                    'supported', 'contradicted', 'insufficient_evidence', 'unavailable'
                )
                OR jsonb_typeof(candidate->'summary') <> 'string'
                OR char_length(candidate->>'summary') NOT BETWEEN 1 AND 1024
                OR candidate->>'summary' <> btrim(candidate->>'summary')
            THEN
                RETURN FALSE;
            END IF;
            attempted := (candidate->>'call_attempted')::boolean;
            status := candidate->>'status';
            IF NOT public.semantic_verification_claims_valid(candidate->'claims', status) THEN
                RETURN FALSE;
            END IF;
            IF attempted THEN
                IF NOT public.semantic_verification_lineage_valid(candidate->'lineage') THEN
                    RETURN FALSE;
                END IF;
                IF status = 'unavailable' THEN
                    IF jsonb_typeof(candidate->'failure_code') <> 'string'
                        OR candidate->>'failure_code' NOT IN (
                            'authentication', 'permission_denied', 'rate_limited', 'timeout',
                            'content_filtered', 'invalid_request', 'invalid_response',
                            'resource_limit', 'cost_limit', 'provider_unavailable',
                            'unavailable-or-invalid-result'
                        )
                        OR (
                            jsonb_typeof(candidate->'accounting') <> 'null'
                            AND NOT public.semantic_verification_accounting_valid(
                                candidate->'accounting'
                            )
                        )
                    THEN
                        RETURN FALSE;
                    END IF;
                ELSIF jsonb_typeof(candidate->'failure_code') <> 'null'
                    OR NOT public.semantic_verification_accounting_valid(candidate->'accounting')
                THEN
                    RETURN FALSE;
                END IF;
            ELSE
                IF status <> 'unavailable'
                    OR jsonb_typeof(candidate->'failure_code') <> 'string'
                    OR jsonb_typeof(candidate->'accounting') <> 'null'
                THEN
                    RETURN FALSE;
                END IF;
                failure_code := candidate->>'failure_code';
                IF failure_code = 'not_configured' THEN
                    IF jsonb_typeof(candidate->'lineage') <> 'null' THEN
                        RETURN FALSE;
                    END IF;
                ELSIF failure_code NOT IN (
                    'invalid_request', 'resource_limit', 'cost_limit',
                    'unavailable-or-invalid-result'
                ) OR NOT public.semantic_verification_lineage_valid(candidate->'lineage')
                THEN
                    RETURN FALSE;
                END IF;
            END IF;
            RETURN TRUE;
        END;
        $$
        """
    )


def _replace_evidence_validation_function(*, structured_details: bool) -> None:
    if structured_details:
        details_checks = """
                    OR item - ARRAY['location', 'expected', 'observed', 'details'] <> '{}'::jsonb
                    OR (
                        item ? 'details'
                        AND (
                            jsonb_typeof(item->'details') <> 'object'
                            OR octet_length(convert_to((item->'details')::text, 'UTF8')) > 65536
                        )
                    )
                    OR (
                        item->>'location' = '$.semantic_verification'
                        AND (
                            NOT item ? 'details'
                            OR NOT public.semantic_verification_details_valid(item->'details')
                        )
                    )
        """
        semantic_count_check = """
            IF (
                SELECT count(*)
                FROM jsonb_array_elements(candidate)
                WHERE value->>'location' = '$.semantic_verification'
            ) > 1
            THEN
                RETURN FALSE;
            END IF;
        """
    else:
        details_checks = """
                    OR item - ARRAY['location', 'expected', 'observed'] <> '{}'::jsonb
        """
        semantic_count_check = ""
    statement = """
        CREATE OR REPLACE FUNCTION validation_evidence_valid(
            candidate jsonb,
            expected_count integer
        )
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
                    __DETAILS_CHECKS__
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
            __SEMANTIC_COUNT_CHECK__
            SELECT count(*) INTO distinct_count
            FROM (SELECT DISTINCT value FROM jsonb_array_elements(candidate)) AS evidence_items;
            RETURN distinct_count = expected_count;
        END;
        $$
        """
    op.execute(
        statement.replace("__DETAILS_CHECKS__", details_checks).replace(
            "__SEMANTIC_COUNT_CHECK__", semantic_count_check
        )
    )


def upgrade() -> None:
    _create_semantic_validation_functions()
    _replace_evidence_validation_function(structured_details=True)
    op.execute(
        """
        CREATE UNIQUE INDEX uq_validation_findings_semantic_verification_per_run
        ON validation_findings (validation_run_id)
        WHERE evidence @> '[{"location": "$.semantic_verification"}]'::jsonb
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX uq_validation_findings_semantic_verification_per_run")
    _replace_evidence_validation_function(structured_details=False)
    op.execute("DROP FUNCTION semantic_verification_details_valid(jsonb)")
    op.execute("DROP FUNCTION semantic_verification_claims_valid(jsonb, text)")
    op.execute("DROP FUNCTION semantic_verification_accounting_valid(jsonb)")
    op.execute("DROP FUNCTION semantic_verification_lineage_valid(jsonb)")

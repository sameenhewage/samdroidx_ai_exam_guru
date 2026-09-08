from collections.abc import Sequence

from alembic import op

revision: str = "0039_source_fidelity_v2"
down_revision: str | None = "0038_upload_request_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(r"""
        CREATE FUNCTION public.source_candidate_is_confirmable(candidate_id uuid)
        RETURNS boolean LANGUAGE sql STABLE AS $$
            SELECT EXISTS (
                SELECT 1 FROM public.source_page_text_candidates c
                WHERE c.id = candidate_id AND c.can_confirm IS TRUE
                    AND c.normalized_text IS NOT NULL
                    AND length(btrim(c.normalized_text,
                        U&'\0009\000A\000B\000C\000D\001C\001D\001E\001F\0020'
                        || U&'\0085\00A0\1680\2000\2001\2002\2003\2004\2005\2006'
                        || U&'\2007\2008\2009\200A\2028\2029\202F\205F\3000'
                    )) > 0
                    AND NOT (c.provenance ? 'failure_code')
                    AND coalesce(c.diagnostics->>'algorithm_version', '')
                        LIKE 'source-fidelity-v2/%'
            );
        $$;
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION public.source_page_fidelity_is_current(
            source_id uuid, number integer, candidate uuid DEFAULT NULL
        ) RETURNS boolean LANGUAGE sql STABLE AS $$
            SELECT EXISTS (
                SELECT 1 FROM public.source_page_review_states s
                JOIN public.source_page_text_candidates c ON c.id = s.current_candidate_id
                WHERE s.document_id = source_id AND s.page_number = number AND s.state = 'verified'
                AND public.source_candidate_is_confirmable(c.id)
                AND (candidate IS NULL OR candidate = c.id)
            );
        $$;
    """)
    op.execute("""
        CREATE FUNCTION public.source_document_fidelity_is_current(source_id uuid)
        RETURNS boolean LANGUAGE sql STABLE AS $$
            SELECT EXISTS (
                SELECT 1 FROM public.source_documents d
                JOIN public.source_page_review_states s ON s.document_id = d.id
                    AND s.page_number BETWEEN 1 AND d.original_page_count
                LEFT JOIN public.source_page_review_events e ON e.id = s.event_id
                WHERE d.id = source_id AND d.original_page_count > 0
                GROUP BY d.id, d.original_page_count
                HAVING count(*) FILTER (WHERE
                    public.source_page_fidelity_is_current(d.id, s.page_number)
                    OR (s.state = 'excluded' AND e.action = 'excluded' AND e.state = s.state
                        AND e.document_id = s.document_id AND e.page_number = s.page_number
                        AND e.version = s.version
                        AND e.candidate_id IS NOT DISTINCT FROM s.current_candidate_id)
                ) = d.original_page_count
                AND bool_or(public.source_page_fidelity_is_current(d.id, s.page_number))
            );
        $$;
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION public.knowledge_source_lineage_is_current(
            source_id uuid, number integer, candidate_id uuid, curriculum_id uuid, source_text text
        ) RETURNS boolean LANGUAGE sql STABLE AS $$
            SELECT candidate_id IS NOT NULL AND EXISTS (
                SELECT 1 FROM public.source_documents d
                JOIN public.source_page_text_candidates c ON c.document_id = d.id
                    AND c.page_number = number AND c.id = candidate_id
                WHERE d.id = source_id AND d.curriculum_version_id = curriculum_id
                    AND d.active_for_ai AND NOT d.metadata_review_required
                    AND public.catalogue_curriculum_is_admitted(curriculum_id)
                    AND public.source_document_fidelity_is_current(source_id)
                    AND public.source_page_fidelity_is_current(source_id, number, candidate_id)
                    AND c.normalized_text IS NOT NULL AND c.can_confirm
                    AND c.text_sha256 = encode(sha256(convert_to(c.normalized_text, 'UTF8')), 'hex')
                    AND source_text = normalize(source_text, NFC)
                    AND length(btrim(source_text)) > 0
                    AND strpos(c.normalized_text, source_text) > 0
            )
        $$
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION public.lock_knowledge_source_lineage(
            source_id uuid, number integer, curriculum_id uuid
        ) RETURNS void LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM 1 FROM public.source_documents WHERE id = source_id FOR SHARE;
            PERFORM 1 FROM public.source_page_review_states
                WHERE document_id = source_id ORDER BY page_number FOR SHARE;
            PERFORM 1 FROM public.curriculum_versions cv
                JOIN public.exam_configurations e ON e.id = cv.exam_configuration_id
                JOIN public.media m ON m.id = cv.medium_id
                JOIN public.subjects s ON s.id = cv.subject_id
                WHERE cv.id = curriculum_id FOR SHARE OF cv, e, m, s;
            PERFORM 1 FROM public.catalogue_admission_current
                WHERE curriculum_version_id = curriculum_id FOR SHARE;
        END;
        $$
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION public.validate_page_review_evidence()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            event public.source_page_review_events%ROWTYPE;
            candidate public.source_page_text_candidates%ROWTYPE;
        BEGIN
            IF NEW.version = 0 THEN RETURN NEW; END IF;
            SELECT * INTO event FROM public.source_page_review_events WHERE id = NEW.event_id;
            IF NOT FOUND OR event.document_id <> NEW.document_id
                OR event.page_number <> NEW.page_number
                OR event.version <> NEW.version OR event.state <> NEW.state
                OR event.candidate_id IS DISTINCT FROM NEW.current_candidate_id THEN
                RAISE EXCEPTION 'page review requires matching immutable evidence'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.state = 'verified' THEN
                SELECT * INTO candidate FROM public.source_page_text_candidates
                    WHERE id = NEW.current_candidate_id;
                IF NOT FOUND OR NOT public.source_candidate_is_confirmable(candidate.id)
                    OR event.action NOT IN ('confirmed','reference_verified')
                    OR event.payload->>'text_sha256' IS DISTINCT FROM candidate.text_sha256
                    OR event.payload->>'compared_with_original' IS DISTINCT FROM 'true' THEN
                    RAISE EXCEPTION 'verified page requires exact candidate confirmation'
                        USING ERRCODE = '23514';
                END IF;
                IF event.action = 'reference_verified' AND NOT EXISTS (
                    SELECT 1 FROM public.source_page_ground_truth g
                    JOIN public.source_documents d ON d.id = g.document_id
                    WHERE g.id::text = event.payload->>'ground_truth_id'
                    AND g.document_id = NEW.document_id AND g.page_number = NEW.page_number
                    AND g.source_checksum_sha256 = d.checksum_sha256
                    AND g.text_sha256 = candidate.text_sha256
                ) THEN
                    RAISE EXCEPTION 'automatic verification requires exact adjudicated reference'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NEW;
        END; $$;
    """)


def downgrade() -> None:
    op.execute("""
        LOCK TABLE public.source_page_text_candidates, public.source_page_review_events,
            public.source_page_review_states, public.source_page_ground_truth
            IN SHARE ROW EXCLUSIVE MODE;
    """)
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM public.source_page_text_candidates)
                OR EXISTS (SELECT 1 FROM public.source_page_review_events)
                OR EXISTS (SELECT 1 FROM public.source_page_ground_truth) THEN
                RAISE EXCEPTION 'cannot discard source fidelity v2 protections'
                    USING ERRCODE = '23514';
            END IF;
        END; $$;
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION public.knowledge_source_lineage_is_current(
            source_id uuid, number integer, candidate_id uuid, curriculum_id uuid, source_text text
        ) RETURNS boolean LANGUAGE sql STABLE AS $$
            SELECT candidate_id IS NOT NULL AND EXISTS (
                SELECT 1 FROM public.source_documents d
                JOIN public.source_page_text_candidates c ON c.document_id = d.id
                    AND c.page_number = number AND c.id = candidate_id
                WHERE d.id = source_id AND d.curriculum_version_id = curriculum_id
                    AND d.active_for_ai AND NOT d.metadata_review_required
                    AND public.catalogue_curriculum_is_admitted(curriculum_id)
                    AND public.source_page_fidelity_is_current(source_id, number, candidate_id)
                    AND c.normalized_text IS NOT NULL AND c.can_confirm
                    AND c.text_sha256 = encode(sha256(convert_to(c.normalized_text, 'UTF8')), 'hex')
                    AND source_text = normalize(source_text, NFC)
                    AND length(btrim(source_text)) > 0
                    AND strpos(c.normalized_text, source_text) > 0
            )
        $$
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION public.lock_knowledge_source_lineage(
            source_id uuid, number integer, curriculum_id uuid
        ) RETURNS void LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM 1 FROM public.source_documents WHERE id = source_id FOR SHARE;
            PERFORM 1 FROM public.source_page_review_states
                WHERE document_id = source_id AND page_number = number FOR SHARE;
            PERFORM 1 FROM public.curriculum_versions cv
                JOIN public.exam_configurations e ON e.id = cv.exam_configuration_id
                JOIN public.media m ON m.id = cv.medium_id
                JOIN public.subjects s ON s.id = cv.subject_id
                WHERE cv.id = curriculum_id FOR SHARE OF cv, e, m, s;
            PERFORM 1 FROM public.catalogue_admission_current
                WHERE curriculum_version_id = curriculum_id FOR SHARE;
        END;
        $$
    """)
    op.execute("DROP FUNCTION public.source_document_fidelity_is_current(uuid)")
    op.execute("""
        CREATE OR REPLACE FUNCTION public.validate_page_review_evidence()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            event public.source_page_review_events%ROWTYPE;
            candidate public.source_page_text_candidates%ROWTYPE;
        BEGIN
            IF NEW.version = 0 THEN RETURN NEW; END IF;
            SELECT * INTO event FROM public.source_page_review_events WHERE id = NEW.event_id;
            IF NOT FOUND OR event.document_id <> NEW.document_id
                OR event.page_number <> NEW.page_number
                OR event.version <> NEW.version OR event.state <> NEW.state
                OR event.candidate_id IS DISTINCT FROM NEW.current_candidate_id THEN
                RAISE EXCEPTION 'page review requires matching immutable evidence'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.state = 'verified' THEN
                SELECT * INTO candidate FROM public.source_page_text_candidates
                    WHERE id = NEW.current_candidate_id;
                IF NOT FOUND OR NOT candidate.can_confirm
                    OR event.action NOT IN ('confirmed','reference_verified')
                    OR event.payload->>'text_sha256' IS DISTINCT FROM candidate.text_sha256
                    OR event.payload->>'compared_with_original' IS DISTINCT FROM 'true' THEN
                    RAISE EXCEPTION 'verified page requires exact candidate confirmation'
                        USING ERRCODE = '23514';
                END IF;
                IF event.action = 'reference_verified' AND NOT EXISTS (
                    SELECT 1 FROM public.source_page_ground_truth g
                    JOIN public.source_documents d ON d.id = g.document_id
                    WHERE g.id::text = event.payload->>'ground_truth_id'
                    AND g.document_id = NEW.document_id AND g.page_number = NEW.page_number
                    AND g.source_checksum_sha256 = d.checksum_sha256
                    AND g.text_sha256 = candidate.text_sha256
                ) THEN
                    RAISE EXCEPTION 'automatic verification requires exact adjudicated reference'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION public.source_page_fidelity_is_current(
            source_id uuid, number integer, candidate uuid DEFAULT NULL
        ) RETURNS boolean LANGUAGE sql STABLE AS $$
            SELECT EXISTS (
                SELECT 1 FROM public.source_page_review_states s
                JOIN public.source_page_text_candidates c ON c.id = s.current_candidate_id
                WHERE s.document_id = source_id AND s.page_number = number AND s.state = 'verified'
                AND c.can_confirm AND (candidate IS NULL OR candidate = c.id)
            );
        $$;
    """)
    op.execute("DROP FUNCTION public.source_candidate_is_confirmable(uuid)")

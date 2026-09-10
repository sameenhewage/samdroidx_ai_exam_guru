from collections.abc import Sequence

from alembic import op

revision: str = "0040_source_fidelity_rules_v2"
down_revision: str | None = "0039_source_fidelity_v2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _candidate_policy(pattern: str) -> None:
    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION public.source_candidate_is_confirmable(candidate_id uuid)
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
                        LIKE '__POLICY_PATTERN__'
            );
        $$;
        """.replace("__POLICY_PATTERN__", pattern)
    )


def upgrade() -> None:
    _candidate_policy("source-fidelity-v2/rules-2/%")


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
                RAISE EXCEPTION 'cannot discard source fidelity v2 protections (rules-2)'
                    USING ERRCODE = '23514';
            END IF;
        END; $$;
    """)
    _candidate_policy("source-fidelity-v2/%")

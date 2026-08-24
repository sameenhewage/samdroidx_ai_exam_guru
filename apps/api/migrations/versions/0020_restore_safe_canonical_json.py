from collections.abc import Sequence

from alembic import op

revision: str = "0020_restore_safe_json"
down_revision: str | None = "0019_extraction_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _replace_canonical_json_function(*, schema_qualified_recursion: bool) -> None:
    recursive_function = (
        "public.paper_canonical_jsonb" if schema_qualified_recursion else "paper_canonical_jsonb"
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.paper_canonical_jsonb(document jsonb)
        RETURNS text
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        AS $$
        DECLARE
            value_type text;
            canonical text;
        BEGIN
            value_type := jsonb_typeof(document);
            IF value_type = 'object' THEN
                SELECT '{{' || COALESCE(
                    string_agg(
                        to_jsonb(entry.key)::text || ':' ||
                            {recursive_function}(entry.value),
                        ',' ORDER BY entry.key COLLATE "C"
                    ),
                    ''
                ) || '}}'
                INTO canonical
                FROM jsonb_each(document) AS entry(key, value);
                RETURN canonical;
            ELSIF value_type = 'array' THEN
                SELECT '[' || COALESCE(
                    string_agg(
                        {recursive_function}(entry.value),
                        ',' ORDER BY entry.ordinal
                    ),
                    ''
                ) || ']'
                INTO canonical
                FROM jsonb_array_elements(document)
                    WITH ORDINALITY AS entry(value, ordinal);
                RETURN canonical;
            ELSIF value_type = 'string' THEN
                RETURN to_jsonb(document #>> '{{}}')::text;
            END IF;
            RETURN document::text;
        END;
        $$
        """  # noqa: S608 - the selected function name is migration-owned, not external input
    )


def upgrade() -> None:
    # pg_dump intentionally restores with an empty search_path. Schema-qualifying
    # this recursive call lets publication CHECK constraints validate data during
    # COPY without changing canonical bytes or weakening the invariant.
    _replace_canonical_json_function(schema_qualified_recursion=True)


def downgrade() -> None:
    _replace_canonical_json_function(schema_qualified_recursion=False)

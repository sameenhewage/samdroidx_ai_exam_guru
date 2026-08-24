from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0016_published_papers"
down_revision: str | None = "0015_review_candidates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MAX_PAPER_VERSIONS = 32
_MAX_PAPER_SLOTS = 200
_MAX_TITLE_CHARACTERS = 512
_MAX_ARCHIVE_REASON_CHARACTERS = 1_024
_MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
_MAX_CANDIDATE_VERSION = 35
_MAX_CANDIDATE_REVISIONS = 32
_FINGERPRINT_SQL = "^sha256:[0-9a-f]{64}$"
_HASH_SQL = "^[0-9a-f]{64}$"


def upgrade() -> None:
    _create_canonical_json_function()
    op.create_unique_constraint(
        "uq_question_candidates_id_curriculum",
        "question_candidates",
        ["id", "curriculum_version_id"],
    )
    _create_tables()
    _create_expected_snapshot_function()
    _create_mutation_triggers()
    _create_completeness_triggers()


def _create_canonical_json_function() -> None:
    op.execute(
        """
        CREATE FUNCTION paper_canonical_jsonb(document jsonb)
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
                SELECT '{' || COALESCE(
                    string_agg(
                        to_jsonb(entry.key)::text || ':' || paper_canonical_jsonb(entry.value),
                        ',' ORDER BY entry.key COLLATE "C"
                    ),
                    ''
                ) || '}'
                INTO canonical
                FROM jsonb_each(document) AS entry(key, value);
                RETURN canonical;
            ELSIF value_type = 'array' THEN
                SELECT '[' || COALESCE(
                    string_agg(
                        paper_canonical_jsonb(entry.value),
                        ',' ORDER BY entry.ordinal
                    ),
                    ''
                ) || ']'
                INTO canonical
                FROM jsonb_array_elements(document)
                    WITH ORDINALITY AS entry(value, ordinal);
                RETURN canonical;
            ELSIF value_type = 'string' THEN
                RETURN to_jsonb(document #>> '{}')::text;
            END IF;
            RETURN document::text;
        END;
        $$
        """
    )


def _create_tables() -> None:
    op.create_table(
        "practice_papers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("curriculum_version_id", sa.Uuid(), nullable=False),
        sa.Column("paper_blueprint_id", sa.Uuid(), nullable=False),
        sa.Column("blueprint_id", sa.String(128), nullable=False),
        sa.Column("blueprint_version", sa.String(128), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(71), nullable=False),
        sa.Column("create_request_fingerprint", sa.String(71), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["curriculum_version_id"],
            ["curriculum_versions.id"],
            name="fk_practice_papers_curriculum_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["paper_blueprint_id", "curriculum_version_id"],
            ["paper_blueprints.id", "paper_blueprints.curriculum_version_id"],
            name="fk_practice_papers_blueprint_curriculum",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "id",
            "curriculum_version_id",
            name="uq_practice_papers_id_curriculum",
        ),
        sa.UniqueConstraint(
            "idempotency_key_hash",
            name="uq_practice_papers_idempotency_key_hash",
        ),
        sa.CheckConstraint(
            f"state IN ('draft', 'published', 'archived') AND current_version BETWEEN 1 AND "
            f"{_MAX_PAPER_VERSIONS}",
            name="ck_practice_papers_state_version",
        ),
        sa.CheckConstraint(
            "blueprint_id = btrim(blueprint_id) AND length(blueprint_id) BETWEEN 1 AND 128 "
            "AND blueprint_version = btrim(blueprint_version) "
            "AND length(blueprint_version) BETWEEN 1 AND 128",
            name="ck_practice_papers_blueprint_identity",
        ),
        sa.CheckConstraint(
            f"idempotency_key_hash ~ '{_FINGERPRINT_SQL}' "
            f"AND create_request_fingerprint ~ '{_FINGERPRINT_SQL}'",
            name="ck_practice_papers_fingerprints",
        ),
    )
    op.create_index(
        "ix_practice_papers_curriculum_state_updated",
        "practice_papers",
        ["curriculum_version_id", "state", "updated_at", "id"],
    )
    op.create_index(
        "ix_practice_papers_curriculum_blueprint_updated",
        "practice_papers",
        ["curriculum_version_id", "paper_blueprint_id", "updated_at", "id"],
    )

    op.create_table(
        "paper_draft_versions",
        sa.Column("paper_id", sa.Uuid(), nullable=False),
        sa.Column("curriculum_version_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(_MAX_TITLE_CHARACTERS), nullable=False),
        sa.Column("supersedes_content_hash", sa.String(64), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("paper_id", "version", name="pk_paper_draft_versions"),
        sa.UniqueConstraint(
            "paper_id",
            "curriculum_version_id",
            "version",
            name="uq_paper_draft_versions_scope",
        ),
        sa.ForeignKeyConstraint(
            ["paper_id", "curriculum_version_id"],
            ["practice_papers.id", "practice_papers.curriculum_version_id"],
            name="fk_paper_draft_versions_paper_curriculum",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            f"version BETWEEN 1 AND {_MAX_PAPER_VERSIONS}",
            name="ck_paper_draft_versions_version",
        ),
        sa.CheckConstraint(
            f"title = btrim(title) AND char_length(title) BETWEEN 1 AND {_MAX_TITLE_CHARACTERS}",
            name="ck_paper_draft_versions_title",
        ),
        sa.CheckConstraint(
            f"supersedes_content_hash IS NULL OR supersedes_content_hash ~ '{_HASH_SQL}'",
            name="ck_paper_draft_versions_supersedes_hash",
        ),
    )
    op.create_index(
        "ix_paper_draft_versions_paper_version",
        "paper_draft_versions",
        ["paper_id", "version"],
    )

    op.create_table(
        "paper_draft_candidates",
        sa.Column("paper_id", sa.Uuid(), nullable=False),
        sa.Column("curriculum_version_id", sa.Uuid(), nullable=False),
        sa.Column("paper_version", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("blueprint_slot_id", sa.String(128), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_version", sa.Integer(), nullable=False),
        sa.Column("candidate_revision", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint(
            "paper_id",
            "paper_version",
            "ordinal",
            name="pk_paper_draft_candidates",
        ),
        sa.UniqueConstraint(
            "paper_id",
            "paper_version",
            "blueprint_slot_id",
            name="uq_paper_draft_candidates_slot",
        ),
        sa.UniqueConstraint(
            "paper_id",
            "paper_version",
            "candidate_id",
            name="uq_paper_draft_candidates_candidate",
        ),
        sa.ForeignKeyConstraint(
            ["paper_id", "curriculum_version_id", "paper_version"],
            [
                "paper_draft_versions.paper_id",
                "paper_draft_versions.curriculum_version_id",
                "paper_draft_versions.version",
            ],
            name="fk_paper_draft_candidates_draft_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id", "curriculum_version_id"],
            ["question_candidates.id", "question_candidates.curriculum_version_id"],
            name="fk_paper_draft_candidates_candidate_curriculum",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            f"paper_version BETWEEN 1 AND {_MAX_PAPER_VERSIONS} "
            f"AND ordinal BETWEEN 1 AND {_MAX_PAPER_SLOTS}",
            name="ck_paper_draft_candidates_paper_bounds",
        ),
        sa.CheckConstraint(
            f"candidate_version BETWEEN 1 AND {_MAX_CANDIDATE_VERSION} "
            f"AND candidate_revision BETWEEN 1 AND {_MAX_CANDIDATE_REVISIONS}",
            name="ck_paper_draft_candidates_candidate_bounds",
        ),
        sa.CheckConstraint(
            "blueprint_slot_id = btrim(blueprint_slot_id) "
            "AND length(blueprint_slot_id) BETWEEN 1 AND 128",
            name="ck_paper_draft_candidates_slot_id",
        ),
    )
    op.create_index(
        "ix_paper_draft_candidates_candidate",
        "paper_draft_candidates",
        ["candidate_id", "paper_id", "paper_version"],
    )

    op.create_table(
        "published_paper_versions",
        sa.Column("paper_id", sa.Uuid(), nullable=False),
        sa.Column("curriculum_version_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("previous_version", sa.Integer(), nullable=True),
        sa.Column("supersedes_content_hash", sa.String(64), nullable=True),
        sa.Column("snapshot", JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("published_by", sa.Uuid(), nullable=False),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("paper_id", "version", name="pk_published_paper_versions"),
        sa.UniqueConstraint(
            "paper_id",
            "curriculum_version_id",
            "version",
            name="uq_published_paper_versions_scope",
        ),
        sa.ForeignKeyConstraint(
            ["paper_id", "curriculum_version_id", "version"],
            [
                "paper_draft_versions.paper_id",
                "paper_draft_versions.curriculum_version_id",
                "paper_draft_versions.version",
            ],
            name="fk_published_paper_versions_draft_scope",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            f"version BETWEEN 1 AND {_MAX_PAPER_VERSIONS}",
            name="ck_published_paper_versions_version",
        ),
        sa.CheckConstraint(
            "(version = 1 AND previous_version IS NULL AND supersedes_content_hash IS NULL) "
            "OR (version > 1 AND previous_version = version - 1 "
            "AND supersedes_content_hash IS NOT NULL)",
            name="ck_published_paper_versions_chain",
        ),
        sa.CheckConstraint(
            f"content_hash ~ '{_HASH_SQL}' AND (supersedes_content_hash IS NULL "
            f"OR supersedes_content_hash ~ '{_HASH_SQL}')",
            name="ck_published_paper_versions_hashes",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(snapshot) = 'object' AND "
            f"pg_column_size(snapshot) <= {_MAX_SNAPSHOT_BYTES} AND "
            f"octet_length(paper_canonical_jsonb(snapshot)) <= {_MAX_SNAPSHOT_BYTES}",
            name="ck_published_paper_versions_snapshot_bound",
        ),
    )
    op.create_index(
        "ix_published_paper_versions_paper_version",
        "published_paper_versions",
        ["paper_id", "version"],
    )
    op.create_index(
        "ix_published_paper_versions_curriculum_published",
        "published_paper_versions",
        ["curriculum_version_id", "published_at", "paper_id", "version"],
    )

    op.create_table(
        "paper_archive_events",
        sa.Column("paper_id", sa.Uuid(), primary_key=True),
        sa.Column("curriculum_version_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(_MAX_ARCHIVE_REASON_CHARACTERS), nullable=False),
        sa.Column("archived_by", sa.Uuid(), nullable=False),
        sa.Column(
            "archived_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "paper_id",
            "curriculum_version_id",
            "version",
            name="uq_paper_archive_events_scope",
        ),
        sa.ForeignKeyConstraint(
            ["paper_id", "curriculum_version_id", "version"],
            [
                "published_paper_versions.paper_id",
                "published_paper_versions.curriculum_version_id",
                "published_paper_versions.version",
            ],
            name="fk_paper_archive_events_publication_scope",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            f"version BETWEEN 1 AND {_MAX_PAPER_VERSIONS}",
            name="ck_paper_archive_events_version",
        ),
        sa.CheckConstraint(
            "reason = btrim(reason) AND "
            f"char_length(reason) BETWEEN 1 AND {_MAX_ARCHIVE_REASON_CHARACTERS}",
            name="ck_paper_archive_events_reason",
        ),
    )
    op.create_index(
        "ix_paper_archive_events_curriculum_archived",
        "paper_archive_events",
        ["curriculum_version_id", "archived_at", "paper_id"],
    )


def _create_expected_snapshot_function() -> None:
    op.execute(
        """
        CREATE FUNCTION paper_expected_publication_snapshot(
            expected_paper_id uuid,
            expected_curriculum_id uuid,
            expected_version integer
        )
        RETURNS jsonb
        LANGUAGE plpgsql
        STABLE
        STRICT
        AS $$
        DECLARE
            aggregate_row practice_papers%ROWTYPE;
            draft_row paper_draft_versions%ROWTYPE;
            blueprint_row paper_blueprints%ROWTYPE;
            ordered_slots jsonb;
            ordered_questions jsonb;
        BEGIN
            SELECT * INTO aggregate_row
            FROM practice_papers
            WHERE id = expected_paper_id
                AND curriculum_version_id = expected_curriculum_id;
            IF NOT FOUND THEN
                RETURN NULL;
            END IF;
            SELECT * INTO draft_row
            FROM paper_draft_versions
            WHERE paper_id = expected_paper_id
                AND curriculum_version_id = expected_curriculum_id
                AND version = expected_version;
            IF NOT FOUND THEN
                RETURN NULL;
            END IF;
            SELECT * INTO blueprint_row
            FROM paper_blueprints
            WHERE id = aggregate_row.paper_blueprint_id
                AND curriculum_version_id = expected_curriculum_id;
            IF NOT FOUND THEN
                RETURN NULL;
            END IF;

            SELECT jsonb_agg(to_jsonb(slot->>'slot_id') ORDER BY ordinal)
            INTO ordered_slots
            FROM jsonb_array_elements(blueprint_row.blueprint->'slots')
                WITH ORDINALITY AS blueprint_slot(slot, ordinal);

            SELECT jsonb_agg(
                jsonb_build_object(
                    'candidate_id', candidate.id::text,
                    'candidate_version', candidate.version,
                    'content', current_revision.content,
                    'content_revision', item.candidate_revision,
                    'decision', jsonb_build_object(
                        'candidate_version', final_event.candidate_version,
                        'reason', final_event.reason,
                        'reviewer_id', final_event.reviewer_id::text,
                        'state', candidate.state
                    ),
                    'lineage', candidate.generation_lineage - 'paper_blueprint_id',
                    'review_history', history.events,
                    'revisions', revisions.items,
                    'slot_id', item.blueprint_slot_id,
                    'validation', candidate.validation_evidence
                ) ORDER BY item.ordinal
            ) INTO ordered_questions
            FROM paper_draft_candidates AS item
            JOIN question_candidates AS candidate
                ON candidate.id = item.candidate_id
                AND candidate.curriculum_version_id = item.curriculum_version_id
            JOIN question_candidate_revisions AS current_revision
                ON current_revision.candidate_id = candidate.id
                AND current_revision.revision = item.candidate_revision
            CROSS JOIN LATERAL (
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'action', event.action,
                        'candidate_version', event.candidate_version,
                        'reason', event.reason,
                        'reviewer_id', event.reviewer_id::text
                    ) ORDER BY event.candidate_version
                ) AS events
                FROM candidate_review_events AS event
                WHERE event.candidate_id = candidate.id
            ) AS history
            CROSS JOIN LATERAL (
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'content', revision.content,
                        'reason', revision.reason,
                        'reviewer_id', CASE
                            WHEN revision.reviewer_id IS NULL THEN NULL
                            ELSE to_jsonb(revision.reviewer_id::text)
                        END,
                        'revision', revision.revision
                    ) ORDER BY revision.revision
                ) AS items
                FROM question_candidate_revisions AS revision
                WHERE revision.candidate_id = candidate.id
            ) AS revisions
            CROSS JOIN LATERAL (
                SELECT event.candidate_version, event.reason, event.reviewer_id
                FROM candidate_review_events AS event
                WHERE event.candidate_id = candidate.id
                ORDER BY event.candidate_version DESC
                LIMIT 1
            ) AS final_event
            WHERE item.paper_id = expected_paper_id
                AND item.curriculum_version_id = expected_curriculum_id
                AND item.paper_version = expected_version;

            IF ordered_slots IS NULL OR ordered_questions IS NULL THEN
                RETURN NULL;
            END IF;
            RETURN jsonb_build_object(
                'blueprint', jsonb_build_object(
                    'blueprint_id', aggregate_row.blueprint_id,
                    'blueprint_version', aggregate_row.blueprint_version,
                    'paper_blueprint_id', aggregate_row.paper_blueprint_id::text,
                    'slot_ids', ordered_slots
                ),
                'paper_id', aggregate_row.id::text,
                'paper_version', expected_version,
                'questions', ordered_questions,
                'schema', 'published-paper.v1',
                'title', draft_row.title
            );
        END;
        $$
        """
    )


def _create_mutation_triggers() -> None:
    op.execute(
        """
        CREATE FUNCTION enforce_practice_paper_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            blueprint_row paper_blueprints%ROWTYPE;
        BEGIN
            IF NEW.state <> 'draft' OR NEW.current_version <> 1 THEN
                RAISE EXCEPTION 'practice papers must begin as draft version 1'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.created_by IS DISTINCT FROM NEW.updated_by THEN
                RAISE EXCEPTION 'initial paper actor metadata is inconsistent'
                    USING ERRCODE = '23514';
            END IF;
            SELECT * INTO blueprint_row
            FROM paper_blueprints
            WHERE id = NEW.paper_blueprint_id
                AND curriculum_version_id = NEW.curriculum_version_id;
            IF NOT FOUND
                OR blueprint_row.blueprint_id IS DISTINCT FROM NEW.blueprint_id
                OR blueprint_row.blueprint->'version'->>'blueprint_id'
                    IS DISTINCT FROM NEW.blueprint_version
                OR blueprint_row.slot_count NOT BETWEEN 1 AND 200
            THEN
                RAISE EXCEPTION 'practice paper blueprint identity is inconsistent'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_practice_paper_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.id IS DISTINCT FROM OLD.id
                OR NEW.curriculum_version_id IS DISTINCT FROM OLD.curriculum_version_id
                OR NEW.paper_blueprint_id IS DISTINCT FROM OLD.paper_blueprint_id
                OR NEW.blueprint_id IS DISTINCT FROM OLD.blueprint_id
                OR NEW.blueprint_version IS DISTINCT FROM OLD.blueprint_version
                OR NEW.idempotency_key_hash IS DISTINCT FROM OLD.idempotency_key_hash
                OR NEW.create_request_fingerprint IS DISTINCT FROM OLD.create_request_fingerprint
                OR NEW.created_by IS DISTINCT FROM OLD.created_by
                OR NEW.created_at IS DISTINCT FROM OLD.created_at
            THEN
                RAISE EXCEPTION 'practice paper identity and creation metadata are immutable'
                    USING ERRCODE = '23514';
            END IF;
            IF OLD.state = 'archived' THEN
                RAISE EXCEPTION 'archived practice papers are terminal and immutable'
                    USING ERRCODE = '23514';
            END IF;
            IF NOT (
                OLD.state = 'draft' AND NEW.state = 'published'
                    AND NEW.current_version = OLD.current_version
                OR OLD.state = 'published' AND NEW.state = 'draft'
                    AND NEW.current_version = OLD.current_version + 1
                OR OLD.state = 'published' AND NEW.state = 'archived'
                    AND NEW.current_version = OLD.current_version
            ) THEN
                RAISE EXCEPTION 'invalid practice paper aggregate transition'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.updated_by IS NULL OR NEW.updated_at < OLD.updated_at THEN
                RAISE EXCEPTION 'practice paper CAS metadata is invalid'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_practice_paper_delete()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'practice papers are durable and cannot be deleted'
                USING ERRCODE = '23514';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_practice_paper_insert_trigger
        BEFORE INSERT ON practice_papers
        FOR EACH ROW EXECUTE FUNCTION enforce_practice_paper_insert()
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_practice_paper_update_trigger
        BEFORE UPDATE ON practice_papers
        FOR EACH ROW EXECUTE FUNCTION enforce_practice_paper_update()
        """
    )
    op.execute(
        """
        CREATE TRIGGER reject_practice_paper_delete_trigger
        BEFORE DELETE ON practice_papers
        FOR EACH ROW EXECUTE FUNCTION reject_practice_paper_delete()
        """
    )

    op.execute(
        """
        CREATE FUNCTION enforce_paper_draft_version_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            aggregate_row practice_papers%ROWTYPE;
            prior_hash text;
            existing_count integer;
        BEGIN
            SELECT * INTO aggregate_row
            FROM practice_papers
            WHERE id = NEW.paper_id
                AND curriculum_version_id = NEW.curriculum_version_id
            FOR UPDATE;
            IF NOT FOUND OR aggregate_row.state <> 'draft'
                OR aggregate_row.current_version <> NEW.version
            THEN
                RAISE EXCEPTION 'paper draft version must match the current draft aggregate'
                    USING ERRCODE = '23514';
            END IF;
            SELECT count(*) INTO existing_count
            FROM paper_draft_versions WHERE paper_id = NEW.paper_id;
            IF NEW.version <> existing_count + 1 THEN
                RAISE EXCEPTION 'paper draft versions must be contiguous'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.version = 1 THEN
                IF NEW.supersedes_content_hash IS NOT NULL THEN
                    RAISE EXCEPTION 'initial paper draft cannot supersede a publication'
                        USING ERRCODE = '23514';
                END IF;
            ELSE
                SELECT content_hash INTO prior_hash
                FROM published_paper_versions
                WHERE paper_id = NEW.paper_id AND version = NEW.version - 1;
                IF prior_hash IS NULL
                    OR NEW.supersedes_content_hash IS DISTINCT FROM prior_hash
                THEN
                    RAISE EXCEPTION 'revised draft must preserve the prior publication hash'
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
        CREATE FUNCTION enforce_paper_draft_candidate_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            aggregate_row practice_papers%ROWTYPE;
            candidate_row question_candidates%ROWTYPE;
            expected_slot text;
        BEGIN
            SELECT * INTO aggregate_row
            FROM practice_papers
            WHERE id = NEW.paper_id
                AND curriculum_version_id = NEW.curriculum_version_id
            FOR UPDATE;
            IF NOT FOUND OR aggregate_row.state <> 'draft'
                OR aggregate_row.current_version <> NEW.paper_version
            THEN
                RAISE EXCEPTION 'paper draft candidate must belong to the current draft'
                    USING ERRCODE = '23514';
            END IF;
            SELECT * INTO candidate_row
            FROM question_candidates
            WHERE id = NEW.candidate_id
                AND curriculum_version_id = NEW.curriculum_version_id;
            IF NOT FOUND
                OR candidate_row.state <> 'approved'
                OR candidate_row.version <> NEW.candidate_version
                OR candidate_row.current_revision <> NEW.candidate_revision
                OR candidate_row.validation_evidence->'passed' IS DISTINCT FROM 'true'::jsonb
                OR candidate_row.paper_blueprint_id IS DISTINCT FROM
                    aggregate_row.paper_blueprint_id
                OR candidate_row.blueprint_id IS DISTINCT FROM aggregate_row.blueprint_id
                OR candidate_row.blueprint_version IS DISTINCT FROM aggregate_row.blueprint_version
                OR candidate_row.blueprint_slot_id IS DISTINCT FROM NEW.blueprint_slot_id
            THEN
                RAISE EXCEPTION
                    'paper draft candidate must be exact, current, approved, and validated'
                    USING ERRCODE = '23514';
            END IF;
            SELECT slot->>'slot_id' INTO expected_slot
            FROM paper_blueprints AS blueprint
            CROSS JOIN LATERAL jsonb_array_elements(blueprint.blueprint->'slots')
                WITH ORDINALITY AS blueprint_slot(slot, ordinal)
            WHERE blueprint.id = aggregate_row.paper_blueprint_id
                AND blueprint.curriculum_version_id = NEW.curriculum_version_id
                AND blueprint_slot.ordinal = NEW.ordinal;
            IF expected_slot IS NULL OR expected_slot IS DISTINCT FROM NEW.blueprint_slot_id THEN
                RAISE EXCEPTION 'paper draft candidate does not match blueprint slot order'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_published_paper_version_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            aggregate_row practice_papers%ROWTYPE;
            draft_row paper_draft_versions%ROWTYPE;
            prior_hash text;
            expected_snapshot jsonb;
            expected_hash text;
            existing_count integer;
        BEGIN
            SELECT * INTO aggregate_row
            FROM practice_papers
            WHERE id = NEW.paper_id
                AND curriculum_version_id = NEW.curriculum_version_id
            FOR UPDATE;
            IF NOT FOUND OR aggregate_row.state <> 'draft'
                OR aggregate_row.current_version <> NEW.version
            THEN
                RAISE EXCEPTION 'publication must belong to the current draft aggregate'
                    USING ERRCODE = '23514';
            END IF;
            SELECT * INTO draft_row
            FROM paper_draft_versions
            WHERE paper_id = NEW.paper_id
                AND curriculum_version_id = NEW.curriculum_version_id
                AND version = NEW.version;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'publication requires its exact immutable draft'
                    USING ERRCODE = '23514';
            END IF;
            SELECT count(*) INTO existing_count
            FROM published_paper_versions WHERE paper_id = NEW.paper_id;
            IF NEW.version <> existing_count + 1 THEN
                RAISE EXCEPTION 'publication versions must be contiguous'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.version = 1 THEN
                IF NEW.previous_version IS NOT NULL OR NEW.supersedes_content_hash IS NOT NULL
                    OR draft_row.supersedes_content_hash IS NOT NULL
                THEN
                    RAISE EXCEPTION 'initial publication chain metadata is invalid'
                        USING ERRCODE = '23514';
                END IF;
            ELSE
                SELECT content_hash INTO prior_hash
                FROM published_paper_versions
                WHERE paper_id = NEW.paper_id AND version = NEW.version - 1;
                IF prior_hash IS NULL
                    OR NEW.previous_version IS DISTINCT FROM NEW.version - 1
                    OR NEW.supersedes_content_hash IS DISTINCT FROM prior_hash
                    OR draft_row.supersedes_content_hash IS DISTINCT FROM prior_hash
                THEN
                    RAISE EXCEPTION 'publication chain does not preserve the prior version'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            expected_snapshot := paper_expected_publication_snapshot(
                NEW.paper_id,
                NEW.curriculum_version_id,
                NEW.version
            );
            IF expected_snapshot IS NULL OR NEW.snapshot IS DISTINCT FROM expected_snapshot THEN
                RAISE EXCEPTION 'publication snapshot does not reproduce its authoritative draft'
                    USING ERRCODE = '23514';
            END IF;
            expected_hash := encode(
                sha256(convert_to(paper_canonical_jsonb(NEW.snapshot), 'UTF8')),
                'hex'
            );
            IF NEW.content_hash IS DISTINCT FROM expected_hash THEN
                RAISE EXCEPTION 'publication content hash is not reproducible'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_paper_archive_event_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            aggregate_row practice_papers%ROWTYPE;
            publication_hash text;
        BEGIN
            SELECT * INTO aggregate_row
            FROM practice_papers
            WHERE id = NEW.paper_id
                AND curriculum_version_id = NEW.curriculum_version_id
            FOR UPDATE;
            IF NOT FOUND OR aggregate_row.state <> 'published'
                OR aggregate_row.current_version <> NEW.version
            THEN
                RAISE EXCEPTION 'archive event must target the current publication'
                    USING ERRCODE = '23514';
            END IF;
            SELECT content_hash INTO publication_hash
            FROM published_paper_versions
            WHERE paper_id = NEW.paper_id
                AND curriculum_version_id = NEW.curriculum_version_id
                AND version = NEW.version;
            IF publication_hash IS NULL THEN
                RAISE EXCEPTION 'archive event requires an exact publication'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_paper_version_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'paper drafts, selections, publications, and archives are append-only'
                USING ERRCODE = '23514';
        END;
        $$
        """
    )

    for table_name, insert_function, insert_trigger, mutation_trigger in (
        (
            "paper_draft_versions",
            "enforce_paper_draft_version_insert",
            "enforce_paper_draft_version_insert_trigger",
            "reject_paper_draft_version_mutation_trigger",
        ),
        (
            "paper_draft_candidates",
            "enforce_paper_draft_candidate_insert",
            "enforce_paper_draft_candidate_insert_trigger",
            "reject_paper_draft_candidate_mutation_trigger",
        ),
        (
            "published_paper_versions",
            "enforce_published_paper_version_insert",
            "enforce_published_paper_version_insert_trigger",
            "reject_published_paper_version_mutation_trigger",
        ),
        (
            "paper_archive_events",
            "enforce_paper_archive_event_insert",
            "enforce_paper_archive_event_insert_trigger",
            "reject_paper_archive_event_mutation_trigger",
        ),
    ):
        op.execute(
            f"""
            CREATE TRIGGER {insert_trigger}
            BEFORE INSERT ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION {insert_function}()
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER {mutation_trigger}
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION reject_paper_version_mutation()
            """
        )


def _create_completeness_triggers() -> None:
    op.execute(
        """
        CREATE FUNCTION enforce_practice_paper_complete()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            target_id uuid;
            target_curriculum_id uuid;
            aggregate_row practice_papers%ROWTYPE;
            blueprint_row paper_blueprints%ROWTYPE;
            draft_count integer;
            minimum_draft integer;
            maximum_draft integer;
            publication_count integer;
            minimum_publication integer;
            maximum_publication integer;
            archive_count integer;
            current_item_count integer;
            minimum_ordinal integer;
            maximum_ordinal integer;
            expected_publication_count integer;
            current_snapshot jsonb;
            current_hash text;
        BEGIN
            IF TG_TABLE_NAME = 'practice_papers' THEN
                target_id := NEW.id;
                target_curriculum_id := NEW.curriculum_version_id;
            ELSIF TG_TABLE_NAME = 'paper_draft_candidates' THEN
                target_id := NEW.paper_id;
                target_curriculum_id := NEW.curriculum_version_id;
            ELSE
                target_id := NEW.paper_id;
                target_curriculum_id := NEW.curriculum_version_id;
            END IF;
            SELECT * INTO aggregate_row
            FROM practice_papers
            WHERE id = target_id AND curriculum_version_id = target_curriculum_id;
            IF NOT FOUND THEN
                RETURN NEW;
            END IF;
            SELECT * INTO blueprint_row
            FROM paper_blueprints
            WHERE id = aggregate_row.paper_blueprint_id
                AND curriculum_version_id = target_curriculum_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'practice paper blueprint is missing'
                    USING ERRCODE = '23514';
            END IF;

            SELECT count(*), min(version), max(version)
            INTO draft_count, minimum_draft, maximum_draft
            FROM paper_draft_versions WHERE paper_id = target_id;
            IF draft_count <> aggregate_row.current_version
                OR minimum_draft IS DISTINCT FROM 1
                OR maximum_draft IS DISTINCT FROM aggregate_row.current_version
            THEN
                RAISE EXCEPTION 'paper draft version history is incomplete'
                    USING ERRCODE = '23514';
            END IF;

            SELECT count(*), min(ordinal), max(ordinal)
            INTO current_item_count, minimum_ordinal, maximum_ordinal
            FROM paper_draft_candidates
            WHERE paper_id = target_id
                AND paper_version = aggregate_row.current_version;
            IF current_item_count <> blueprint_row.slot_count
                OR minimum_ordinal IS DISTINCT FROM 1
                OR maximum_ordinal IS DISTINCT FROM blueprint_row.slot_count
            THEN
                RAISE EXCEPTION 'current paper draft slot selection is incomplete'
                    USING ERRCODE = '23514';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM paper_draft_candidates AS item
                LEFT JOIN question_candidates AS candidate
                    ON candidate.id = item.candidate_id
                    AND candidate.curriculum_version_id = item.curriculum_version_id
                LEFT JOIN LATERAL (
                    SELECT slot->>'slot_id' AS slot_id
                    FROM jsonb_array_elements(blueprint_row.blueprint->'slots')
                        WITH ORDINALITY AS blueprint_slot(slot, ordinal)
                    WHERE blueprint_slot.ordinal = item.ordinal
                ) AS expected_slot ON TRUE
                WHERE item.paper_id = target_id
                    AND item.paper_version = aggregate_row.current_version
                    AND (
                        expected_slot.slot_id IS DISTINCT FROM item.blueprint_slot_id
                        OR candidate.id IS NULL
                        OR candidate.state <> 'approved'
                        OR candidate.version <> item.candidate_version
                        OR candidate.current_revision <> item.candidate_revision
                        OR candidate.validation_evidence->'passed' IS DISTINCT FROM 'true'::jsonb
                        OR candidate.paper_blueprint_id IS DISTINCT FROM
                            aggregate_row.paper_blueprint_id
                        OR candidate.blueprint_id IS DISTINCT FROM aggregate_row.blueprint_id
                        OR candidate.blueprint_version IS DISTINCT FROM
                            aggregate_row.blueprint_version
                        OR candidate.blueprint_slot_id IS DISTINCT FROM item.blueprint_slot_id
                    )
            ) THEN
                RAISE EXCEPTION 'current paper draft selection violates candidate invariants'
                    USING ERRCODE = '23514';
            END IF;

            SELECT count(*), min(version), max(version)
            INTO publication_count, minimum_publication, maximum_publication
            FROM published_paper_versions WHERE paper_id = target_id;
            expected_publication_count := CASE
                WHEN aggregate_row.state = 'draft' THEN aggregate_row.current_version - 1
                ELSE aggregate_row.current_version
            END;
            IF publication_count <> expected_publication_count
                OR (expected_publication_count = 0 AND (
                    minimum_publication IS NOT NULL OR maximum_publication IS NOT NULL
                ))
                OR (expected_publication_count > 0 AND (
                    minimum_publication IS DISTINCT FROM 1
                    OR maximum_publication IS DISTINCT FROM expected_publication_count
                ))
            THEN
                RAISE EXCEPTION 'paper publication version history is incomplete'
                    USING ERRCODE = '23514';
            END IF;

            SELECT count(*) INTO archive_count
            FROM paper_archive_events WHERE paper_id = target_id;
            IF aggregate_row.state = 'draft' THEN
                IF EXISTS (
                    SELECT 1 FROM published_paper_versions
                    WHERE paper_id = target_id
                        AND version = aggregate_row.current_version
                ) OR archive_count <> 0 THEN
                    RAISE EXCEPTION 'draft aggregate has publication or archive state'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF aggregate_row.state = 'published' THEN
                IF archive_count <> 0 OR NOT EXISTS (
                    SELECT 1 FROM published_paper_versions
                    WHERE paper_id = target_id
                        AND curriculum_version_id = target_curriculum_id
                        AND version = aggregate_row.current_version
                ) THEN
                    RAISE EXCEPTION 'published aggregate lacks its current publication'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF aggregate_row.state = 'archived' THEN
                IF archive_count <> 1 OR NOT EXISTS (
                    SELECT 1 FROM paper_archive_events
                    WHERE paper_id = target_id
                        AND curriculum_version_id = target_curriculum_id
                        AND version = aggregate_row.current_version
                ) THEN
                    RAISE EXCEPTION 'archived aggregate lacks its exact terminal event'
                        USING ERRCODE = '23514';
                END IF;
            ELSE
                RAISE EXCEPTION 'practice paper state is invalid' USING ERRCODE = '23514';
            END IF;

            IF aggregate_row.state IN ('published', 'archived') THEN
                SELECT snapshot, content_hash INTO current_snapshot, current_hash
                FROM published_paper_versions
                WHERE paper_id = target_id
                    AND curriculum_version_id = target_curriculum_id
                    AND version = aggregate_row.current_version;
                IF current_snapshot IS DISTINCT FROM paper_expected_publication_snapshot(
                        target_id,
                        target_curriculum_id,
                        aggregate_row.current_version
                    )
                    OR current_hash IS DISTINCT FROM encode(
                        sha256(convert_to(paper_canonical_jsonb(current_snapshot), 'UTF8')),
                        'hex'
                    )
                THEN
                    RAISE EXCEPTION 'current publication snapshot or hash is not reproducible'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    for table_name, operation in (
        ("practice_papers", "INSERT OR UPDATE"),
        ("paper_draft_versions", "INSERT"),
        ("paper_draft_candidates", "INSERT"),
        ("published_paper_versions", "INSERT"),
        ("paper_archive_events", "INSERT"),
    ):
        op.execute(
            f"""
            CREATE CONSTRAINT TRIGGER enforce_{table_name}_complete_trigger
            AFTER {operation} ON {table_name}
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION enforce_practice_paper_complete()
            """
        )


def downgrade() -> None:
    for table_name in (
        "paper_archive_events",
        "published_paper_versions",
        "paper_draft_candidates",
        "paper_draft_versions",
        "practice_papers",
    ):
        op.execute(f"DROP TRIGGER enforce_{table_name}_complete_trigger ON {table_name}")
    op.execute("DROP FUNCTION enforce_practice_paper_complete()")

    for table_name, mutation_trigger, insert_trigger in (
        (
            "paper_archive_events",
            "reject_paper_archive_event_mutation_trigger",
            "enforce_paper_archive_event_insert_trigger",
        ),
        (
            "published_paper_versions",
            "reject_published_paper_version_mutation_trigger",
            "enforce_published_paper_version_insert_trigger",
        ),
        (
            "paper_draft_candidates",
            "reject_paper_draft_candidate_mutation_trigger",
            "enforce_paper_draft_candidate_insert_trigger",
        ),
        (
            "paper_draft_versions",
            "reject_paper_draft_version_mutation_trigger",
            "enforce_paper_draft_version_insert_trigger",
        ),
    ):
        op.execute(f"DROP TRIGGER {mutation_trigger} ON {table_name}")
        op.execute(f"DROP TRIGGER {insert_trigger} ON {table_name}")
    op.execute("DROP FUNCTION reject_paper_version_mutation()")
    op.execute("DROP FUNCTION enforce_paper_archive_event_insert()")
    op.execute("DROP FUNCTION enforce_published_paper_version_insert()")
    op.execute("DROP FUNCTION enforce_paper_draft_candidate_insert()")
    op.execute("DROP FUNCTION enforce_paper_draft_version_insert()")

    op.execute("DROP TRIGGER reject_practice_paper_delete_trigger ON practice_papers")
    op.execute("DROP TRIGGER enforce_practice_paper_update_trigger ON practice_papers")
    op.execute("DROP TRIGGER enforce_practice_paper_insert_trigger ON practice_papers")
    op.execute("DROP FUNCTION reject_practice_paper_delete()")
    op.execute("DROP FUNCTION enforce_practice_paper_update()")
    op.execute("DROP FUNCTION enforce_practice_paper_insert()")
    op.execute("DROP FUNCTION paper_expected_publication_snapshot(uuid, uuid, integer)")

    op.drop_index(
        "ix_paper_archive_events_curriculum_archived",
        table_name="paper_archive_events",
    )
    op.drop_table("paper_archive_events")
    op.drop_index(
        "ix_published_paper_versions_curriculum_published",
        table_name="published_paper_versions",
    )
    op.drop_index(
        "ix_published_paper_versions_paper_version",
        table_name="published_paper_versions",
    )
    op.drop_table("published_paper_versions")
    op.drop_index(
        "ix_paper_draft_candidates_candidate",
        table_name="paper_draft_candidates",
    )
    op.drop_table("paper_draft_candidates")
    op.drop_index(
        "ix_paper_draft_versions_paper_version",
        table_name="paper_draft_versions",
    )
    op.drop_table("paper_draft_versions")
    op.drop_index(
        "ix_practice_papers_curriculum_blueprint_updated",
        table_name="practice_papers",
    )
    op.drop_index(
        "ix_practice_papers_curriculum_state_updated",
        table_name="practice_papers",
    )
    op.drop_table("practice_papers")
    op.drop_constraint(
        "uq_question_candidates_id_curriculum",
        "question_candidates",
        type_="unique",
    )
    op.execute("DROP FUNCTION paper_canonical_jsonb(jsonb)")

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0046_knowledge_units"
down_revision = "0045_understanding_review"
branch_labels = None
depends_on = None


def _created() -> list[sa.Column[Any]]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "audit_event_id",
            sa.Uuid(),
            sa.ForeignKey(
                "admin_audit_events.id", ondelete="RESTRICT", deferrable=True, initially="DEFERRED"
            ),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\" WITH SCHEMA public VERSION '1.1'")
    op.create_table(
        "knowledge_units",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("trusted_page_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("curriculum_version_id", sa.Uuid(), nullable=False),
        sa.Column("curriculum_unit_id", sa.Uuid(), nullable=True),
        sa.Column("lesson_id", sa.Uuid(), nullable=True),
        sa.Column("catalogue_decision_id", sa.Uuid(), nullable=False),
        sa.Column("catalogue_version", sa.Integer(), nullable=False),
        sa.Column("metadata_scope_version", sa.Integer(), nullable=False),
        sa.Column("scope_fingerprint", sa.String(64), nullable=False),
        sa.Column("derivation_version", sa.String(64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        *_created(),
        sa.UniqueConstraint(
            "id", "candidate_id", "document_id", "page_number", name="uq_knowledge_unit_source"
        ),
        sa.UniqueConstraint("id", "fingerprint", name="uq_knowledge_unit_fingerprint"),
        sa.UniqueConstraint(
            "trusted_page_id",
            "scope_fingerprint",
            "derivation_version",
            "sequence",
            name="uq_knowledge_unit_derivation",
        ),
        sa.ForeignKeyConstraint(
            ["trusted_page_id", "candidate_id", "document_id", "page_number"],
            [
                "trusted_page_knowledge.id",
                "trusted_page_knowledge.candidate_id",
                "trusted_page_knowledge.document_id",
                "trusted_page_knowledge.page_number",
            ],
            ondelete="RESTRICT",
            name="fk_knowledge_unit_trusted_page",
        ),
        sa.ForeignKeyConstraint(
            ["curriculum_version_id", "catalogue_decision_id", "catalogue_version"],
            [
                "catalogue_admission_decisions.curriculum_version_id",
                "catalogue_admission_decisions.id",
                "catalogue_admission_decisions.version",
            ],
            ondelete="RESTRICT",
            name="fk_knowledge_unit_admission",
        ),
        sa.ForeignKeyConstraint(
            ["curriculum_unit_id", "curriculum_version_id"],
            ["curriculum_units.id", "curriculum_units.curriculum_version_id"],
            ondelete="RESTRICT",
            name="fk_knowledge_unit_curriculum_unit",
        ),
        sa.ForeignKeyConstraint(
            ["lesson_id", "curriculum_unit_id", "curriculum_version_id"],
            [
                "curriculum_lessons.id",
                "curriculum_lessons.unit_id",
                "curriculum_lessons.curriculum_version_id",
            ],
            ondelete="RESTRICT",
            name="fk_knowledge_unit_lesson",
        ),
        sa.CheckConstraint(
            "page_number>0 AND sequence BETWEEN 0 AND 127 AND metadata_scope_version>=0",
            name="ck_knowledge_unit_versions",
        ),
        sa.CheckConstraint(
            "lesson_id IS NULL OR curriculum_unit_id IS NOT NULL", name="ck_knowledge_unit_lesson"
        ),
        sa.CheckConstraint(
            "derivation_version='page-region-components.v1'", name="ck_knowledge_unit_derivation"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload)='object' AND octet_length(payload::text)<=4194304",
            name="ck_knowledge_unit_payload",
        ),
        sa.CheckConstraint(
            "fingerprint=public.source_understanding_fingerprint(payload)",
            name="ck_knowledge_unit_payload_hash",
        ),
        sa.CheckConstraint(
            "scope_fingerprint=public.source_understanding_fingerprint(payload->'scope')",
            name="ck_knowledge_unit_scope_hash",
        ),
    )
    op.create_index(
        "ix_knowledge_unit_scope",
        "knowledge_units",
        ["curriculum_version_id", "curriculum_unit_id", "lesson_id"],
    )
    op.create_index("ix_knowledge_unit_document", "knowledge_units", ["document_id", "page_number"])
    op.create_table(
        "knowledge_unit_regions",
        sa.Column("unit_id", sa.Uuid(), primary_key=True),
        sa.Column("region_id", sa.Uuid(), primary_key=True),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("region_key", sa.String(64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.UniqueConstraint("unit_id", "ordinal", name="uq_knowledge_unit_region_order"),
        sa.ForeignKeyConstraint(
            ["unit_id", "candidate_id", "document_id", "page_number"],
            [
                "knowledge_units.id",
                "knowledge_units.candidate_id",
                "knowledge_units.document_id",
                "knowledge_units.page_number",
            ],
            ondelete="RESTRICT",
            name="fk_knowledge_unit_region_unit",
        ),
        sa.ForeignKeyConstraint(
            ["region_id", "candidate_id", "document_id", "page_number"],
            [
                "source_understanding_regions.id",
                "source_understanding_regions.candidate_id",
                "source_understanding_regions.document_id",
                "source_understanding_regions.page_number",
            ],
            ondelete="RESTRICT",
            name="fk_knowledge_unit_region_source",
        ),
        sa.CheckConstraint(
            "ordinal BETWEEN 0 AND 127 AND region_key ~ '^[a-z][a-z0-9_-]{0,63}$'",
            name="ck_knowledge_unit_region_order",
        ),
    )
    op.create_table(
        "knowledge_projections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("unit_id", sa.Uuid(), nullable=False),
        sa.Column("unit_fingerprint", sa.String(64), nullable=False),
        sa.Column("transformation_version", sa.String(64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        *_created(),
        sa.ForeignKeyConstraint(
            ["unit_id", "unit_fingerprint"],
            ["knowledge_units.id", "knowledge_units.fingerprint"],
            ondelete="RESTRICT",
            name="fk_knowledge_projection_unit",
        ),
        sa.UniqueConstraint(
            "unit_id", "transformation_version", name="uq_knowledge_projection_transform"
        ),
        sa.CheckConstraint(
            "transformation_version='source-observation-meaning.v1'",
            name="ck_knowledge_projection_transform",
        ),
        sa.CheckConstraint(
            "char_length(text) BETWEEN 1 AND 32768 AND octet_length(text)<=65536 "
            "AND text=normalize(text,NFC)",
            name="ck_knowledge_projection_text",
        ),
        sa.CheckConstraint(
            "text_sha256=encode(sha256(convert_to(text,'UTF8')),'hex')",
            name="ck_knowledge_projection_text_hash",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload)='object' AND octet_length(payload::text)<=131072",
            name="ck_knowledge_projection_payload",
        ),
        sa.CheckConstraint(
            "fingerprint=public.source_understanding_fingerprint(payload)",
            name="ck_knowledge_projection_payload_hash",
        ),
    )
    _source_functions()
    _component_function()
    _projection_function()
    _guards()


def _source_functions() -> None:
    op.execute("""
        CREATE FUNCTION public.source_understanding_document_is_resolved(source_id uuid)
        RETURNS boolean LANGUAGE sql STABLE AS $$
            SELECT EXISTS (SELECT 1 FROM public.source_documents d
                JOIN public.source_understanding_pages p ON p.document_id=d.id
                    AND p.page_number BETWEEN 1 AND d.original_page_count
                LEFT JOIN public.admin_audit_events a ON a.id=p.event_id
                WHERE d.id=source_id AND d.original_page_count>0
                    AND d.active_for_ai AND NOT d.quarantined_for_teacher_use
                GROUP BY d.id,d.original_page_count
                HAVING count(*) FILTER (WHERE
                    (p.state='verified'
                        AND public.trusted_page_knowledge_is_current(p.current_trusted_id))
                    OR (p.state='excluded' AND a.action='page_understanding.excluded'
                        AND a.actor_id=p.updated_by AND a.resource_type='page_understanding'
                        AND a.resource_id=d.id
                        AND a.payload->'confirmed_exclusion'='true'::jsonb
                        AND a.payload->>'source_sha256'=d.checksum_sha256
                        AND a.payload->'version'=to_jsonb(p.version)
                        AND a.payload->'page_number'=to_jsonb(p.page_number)
                        AND a.payload->>'candidate_id'
                            IS NOT DISTINCT FROM p.current_candidate_id::text
                        AND a.payload->>'report_id' IS NOT DISTINCT FROM p.current_report_id::text))
                    =d.original_page_count
                AND bool_or(public.trusted_page_knowledge_is_current(p.current_trusted_id)));
        $$;
    """)
    op.execute("""
        CREATE FUNCTION public.knowledge_scope_snapshot(source_id uuid)
        RETURNS jsonb LANGUAGE sql STABLE AS $$
            SELECT jsonb_build_object('schema_version','knowledge-scope.v2',
                'document_id',d.id::text,'source_sha256',d.checksum_sha256,
                'material_type',d.document_type,'year',d.year,'paper_code',d.paper_code,
                'metadata_scope_version',d.metadata_scope_version,
                'curriculum_version_id',d.curriculum_version_id::text,'grade',e.grade,
                'medium_id',cv.medium_id::text,'subject_id',cv.subject_id::text,
                'catalogue_decision_id',a.id::text,'catalogue_version',a.version,
                'catalogue_scope_fingerprint',a.scope_fingerprint,
                'curriculum_unit_id',d.unit_id::text,'lesson_id',d.lesson_id::text)
            FROM public.source_documents d
            JOIN public.curriculum_versions cv ON cv.id=d.curriculum_version_id
            JOIN public.exam_configurations e ON e.id=cv.exam_configuration_id
            JOIN public.catalogue_admission_current c ON c.curriculum_version_id=cv.id
            JOIN public.catalogue_admission_decisions a ON a.id=c.decision_id
                AND a.curriculum_version_id=c.curriculum_version_id AND a.version=c.version
            LEFT JOIN public.curriculum_units u ON u.id=d.unit_id AND u.curriculum_version_id=cv.id
            LEFT JOIN public.curriculum_lessons l ON l.id=d.lesson_id
                AND l.unit_id=d.unit_id AND l.curriculum_version_id=cv.id
            WHERE d.id=source_id AND d.active_for_ai AND NOT d.quarantined_for_teacher_use
                AND NOT d.metadata_review_required
                AND public.catalogue_curriculum_is_admitted(cv.id)
                AND a.state='approved' AND a.educational_approval
                AND (d.unit_id IS NULL OR u.active) AND (d.lesson_id IS NULL OR l.active);
        $$;
    """)
    op.execute("""
        CREATE FUNCTION public.lock_knowledge_unit_source(source_id uuid)
        RETURNS void LANGUAGE plpgsql AS $$
        DECLARE source public.source_documents%ROWTYPE;
        BEGIN
            PERFORM pg_advisory_xact_lock(hashtextextended(
                'knowledge-preparation:'||source_id::text,0));
            SELECT * INTO source FROM public.source_documents WHERE id=source_id FOR SHARE;
            PERFORM 1 FROM public.source_understanding_pages
                WHERE document_id=source_id ORDER BY page_number FOR SHARE;
            PERFORM 1 FROM public.curriculum_versions cv
                JOIN public.exam_configurations e ON e.id=cv.exam_configuration_id
                JOIN public.media m ON m.id=cv.medium_id
                JOIN public.subjects s ON s.id=cv.subject_id
                WHERE cv.id=source.curriculum_version_id FOR SHARE OF cv,e,m,s;
            PERFORM 1 FROM public.catalogue_admission_current
                WHERE curriculum_version_id=source.curriculum_version_id FOR SHARE;
            PERFORM 1 FROM public.curriculum_units WHERE id=source.unit_id FOR SHARE;
            PERFORM 1 FROM public.curriculum_lessons WHERE id=source.lesson_id FOR SHARE;
        END; $$;
    """)
    op.execute("""
        CREATE FUNCTION public.knowledge_unit_is_current(identifier uuid)
        RETURNS boolean LANGUAGE sql STABLE AS $$
            SELECT EXISTS (SELECT 1 FROM public.knowledge_units u
                WHERE u.id=identifier AND u.derivation_version='page-region-components.v1'
                    AND u.payload->'scope'=public.knowledge_scope_snapshot(u.document_id)
                    AND public.source_understanding_document_is_resolved(u.document_id)
                    AND public.trusted_page_knowledge_is_current(u.trusted_page_id));
        $$;
    """)
    op.execute("""
        CREATE FUNCTION public.knowledge_projection_is_current(identifier uuid)
        RETURNS boolean LANGUAGE sql STABLE AS $$
            SELECT EXISTS (SELECT 1 FROM public.knowledge_projections p
                WHERE p.id=identifier AND p.transformation_version='source-observation-meaning.v1'
                    AND public.knowledge_unit_is_current(p.unit_id));
        $$;
    """)


def _component_function() -> None:
    op.execute("""
        CREATE FUNCTION public.knowledge_unit_component_keys(value jsonb)
        RETURNS jsonb LANGUAGE plpgsql IMMUTABLE STRICT AS $$
        DECLARE keys text[]; parents integer[]; labels integer[]:='{}';
            edge record; i integer; a integer; b integer; group_index integer;
            result jsonb:='[]'::jsonb;
        BEGIN
            SELECT array_agg(r->>'key' ORDER BY (r->>'reading_order')::integer,r->>'key')
                INTO keys FROM jsonb_array_elements(value->'observation'->'regions') r;
            IF coalesce(array_length(keys,1),0) NOT BETWEEN 1 AND 128 THEN
                RAISE EXCEPTION 'knowledge components require bounded observed regions'
                    USING ERRCODE='23514'; END IF;
            parents:=ARRAY(SELECT generate_series(1,array_length(keys,1)));
            FOR edge IN
                SELECT r->>'key' left_key,r->>'parent_key' right_key
                    FROM jsonb_array_elements(value->'observation'->'regions') r
                    WHERE r->>'parent_key' IS NOT NULL
                UNION SELECT r->>'source_key',r->>'target_key'
                    FROM jsonb_array_elements(value->'observation'->'relationships') r
                    WHERE r->>'kind'<>'reading_next'
                UNION SELECT c->'region_keys'->>0,k
                    FROM jsonb_array_elements(value->'education'->'claims') c,
                        LATERAL jsonb_array_elements_text(c->'region_keys') k
                UNION SELECT c->'region_keys'->>0,k
                    FROM jsonb_array_elements(value->'resolved_uncertainties') c,
                        LATERAL jsonb_array_elements_text(c->'region_keys') k
            LOOP
                a:=array_position(keys,edge.left_key); b:=array_position(keys,edge.right_key);
                IF a IS NULL OR b IS NULL THEN
                    RAISE EXCEPTION 'knowledge links must refer to known source regions'
                        USING ERRCODE='23514'; END IF;
                WHILE parents[a]<>a LOOP parents[a]:=parents[parents[a]]; a:=parents[a]; END LOOP;
                WHILE parents[b]<>b LOOP parents[b]:=parents[parents[b]]; b:=parents[b]; END LOOP;
                parents[b]:=a;
            END LOOP;
            FOR i IN 1..array_length(keys,1) LOOP
                a:=i;
                WHILE parents[a]<>a LOOP a:=parents[a]; END LOOP;
                group_index:=array_position(labels,a);
                IF group_index IS NULL THEN
                    labels:=array_append(labels,a);
                    result:=result||jsonb_build_array(jsonb_build_array(keys[i]));
                ELSE
                    result:=jsonb_set(result,ARRAY[(group_index-1)::text],
                        (result->(group_index-1))||jsonb_build_array(keys[i]),false);
                END IF;
            END LOOP;
            RETURN result;
        END; $$;
    """)


def _projection_function() -> None:
    op.execute(r"""
        CREATE FUNCTION public.knowledge_has_text(value text)
        RETURNS boolean LANGUAGE sql IMMUTABLE STRICT AS $$
            SELECT length(btrim(value,
                U&'\0009\000a\000b\000c\000d\001c\001d\001e\001f\0020\0085\00a0\1680'
                ||U&'\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200a'
                ||U&'\2028\2029\202f\205f\3000'))>0;
        $$;
    """)
    op.execute("""
        CREATE FUNCTION public.knowledge_projection_text(value jsonb)
        RETURNS text LANGUAGE plpgsql IMMUTABLE STRICT AS $$
        DECLARE region jsonb; equation text; cell jsonb; fact jsonb; claim jsonb;
            lines text[]:=ARRAY['Observed source']; meaningful boolean:=false;
        BEGIN
            FOR region IN SELECT r FROM jsonb_array_elements(value->'observation'->'regions') r LOOP
                lines:=array_append(lines,'Source region: '||(region->>'kind'));
                IF public.knowledge_has_text(region->>'exact_text') THEN
                    lines:=array_append(lines,region->>'exact_text'); meaningful:=true;
                END IF;
                FOR equation IN SELECT jsonb_array_elements_text(region->'equations') LOOP
                    lines:=array_append(lines,'Printed equation: '||equation); meaningful:=true;
                END LOOP;
                IF region->'table'<>'null'::jsonb THEN
                    FOR cell IN SELECT c FROM jsonb_array_elements(region->'table'->'cells') c
                        ORDER BY (c->>'row')::integer,(c->>'column')::integer LOOP
                        lines:=array_append(lines,'Cell '||((cell->>'row')::integer+1)::text||','
                            ||((cell->>'column')::integer+1)::text||' ('||(cell->>'row_span')||'x'
                            ||(cell->>'column_span')||'): '||CASE WHEN cell->>'state'='visible'
                                THEN cell->>'exact_text' ELSE '['||(cell->>'state')||']' END);
                        meaningful:=meaningful OR cell->>'state'='visible';
                    END LOOP;
                END IF;
                FOR fact IN SELECT f FROM jsonb_array_elements(region->'visual_facts') f LOOP
                    lines:=array_append(lines,fact->>'description'); meaningful:=true;
                    IF fact->'group_count'<>'null'::jsonb THEN
                        lines:=array_append(lines,'Visible groups: '||(fact->>'group_count'));
                    END IF;
                    IF fact->'items_per_group'<>'null'::jsonb THEN
                        lines:=array_append(lines,'Items per group: '||(fact->>'items_per_group'));
                    END IF;
                    IF fact->'printed_total'<>'null'::jsonb THEN
                        lines:=array_append(lines,'Printed total: '||(fact->>'printed_total'));
                    END IF;
                END LOOP;
            END LOOP;
            IF jsonb_array_length(value->'education'->'claims')>0 THEN
                lines:=array_append(lines,'Accepted educational meaning'); meaningful:=true;
                FOR claim IN SELECT c FROM jsonb_array_elements(value->'education'->'claims') c LOOP
                    lines:=array_append(lines,(claim->>'kind')||': '||(claim->>'description'));
                END LOOP;
            END IF;
            IF NOT meaningful THEN RETURN NULL; END IF;
            RETURN normalize(array_to_string(lines,chr(10)),NFC);
        END; $$;
    """)


def _guards() -> None:
    op.execute("""
        CREATE FUNCTION public.guard_knowledge_source_metadata() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF (NEW.curriculum_version_id,NEW.unit_id,NEW.lesson_id,
                NEW.document_type,NEW.year,NEW.paper_code) IS DISTINCT FROM
               (OLD.curriculum_version_id,OLD.unit_id,OLD.lesson_id,
                OLD.document_type,OLD.year,OLD.paper_code)
                AND EXISTS (SELECT 1 FROM public.knowledge_units WHERE document_id=OLD.id)
            THEN RAISE EXCEPTION 'verified knowledge source metadata is immutable; remove from use'
                USING ERRCODE='23514'; END IF;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE TRIGGER guard_knowledge_source_metadata_trigger
            BEFORE UPDATE ON public.source_documents
            FOR EACH ROW EXECUTE FUNCTION public.guard_knowledge_source_metadata();
    """)
    op.execute("""
        CREATE FUNCTION public.validate_knowledge_unit() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE trusted public.trusted_page_knowledge%ROWTYPE;
            scope jsonb; keys jsonb; observed jsonb; meaning jsonb; uncertainties jsonb;
            region_ids jsonb; expected jsonb;
        BEGIN
            PERFORM public.lock_knowledge_unit_source(NEW.document_id);
            SELECT * INTO trusted FROM public.trusted_page_knowledge WHERE id=NEW.trusted_page_id;
            scope:=public.knowledge_scope_snapshot(NEW.document_id);
            IF trusted.id IS NULL OR scope IS NULL
                OR NOT public.source_understanding_document_is_resolved(NEW.document_id)
                OR NOT public.trusted_page_knowledge_is_current(trusted.id)
                OR (NEW.candidate_id,NEW.document_id,NEW.page_number) IS DISTINCT FROM
                   (trusted.candidate_id,trusted.document_id,trusted.page_number)
                OR (NEW.curriculum_version_id,NEW.curriculum_unit_id,NEW.lesson_id,
                    NEW.catalogue_decision_id,NEW.catalogue_version,NEW.metadata_scope_version)
                    IS DISTINCT FROM
                   ((scope->>'curriculum_version_id')::uuid,(scope->>'curriculum_unit_id')::uuid,
                    (scope->>'lesson_id')::uuid,(scope->>'catalogue_decision_id')::uuid,
                    (scope->>'catalogue_version')::integer,(scope->>'metadata_scope_version')::integer)
            THEN RAISE EXCEPTION 'knowledge requires current verified document and admitted scope'
                USING ERRCODE='23514'; END IF;
            keys:=public.knowledge_unit_component_keys(trusted.payload)->NEW.sequence;
            IF keys IS NULL THEN RAISE EXCEPTION 'knowledge component does not exist'
                USING ERRCODE='23514'; END IF;
            SELECT jsonb_build_object('language',trusted.payload->'observation'->'language',
                'regions',jsonb_agg(r ORDER BY (r->>'reading_order')::integer,r->>'key'),
                'relationships',coalesce((SELECT jsonb_agg(link ORDER BY ord)
                    FROM jsonb_array_elements(trusted.payload->'observation'->'relationships')
                        WITH ORDINALITY AS links(link,ord)
                    WHERE keys ? (link->>'source_key') AND keys ? (link->>'target_key')),
                    '[]'::jsonb)),
                jsonb_agg(public.uuid_generate_v5(trusted.candidate_id,r->>'key')::text
                    ORDER BY (r->>'reading_order')::integer,r->>'key')
                INTO observed,region_ids
                FROM jsonb_array_elements(trusted.payload->'observation'->'regions') r
                WHERE keys ? (r->>'key');
            SELECT jsonb_build_object('claims',coalesce(jsonb_agg(claim ORDER BY ord),'[]'::jsonb))
                INTO meaning FROM jsonb_array_elements(trusted.payload->'education'->'claims')
                    WITH ORDINALITY AS claims(claim,ord)
                WHERE public.source_understanding_keys_match(claim->'region_keys',keys,false);
            SELECT coalesce(jsonb_agg(item ORDER BY ord),'[]'::jsonb) INTO uncertainties
                FROM jsonb_array_elements(trusted.payload->'resolved_uncertainties')
                    WITH ORDINALITY AS items(item,ord)
                WHERE public.source_understanding_keys_match(item->'region_keys',keys,false);
            expected:=jsonb_build_object('schema_version','knowledge-unit.v1',
                'derivation_version','page-region-components.v1','id',NEW.id::text,
                'trusted_page_id',trusted.id::text,'trusted_revision',trusted.revision,
                'trusted_fingerprint',trusted.fingerprint,'candidate_id',trusted.candidate_id::text,
                'source',trusted.payload->'source','scope',scope,'sequence',NEW.sequence,
                'region_ids',region_ids,'observation',observed,'education',meaning,
                'resolved_uncertainties',uncertainties);
            IF NEW.payload IS DISTINCT FROM expected
                OR NEW.id<>public.uuid_generate_v5(trusted.id,'knowledge-unit.v1:'
                    ||public.source_understanding_fingerprint(expected-'id'))
            THEN RAISE EXCEPTION 'knowledge unit must preserve its exact verified component'
                USING ERRCODE='23514'; END IF;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE TRIGGER validate_knowledge_unit_trigger BEFORE INSERT ON public.knowledge_units
            FOR EACH ROW EXECUTE FUNCTION public.validate_knowledge_unit();
    """)
    op.execute("""
        CREATE FUNCTION public.validate_knowledge_unit_region() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE unit public.knowledge_units%ROWTYPE;
            region public.source_understanding_regions%ROWTYPE;
        BEGIN
            SELECT * INTO unit FROM public.knowledge_units WHERE id=NEW.unit_id;
            SELECT * INTO region FROM public.source_understanding_regions WHERE id=NEW.region_id;
            IF unit.id IS NULL OR region.id IS NULL
                OR unit.payload->'region_ids'->>NEW.ordinal IS DISTINCT FROM NEW.region_id::text
                OR unit.payload->'observation'->'regions'->NEW.ordinal->>'key'
                    IS DISTINCT FROM NEW.region_key
                OR region.region_key<>NEW.region_key
            THEN RAISE EXCEPTION 'knowledge region link differs from its observed component'
                USING ERRCODE='23514'; END IF;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE TRIGGER validate_knowledge_unit_region_trigger
            BEFORE INSERT ON public.knowledge_unit_regions
            FOR EACH ROW EXECUTE FUNCTION public.validate_knowledge_unit_region();
    """)
    op.execute("""
        CREATE FUNCTION public.validate_knowledge_projection() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE unit public.knowledge_units%ROWTYPE; rendered text; expected jsonb;
        BEGIN
            SELECT * INTO unit FROM public.knowledge_units WHERE id=NEW.unit_id;
            IF unit.id IS NULL THEN RAISE EXCEPTION 'knowledge projection requires a source unit'
                USING ERRCODE='23514'; END IF;
            PERFORM public.lock_knowledge_unit_source(unit.document_id);
            rendered:=public.knowledge_projection_text(unit.payload);
            expected:=jsonb_build_object('schema_version','knowledge-projection.v1',
                'transformation_version','source-observation-meaning.v1','id',NEW.id::text,
                'unit_id',unit.id::text,'unit_fingerprint',unit.fingerprint,
                'text',rendered,'text_sha256',NEW.text_sha256);
            IF NOT public.knowledge_unit_is_current(unit.id) OR rendered IS NULL
                OR NEW.text IS DISTINCT FROM rendered OR NEW.payload IS DISTINCT FROM expected
                OR NEW.id<>public.uuid_generate_v5(unit.id,'source-observation-meaning.v1:'
                    ||unit.fingerprint||':'||NEW.text_sha256)
            THEN RAISE EXCEPTION 'knowledge projection must be its current deterministic rendering'
                USING ERRCODE='23514'; END IF;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE TRIGGER validate_knowledge_projection_trigger
            BEFORE INSERT ON public.knowledge_projections
            FOR EACH ROW EXECUTE FUNCTION public.validate_knowledge_projection();
    """)
    op.execute("""
        CREATE FUNCTION public.validate_knowledge_preparation_audit() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE audit public.admin_audit_events%ROWTYPE; unit public.knowledge_units%ROWTYPE;
            id_field text;
        BEGIN
            IF TG_TABLE_NAME='knowledge_units' THEN
                unit:=NEW; id_field:='unit_ids';
                IF (SELECT count(*) FROM public.knowledge_unit_regions WHERE unit_id=NEW.id)
                    <>jsonb_array_length(NEW.payload->'region_ids')
                THEN RAISE EXCEPTION 'knowledge unit requires all of its region links'
                    USING ERRCODE='23514'; END IF;
            ELSE
                SELECT * INTO unit FROM public.knowledge_units WHERE id=NEW.unit_id;
                id_field:='projection_ids';
            END IF;
            SELECT * INTO audit FROM public.admin_audit_events WHERE id=NEW.audit_event_id;
            IF audit.id IS NULL OR audit.actor_id<>NEW.created_by
                OR audit.resource_type<>'verified_knowledge' OR audit.resource_id<>unit.document_id
                OR audit.action<>'verified_knowledge.prepared'
                OR audit.payload->'page_number' IS DISTINCT FROM to_jsonb(unit.page_number)
                OR audit.payload->>'trusted_page_id' IS DISTINCT FROM unit.trusted_page_id::text
                OR audit.payload->>'scope_fingerprint' IS DISTINCT FROM unit.scope_fingerprint
                OR audit.payload->>'source_sha256'
                    IS DISTINCT FROM unit.payload->'source'->>'source_sha256'
                OR NOT coalesce((audit.payload->id_field) ? NEW.id::text,false)
            THEN RAISE EXCEPTION 'knowledge derivation requires matching immutable audit evidence'
                USING ERRCODE='23514'; END IF;
            RETURN NULL;
        END; $$;
    """)
    for table in ("knowledge_units", "knowledge_projections"):
        op.execute(f"""
            CREATE CONSTRAINT TRIGGER {table}_audit AFTER INSERT ON public.{table}
                DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
                EXECUTE FUNCTION public.validate_knowledge_preparation_audit();
        """)
    for table in ("knowledge_units", "knowledge_unit_regions", "knowledge_projections"):
        op.execute(
            f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON public.{table} "
            "FOR EACH ROW EXECUTE FUNCTION public.reject_source_fidelity_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER immutable_{table}_truncate BEFORE TRUNCATE ON public.{table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION public.reject_source_fidelity_mutation()"
        )


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM public.knowledge_units)
                OR EXISTS (SELECT 1 FROM public.knowledge_projections)
            THEN RAISE EXCEPTION 'cannot discard verified knowledge derivation history'; END IF;
        END; $$;
    """)
    op.execute("DROP TRIGGER guard_knowledge_source_metadata_trigger ON public.source_documents")
    op.execute("DROP FUNCTION public.guard_knowledge_source_metadata()")
    op.execute("DROP FUNCTION public.knowledge_projection_is_current(uuid)")
    op.execute("DROP FUNCTION public.knowledge_unit_is_current(uuid)")
    for table in ("knowledge_projections", "knowledge_unit_regions", "knowledge_units"):
        op.drop_table(table)
    for name in (
        "validate_knowledge_preparation_audit",
        "validate_knowledge_projection",
        "validate_knowledge_unit_region",
        "validate_knowledge_unit",
    ):
        op.execute(f"DROP FUNCTION public.{name}()")
    op.execute("DROP FUNCTION public.knowledge_projection_text(jsonb)")
    op.execute("DROP FUNCTION public.knowledge_has_text(text)")
    op.execute("DROP FUNCTION public.knowledge_unit_component_keys(jsonb)")
    op.execute("DROP FUNCTION public.lock_knowledge_unit_source(uuid)")
    op.execute("DROP FUNCTION public.knowledge_scope_snapshot(uuid)")
    op.execute("DROP FUNCTION public.source_understanding_document_is_resolved(uuid)")

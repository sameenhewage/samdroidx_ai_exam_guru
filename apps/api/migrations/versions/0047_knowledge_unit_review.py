import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0047_knowledge_unit_review"
down_revision = "0046_knowledge_units"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
        CREATE FUNCTION public.knowledge_review_reason_valid(value text)
        RETURNS boolean LANGUAGE sql IMMUTABLE STRICT AS $$
            SELECT char_length(value) BETWEEN 1 AND 2000
                AND value=btrim(value,
                    U&'\0009\000a\000b\000c\000d\001c\001d\001e\001f\0020\0085\00a0\1680'
                    ||U&'\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200a'
                    ||U&'\2028\2029\202f\205f\3000')
                AND value !~ U&'[\0001-\001f\007f-\009f]';
        $$;
    """)
    op.create_table(
        "knowledge_unit_reviews",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("unit_id", sa.Uuid(), nullable=False),
        sa.Column("unit_fingerprint", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("confirmed_mapping", sa.Boolean(), nullable=False),
        sa.Column("curriculum_version_id", sa.Uuid(), nullable=False),
        sa.Column("curriculum_unit_id", sa.Uuid(), nullable=True),
        sa.Column("lesson_id", sa.Uuid(), nullable=True),
        sa.Column("competency_id", sa.Uuid(), nullable=True),
        sa.Column("skill_id", sa.Uuid(), nullable=True),
        sa.Column("sub_skill_id", sa.Uuid(), nullable=True),
        sa.Column("learning_concept_id", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.String(2000), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
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
        sa.UniqueConstraint("unit_id", "version", name="uq_knowledge_unit_review_version"),
        sa.ForeignKeyConstraint(
            ["unit_id", "unit_fingerprint"],
            ["knowledge_units.id", "knowledge_units.fingerprint"],
            ondelete="RESTRICT",
            name="fk_knowledge_unit_review_unit",
        ),
        sa.ForeignKeyConstraint(
            ["curriculum_unit_id", "curriculum_version_id"],
            ["curriculum_units.id", "curriculum_units.curriculum_version_id"],
            ondelete="RESTRICT",
            name="fk_knowledge_unit_review_curriculum_unit",
        ),
        sa.ForeignKeyConstraint(
            ["lesson_id", "curriculum_unit_id", "curriculum_version_id"],
            [
                "curriculum_lessons.id",
                "curriculum_lessons.unit_id",
                "curriculum_lessons.curriculum_version_id",
            ],
            ondelete="RESTRICT",
            name="fk_knowledge_unit_review_lesson",
        ),
        *(
            sa.ForeignKeyConstraint(
                [column, "curriculum_version_id"],
                ["taxonomy_nodes.id", "taxonomy_nodes.curriculum_version_id"],
                ondelete="RESTRICT",
                name="fk_knowledge_unit_review_" + column,
            )
            for column in ("competency_id", "skill_id", "sub_skill_id", "learning_concept_id")
        ),
        sa.CheckConstraint(
            "version BETWEEN 1 AND 2147483646", name="ck_knowledge_unit_review_version"
        ),
        sa.CheckConstraint(
            "(state='reviewed' AND confirmed_mapping AND competency_id IS NOT NULL) OR "
            "(state='rejected' AND NOT confirmed_mapping AND competency_id IS NULL "
            "AND skill_id IS NULL AND sub_skill_id IS NULL AND learning_concept_id IS NULL "
            "AND curriculum_unit_id IS NULL AND lesson_id IS NULL)",
            name="ck_knowledge_unit_review_decision",
        ),
        sa.CheckConstraint(
            "(lesson_id IS NULL OR curriculum_unit_id IS NOT NULL) "
            "AND (skill_id IS NULL OR competency_id IS NOT NULL) "
            "AND (sub_skill_id IS NULL OR skill_id IS NOT NULL) "
            "AND (learning_concept_id IS NULL OR sub_skill_id IS NOT NULL)",
            name="ck_knowledge_unit_review_path",
        ),
        sa.CheckConstraint(
            "public.knowledge_review_reason_valid(reason)", name="ck_knowledge_unit_review_reason"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload)='object' AND octet_length(payload::text)<=65536",
            name="ck_knowledge_unit_review_payload",
        ),
        sa.CheckConstraint(
            "fingerprint=public.source_understanding_fingerprint(payload)",
            name="ck_knowledge_unit_review_hash",
        ),
    )
    op.create_index(
        "ix_knowledge_unit_review_scope",
        "knowledge_unit_reviews",
        ["curriculum_version_id", "competency_id", "skill_id"],
    )
    op.execute("""
        CREATE FUNCTION public.lock_knowledge_review_taxonomy(
            requested_unit uuid, requested_lesson uuid, competency uuid,
            skill uuid, sub_skill uuid, concept uuid
        ) RETURNS void LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM 1 FROM public.curriculum_units WHERE id=requested_unit FOR SHARE;
            PERFORM 1 FROM public.curriculum_lessons WHERE id=requested_lesson FOR SHARE;
            PERFORM 1 FROM public.taxonomy_nodes WHERE id IN (competency,skill,sub_skill,concept)
                ORDER BY id FOR SHARE;
        END; $$;
    """)
    op.execute("""
        CREATE FUNCTION public.knowledge_review_scope_valid(
            identifier uuid, requested_unit uuid, requested_lesson uuid,
            competency uuid, skill uuid, sub_skill uuid, concept uuid
        ) RETURNS boolean LANGUAGE sql STABLE AS $$
            SELECT EXISTS (SELECT 1 FROM public.knowledge_units k
                JOIN public.taxonomy_nodes c ON c.id=competency
                    AND c.curriculum_version_id=k.curriculum_version_id
                    AND c.level='competency' AND c.active AND c.review_state='reviewed'
                LEFT JOIN public.taxonomy_nodes s ON s.id=skill
                LEFT JOIN public.taxonomy_nodes b ON b.id=sub_skill
                LEFT JOIN public.taxonomy_nodes n ON n.id=concept
                LEFT JOIN public.curriculum_units u ON u.id=requested_unit
                LEFT JOIN public.curriculum_lessons l ON l.id=requested_lesson
                WHERE k.id=identifier
                    AND (k.curriculum_unit_id IS NULL OR k.curriculum_unit_id=requested_unit)
                    AND (k.lesson_id IS NULL OR k.lesson_id=requested_lesson)
                    AND (requested_unit IS NULL OR (u.active
                        AND u.curriculum_version_id=k.curriculum_version_id))
                    AND (requested_lesson IS NULL OR (l.active AND l.unit_id=requested_unit
                        AND l.curriculum_version_id=k.curriculum_version_id))
                    AND (skill IS NULL OR (s.active AND s.review_state='reviewed'
                        AND s.level='skill' AND s.parent_id=c.id
                        AND s.curriculum_version_id=k.curriculum_version_id))
                    AND (sub_skill IS NULL OR (b.active AND b.review_state='reviewed'
                        AND b.level='sub_skill' AND b.parent_id=s.id
                        AND b.curriculum_version_id=k.curriculum_version_id))
                    AND (concept IS NULL OR (n.active AND n.review_state='reviewed'
                        AND n.level='learning_concept' AND n.parent_id=b.id
                        AND n.curriculum_version_id=k.curriculum_version_id)));
        $$;
    """)
    op.execute("""
        CREATE FUNCTION public.knowledge_unit_review_is_eligible(identifier uuid)
        RETURNS boolean LANGUAGE sql STABLE AS $$
            SELECT EXISTS (SELECT 1 FROM public.knowledge_unit_reviews r
                WHERE r.id=identifier AND r.state='reviewed' AND r.confirmed_mapping
                    AND r.version=(SELECT max(version) FROM public.knowledge_unit_reviews x
                        WHERE x.unit_id=r.unit_id)
                    AND public.knowledge_unit_is_current(r.unit_id)
                    AND public.knowledge_review_scope_valid(r.unit_id,r.curriculum_unit_id,
                        r.lesson_id,r.competency_id,r.skill_id,r.sub_skill_id,r.learning_concept_id));
        $$;
    """)
    op.execute("""
        CREATE FUNCTION public.knowledge_projection_is_eligible(identifier uuid)
        RETURNS boolean LANGUAGE sql STABLE AS $$
            SELECT EXISTS (SELECT 1 FROM public.knowledge_projections p
                JOIN public.knowledge_unit_reviews r ON r.unit_id=p.unit_id
                WHERE p.id=identifier AND public.knowledge_projection_is_current(p.id)
                    AND public.knowledge_unit_review_is_eligible(r.id));
        $$;
    """)
    op.execute("""
        CREATE FUNCTION public.validate_knowledge_unit_review() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE unit public.knowledge_units%ROWTYPE; previous_version integer; expected jsonb;
        BEGIN
            SELECT * INTO unit FROM public.knowledge_units WHERE id=NEW.unit_id;
            IF unit.id IS NULL THEN RAISE EXCEPTION 'knowledge review requires an existing unit'
                USING ERRCODE='23514'; END IF;
            PERFORM public.lock_knowledge_unit_source(unit.document_id);
            PERFORM public.lock_knowledge_review_taxonomy(NEW.curriculum_unit_id,NEW.lesson_id,
                NEW.competency_id,NEW.skill_id,NEW.sub_skill_id,NEW.learning_concept_id);
            SELECT coalesce(max(version),0) INTO previous_version
                FROM public.knowledge_unit_reviews WHERE unit_id=NEW.unit_id;
            IF NEW.version<>previous_version+1
                OR NEW.curriculum_version_id<>unit.curriculum_version_id
                OR NEW.unit_fingerprint<>unit.fingerprint
                OR (NEW.state='reviewed' AND (NOT public.knowledge_unit_is_current(unit.id)
                    OR NOT public.knowledge_review_scope_valid(unit.id,NEW.curriculum_unit_id,
                        NEW.lesson_id,NEW.competency_id,NEW.skill_id,NEW.sub_skill_id,
                        NEW.learning_concept_id)))
            THEN RAISE EXCEPTION 'knowledge review requires current source, taxonomy and version'
                USING ERRCODE='23514'; END IF;
            expected:=jsonb_build_object('schema_version','knowledge-unit-review.v1',
                'id',NEW.id::text,'unit_id',NEW.unit_id::text,'unit_fingerprint',unit.fingerprint,
                'curriculum_version_id',unit.curriculum_version_id::text,'version',NEW.version,
                'actor_id',NEW.created_by::text,'state',NEW.state,'confirmed_mapping',NEW.confirmed_mapping,
                'curriculum_unit_id',NEW.curriculum_unit_id::text,'lesson_id',NEW.lesson_id::text,
                'competency_id',NEW.competency_id::text,'skill_id',NEW.skill_id::text,
                'sub_skill_id',NEW.sub_skill_id::text,'learning_concept_id',NEW.learning_concept_id::text,
                'reason',NEW.reason);
            IF NEW.payload IS DISTINCT FROM expected THEN
                RAISE EXCEPTION 'knowledge review payload differs from its indexed identities'
                    USING ERRCODE='23514'; END IF;
            RETURN NEW;
        END; $$;
    """)
    op.execute("""
        CREATE TRIGGER validate_knowledge_unit_review_trigger
            BEFORE INSERT ON public.knowledge_unit_reviews
            FOR EACH ROW EXECUTE FUNCTION public.validate_knowledge_unit_review();
    """)
    op.execute("""
        CREATE FUNCTION public.validate_knowledge_unit_review_audit() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE audit public.admin_audit_events%ROWTYPE;
        BEGIN
            SELECT * INTO audit FROM public.admin_audit_events WHERE id=NEW.audit_event_id;
            IF audit.id IS NULL OR audit.actor_id<>NEW.created_by
                OR audit.resource_id<>NEW.unit_id OR audit.resource_type<>'knowledge_unit_review'
                OR audit.action<>'verified_knowledge.mapping_'||NEW.state
                OR audit.payload IS DISTINCT FROM NEW.payload
            THEN RAISE EXCEPTION 'knowledge review requires exact immutable audit evidence'
                USING ERRCODE='23514'; END IF;
            RETURN NULL;
        END; $$;
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER knowledge_unit_review_audit
            AFTER INSERT ON public.knowledge_unit_reviews DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION public.validate_knowledge_unit_review_audit();
    """)
    op.execute("""
        CREATE TRIGGER immutable_knowledge_unit_reviews
            BEFORE UPDATE OR DELETE ON public.knowledge_unit_reviews
            FOR EACH ROW EXECUTE FUNCTION public.reject_source_fidelity_mutation();
    """)
    op.execute("""
        CREATE TRIGGER immutable_knowledge_unit_reviews_truncate
            BEFORE TRUNCATE ON public.knowledge_unit_reviews
            FOR EACH STATEMENT EXECUTE FUNCTION public.reject_source_fidelity_mutation();
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM public.knowledge_unit_reviews) THEN
                RAISE EXCEPTION 'cannot discard knowledge unit review history'
                    USING ERRCODE='23514';
            END IF;
        END; $$;
    """)
    op.execute("DROP FUNCTION public.knowledge_projection_is_eligible(uuid)")
    op.execute("DROP FUNCTION public.knowledge_unit_review_is_eligible(uuid)")
    op.drop_table("knowledge_unit_reviews")
    op.execute("DROP FUNCTION public.validate_knowledge_unit_review_audit()")
    op.execute("DROP FUNCTION public.validate_knowledge_unit_review()")
    op.execute(
        "DROP FUNCTION public.knowledge_review_scope_valid(uuid,uuid,uuid,uuid,uuid,uuid,uuid)"
    )
    op.execute("DROP FUNCTION public.lock_knowledge_review_taxonomy(uuid,uuid,uuid,uuid,uuid,uuid)")
    op.execute("DROP FUNCTION public.knowledge_review_reason_valid(text)")

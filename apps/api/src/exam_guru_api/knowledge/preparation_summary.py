from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.domain import Permission, Principal, authorize
from exam_guru_api.documents.service import SourceDocumentNotFoundError


class MaterialKnowledgePreparationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    document_id: UUID
    requested: bool
    source_ready: bool
    scope_ready: bool
    status: Literal[
        "not_requested", "waiting", "preparing", "prepared", "needs_attention", "removed"
    ]
    verified_pages: int = Field(ge=0)
    prepared_pages: int = Field(ge=0)
    unit_count: int = Field(ge=0)
    projection_count: int = Field(ge=0)
    pending_pages: int = Field(ge=0)
    failed_pages: int = Field(ge=0)


async def get_material_knowledge_preparation(
    session: AsyncSession, *, principal: Principal, document_id: UUID
) -> MaterialKnowledgePreparationResponse:
    authorize(principal, Permission.SOURCE_READ)
    authorize(principal, Permission.KNOWLEDGE_READ)
    with session.no_autoflush:
        row = (
            (
                await session.execute(
                    text("""
            WITH document AS MATERIALIZED (
                SELECT d.id,d.active_for_ai,
                    EXISTS (SELECT 1 FROM public.material_knowledge_requests r
                        WHERE r.document_id=d.id AND r.source_sha256=d.checksum_sha256)
                        AS requested,
                    public.source_understanding_document_is_resolved(d.id) AS source_ready,
                    public.knowledge_scope_snapshot(d.id) AS scope
                FROM public.source_documents d WHERE d.id=:id AND NOT d.quarantined_for_teacher_use
            ), pages AS MATERIALIZED (
                SELECT p.current_trusted_id AS trusted_id FROM public.source_understanding_pages p
                JOIN document d ON d.id=p.document_id
                WHERE p.state='verified'
                    AND public.trusted_page_knowledge_is_current(p.current_trusted_id)
            )
            SELECT d.id,d.active_for_ai,d.requested,d.source_ready,
                d.scope IS NOT NULL AS scope_ready,
                count(p.trusted_id)::integer AS verified_pages,
                count(*) FILTER (WHERE c.complete)::integer AS prepared_pages,
                coalesce(sum(c.unit_count),0)::integer AS unit_count,
                coalesce(sum(c.projection_count),0)::integer AS projection_count,
                count(*) FILTER (WHERE j.status='failed' AND NOT coalesce(c.complete,false))
                    ::integer AS failed_pages
            FROM document d LEFT JOIN pages p ON true
            LEFT JOIN LATERAL public.knowledge_preparation_page_counts(
                p.trusted_id,public.source_understanding_fingerprint(d.scope)) c
                ON d.source_ready AND d.scope IS NOT NULL
            LEFT JOIN public.knowledge_preparation_jobs j ON j.trusted_page_id=p.trusted_id
                AND j.scope_fingerprint=public.source_understanding_fingerprint(d.scope)
                AND j.derivation_version='page-region-components.v1'
                AND j.transformation_version='source-observation-meaning.v1'
                AND d.source_ready AND d.scope IS NOT NULL
            GROUP BY d.id,d.active_for_ai,d.requested,d.source_ready,d.scope
        """),
                    {"id": document_id},
                )
            )
            .mappings()
            .one_or_none()
        )
    if row is None:
        raise SourceDocumentNotFoundError(document_id)
    status: Literal[
        "not_requested", "waiting", "preparing", "prepared", "needs_attention", "removed"
    ]
    if not row["active_for_ai"]:
        status = "removed"
    elif not row["requested"]:
        status = "not_requested"
    elif not row["source_ready"] or not row["scope_ready"]:
        status = "waiting"
    elif row["failed_pages"]:
        status = "needs_attention"
    elif row["verified_pages"] > 0 and row["prepared_pages"] == row["verified_pages"]:
        status = "prepared"
    else:
        status = "preparing"
    return MaterialKnowledgePreparationResponse(
        document_id=document_id,
        requested=row["requested"],
        source_ready=row["source_ready"],
        scope_ready=row["scope_ready"],
        status=status,
        verified_pages=row["verified_pages"],
        prepared_pages=row["prepared_pages"],
        unit_count=row["unit_count"],
        projection_count=row["projection_count"],
        failed_pages=row["failed_pages"],
        pending_pages=max(0, row["verified_pages"] - row["prepared_pages"] - row["failed_pages"])
        if row["requested"]
        else 0,
    )

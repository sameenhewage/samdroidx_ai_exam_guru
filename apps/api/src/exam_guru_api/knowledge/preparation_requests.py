from uuid import UUID, uuid4

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.domain import Permission, Principal, authorize
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.knowledge.preparation_models import MaterialKnowledgeRequestModel


class MaterialKnowledgeRequestRecorder:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def __call__(
        self,
        *,
        principal: Principal,
        document_id: UUID,
        source_sha256: str,
        source_audit_event_id: UUID,
    ) -> None:
        authorize(principal, Permission.SOURCE_TRUST)
        authorize(principal, Permission.KNOWLEDGE_WRITE)
        request_id, audit_id = uuid4(), uuid4()
        inserted = await self.session.scalar(
            insert(MaterialKnowledgeRequestModel)
            .values(
                id=request_id,
                document_id=document_id,
                source_sha256=source_sha256,
                requested_by=principal.subject_id,
                source_audit_event_id=source_audit_event_id,
                audit_event_id=audit_id,
            )
            .on_conflict_do_nothing(index_elements=["document_id"])
            .returning(MaterialKnowledgeRequestModel.id)
        )
        if inserted is not None:
            self.session.add(
                AdminAuditEventModel(
                    id=audit_id,
                    actor_id=principal.subject_id,
                    resource_type="material_knowledge",
                    resource_id=document_id,
                    action="material_knowledge.requested",
                    payload={
                        "request_id": str(request_id),
                        "source_sha256": source_sha256,
                        "source_audit_event_id": str(source_audit_event_id),
                    },
                )
            )
            await self.session.flush()

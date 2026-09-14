from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import Field
from sqlalchemy import and_, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from exam_guru_api.auth.domain import Permission, Principal, authorize
from exam_guru_api.core.config import Settings
from exam_guru_api.curriculum.models import CurriculumLessonModel, CurriculumUnitModel
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.documents.understanding_contracts import UnderstandingModel
from exam_guru_api.knowledge.material_index_models import MaterialKnowledgeIndexIntentModel
from exam_guru_api.knowledge.material_indexing import (
    MaterialKnowledgeError,
    MaterialKnowledgeIndexingStatus,
    MaterialKnowledgeIndexRetryRequest,
    MaterialKnowledgeNotFoundError,
    bound_job,
    index_audit,
    index_current,
    index_transition,
    indexing_status,
    intent_fingerprint,
    locked_intent,
    material_embedding_config,
    retry_allowed,
    source_lock,
)
from exam_guru_api.knowledge.unit_models import KnowledgeProjectionModel, KnowledgeUnitModel
from exam_guru_api.knowledge.unit_review import (
    KnowledgeReviewRequest,
    KnowledgeUnitReview,
    KnowledgeUnitReviewService,
    KnowledgeUnitWorkspace,
    _snapshot,
)
from exam_guru_api.knowledge.unit_review_models import KnowledgeUnitReviewModel
from exam_guru_api.knowledge.unit_service import _unit
from exam_guru_api.retrieval.embeddings import EmbeddingProviderRegistry


class MaterialKnowledgeUnitSummary(UnderstandingModel):
    unit_id: UUID
    page_number: int
    sequence: int
    source_curriculum_unit_id: UUID | None
    source_lesson_id: UUID | None
    source_unit_title: str | None
    source_lesson_title: str | None
    review: KnowledgeUnitReview | None
    has_projection: bool
    indexing: MaterialKnowledgeIndexingStatus


class MaterialKnowledgeUnitsResponse(UnderstandingModel):
    document_id: UUID
    curriculum_version_id: UUID | None
    source_current: bool
    total: int
    limit: int = Field(ge=1, le=25)
    offset: int = Field(ge=0)
    items: list[MaterialKnowledgeUnitSummary] = Field(max_length=25)


class MaterialKnowledgeUnitWorkspace(UnderstandingModel):
    workspace: KnowledgeUnitWorkspace
    indexing: MaterialKnowledgeIndexingStatus


class MaterialKnowledgeReviewService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _document(self, document_id: UUID) -> SourceDocumentModel:
        await self.session.execute(text("SET LOCAL statement_timeout = '30s'"))
        await self.session.execute(text("SET LOCAL lock_timeout = '5s'"))
        document = await self.session.scalar(
            select(SourceDocumentModel)
            .where(
                SourceDocumentModel.id == document_id,
                SourceDocumentModel.quarantined_for_teacher_use.is_(False),
            )
            .execution_options(populate_existing=True)
        )
        if document is None:
            raise MaterialKnowledgeNotFoundError("source_document_not_found")
        return document

    async def _member(self, document_id: UUID, unit_id: UUID) -> KnowledgeUnitModel:
        document = await self._document(document_id)
        row = await self.session.scalar(
            select(KnowledgeUnitModel)
            .where(
                KnowledgeUnitModel.id == unit_id,
                KnowledgeUnitModel.document_id == document_id,
                KnowledgeUnitModel.curriculum_version_id == document.curriculum_version_id,
            )
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise MaterialKnowledgeNotFoundError("material_knowledge_unit_not_found")
        return row

    async def _latest_intent(self, unit_id: UUID) -> MaterialKnowledgeIndexIntentModel | None:
        result = await self.session.scalar(
            select(MaterialKnowledgeIndexIntentModel)
            .where(
                MaterialKnowledgeIndexIntentModel.unit_id == unit_id,
            )
            .order_by(MaterialKnowledgeIndexIntentModel.review_version.desc())
            .limit(1)
            .execution_options(populate_existing=True)
        )
        return result if isinstance(result, MaterialKnowledgeIndexIntentModel) else None

    async def review(
        self,
        *,
        principal: Principal,
        document_id: UUID,
        unit_id: UUID,
        request: KnowledgeReviewRequest,
    ) -> KnowledgeUnitReview:
        authorize(principal, Permission.SOURCE_READ)
        authorize(principal, Permission.KNOWLEDGE_WRITE)
        request = KnowledgeReviewRequest.model_validate(request)
        try:
            await self._member(document_id, unit_id)
            await source_lock(self.session, document_id)
            row = await self._member(document_id, unit_id)
            _unit(row)
            if request.state == "reviewed" and not await self.session.scalar(
                select(func.knowledge_unit_is_current(unit_id))
            ):
                raise MaterialKnowledgeError("knowledge_review_source_not_current")
            reviewed = await KnowledgeUnitReviewService(self.session).review(
                principal=principal,
                unit_id=unit_id,
                request=request,
                curriculum_version_id=row.curriculum_version_id,
                commit=False,
            )
            prior = await self.session.scalar(
                select(MaterialKnowledgeIndexIntentModel)
                .where(
                    MaterialKnowledgeIndexIntentModel.unit_id == unit_id,
                    MaterialKnowledgeIndexIntentModel.status != "superseded",
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if prior is not None and prior.review_id != reviewed.id:
                await index_transition(
                    self.session,
                    prior,
                    "superseded",
                    event="superseded",
                    actor_id=principal.subject_id,
                    reason="The unit has a newer curriculum review",
                    failure_code="indexing_source_superseded",
                )
            if reviewed.state == "reviewed":
                await self._record_intent(row, reviewed)
            await self.session.commit()
            return reviewed
        except Exception:
            await self.session.rollback()
            raise

    async def _record_intent(self, unit: KnowledgeUnitModel, review: KnowledgeUnitReview) -> None:
        existing = await self.session.scalar(
            select(MaterialKnowledgeIndexIntentModel).where(
                MaterialKnowledgeIndexIntentModel.review_id == review.id,
            )
        )
        if existing is not None:
            if existing.review_fingerprint != review.fingerprint or existing.unit_id != unit.id:
                raise MaterialKnowledgeError("material_indexing_binding_invalid")
            return
        value = await self.session.scalar(
            select(func.material_knowledge_index_input(unit.id, review.id))
        )
        if not isinstance(value, dict):
            raise MaterialKnowledgeError("material_indexing_binding_invalid")
        now = datetime.now(UTC)
        projection_id = None if value["projection_id"] is None else UUID(value["projection_id"])
        intent = MaterialKnowledgeIndexIntentModel(
            id=uuid4(),
            document_id=unit.document_id,
            unit_id=unit.id,
            review_id=review.id,
            review_version=review.version,
            review_fingerprint=review.fingerprint,
            curriculum_version_id=unit.curriculum_version_id,
            projection_id=projection_id,
            input_snapshot=value,
            input_fingerprint=intent_fingerprint(value),
            requested_by=review.actor_id,
            status="not_searchable" if projection_id is None else "pending",
            version=0,
            attempt_number=0,
            dispatch_key=None,
            config_snapshot=None,
            config_snapshot_fingerprint=None,
            embedding_job_id=None,
            failure_code=None,
            event="not_searchable" if projection_id is None else "requested",
            reason=review.reason,
            confirmed_retry=False,
            updated_by=review.actor_id,
            previous_audit_event_id=None,
            created_at=now,
            updated_at=now,
        )
        audit = index_audit(intent)
        intent.audit_event_id = audit.id
        self.session.add_all((audit, intent))
        await self.session.flush()

    async def get_workspace(
        self,
        *,
        principal: Principal,
        document_id: UUID,
        unit_id: UUID,
        settings: Settings,
        providers: EmbeddingProviderRegistry,
    ) -> MaterialKnowledgeUnitWorkspace:
        authorize(principal, Permission.SOURCE_READ)
        authorize(principal, Permission.KNOWLEDGE_READ)
        with self.session.no_autoflush:
            row = await self._member(document_id, unit_id)
            workspace = await KnowledgeUnitReviewService(self.session).get_workspace(
                principal=principal,
                unit_id=unit_id,
                curriculum_version_id=row.curriculum_version_id,
            )
            projection_id = await self.session.scalar(
                select(KnowledgeProjectionModel.id).where(
                    KnowledgeProjectionModel.unit_id == unit_id,
                    KnowledgeProjectionModel.transformation_version
                    == "source-observation-meaning.v1",
                )
            )
            return MaterialKnowledgeUnitWorkspace(
                workspace=workspace,
                indexing=await indexing_status(
                    self.session,
                    intent=await self._latest_intent(unit_id),
                    review_id=None if workspace.review is None else workspace.review.id,
                    eligible=workspace.eligible and workspace.source_current,
                    projection_id=projection_id,
                    config=material_embedding_config(settings, providers),
                ),
            )

    async def list_units(
        self,
        *,
        principal: Principal,
        document_id: UUID,
        settings: Settings,
        providers: EmbeddingProviderRegistry,
        limit: int = 20,
        offset: int = 0,
    ) -> MaterialKnowledgeUnitsResponse:
        authorize(principal, Permission.SOURCE_READ)
        authorize(principal, Permission.KNOWLEDGE_READ)
        if (
            type(limit) is not int
            or not 1 <= limit <= 25
            or type(offset) is not int
            or not 0 <= offset <= 2_147_483_647
        ):
            raise MaterialKnowledgeError("invalid_material_knowledge_pagination")
        with self.session.no_autoflush:
            document = await self._document(document_id)
            source_current = bool(
                await self.session.scalar(
                    select(
                        and_(
                            func.source_understanding_document_is_resolved(document_id).is_(True),
                            func.knowledge_scope_snapshot(document_id).is_not(None),
                        )
                    )
                )
            )
            filters = (
                KnowledgeUnitModel.document_id == document_id,
                KnowledgeUnitModel.curriculum_version_id == document.curriculum_version_id,
                func.knowledge_unit_is_current(KnowledgeUnitModel.id).is_(True),
            )
            total = int(
                await self.session.scalar(
                    select(func.count()).select_from(KnowledgeUnitModel).where(*filters)
                )
                or 0
            )
            previous = aliased(KnowledgeUnitReviewModel)
            latest_version = (
                select(func.max(previous.version))
                .where(previous.unit_id == KnowledgeUnitModel.id)
                .correlate(KnowledgeUnitModel)
                .scalar_subquery()
            )
            rows = (
                await self.session.execute(
                    select(
                        KnowledgeUnitModel.id,
                        KnowledgeUnitModel.page_number,
                        KnowledgeUnitModel.sequence,
                        KnowledgeUnitModel.curriculum_unit_id,
                        KnowledgeUnitModel.lesson_id,
                        CurriculumUnitModel.title.label("unit_title"),
                        CurriculumLessonModel.title.label("lesson_title"),
                        KnowledgeUnitReviewModel,
                        KnowledgeProjectionModel.id.label("projection_id"),
                        func.knowledge_unit_review_is_eligible(KnowledgeUnitReviewModel.id).label(
                            "eligible"
                        ),
                    )
                    .outerjoin(
                        CurriculumUnitModel,
                        CurriculumUnitModel.id == KnowledgeUnitModel.curriculum_unit_id,
                    )
                    .outerjoin(
                        CurriculumLessonModel,
                        CurriculumLessonModel.id == KnowledgeUnitModel.lesson_id,
                    )
                    .outerjoin(
                        KnowledgeUnitReviewModel,
                        and_(
                            KnowledgeUnitReviewModel.unit_id == KnowledgeUnitModel.id,
                            KnowledgeUnitReviewModel.version == latest_version,
                        ),
                    )
                    .outerjoin(
                        KnowledgeProjectionModel,
                        and_(
                            KnowledgeProjectionModel.unit_id == KnowledgeUnitModel.id,
                            KnowledgeProjectionModel.transformation_version
                            == "source-observation-meaning.v1",
                        ),
                    )
                    .where(*filters)
                    .order_by(
                        KnowledgeUnitModel.page_number,
                        KnowledgeUnitModel.sequence,
                        KnowledgeUnitModel.id,
                    )
                    .offset(offset)
                    .limit(limit)
                )
            ).all()
            config = material_embedding_config(settings, providers)
            items: list[MaterialKnowledgeUnitSummary] = []
            for row in rows:
                review = (
                    None
                    if row.KnowledgeUnitReviewModel is None
                    else _snapshot(row.KnowledgeUnitReviewModel)
                )
                items.append(
                    MaterialKnowledgeUnitSummary(
                        unit_id=row.id,
                        page_number=row.page_number,
                        sequence=row.sequence,
                        source_curriculum_unit_id=row.curriculum_unit_id,
                        source_lesson_id=row.lesson_id,
                        source_unit_title=row.unit_title,
                        source_lesson_title=row.lesson_title,
                        review=review,
                        has_projection=row.projection_id is not None,
                        indexing=await indexing_status(
                            self.session,
                            intent=await self._latest_intent(row.id),
                            review_id=None if review is None else review.id,
                            eligible=bool(row.eligible),
                            projection_id=row.projection_id,
                            config=config,
                        ),
                    )
                )
            return MaterialKnowledgeUnitsResponse(
                document_id=document_id,
                curriculum_version_id=document.curriculum_version_id,
                source_current=source_current,
                total=total,
                limit=limit,
                offset=offset,
                items=items,
            )

    async def retry(
        self,
        *,
        principal: Principal,
        document_id: UUID,
        unit_id: UUID,
        request: MaterialKnowledgeIndexRetryRequest,
        settings: Settings,
        providers: EmbeddingProviderRegistry,
    ) -> None:
        authorize(principal, Permission.SOURCE_READ)
        authorize(principal, Permission.KNOWLEDGE_WRITE)
        request = MaterialKnowledgeIndexRetryRequest.model_validate(request)
        if not request.confirmed_retry:
            raise MaterialKnowledgeError("material_indexing_retry_requires_confirmation")
        try:
            await self._member(document_id, unit_id)
            intent = await self._latest_intent(unit_id)
            if intent is None:
                raise MaterialKnowledgeError("material_indexing_retry_not_allowed")
            intent = await locked_intent(self.session, intent.id)
            if intent.version != request.expected_version:
                raise MaterialKnowledgeError("material_indexing_version_conflict")
            if not await index_current(self.session, intent):
                raise MaterialKnowledgeError("material_indexing_source_not_current")
            job = await bound_job(self.session, intent)
            config = material_embedding_config(settings, providers)
            if not await retry_allowed(self.session, intent, config, job):
                raise MaterialKnowledgeError("material_indexing_retry_not_allowed")
            await index_transition(
                self.session,
                intent,
                "dispatching",
                event="retry_approved",
                reason=request.reason,
                actor_id=principal.subject_id,
                config=config,
            )
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

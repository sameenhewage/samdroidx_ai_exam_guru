import hashlib
import json
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.domain import Permission, Principal, authorize
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.documents.fidelity_service import _reason
from exam_guru_api.documents.understanding_contracts import UnderstandingModel, _canonical_bytes
from exam_guru_api.documents.understanding_verification import Checksum
from exam_guru_api.knowledge.unit_models import KnowledgeUnitModel
from exam_guru_api.knowledge.unit_review_models import KnowledgeUnitReviewModel
from exam_guru_api.knowledge.unit_service import _unit
from exam_guru_api.knowledge.units import KnowledgeUnit


class KnowledgeUnitReviewError(ValueError):
    pass


class _ReviewFields(UnderstandingModel):
    state: Literal["reviewed", "rejected"]
    confirmed_mapping: bool
    curriculum_unit_id: UUID | None = None
    lesson_id: UUID | None = None
    competency_id: UUID | None = None
    skill_id: UUID | None = None
    sub_skill_id: UUID | None = None
    learning_concept_id: UUID | None = None
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def review_reason(cls, value: str) -> str:
        return _reason(value)

    @model_validator(mode="after")
    def explicit_mapping(self) -> Self:
        if self.confirmed_mapping != (self.state == "reviewed"):
            raise KnowledgeUnitReviewError("knowledge_mapping_requires_explicit_confirmation")
        if self.state == "reviewed" and self.competency_id is None:
            raise KnowledgeUnitReviewError("knowledge_mapping_requires_competency")
        if self.state == "rejected" and any(
            value is not None
            for value in (
                self.curriculum_unit_id,
                self.lesson_id,
                self.competency_id,
                self.skill_id,
                self.sub_skill_id,
                self.learning_concept_id,
            )
        ):
            raise KnowledgeUnitReviewError("rejected_mapping_cannot_supply_classification")
        for child, parent in (
            (self.lesson_id, self.curriculum_unit_id),
            (self.skill_id, self.competency_id),
            (self.sub_skill_id, self.skill_id),
            (self.learning_concept_id, self.sub_skill_id),
        ):
            if child is not None and parent is None:
                raise KnowledgeUnitReviewError("knowledge_mapping_requires_parent_scope")
        return self


class KnowledgeReviewRequest(_ReviewFields):
    expected_version: int = Field(ge=0, le=2_147_483_645)


class KnowledgeUnitReview(_ReviewFields):
    schema_version: Literal["knowledge-unit-review.v1"] = "knowledge-unit-review.v1"
    id: UUID
    unit_id: UUID
    unit_fingerprint: Checksum
    curriculum_version_id: UUID
    version: int = Field(ge=1, le=2_147_483_646)
    actor_id: UUID

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(_canonical_bytes(self)).hexdigest()


class KnowledgeUnitWorkspace(UnderstandingModel):
    unit: KnowledgeUnit = Field(repr=False)
    review: KnowledgeUnitReview | None
    source_current: bool
    eligible: bool


def _snapshot(row: KnowledgeUnitReviewModel) -> KnowledgeUnitReview:
    value = KnowledgeUnitReview.model_validate_json(json.dumps(row.payload, ensure_ascii=False))
    expected = KnowledgeUnitReviewModel.from_domain(value, audit_event_id=row.audit_event_id)
    if any(
        getattr(row, column.name) != getattr(expected, column.name)
        for column in row.__table__.columns
        if column.name != "created_at"
    ):
        raise KnowledgeUnitReviewError("stored_knowledge_review_invalid")
    return value


class KnowledgeUnitReviewService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def review(
        self,
        *,
        principal: Principal,
        unit_id: UUID,
        request: KnowledgeReviewRequest,
        curriculum_version_id: UUID | None = None,
        commit: bool = True,
    ) -> KnowledgeUnitReview:
        authorize(principal, Permission.KNOWLEDGE_WRITE)
        request = KnowledgeReviewRequest.model_validate(request)
        try:
            row = await self._load_unit(unit_id, curriculum_version_id)
            await self.session.execute(select(func.lock_knowledge_unit_source(row.document_id)))
            unit = _unit(row)
            latest = await self._latest(unit.id)
            fields = request.model_dump(exclude={"expected_version"})
            if (
                latest is not None
                and latest.version == request.expected_version + 1
                and latest.actor_id == principal.subject_id
                and all(getattr(latest, key) == value for key, value in fields.items())
            ):
                if commit:
                    await self.session.commit()
                return latest
            if (0 if latest is None else latest.version) != request.expected_version:
                raise KnowledgeUnitReviewError("knowledge_review_version_conflict")
            await self.session.execute(
                select(
                    func.lock_knowledge_review_taxonomy(
                        request.curriculum_unit_id,
                        request.lesson_id,
                        request.competency_id,
                        request.skill_id,
                        request.sub_skill_id,
                        request.learning_concept_id,
                    )
                )
            )
            if request.state == "reviewed":
                if not await self.session.scalar(select(func.knowledge_unit_is_current(unit.id))):
                    raise KnowledgeUnitReviewError("knowledge_review_source_not_current")
                if not await self.session.scalar(
                    select(
                        func.knowledge_review_scope_valid(
                            unit.id,
                            request.curriculum_unit_id,
                            request.lesson_id,
                            request.competency_id,
                            request.skill_id,
                            request.sub_skill_id,
                            request.learning_concept_id,
                        )
                    )
                ):
                    raise KnowledgeUnitReviewError("knowledge_review_taxonomy_invalid")
            reviewed = KnowledgeUnitReview(
                id=uuid4(),
                unit_id=unit.id,
                unit_fingerprint=unit.fingerprint,
                curriculum_version_id=unit.scope.curriculum_version_id,
                version=request.expected_version + 1,
                actor_id=principal.subject_id,
                **fields,
            )
            audit = AdminAuditEventModel(
                id=uuid4(),
                actor_id=principal.subject_id,
                resource_type="knowledge_unit_review",
                resource_id=unit.id,
                action="verified_knowledge.mapping_" + reviewed.state,
                payload=reviewed.model_dump(mode="json"),
            )
            self.session.add(audit)
            await self.session.flush()
            self.session.add(
                KnowledgeUnitReviewModel.from_domain(reviewed, audit_event_id=audit.id)
            )
            if commit:
                await self.session.commit()
            else:
                await self.session.flush()
            return reviewed
        except Exception:
            if commit:
                await self.session.rollback()
            raise

    async def _load_unit(
        self, unit_id: UUID, curriculum_version_id: UUID | None
    ) -> KnowledgeUnitModel:
        statement = select(KnowledgeUnitModel).where(KnowledgeUnitModel.id == unit_id)
        if curriculum_version_id is not None:
            statement = statement.where(
                KnowledgeUnitModel.curriculum_version_id == curriculum_version_id
            )
        row = await self.session.scalar(statement.execution_options(populate_existing=True))
        if row is None:
            raise KnowledgeUnitReviewError("knowledge_unit_not_found")
        return row

    async def _latest(self, unit_id: UUID) -> KnowledgeUnitReview | None:
        row = await self.session.scalar(
            select(KnowledgeUnitReviewModel)
            .where(KnowledgeUnitReviewModel.unit_id == unit_id)
            .order_by(KnowledgeUnitReviewModel.version.desc())
            .limit(1)
        )
        return None if row is None else _snapshot(row)

    async def get_workspace(
        self, *, principal: Principal, unit_id: UUID, curriculum_version_id: UUID
    ) -> KnowledgeUnitWorkspace:
        authorize(principal, Permission.KNOWLEDGE_READ)
        unit = _unit(await self._load_unit(unit_id, curriculum_version_id))
        latest = await self._latest(unit_id)
        return KnowledgeUnitWorkspace(
            unit=unit,
            review=latest,
            source_current=bool(
                await self.session.scalar(select(func.knowledge_unit_is_current(unit_id)))
            ),
            eligible=latest is not None
            and bool(
                await self.session.scalar(select(func.knowledge_unit_review_is_eligible(latest.id)))
            ),
        )

    async def get_review(self, *, principal: Principal, review_id: UUID) -> KnowledgeUnitReview:
        authorize(principal, Permission.KNOWLEDGE_READ)
        row = await self.session.get(KnowledgeUnitReviewModel, review_id, populate_existing=True)
        if row is None:
            raise KnowledgeUnitReviewError("knowledge_review_not_found")
        return _snapshot(row)

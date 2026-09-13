import hashlib
import json
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.domain import Permission, Principal, authorize
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.documents.understanding_contracts import _canonical_bytes
from exam_guru_api.documents.understanding_models import (
    PageUnderstandingStateModel,
    TrustedPageKnowledgeModel,
)
from exam_guru_api.documents.understanding_verification import TrustedPageKnowledge
from exam_guru_api.knowledge.unit_models import (
    KnowledgeProjectionModel,
    KnowledgeUnitModel,
    KnowledgeUnitRegionModel,
)
from exam_guru_api.knowledge.units import (
    KnowledgeProjection,
    KnowledgeScope,
    KnowledgeUnit,
    UnsearchableKnowledgeUnitError,
    derive_knowledge_units,
    project_knowledge_unit,
)


class KnowledgePreparationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PreparedPageKnowledge:
    units: tuple[KnowledgeUnit, ...]
    projections: tuple[KnowledgeProjection, ...]
    unsearchable_unit_ids: tuple[UUID, ...]
    created: bool


def _matching_columns(
    row: KnowledgeUnitModel | KnowledgeProjectionModel,
    expected: KnowledgeUnitModel | KnowledgeProjectionModel,
) -> bool:
    return all(
        getattr(row, column.name) == getattr(expected, column.name)
        for column in row.__table__.columns
        if column.name not in {"created_at", "created_by", "audit_event_id"}
    )


def _unit(row: KnowledgeUnitModel) -> KnowledgeUnit:
    unit = KnowledgeUnit.model_validate_json(json.dumps(row.payload, ensure_ascii=False))
    expected = KnowledgeUnitModel.from_domain(
        unit, actor_id=row.created_by, audit_event_id=row.audit_event_id
    )
    if not _matching_columns(row, expected):
        raise KnowledgePreparationError("stored_knowledge_unit_invalid")
    return unit


def _projection(row: KnowledgeProjectionModel) -> KnowledgeProjection:
    value = KnowledgeProjection.model_validate_json(json.dumps(row.payload, ensure_ascii=False))
    expected = KnowledgeProjectionModel.from_domain(
        value, actor_id=row.created_by, audit_event_id=row.audit_event_id
    )
    if not _matching_columns(row, expected):
        raise KnowledgePreparationError("stored_knowledge_projection_invalid")
    return value


class KnowledgeUnitService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def prepare_page(
        self,
        *,
        principal: Principal,
        document_id: UUID,
        page_number: int,
        expected_trusted_page_id: UUID,
        commit: bool = True,
    ) -> PreparedPageKnowledge:
        authorize(principal, Permission.KNOWLEDGE_WRITE)
        if type(page_number) is not int or page_number < 1:
            raise KnowledgePreparationError("knowledge_page_invalid")
        try:
            await self.session.execute(select(func.lock_knowledge_unit_source(document_id)))
            scope_payload = await self.session.scalar(
                select(func.knowledge_scope_snapshot(document_id))
            )
            if scope_payload is None:
                raise KnowledgePreparationError("knowledge_scope_unavailable")
            scope = KnowledgeScope.model_validate_json(
                json.dumps(scope_payload, ensure_ascii=False)
            )
            if not await self.session.scalar(
                select(func.source_understanding_document_is_resolved(document_id))
            ):
                raise KnowledgePreparationError("source_document_unresolved")
            page = await self.session.get(
                PageUnderstandingStateModel, (document_id, page_number), populate_existing=True
            )
            if (
                page is None
                or page.state != "verified"
                or page.current_trusted_id != expected_trusted_page_id
            ):
                raise KnowledgePreparationError("trusted_page_changed")
            record = await self.session.get(TrustedPageKnowledgeModel, expected_trusted_page_id)
            if record is None:
                raise KnowledgePreparationError("trusted_page_unavailable")
            trusted = TrustedPageKnowledge.model_validate_json(
                json.dumps(record.payload, ensure_ascii=False)
            )
            if record.fingerprint != hashlib.sha256(_canonical_bytes(trusted)).hexdigest():
                raise KnowledgePreparationError("trusted_page_fingerprint_invalid")
            units = derive_knowledge_units(trusted, scope)
            projections: list[KnowledgeProjection] = []
            unsearchable: list[UUID] = []
            for unit in units:
                try:
                    projections.append(project_knowledge_unit(unit))
                except UnsearchableKnowledgeUnitError:
                    unsearchable.append(unit.id)
            existing = (
                await self.session.scalars(
                    select(KnowledgeUnitModel)
                    .where(KnowledgeUnitModel.id.in_([unit.id for unit in units]))
                    .order_by(KnowledgeUnitModel.sequence)
                )
            ).all()
            if existing:
                saved = tuple(_unit(row) for row in existing)
                projected = (
                    await self.session.scalars(
                        select(KnowledgeProjectionModel).where(
                            KnowledgeProjectionModel.unit_id.in_([unit.id for unit in units])
                        )
                    )
                ).all()
                by_id = {row.id: _projection(row) for row in projected}
                if (
                    saved != units
                    or set(by_id) != {item.id for item in projections}
                    or any(by_id[item.id] != item for item in projections)
                ):
                    raise KnowledgePreparationError("knowledge_preparation_conflict")
                if commit:
                    await self.session.commit()
                return PreparedPageKnowledge(units, tuple(projections), tuple(unsearchable), False)
            scope_fingerprint = hashlib.sha256(_canonical_bytes(scope)).hexdigest()
            audit = AdminAuditEventModel(
                id=uuid4(),
                actor_id=principal.subject_id,
                resource_type="verified_knowledge",
                resource_id=document_id,
                action="verified_knowledge.prepared",
                payload={
                    "page_number": page_number,
                    "source_sha256": trusted.source.source_sha256,
                    "trusted_page_id": str(trusted.id),
                    "scope_fingerprint": scope_fingerprint,
                    "unit_ids": [str(unit.id) for unit in units],
                    "projection_ids": [str(item.id) for item in projections],
                    "unsearchable_unit_ids": [str(identifier) for identifier in unsearchable],
                    "derivation_version": "page-region-components.v1",
                    "transformation_version": "source-observation-meaning.v1",
                },
            )
            self.session.add(audit)
            await self.session.flush()
            self.session.add_all(
                KnowledgeUnitModel.from_domain(
                    unit, actor_id=principal.subject_id, audit_event_id=audit.id
                )
                for unit in units
            )
            await self.session.flush()
            self.session.add_all(
                KnowledgeUnitRegionModel(
                    unit_id=unit.id,
                    region_id=region_id,
                    candidate_id=unit.candidate_id,
                    document_id=document_id,
                    page_number=page_number,
                    region_key=region.key,
                    ordinal=ordinal,
                )
                for unit in units
                for ordinal, (region_id, region) in enumerate(
                    zip(unit.region_ids, unit.observation.regions, strict=True)
                )
            )
            self.session.add_all(
                KnowledgeProjectionModel.from_domain(
                    item, actor_id=principal.subject_id, audit_event_id=audit.id
                )
                for item in projections
            )
            if commit:
                await self.session.commit()
            else:
                await self.session.flush()
            return PreparedPageKnowledge(units, tuple(projections), tuple(unsearchable), True)
        except Exception:
            if commit:
                await self.session.rollback()
            raise

    async def get_unit(self, *, principal: Principal, unit_id: UUID) -> KnowledgeUnit:
        authorize(principal, Permission.KNOWLEDGE_READ)
        row = await self.session.get(KnowledgeUnitModel, unit_id, populate_existing=True)
        if row is None:
            raise KnowledgePreparationError("knowledge_unit_not_found")
        return _unit(row)

    async def list_current_projections(
        self, *, principal: Principal, curriculum_version_id: UUID, limit: int = 100
    ) -> tuple[KnowledgeProjection, ...]:
        authorize(principal, Permission.KNOWLEDGE_READ)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise KnowledgePreparationError("knowledge_projection_limit_invalid")
        rows = await self.session.scalars(
            select(KnowledgeProjectionModel)
            .join(KnowledgeUnitModel, KnowledgeUnitModel.id == KnowledgeProjectionModel.unit_id)
            .where(
                KnowledgeUnitModel.curriculum_version_id == curriculum_version_id,
                func.knowledge_projection_is_current(KnowledgeProjectionModel.id).is_(True),
            )
            .order_by(
                KnowledgeUnitModel.document_id,
                KnowledgeUnitModel.page_number,
                KnowledgeUnitModel.sequence,
                KnowledgeProjectionModel.id,
            )
            .limit(limit)
        )
        return tuple(_projection(row) for row in rows)

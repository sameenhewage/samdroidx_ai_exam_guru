import hashlib
import json
import re
import unicodedata
from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID, uuid4

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    model_validator,
)
from sqlalchemy import Boolean, ColumnElement, Select, SQLColumnExpression, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.domain import Permission, Principal, authorize
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.curriculum.admission_models import (
    CatalogueAdmissionCurrentModel,
    CatalogueAdmissionDecisionModel,
)
from exam_guru_api.curriculum.domain import LEGACY_UNCLASSIFIED_SUBJECT_ID
from exam_guru_api.curriculum.models import (
    CurriculumVersionModel,
    ExamConfigurationModel,
    MediumModel,
    SubjectModel,
)

AdmissionState = Literal["approved", "rejected", "quarantined"]
ScopeFingerprint = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
_INTERNAL_MARKER = re.compile(
    r"(^|[^a-z0-9])(e2e[a-z0-9]*|fixtures?[0-9]*|internal|smoke|synthetic)([^a-z0-9]|$)",
    re.IGNORECASE,
)
_TECHNICAL_LABEL = re.compile(
    r"(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|(?:sha256:)?[0-9a-f]{64})",
    re.IGNORECASE,
)


def _bounded_text(value: str) -> str:
    if value != value.strip() or any(unicodedata.category(char) in {"Cc", "Cs"} for char in value):
        raise ValueError("Use trimmed readable text without control characters.")
    return value


DecisionText = Annotated[
    str, StringConstraints(min_length=1, max_length=1024), AfterValidator(_bounded_text)
]


class AdmissionDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: AdmissionState
    expected_version: Annotated[int, Field(strict=True, ge=0, le=2_147_483_646)]
    expected_scope_fingerprint: ScopeFingerprint
    educational_approval: StrictBool
    reason: DecisionText
    source_reference: DecisionText
    evidence: Annotated[tuple[DecisionText, ...], Field(min_length=1, max_length=16)]

    @model_validator(mode="after")
    def require_explicit_educational_approval(self) -> Self:
        if self.educational_approval != (self.state == "approved"):
            raise ValueError("Only approval requires an explicit positive educational review.")
        return self


class CatalogueScopeSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["catalogue-scope.v1"] = "catalogue-scope.v1"
    curriculum_version_id: UUID
    curriculum_code: str
    curriculum_title: str
    exam_configuration_id: UUID
    exam_configuration_code: str
    exam_configuration_name: str
    grade: Annotated[int, Field(ge=1, le=13)]
    medium_id: UUID
    medium_code: str
    medium_name: str
    subject_id: UUID
    subject_code: str
    subject_name: str


class CatalogueAdmissionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)

    id: UUID
    curriculum_version_id: UUID
    version: Annotated[int, Field(ge=1)]
    state: AdmissionState
    scope_fingerprint: ScopeFingerprint
    scope_snapshot: CatalogueScopeSnapshot
    educational_approval: bool
    reason: str
    source_reference: str
    evidence: tuple[str, ...]
    actor_id: UUID
    decided_at: datetime


class CatalogueAdmissionReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    curriculum_version_id: UUID
    scope: CatalogueScopeSnapshot
    scope_fingerprint: ScopeFingerprint
    active_chain: bool
    labels_approvable: bool
    version: Annotated[int, Field(ge=0)]
    state: Literal["unreviewed", "approved", "rejected", "quarantined"]
    admitted: bool
    stale: bool
    latest_decision: CatalogueAdmissionDecision | None


class MaterialCatalogueEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    curriculum_version_id: UUID
    curriculum_title: str
    exam_configuration_id: UUID
    exam_configuration_name: str
    grade: Annotated[int, Field(ge=1, le=13)]
    grade_label: str
    medium_id: UUID
    medium_name: str
    subject_id: UUID
    subject_name: str


class CatalogueScopeNotFoundError(LookupError):
    def __init__(self, curriculum_id: UUID) -> None:
        self.curriculum_id = curriculum_id
        super().__init__(str(curriculum_id))


class CatalogueAdmissionConflictError(RuntimeError):
    def __init__(self, code: str = "catalogue_admission_conflict") -> None:
        self.code = code
        super().__init__(code)


class CatalogueApprovalError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class CurriculumNotAdmittedError(ValueError):
    def __init__(self, curriculum_id: UUID | None, reason: str) -> None:
        self.curriculum_id = curriculum_id
        self.reason = reason
        self.code = "curriculum_not_admitted"
        super().__init__(f"{self.code}: {curriculum_id}: {reason}")


def catalogue_scope_fingerprint(scope: CatalogueScopeSnapshot) -> str:
    canonical = json.dumps(
        scope.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def scope_labels_are_approvable(scope: CatalogueScopeSnapshot) -> bool:
    if scope.subject_id == LEGACY_UNCLASSIFIED_SUBJECT_ID:
        return False
    labels = (
        scope.exam_configuration_name,
        scope.medium_name,
        scope.subject_name,
        scope.curriculum_title,
    )
    codes = (
        scope.exam_configuration_code,
        scope.medium_code,
        scope.subject_code,
        scope.curriculum_code,
    )
    for value in (*labels, *codes):
        try:
            _bounded_text(value)
        except ValueError:
            return False
        if (
            not 1 <= len(value) <= 255
            or not any(char.isalnum() for char in value)
            or _INTERNAL_MARKER.search(unicodedata.normalize("NFKC", value))
        ):
            return False
    return all(not _TECHNICAL_LABEL.fullmatch(value) for value in labels)


def admitted_curriculum_predicate(
    curriculum_id: UUID | SQLColumnExpression[UUID],
) -> ColumnElement[bool]:
    return func.catalogue_curriculum_is_admitted(curriculum_id, type_=Boolean())


def _scope_query() -> Select[
    tuple[UUID, str, str, UUID, str, str, int, UUID, str, str, UUID, str, str, bool]
]:
    cv, exam, medium, subject = (
        CurriculumVersionModel,
        ExamConfigurationModel,
        MediumModel,
        SubjectModel,
    )
    return (
        select(
            cv.id.label("curriculum_version_id"),
            cv.code.label("curriculum_code"),
            cv.title.label("curriculum_title"),
            exam.id.label("exam_configuration_id"),
            exam.code.label("exam_configuration_code"),
            exam.name.label("exam_configuration_name"),
            exam.grade.label("grade"),
            medium.id.label("medium_id"),
            medium.code.label("medium_code"),
            medium.name.label("medium_name"),
            subject.id.label("subject_id"),
            subject.code.label("subject_code"),
            subject.name.label("subject_name"),
            and_(cv.active, exam.active, medium.active, subject.active).label("active_chain"),
        )
        .select_from(cv)
        .join(exam, exam.id == cv.exam_configuration_id)
        .join(medium, medium.id == cv.medium_id)
        .join(subject, subject.id == cv.subject_id)
    )


async def _load_scope(
    session: AsyncSession, curriculum_id: UUID, *, lock: bool = False
) -> tuple[CatalogueScopeSnapshot, bool] | None:
    query = _scope_query().where(CurriculumVersionModel.id == curriculum_id)
    if lock:
        query = query.with_for_update(
            read=True,
            of=(CurriculumVersionModel, ExamConfigurationModel, MediumModel, SubjectModel),
        )
    record = (await session.execute(query)).mappings().one_or_none()
    if record is None:
        return None
    fields = dict(record)
    active_chain = bool(fields.pop("active_chain"))
    return CatalogueScopeSnapshot.model_validate(fields), active_chain


async def _latest_decision(
    session: AsyncSession, curriculum_id: UUID
) -> CatalogueAdmissionDecision | None:
    decision, current = CatalogueAdmissionDecisionModel, CatalogueAdmissionCurrentModel
    model = await session.scalar(
        select(decision)
        .join(
            current,
            and_(
                current.curriculum_version_id == decision.curriculum_version_id,
                current.decision_id == decision.id,
                current.version == decision.version,
            ),
        )
        .where(current.curriculum_version_id == curriculum_id)
        .execution_options(populate_existing=True)
    )
    return CatalogueAdmissionDecision.model_validate(model) if model is not None else None


def _review_response(
    scope: CatalogueScopeSnapshot, *, active_chain: bool, latest: CatalogueAdmissionDecision | None
) -> CatalogueAdmissionReview:
    fingerprint = catalogue_scope_fingerprint(scope)
    labels_approvable = scope_labels_are_approvable(scope)
    stale = latest is not None and (
        latest.curriculum_version_id != scope.curriculum_version_id
        or latest.scope_fingerprint != fingerprint
        or latest.scope_snapshot != scope
    )
    return CatalogueAdmissionReview(
        curriculum_version_id=scope.curriculum_version_id,
        scope=scope,
        scope_fingerprint=fingerprint,
        active_chain=active_chain,
        labels_approvable=labels_approvable,
        version=latest.version if latest is not None else 0,
        state=latest.state if latest is not None else "unreviewed",
        admitted=bool(
            latest is not None
            and latest.state == "approved"
            and latest.educational_approval
            and active_chain
            and labels_approvable
            and not stale
        ),
        stale=stale,
        latest_decision=latest,
    )


async def get_catalogue_admission(
    session: AsyncSession, curriculum_id: UUID
) -> CatalogueAdmissionReview:
    scope = await _load_scope(session, curriculum_id)
    if scope is None:
        raise CatalogueScopeNotFoundError(curriculum_id)
    return _review_response(
        scope[0], active_chain=scope[1], latest=await _latest_decision(session, curriculum_id)
    )


async def require_admitted_curriculum(
    session: AsyncSession, curriculum_id: UUID | None
) -> CatalogueScopeSnapshot:
    if curriculum_id is None:
        raise CurriculumNotAdmittedError(curriculum_id, "unknown_scope")
    scope = await _load_scope(session, curriculum_id, lock=True)
    if scope is None:
        raise CurriculumNotAdmittedError(curriculum_id, "unknown_scope")
    review = _review_response(
        scope[0], active_chain=scope[1], latest=await _latest_decision(session, curriculum_id)
    )
    if not review.admitted:
        reason = (
            "inactive"
            if not review.active_chain
            else "stale"
            if review.stale
            else "invalid_labels"
            if not review.labels_approvable
            else review.state
        )
        raise CurriculumNotAdmittedError(curriculum_id, reason)
    return review.scope


async def record_catalogue_admission(
    session: AsyncSession,
    curriculum_id: UUID,
    request: AdmissionDecisionRequest,
    *,
    principal: Principal,
) -> CatalogueAdmissionReview:
    authorize(principal, Permission.TAXONOMY_WRITE)
    locked = await session.scalar(
        select(CurriculumVersionModel.id)
        .where(CurriculumVersionModel.id == curriculum_id)
        .with_for_update()
    )
    if locked is None:
        raise CatalogueScopeNotFoundError(curriculum_id)
    scope = await _load_scope(session, curriculum_id, lock=True)
    if scope is None:
        raise CatalogueScopeNotFoundError(curriculum_id)
    current = _review_response(
        scope[0], active_chain=scope[1], latest=await _latest_decision(session, curriculum_id)
    )
    if current.version != request.expected_version:
        raise CatalogueAdmissionConflictError()
    if current.scope_fingerprint != request.expected_scope_fingerprint:
        raise CatalogueAdmissionConflictError("catalogue_scope_changed")
    if request.state == "approved":
        if not current.active_chain:
            raise CatalogueApprovalError("catalogue_scope_inactive")
        if not current.labels_approvable:
            raise CatalogueApprovalError("catalogue_labels_not_approvable")
    decision_id, audit_id = uuid4(), uuid4()
    snapshot = current.scope.model_dump(mode="json")
    payload: dict[str, object] = {
        "decision_id": str(decision_id),
        "previous_version": current.version,
        "version": current.version + 1,
        "state": request.state,
        "scope_fingerprint": current.scope_fingerprint,
        "scope_snapshot": snapshot,
        "educational_approval": request.educational_approval,
        "reason": request.reason,
        "source_reference": request.source_reference,
        "evidence": list(request.evidence),
    }
    audit = AdminAuditEventModel(
        id=audit_id,
        actor_id=principal.subject_id,
        action=f"catalogue_admission.{request.state}",
        resource_type="curriculum_admission",
        resource_id=curriculum_id,
        payload=payload,
    )
    session.add(audit)
    await session.flush([audit])
    model = CatalogueAdmissionDecisionModel(
        id=decision_id,
        curriculum_version_id=curriculum_id,
        version=current.version + 1,
        state=request.state,
        scope_fingerprint=current.scope_fingerprint,
        scope_snapshot=snapshot,
        educational_approval=request.educational_approval,
        reason=request.reason,
        source_reference=request.source_reference,
        evidence=list(request.evidence),
        actor_id=principal.subject_id,
        audit_event_id=audit_id,
    )
    session.add(model)
    await session.flush([model])
    return _review_response(
        current.scope,
        active_chain=current.active_chain,
        latest=CatalogueAdmissionDecision.model_validate(model),
    )


async def list_catalogue_admission_history(
    session: AsyncSession,
    curriculum_id: UUID,
    *,
    limit: int = 50,
    before_version: int | None = None,
) -> list[CatalogueAdmissionDecision]:
    if await _load_scope(session, curriculum_id) is None:
        raise CatalogueScopeNotFoundError(curriculum_id)
    query = select(CatalogueAdmissionDecisionModel).where(
        CatalogueAdmissionDecisionModel.curriculum_version_id == curriculum_id
    )
    if before_version is not None:
        query = query.where(CatalogueAdmissionDecisionModel.version < before_version)
    models = await session.scalars(
        query.order_by(CatalogueAdmissionDecisionModel.version.desc()).limit(
            min(max(limit, 1), 100)
        )
    )
    return [CatalogueAdmissionDecision.model_validate(model) for model in models]


async def list_material_catalogue(
    session: AsyncSession,
    *,
    grade: int | None = None,
    medium_id: UUID | None = None,
    subject_id: UUID | None = None,
    limit: int = 500,
    offset: int = 0,
) -> list[MaterialCatalogueEntry]:
    query = _scope_query().where(admitted_curriculum_predicate(CurriculumVersionModel.id))
    if grade is not None:
        query = query.where(ExamConfigurationModel.grade == grade)
    if medium_id is not None:
        query = query.where(MediumModel.id == medium_id)
    if subject_id is not None:
        query = query.where(SubjectModel.id == subject_id)
    records = (
        await session.execute(
            query.order_by(
                ExamConfigurationModel.grade,
                MediumModel.name,
                SubjectModel.name,
                CurriculumVersionModel.title,
                CurriculumVersionModel.id,
            )
            .limit(min(max(limit, 1), 1000))
            .offset(max(offset, 0))
        )
    ).mappings()
    return [
        MaterialCatalogueEntry(
            curriculum_version_id=record["curriculum_version_id"],
            curriculum_title=record["curriculum_title"],
            exam_configuration_id=record["exam_configuration_id"],
            exam_configuration_name=record["exam_configuration_name"],
            grade=record["grade"],
            grade_label=f"Grade {record['grade']}",
            medium_id=record["medium_id"],
            medium_name=record["medium_name"],
            subject_id=record["subject_id"],
            subject_name=record["subject_name"],
        )
        for record in records
    ]

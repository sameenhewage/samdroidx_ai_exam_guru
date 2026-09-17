import json
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.domain import Permission, Principal, authorize
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.documents.page_images import PageImageArtifacts, SourceImageStorage
from exam_guru_api.documents.source_verification import VerifiedSourceContent, verify_source_reading
from exam_guru_api.documents.source_verification_models import VerifiedSourceContentModel
from exam_guru_api.documents.understanding_models import (
    ObservationCandidateModel,
    PageVerificationReportModel,
)
from exam_guru_api.documents.understanding_service import (
    PageUnderstandingService,
    UnderstandingConflictError,
    _candidate,
    _snapshot,
)
from exam_guru_api.documents.understanding_verification import PageVerificationReport


def verified_source_snapshot(row: VerifiedSourceContentModel) -> VerifiedSourceContent:
    value = VerifiedSourceContent.model_validate_json(json.dumps(row.payload))
    if value.fingerprint != row.fingerprint or (
        value.id,
        value.source.document_id,
        value.source.page_number,
        value.candidate_id,
        value.revision,
        value.page_version,
        value.decision.actor_id,
    ) != (
        row.id,
        row.document_id,
        row.page_number,
        row.candidate_id,
        row.revision,
        row.page_version,
        row.created_by,
    ):
        raise ValueError("stored verified source identity is invalid")
    return value


class SourceVerificationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def current(
        self,
        *,
        principal: Principal,
        document_id: UUID,
        page_number: int,
    ) -> VerifiedSourceContent | None:
        authorize(principal, Permission.SOURCE_READ)
        await PageUnderstandingService(self.session)._source(document_id, page_number, write=False)
        row = await self.session.scalar(
            select(VerifiedSourceContentModel)
            .where(
                VerifiedSourceContentModel.document_id == document_id,
                VerifiedSourceContentModel.page_number == page_number,
                func.verified_source_content_is_current(VerifiedSourceContentModel.id),
            )
            .order_by(VerifiedSourceContentModel.revision.desc())
            .limit(1)
        )
        return None if row is None else verified_source_snapshot(row)

    async def verify(
        self,
        *,
        principal: Principal,
        document_id: UUID,
        page_number: int,
        candidate_id: UUID,
        expected_version: int,
        compared_with_original: bool,
        reviewed_region_keys: tuple[str, ...],
        resolved_uncertainty_keys: tuple[str, ...],
        reason: str,
        storage: SourceImageStorage,
        artifacts: PageImageArtifacts | None,
    ) -> VerifiedSourceContent:
        authorize(principal, Permission.SOURCE_TRUST)
        try:
            service = PageUnderstandingService(self.session)
            await service._source(document_id, page_number, write=True)
            page = await service._page(document_id, page_number, principal, create=False)
            service._version(page, expected_version)
            if (
                page.active_job_id is not None
                or page.state == "excluded"
                or page.current_candidate_id != candidate_id
                or page.current_report_id is None
            ):
                raise UnderstandingConflictError("source_reading_version_conflict")
            candidate_row = await self.session.get(ObservationCandidateModel, candidate_id)
            report_row = await self.session.get(PageVerificationReportModel, page.current_report_id)
            if candidate_row is None or report_row is None:
                raise UnderstandingConflictError("source_reading_version_conflict")
            await service.candidate_image(
                principal=principal,
                document_id=document_id,
                page_number=page_number,
                candidate_id=candidate_id,
                storage=storage,
                artifacts=artifacts,
            )
            existing = await self.session.scalar(
                select(VerifiedSourceContentModel).where(
                    VerifiedSourceContentModel.candidate_id == candidate_id,
                    VerifiedSourceContentModel.page_version == expected_version,
                )
            )
            revision = await self.session.scalar(
                select(func.max(VerifiedSourceContentModel.revision)).where(
                    VerifiedSourceContentModel.document_id == document_id,
                    VerifiedSourceContentModel.page_number == page_number,
                )
            )
            verified = verify_source_reading(
                _candidate(candidate_row),
                _snapshot(report_row, PageVerificationReport),
                principal=principal,
                identifier=existing.id if existing else uuid4(),
                revision=existing.revision if existing else (revision or 0) + 1,
                page_version=page.version,
                compared_with_original=compared_with_original,
                reviewed_region_keys=reviewed_region_keys,
                resolved_uncertainty_keys=resolved_uncertainty_keys,
                reason=reason,
            )
            if existing is not None:
                if verified_source_snapshot(existing) != verified:
                    raise UnderstandingConflictError("source_reading_already_verified")
                await self.session.commit()
                return verified
            audit = AdminAuditEventModel(
                id=uuid4(),
                actor_id=principal.subject_id,
                resource_type="verified_source_content",
                resource_id=document_id,
                action="source_reading.verified",
                payload={
                    "verified_source_id": str(verified.id),
                    "candidate_id": str(candidate_id),
                    "page_number": page_number,
                    "page_version": page.version,
                    "revision": verified.revision,
                    "fingerprint": verified.fingerprint,
                },
            )
            self.session.add(audit)
            await self.session.flush()
            self.session.add(
                VerifiedSourceContentModel(
                    id=verified.id,
                    document_id=document_id,
                    page_number=page_number,
                    candidate_id=candidate_id,
                    report_id=report_row.id,
                    revision=verified.revision,
                    page_version=page.version,
                    payload=verified.model_dump(mode="json"),
                    fingerprint=verified.fingerprint,
                    created_by=principal.subject_id,
                    audit_event_id=audit.id,
                )
            )
            await self.session.commit()
            return verified
        except Exception:
            await self.session.rollback()
            raise

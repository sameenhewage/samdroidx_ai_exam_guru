import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4, uuid5

import anyio
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.domain import Permission, Principal, authorize
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.documents.page_images import (
    PageImageArtifacts,
    PageImageError,
    SourceCandidateImageMetadata,
    SourceImageIdentity,
    SourceImageStorage,
    open_verified_original,
)
from exam_guru_api.documents.understanding_contracts import UnderstandingModel, _canonical_bytes
from exam_guru_api.documents.understanding_models import (
    DocumentUnderstandingRunModel,
    ObservationCandidateModel,
    PageRegionModel,
    PageUnderstandingStateModel,
    PageVerificationDecisionModel,
    PageVerificationReportModel,
    TrustedPageKnowledgeModel,
)
from exam_guru_api.documents.understanding_provider import (
    UnderstandingProviderResult,
    UnderstandingRequest,
    understanding_request_key,
)
from exam_guru_api.documents.understanding_verification import (
    ObservationCandidate,
    PageVerificationReport,
    TrustedPageKnowledge,
    accept_trusted_page,
    verify_understanding,
)


class UnderstandingConflictError(ValueError):
    pass


class UnderstandingSourceError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class UnderstandingPageSnapshot:
    document_id: UUID
    page_number: int
    version: int
    state: str
    candidate: ObservationCandidate | None
    report: PageVerificationReport | None
    trusted: TrustedPageKnowledge | None
    active_job_id: UUID | None = None


def _candidate(row: ObservationCandidateModel) -> ObservationCandidate:
    value = ObservationCandidate.model_validate_json(
        json.dumps(
            {
                "id": str(row.id),
                "run_id": str(row.run_id),
                "revision": row.revision,
                "method": row.method,
                "source": {
                    "document_id": str(row.document_id),
                    "source_sha256": row.source_sha256,
                    "page_number": row.page_number,
                    "image_sha256": row.image_sha256,
                },
                "content": {
                    "schema_version": "page-understanding.v1",
                    "observation": row.observation,
                    "education": row.educational_understanding,
                    "uncertainties": row.uncertainties,
                },
            }
        )
    )
    if value.fingerprint != row.fingerprint:
        raise ValueError("stored observation fingerprint is invalid")
    return value


def _snapshot[Snapshot: UnderstandingModel](
    row: PageVerificationReportModel | TrustedPageKnowledgeModel, contract: type[Snapshot]
) -> Snapshot:
    value = contract.model_validate_json(json.dumps(row.payload))
    if hashlib.sha256(_canonical_bytes(value)).hexdigest() != row.fingerprint:
        raise ValueError("stored understanding snapshot fingerprint is invalid")
    return value


class PageUnderstandingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _source(
        self, document_id: UUID, page_number: int, *, write: bool
    ) -> SourceDocumentModel:
        source = await self.session.scalar(
            select(SourceDocumentModel)
            .where(SourceDocumentModel.id == document_id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
        if (
            source is None
            or source.quarantined_for_teacher_use
            or (write and not source.active_for_ai)
            or source.original_page_count is None
            or not 1 <= page_number <= source.original_page_count
        ):
            raise UnderstandingSourceError("source_understanding_source_unavailable")
        return source

    async def _page(
        self, document_id: UUID, page_number: int, principal: Principal, *, create: bool
    ) -> PageUnderstandingStateModel:
        if create:
            await self.session.execute(
                insert(PageUnderstandingStateModel)
                .values(
                    document_id=document_id,
                    page_number=page_number,
                    updated_by=principal.subject_id,
                )
                .on_conflict_do_nothing(index_elements=["document_id", "page_number"])
            )
        page = await self.session.scalar(
            select(PageUnderstandingStateModel)
            .where(
                PageUnderstandingStateModel.document_id == document_id,
                PageUnderstandingStateModel.page_number == page_number,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if page is None:
            raise UnderstandingConflictError("source_understanding_version_conflict")
        return page

    @staticmethod
    def _version(page: PageUnderstandingStateModel, expected_version: int) -> None:
        if type(expected_version) is not int or page.version != expected_version:
            raise UnderstandingConflictError("source_understanding_version_conflict")

    @staticmethod
    def _validate_image(
        source: SourceDocumentModel,
        request: UnderstandingRequest,
        metadata: SourceCandidateImageMetadata,
    ) -> None:
        if (
            source.checksum_sha256 != request.source.source_sha256
            or metadata.document_id != str(source.id)
            or metadata.source_checksum_sha256 != source.checksum_sha256
            or metadata.source_object_key != source.object_key
            or metadata.source_size_bytes != source.size_bytes
            or metadata.page_number != request.source.page_number
            or metadata.sha256 != request.source.image_sha256
            or metadata.artifact.size_bytes != len(request.image_png)
            or metadata.artifact.chunk_sha256
            != tuple(
                hashlib.sha256(
                    request.image_png[offset : offset + metadata.artifact.chunk_bytes]
                ).hexdigest()
                for offset in range(0, len(request.image_png), metadata.artifact.chunk_bytes)
            )
            or (metadata.width, metadata.height) != request.image_dimensions
        ):
            raise ValueError("understanding image provenance does not match the original")

    @staticmethod
    def _audit(
        principal: Principal,
        page: PageUnderstandingStateModel,
        *,
        action: str,
        candidate_id: UUID,
        report_id: UUID,
        run_id: UUID,
        trusted_id: UUID | None,
    ) -> AdminAuditEventModel:
        return AdminAuditEventModel(
            id=uuid4(),
            actor_id=principal.subject_id,
            resource_type="page_understanding",
            resource_id=page.document_id,
            action=action,
            payload={
                "page_number": page.page_number,
                "previous_version": page.version,
                "version": page.version + 1,
                "candidate_id": str(candidate_id),
                "report_id": str(report_id),
                "run_id": str(run_id),
                "trusted_knowledge_id": None if trusted_id is None else str(trusted_id),
                "previous_trusted_knowledge_id": None
                if page.current_trusted_id is None
                else str(page.current_trusted_id),
            },
        )

    async def record_result(
        self,
        *,
        principal: Principal,
        request_id: UUID,
        expected_version: int,
        request: UnderstandingRequest,
        result: UnderstandingProviderResult,
        image_metadata: SourceCandidateImageMetadata,
        job_id: UUID | None = None,
    ) -> ObservationCandidate:
        authorize(principal, Permission.SOURCE_WRITE)
        request = UnderstandingRequest.model_validate(request)
        result = UnderstandingProviderResult.model_validate(result)
        metadata = SourceCandidateImageMetadata.model_validate(
            image_metadata.model_dump(mode="json")
        )
        if result.source != request.source or result.profile != request.profile:
            raise ValueError(
                "understanding result does not match the requested source and provider"
            )
        if (
            result.accounting.output_tokens > request.budget.max_output_tokens
            or result.accounting.cost_microusd > request.budget.max_cost_microusd
            or result.accounting.cost_microusd
            != request.profile.cost_microusd(
                result.accounting.input_tokens, result.accounting.output_tokens
            )
        ):
            raise ValueError("understanding result accounting does not match its bounded profile")
        fingerprint = understanding_request_key(request)
        try:
            source = await self._source(
                request.source.document_id, request.source.page_number, write=True
            )
            self._validate_image(source, request, metadata)
            page = await self._page(source.id, request.source.page_number, principal, create=True)
            existing = await self.session.scalar(
                select(DocumentUnderstandingRunModel).where(
                    DocumentUnderstandingRunModel.created_by == principal.subject_id,
                    DocumentUnderstandingRunModel.request_id == request_id,
                )
            )
            if existing is not None:
                row = await self.session.scalar(
                    select(ObservationCandidateModel).where(
                        ObservationCandidateModel.run_id == existing.id
                    )
                )
                if (
                    existing.request_fingerprint != fingerprint
                    or row is None
                    or _candidate(row).content != result.content
                ):
                    raise UnderstandingConflictError("source_understanding_request_conflict")
                await self.session.commit()
                return _candidate(row)
            self._version(page, expected_version)
            if page.active_job_id != job_id:
                raise UnderstandingConflictError("source_understanding_job_conflict")
            candidate = ObservationCandidate(
                id=uuid4(),
                run_id=uuid4(),
                revision=page.candidate_revision + 1,
                method="visual_ai",
                source=request.source,
                content=result.content,
            )
            report = verify_understanding(candidate)
            report_id = uuid4()
            audit = self._audit(
                principal,
                page,
                action="page_understanding.observed",
                candidate_id=candidate.id,
                report_id=report_id,
                run_id=candidate.run_id,
                trusted_id=None,
            )
            self.session.add(audit)
            await self.session.flush()
            self.session.add(
                DocumentUnderstandingRunModel(
                    id=candidate.run_id,
                    document_id=source.id,
                    page_number=page.page_number,
                    source_sha256=source.checksum_sha256,
                    image_sha256=request.source.image_sha256,
                    request_id=request_id,
                    request_fingerprint=fingerprint,
                    method=candidate.method,
                    outcome="succeeded",
                    image_metadata=metadata.model_dump(mode="json"),
                    provider_profile=request.profile.model_dump(mode="json"),
                    budget=request.budget.model_dump(mode="json"),
                    accounting=asdict(result.accounting),
                    created_by=principal.subject_id,
                    audit_event_id=audit.id,
                )
            )
            await self.session.flush()
            self.session.add(
                ObservationCandidateModel(
                    id=candidate.id,
                    run_id=candidate.run_id,
                    document_id=source.id,
                    page_number=page.page_number,
                    revision=candidate.revision,
                    method=candidate.method,
                    source_sha256=source.checksum_sha256,
                    image_sha256=request.source.image_sha256,
                    observation=candidate.content.observation.model_dump(mode="json"),
                    educational_understanding=candidate.content.education.model_dump(mode="json"),
                    uncertainties=[
                        item.model_dump(mode="json") for item in candidate.content.uncertainties
                    ],
                    fingerprint=candidate.fingerprint,
                    created_by=principal.subject_id,
                    audit_event_id=audit.id,
                )
            )
            await self.session.flush()
            self.session.add_all(
                PageRegionModel(
                    id=uuid5(candidate.id, region.key),
                    candidate_id=candidate.id,
                    document_id=source.id,
                    page_number=page.page_number,
                    region_key=region.key,
                    kind=region.kind,
                    reading_order=region.reading_order,
                    parent_key=region.parent_key,
                )
                for region in candidate.content.observation.regions
            )
            self.session.add(
                PageVerificationReportModel(
                    id=report_id,
                    candidate_id=candidate.id,
                    document_id=source.id,
                    page_number=page.page_number,
                    payload=report.model_dump(mode="json"),
                    fingerprint=report.fingerprint,
                    created_by=principal.subject_id,
                    audit_event_id=audit.id,
                )
            )
            await self.session.flush()
            page.current_candidate_id = candidate.id
            page.current_report_id = report_id
            page.current_trusted_id = None
            page.active_job_id = None
            page.candidate_revision = candidate.revision
            page.state = report.state
            page.version += 1
            page.event_id = audit.id
            page.updated_by = principal.subject_id
            page.updated_at = datetime.now(UTC)
            await self.session.commit()
            return candidate
        except Exception:
            await self.session.rollback()
            raise

    async def get_page(
        self, *, principal: Principal, document_id: UUID, page_number: int
    ) -> UnderstandingPageSnapshot:
        authorize(principal, Permission.SOURCE_READ)
        await self._source(document_id, page_number, write=False)
        page = await self.session.get(
            PageUnderstandingStateModel, (document_id, page_number), populate_existing=True
        )
        if page is None:
            return UnderstandingPageSnapshot(
                document_id, page_number, 0, "unprocessed", None, None, None
            )
        row = (
            None
            if page.current_candidate_id is None
            else await self.session.get(ObservationCandidateModel, page.current_candidate_id)
        )
        stored_report = (
            None
            if page.current_report_id is None
            else await self.session.get(PageVerificationReportModel, page.current_report_id)
        )
        stored_trusted = (
            None
            if page.current_trusted_id is None
            else await self.session.get(TrustedPageKnowledgeModel, page.current_trusted_id)
        )
        report = None if stored_report is None else _snapshot(stored_report, PageVerificationReport)
        trusted = (
            None if stored_trusted is None else _snapshot(stored_trusted, TrustedPageKnowledge)
        )
        return UnderstandingPageSnapshot(
            document_id,
            page_number,
            page.version,
            page.state,
            None if row is None else _candidate(row),
            report,
            trusted,
            page.active_job_id,
        )

    async def candidate_image(
        self,
        *,
        principal: Principal,
        document_id: UUID,
        page_number: int,
        candidate_id: UUID,
        storage: SourceImageStorage,
        artifacts: PageImageArtifacts | None,
    ) -> bytes:
        authorize(principal, Permission.SOURCE_READ)
        source = await self._source(document_id, page_number, write=False)
        row = await self.session.get(ObservationCandidateModel, candidate_id)
        if row is None or row.document_id != document_id or row.page_number != page_number:
            raise UnderstandingSourceError("source_understanding_candidate_not_found")
        candidate = _candidate(row)
        if candidate.source.source_sha256 != source.checksum_sha256:
            raise PageImageError("source_original_unavailable")
        run = await self.session.get(DocumentUnderstandingRunModel, candidate.run_id)
        if run is None or artifacts is None:
            raise PageImageError("source_page_image_artifact_unavailable")
        identity = SourceImageIdentity(
            source.id,
            source.checksum_sha256,
            source.object_key,
            source.size_bytes,
            source.original_page_count,
            source.original_filename,
        )
        try:
            parsed = SourceCandidateImageMetadata.model_validate(run.image_metadata)
        except (TypeError, ValueError):
            raise PageImageError("source_page_image_metadata_invalid") from None
        if parsed.sha256 != candidate.source.image_sha256:
            raise PageImageError("source_page_image_metadata_invalid")
        metadata = parsed.model_dump(mode="json")

        def read_image() -> bytes:
            with open_verified_original(storage, identity):
                return artifacts.read(metadata, source=identity, page_number=page_number)

        return await anyio.to_thread.run_sync(read_image)

    async def verify_against_original(
        self,
        *,
        principal: Principal,
        document_id: UUID,
        page_number: int,
        candidate_id: UUID,
        expected_version: int,
        compared_with_original: bool,
        reviewed_region_keys: tuple[str, ...],
        accepted_claim_keys: tuple[str, ...],
        resolved_uncertainty_keys: tuple[str, ...],
        reason: str,
        storage: SourceImageStorage,
        artifacts: PageImageArtifacts | None,
    ) -> TrustedPageKnowledge:
        authorize(principal, Permission.SOURCE_TRUST)
        await self.candidate_image(
            principal=principal,
            document_id=document_id,
            page_number=page_number,
            candidate_id=candidate_id,
            storage=storage,
            artifacts=artifacts,
        )
        return await self.verify(
            principal=principal,
            document_id=document_id,
            page_number=page_number,
            candidate_id=candidate_id,
            expected_version=expected_version,
            compared_with_original=compared_with_original,
            reviewed_region_keys=reviewed_region_keys,
            accepted_claim_keys=accepted_claim_keys,
            resolved_uncertainty_keys=resolved_uncertainty_keys,
            reason=reason,
        )

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
        accepted_claim_keys: tuple[str, ...],
        resolved_uncertainty_keys: tuple[str, ...],
        reason: str,
    ) -> TrustedPageKnowledge:
        authorize(principal, Permission.SOURCE_TRUST)
        try:
            await self._source(document_id, page_number, write=True)
            page = await self._page(document_id, page_number, principal, create=False)
            self._version(page, expected_version)
            if (
                page.active_job_id is not None
                or page.current_candidate_id != candidate_id
                or page.current_report_id is None
            ):
                raise UnderstandingConflictError("source_understanding_version_conflict")
            row = await self.session.get(ObservationCandidateModel, candidate_id)
            stored_report = await self.session.get(
                PageVerificationReportModel, page.current_report_id
            )
            if row is None or stored_report is None:
                raise UnderstandingConflictError("source_understanding_version_conflict")
            candidate = _candidate(row)
            report = _snapshot(stored_report, PageVerificationReport)
            trusted = accept_trusted_page(
                candidate,
                report,
                principal=principal,
                decision_id=uuid4(),
                revision=page.trusted_revision + 1,
                reason=reason,
                compared_with_original=compared_with_original,
                reviewed_region_keys=reviewed_region_keys,
                accepted_claim_keys=accepted_claim_keys,
                resolved_uncertainty_keys=resolved_uncertainty_keys,
            )
            audit = self._audit(
                principal,
                page,
                action="page_understanding.verified",
                candidate_id=candidate.id,
                report_id=stored_report.id,
                run_id=candidate.run_id,
                trusted_id=trusted.id,
            )
            self.session.add(audit)
            await self.session.flush()
            self.session.add(
                PageVerificationDecisionModel(
                    id=trusted.id,
                    report_id=stored_report.id,
                    candidate_id=candidate.id,
                    document_id=document_id,
                    page_number=page_number,
                    payload=trusted.decision.model_dump(mode="json"),
                    fingerprint=hashlib.sha256(_canonical_bytes(trusted.decision)).hexdigest(),
                    created_by=principal.subject_id,
                    audit_event_id=audit.id,
                )
            )
            await self.session.flush()
            self.session.add(
                TrustedPageKnowledgeModel(
                    id=trusted.id,
                    candidate_id=candidate.id,
                    document_id=document_id,
                    page_number=page_number,
                    revision=trusted.revision,
                    payload=trusted.model_dump(mode="json"),
                    fingerprint=hashlib.sha256(_canonical_bytes(trusted)).hexdigest(),
                    created_by=principal.subject_id,
                    audit_event_id=audit.id,
                )
            )
            await self.session.flush()
            page.current_trusted_id = trusted.id
            page.trusted_revision = trusted.revision
            page.state = "verified"
            page.version += 1
            page.event_id = audit.id
            page.updated_by = principal.subject_id
            page.updated_at = datetime.now(UTC)
            await self.session.commit()
            return trusted
        except Exception:
            await self.session.rollback()
            raise

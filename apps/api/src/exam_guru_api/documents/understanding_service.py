import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4, uuid5

import anyio
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.domain import Permission, Principal, authorize
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.documents.fidelity_service import _reason
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.documents.page_images import (
    PageImageArtifacts,
    PageImageError,
    SourceCandidateImageMetadata,
    SourceImageIdentity,
    SourceImageStorage,
    open_verified_original,
)
from exam_guru_api.documents.understanding_contracts import (
    PageUnderstanding,
    ShortText,
    UnderstandingModel,
    _canonical_bytes,
    _canonical_json,
    understanding_fingerprint,
)
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
    Checksum,
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


class UnderstandingPageExclusion(UnderstandingModel):
    event_id: UUID
    document_id: UUID
    page_number: int = Field(ge=1)
    source_sha256: Checksum
    version: int = Field(ge=1)
    actor_id: UUID
    reason: ShortText


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
    parent_candidate_id: UUID | None = None
    exclusion: UnderstandingPageExclusion | None = None


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


class PreparationRequestRecorder(Protocol):
    async def __call__(
        self,
        *,
        principal: Principal,
        document_id: UUID,
        source_sha256: str,
        source_audit_event_id: UUID,
    ) -> None: ...


class PageUnderstandingService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        preparation_recorder: PreparationRequestRecorder | None = None,
    ) -> None:
        self.session = session
        self.preparation_recorder = preparation_recorder

    async def _record_preparation(
        self, principal: Principal, source: SourceDocumentModel, audit: AdminAuditEventModel
    ) -> None:
        if self.preparation_recorder is not None:
            await self.session.flush()
            await self.preparation_recorder(
                principal=principal,
                document_id=source.id,
                source_sha256=source.checksum_sha256,
                source_audit_event_id=audit.id,
            )

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
        candidate_id: UUID | None,
        report_id: UUID | None,
        run_id: UUID | None,
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
                "candidate_id": None if candidate_id is None else str(candidate_id),
                "report_id": None if report_id is None else str(report_id),
                "run_id": None if run_id is None else str(run_id),
                "trusted_knowledge_id": None if trusted_id is None else str(trusted_id),
                "previous_trusted_knowledge_id": None
                if page.current_trusted_id is None
                else str(page.current_trusted_id),
            },
        )

    async def _persist_candidate(
        self,
        *,
        principal: Principal,
        page: PageUnderstandingStateModel,
        candidate: ObservationCandidate,
        run: DocumentUnderstandingRunModel,
        parent_candidate_id: UUID | None = None,
        reason: str | None = None,
    ) -> ObservationCandidate:
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
        if parent_candidate_id is not None:
            audit.payload = {
                **audit.payload,
                "parent_candidate_id": str(parent_candidate_id),
                "reason": reason,
            }
        self.session.add(audit)
        await self.session.flush()
        run.audit_event_id = audit.id
        self.session.add(run)
        await self.session.flush()
        self.session.add(
            ObservationCandidateModel(
                id=candidate.id,
                run_id=candidate.run_id,
                parent_candidate_id=parent_candidate_id,
                document_id=page.document_id,
                page_number=page.page_number,
                revision=candidate.revision,
                method=candidate.method,
                source_sha256=candidate.source.source_sha256,
                image_sha256=candidate.source.image_sha256,
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
                document_id=page.document_id,
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
                document_id=page.document_id,
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
        page.state = (
            "corrected"
            if candidate.method == "human" and report.state == "needs_human_review"
            else report.state
        )
        page.version += 1
        page.event_id = audit.id
        page.updated_by = principal.subject_id
        page.updated_at = datetime.now(UTC)
        await self.session.commit()
        return candidate

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
            if page.active_job_id != job_id or page.state == "excluded":
                raise UnderstandingConflictError("source_understanding_job_conflict")
            candidate = ObservationCandidate(
                id=uuid4(),
                run_id=uuid4(),
                revision=page.candidate_revision + 1,
                method="visual_ai",
                source=request.source,
                content=result.content,
            )
            run = DocumentUnderstandingRunModel(
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
            )
            return await self._persist_candidate(
                principal=principal, page=page, candidate=candidate, run=run
            )
        except Exception:
            await self.session.rollback()
            raise

    async def correct(
        self,
        *,
        principal: Principal,
        document_id: UUID,
        page_number: int,
        parent_candidate_id: UUID,
        request_id: UUID,
        expected_version: int,
        content: PageUnderstanding,
        reason: str,
        storage: SourceImageStorage,
        artifacts: PageImageArtifacts | None,
    ) -> ObservationCandidate:
        authorize(principal, Permission.SOURCE_WRITE)
        content = PageUnderstanding.model_validate(content)
        reason = _reason(reason)
        if not isinstance(request_id, UUID):
            raise ValueError("source correction requires a request identity")
        try:
            source = await self._source(document_id, page_number, write=True)
            page = await self._page(document_id, page_number, principal, create=False)
            parent_row = await self.session.get(ObservationCandidateModel, parent_candidate_id)
            if (
                parent_row is None
                or parent_row.document_id != document_id
                or parent_row.page_number != page_number
            ):
                raise UnderstandingSourceError("source_understanding_candidate_not_found")
            parent = _candidate(parent_row)
            identity = {
                "schema_version": "understanding-correction.v1",
                "source": parent.source.model_dump(mode="json"),
                "parent_candidate_id": str(parent.id),
                "parent_fingerprint": parent.fingerprint,
                "expected_version": expected_version,
                "content_fingerprint": understanding_fingerprint(content),
                "reason": reason,
            }
            fingerprint = hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()
            existing = await self.session.scalar(
                select(DocumentUnderstandingRunModel).where(
                    DocumentUnderstandingRunModel.created_by == principal.subject_id,
                    DocumentUnderstandingRunModel.request_id == request_id,
                )
            )
            if existing is not None:
                saved = await self.session.scalar(
                    select(ObservationCandidateModel).where(
                        ObservationCandidateModel.run_id == existing.id
                    )
                )
                if (
                    existing.method != "human"
                    or existing.request_fingerprint != fingerprint
                    or saved is None
                    or saved.parent_candidate_id != parent.id
                    or _candidate(saved).content != content
                ):
                    raise UnderstandingConflictError("source_understanding_request_conflict")
                await self.session.commit()
                return _candidate(saved)
            self._version(page, expected_version)
            if (
                page.current_candidate_id != parent.id
                or page.active_job_id is not None
                or page.state == "excluded"
            ):
                raise UnderstandingConflictError("source_understanding_correction_conflict")
            await self.candidate_image(
                principal=principal,
                document_id=document_id,
                page_number=page_number,
                candidate_id=parent.id,
                storage=storage,
                artifacts=artifacts,
            )
            parent_run = await self.session.get(DocumentUnderstandingRunModel, parent.run_id)
            if parent_run is None:
                raise UnderstandingSourceError("source_understanding_run_not_found")
            candidate = ObservationCandidate(
                id=uuid4(),
                run_id=uuid4(),
                revision=page.candidate_revision + 1,
                method="human",
                source=parent.source,
                content=content,
            )
            run = DocumentUnderstandingRunModel(
                id=candidate.run_id,
                document_id=document_id,
                page_number=page_number,
                source_sha256=source.checksum_sha256,
                image_sha256=parent.source.image_sha256,
                request_id=request_id,
                request_fingerprint=fingerprint,
                method="human",
                outcome="succeeded",
                image_metadata=dict(parent_run.image_metadata),
                provider_profile=None,
                budget=None,
                accounting=None,
                created_by=principal.subject_id,
            )
            return await self._persist_candidate(
                principal=principal,
                page=page,
                candidate=candidate,
                run=run,
                parent_candidate_id=parent.id,
                reason=reason,
            )
        except Exception:
            await self.session.rollback()
            raise

    async def exclude(
        self,
        *,
        principal: Principal,
        document_id: UUID,
        page_number: int,
        expected_version: int,
        confirm_exclusion: bool,
        reason: str,
    ) -> UnderstandingPageSnapshot:
        authorize(principal, Permission.SOURCE_TRUST)
        if confirm_exclusion is not True:
            raise ValueError("page exclusion requires explicit confirmation")
        return await self._lifecycle(
            principal=principal,
            document_id=document_id,
            page_number=page_number,
            expected_version=expected_version,
            reason=reason,
            reopen=False,
        )

    async def reopen(
        self,
        *,
        principal: Principal,
        document_id: UUID,
        page_number: int,
        expected_version: int,
        confirm_reopen: bool,
        reason: str,
    ) -> UnderstandingPageSnapshot:
        authorize(principal, Permission.SOURCE_TRUST)
        if confirm_reopen is not True:
            raise ValueError("reopening a page requires explicit confirmation")
        return await self._lifecycle(
            principal=principal,
            document_id=document_id,
            page_number=page_number,
            expected_version=expected_version,
            reason=reason,
            reopen=True,
        )

    async def _lifecycle(
        self,
        *,
        principal: Principal,
        document_id: UUID,
        page_number: int,
        expected_version: int,
        reason: str,
        reopen: bool,
    ) -> UnderstandingPageSnapshot:
        reason = _reason(reason)
        action = "page_understanding.reopened" if reopen else "page_understanding.excluded"
        confirmation = "confirmed_reopen" if reopen else "confirmed_exclusion"
        try:
            source = await self._source(document_id, page_number, write=True)
            page = await self._page(document_id, page_number, principal, create=not reopen)
            previous = (
                None
                if page.event_id is None
                else await self.session.get(AdminAuditEventModel, page.event_id)
            )
            if (
                type(expected_version) is int
                and page.version == expected_version + 1
                and previous is not None
                and previous.action == action
                and previous.actor_id == principal.subject_id
                and previous.payload.get("previous_version") == expected_version
                and previous.payload.get("reason") == reason
                and previous.payload.get(confirmation) is True
            ):
                if not reopen:
                    await self._record_preparation(principal, source, previous)
                await self.session.commit()
                return await self.get_page(
                    principal=principal, document_id=document_id, page_number=page_number
                )
            self._version(page, expected_version)
            if page.active_job_id is not None or (page.state == "excluded") != reopen:
                raise UnderstandingConflictError("source_understanding_lifecycle_conflict")
            candidate = (
                None
                if page.current_candidate_id is None
                else await self.session.get(ObservationCandidateModel, page.current_candidate_id)
            )
            if not reopen:
                state = "excluded"
            elif candidate is None:
                state = "unprocessed"
            else:
                report = await self.session.get(PageVerificationReportModel, page.current_report_id)
                state = (
                    "needs_reprocessing"
                    if report is None
                    else _snapshot(report, PageVerificationReport).state
                )
            audit = self._audit(
                principal,
                page,
                action=action,
                candidate_id=page.current_candidate_id,
                report_id=page.current_report_id,
                run_id=None if candidate is None else candidate.run_id,
                trusted_id=None,
            )
            audit.payload = {
                **audit.payload,
                "source_sha256": source.checksum_sha256,
                "reason": reason,
                confirmation: True,
                "state": state,
            }
            self.session.add(audit)
            await self.session.flush()
            page.current_trusted_id = None
            page.state = state
            page.version += 1
            page.event_id = audit.id
            page.updated_by = principal.subject_id
            page.updated_at = datetime.now(UTC)
            if not reopen:
                await self._record_preparation(principal, source, audit)
            await self.session.commit()
            return await self.get_page(
                principal=principal, document_id=document_id, page_number=page_number
            )
        except Exception:
            await self.session.rollback()
            raise

    async def _exclusion(
        self, page: PageUnderstandingStateModel, source_sha256: str
    ) -> UnderstandingPageExclusion | None:
        if page.state != "excluded":
            return None
        audit = (
            None
            if page.event_id is None
            else await self.session.get(AdminAuditEventModel, page.event_id)
        )
        if (
            audit is None
            or audit.action != "page_understanding.excluded"
            or audit.actor_id != page.updated_by
            or audit.resource_type != "page_understanding"
            or audit.resource_id != page.document_id
            or audit.payload.get("source_sha256") != source_sha256
            or type(audit.payload.get("version")) is not int
            or audit.payload.get("version") != page.version
            or type(audit.payload.get("page_number")) is not int
            or audit.payload.get("page_number") != page.page_number
            or audit.payload.get("confirmed_exclusion") is not True
        ):
            raise ValueError("stored page exclusion does not match its source and version")
        reason = audit.payload.get("reason")
        if not isinstance(reason, str) or reason != _reason(reason):
            raise ValueError("stored page exclusion reason is invalid")
        return UnderstandingPageExclusion(
            event_id=audit.id,
            document_id=page.document_id,
            page_number=page.page_number,
            source_sha256=source_sha256,
            version=page.version,
            actor_id=audit.actor_id,
            reason=reason,
        )

    async def get_page(
        self, *, principal: Principal, document_id: UUID, page_number: int
    ) -> UnderstandingPageSnapshot:
        authorize(principal, Permission.SOURCE_READ)
        source = await self._source(document_id, page_number, write=False)
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
            None if row is None else row.parent_candidate_id,
            await self._exclusion(page, source.checksum_sha256),
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
            source = await self._source(document_id, page_number, write=True)
            page = await self._page(document_id, page_number, principal, create=False)
            self._version(page, expected_version)
            if (
                page.active_job_id is not None
                or page.state == "excluded"
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
            await self._record_preparation(principal, source, audit)
            await self.session.commit()
            return trusted
        except Exception:
            await self.session.rollback()
            raise

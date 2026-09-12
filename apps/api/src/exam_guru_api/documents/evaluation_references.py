import hashlib
import unicodedata
from uuid import UUID, uuid4

import anyio
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.domain import Permission, Principal, authorize
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.documents.fidelity_models import (
    PageReviewStateModel,
    PageTextCandidateModel,
    SourceBenchmarkPageModel,
    SourceEvaluationPreviewModel,
    SourceEvaluationReferenceModel,
)
from exam_guru_api.documents.fidelity_queries import get_review_workspace
from exam_guru_api.documents.fidelity_schemas import (
    EvaluationPreviewResponse,
    EvaluationReferenceResponse,
    EvaluationReferenceSaveRequest,
)
from exam_guru_api.documents.fidelity_service import _reason
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.documents.page_images import (
    PageImageArtifacts,
    PageImageError,
    SourceImageIdentity,
    SourceImageStorage,
    load_material_source,
    open_verified_original,
    render_page_image,
)


class EvaluationReferenceError(RuntimeError):
    def __init__(self, code: str, status_code: int) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


def _capture_image(
    source: SourceImageIdentity,
    page_number: int,
    provenance: dict[str, object] | None,
    storage: SourceImageStorage,
    artifacts: PageImageArtifacts,
) -> dict[str, object]:
    metadata = None if provenance is None else provenance.get("page_image")
    if (
        provenance is not None
        and "page_image" in provenance
        and (
            not isinstance(metadata, dict)
            or not metadata
            or metadata.get("failure_code") is not None
            or metadata.get("page_number") != page_number
            or provenance.get("source_checksum_sha256") != source.checksum_sha256
        )
    ):
        raise PageImageError("source_page_image_metadata_invalid")
    with open_verified_original(storage, source) as stream:
        if isinstance(metadata, dict) and "artifact" in metadata:
            artifacts.read(metadata, source=source, page_number=page_number)
            return metadata
        with render_page_image(stream, page_number, expected_page_count=source.page_count) as image:
            return artifacts.persist(image, source=source)


def _verified_preview(
    preview: SourceEvaluationPreviewModel,
    source: SourceImageIdentity,
    storage: SourceImageStorage,
    artifacts: PageImageArtifacts,
) -> bytes:
    with open_verified_original(storage, source):
        return artifacts.read(
            preview.image_metadata, source=source, page_number=preview.page_number
        )


def _reference_response(reference: SourceEvaluationReferenceModel) -> EvaluationReferenceResponse:
    return EvaluationReferenceResponse(
        id=reference.id,
        preview_id=reference.preview_id,
        benchmark_id=reference.benchmark_id,
        document_id=reference.document_id,
        page_number=reference.page_number,
        version=reference.version,
        text=reference.raw_text_utf8.decode("utf-8"),
        normalized_text=reference.normalized_text,
        text_sha256=reference.text_sha256,
        normalized_sha256=reference.normalized_sha256,
        blank_reference=reference.blank_reference,
        reviewer_id=reference.reviewer_id,
        reviewed_at=reference.reviewed_at,
        reason=reference.reason,
    )


class EvaluationReferenceService:
    def __init__(
        self,
        session: AsyncSession,
        storage: SourceImageStorage,
        artifacts: PageImageArtifacts | None,
    ) -> None:
        self._session = session
        self._storage = storage
        self._artifacts = artifacts

    async def _membership(
        self,
        benchmark_id: UUID,
        document_id: UUID,
        page_number: int,
        *,
        lock: bool = False,
    ) -> SourceBenchmarkPageModel:
        statement = select(SourceBenchmarkPageModel).where(
            SourceBenchmarkPageModel.benchmark_id == benchmark_id,
            SourceBenchmarkPageModel.document_id == document_id,
            SourceBenchmarkPageModel.page_number == page_number,
        )
        if lock:
            statement = statement.with_for_update()
        membership = await self._session.scalar(statement)
        if membership is None:
            raise EvaluationReferenceError("evaluation_page_not_selected", 404)
        return membership

    async def _latest(
        self,
        benchmark_id: UUID,
        document_id: UUID,
        page_number: int,
    ) -> SourceEvaluationReferenceModel | None:
        references = await self._session.scalars(
            select(SourceEvaluationReferenceModel)
            .where(
                SourceEvaluationReferenceModel.benchmark_id == benchmark_id,
                SourceEvaluationReferenceModel.document_id == document_id,
                SourceEvaluationReferenceModel.page_number == page_number,
            )
            .order_by(SourceEvaluationReferenceModel.version.desc())
            .limit(1)
        )
        return references.first()

    async def prepare(
        self,
        benchmark_id: UUID,
        document_id: UUID,
        page_number: int,
        *,
        principal: Principal,
    ) -> EvaluationPreviewResponse:
        authorize(principal, Permission.CONTENT_REVIEW)
        await self._membership(benchmark_id, document_id, page_number)
        source = await load_material_source(self._session, document_id, principal=principal)
        source.check_page(page_number)
        if source.page_count is None:
            raise EvaluationReferenceError("source_page_identity_unavailable", 409)
        if self._artifacts is None:
            raise PageImageError("source_page_image_artifact_unavailable")
        candidate = (
            await self._session.execute(
                select(PageTextCandidateModel.id, PageTextCandidateModel.provenance)
                .join(
                    PageReviewStateModel,
                    and_(
                        PageReviewStateModel.current_candidate_id == PageTextCandidateModel.id,
                        PageReviewStateModel.document_id == PageTextCandidateModel.document_id,
                        PageReviewStateModel.page_number == PageTextCandidateModel.page_number,
                    ),
                )
                .where(
                    PageReviewStateModel.document_id == document_id,
                    PageReviewStateModel.page_number == page_number,
                )
            )
        ).one_or_none()
        artifacts = self._artifacts
        metadata = await anyio.to_thread.run_sync(
            lambda: _capture_image(
                source,
                page_number,
                None if candidate is None else candidate.provenance,
                self._storage,
                artifacts,
            )
        )
        preview = SourceEvaluationPreviewModel(
            id=uuid4(),
            benchmark_id=benchmark_id,
            document_id=document_id,
            page_number=page_number,
            candidate_id=None if candidate is None else candidate.id,
            source_checksum_sha256=source.checksum_sha256,
            image_sha256=str(metadata["sha256"]),
            image_metadata=metadata,
            created_by=principal.subject_id,
        )
        self._session.add(preview)
        self._session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=principal.subject_id,
                resource_type="source_evaluation_preview",
                resource_id=preview.id,
                action="source_evaluation.preview_prepared",
                payload={
                    "benchmark_id": str(benchmark_id),
                    "document_id": str(document_id),
                    "page_number": page_number,
                    "source_checksum_sha256": source.checksum_sha256,
                    "image_metadata": metadata,
                },
            )
        )
        await self._session.commit()
        current = await self._latest(benchmark_id, document_id, page_number)
        workspace = await get_review_workspace(
            self._session, document_id, page_number=page_number, principal=principal
        )
        return EvaluationPreviewResponse(
            id=preview.id,
            benchmark_id=benchmark_id,
            document_id=document_id,
            document_title=workspace.document_title,
            page_number=page_number,
            source_checksum_sha256=source.checksum_sha256,
            image_sha256=preview.image_sha256,
            image_width=int(str(metadata["width"])),
            image_height=int(str(metadata["height"])),
            preview_url=f"/api/v1/admin/source-benchmarks/{benchmark_id}/evaluation-previews/{preview.id}/image",
            language=workspace.language,
            reference_version=0 if current is None else current.version,
            latest_reference=None if current is None else _reference_response(current),
        )

    async def image(
        self,
        benchmark_id: UUID,
        preview_id: UUID,
        *,
        principal: Principal,
    ) -> bytes:
        authorize(principal, Permission.SOURCE_READ)
        preview = await self._session.get(SourceEvaluationPreviewModel, preview_id)
        if preview is None or preview.benchmark_id != benchmark_id:
            raise EvaluationReferenceError("evaluation_preview_not_found", 404)
        source = await load_material_source(self._session, preview.document_id, principal=principal)
        if self._artifacts is None:
            raise PageImageError("source_page_image_artifact_unavailable")
        artifacts = self._artifacts
        return await anyio.to_thread.run_sync(
            lambda: _verified_preview(preview, source, self._storage, artifacts)
        )

    async def save(
        self,
        benchmark_id: UUID,
        document_id: UUID,
        page_number: int,
        request: EvaluationReferenceSaveRequest,
        *,
        principal: Principal,
    ) -> EvaluationReferenceResponse:
        authorize(principal, Permission.CONTENT_REVIEW)
        request = EvaluationReferenceSaveRequest.model_validate(request)
        authorize(principal, Permission.SOURCE_READ)
        await self._session.scalar(
            select(SourceDocumentModel)
            .where(
                SourceDocumentModel.id == document_id,
            )
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
        source = await load_material_source(self._session, document_id, principal=principal)
        await self._membership(benchmark_id, document_id, page_number, lock=True)
        preview = await self._session.get(SourceEvaluationPreviewModel, request.preview_id)
        if preview is None or (
            preview.benchmark_id,
            preview.document_id,
            preview.page_number,
            preview.created_by,
        ) != (benchmark_id, document_id, page_number, principal.subject_id):
            raise EvaluationReferenceError("evaluation_preview_not_found", 404)
        current = await self._latest(benchmark_id, document_id, page_number)
        version = 0 if current is None else current.version
        if request.expected_version != version:
            raise EvaluationReferenceError("evaluation_reference_version_conflict", 409)
        if self._artifacts is None:
            raise PageImageError("source_page_image_artifact_unavailable")
        artifacts = self._artifacts
        await anyio.to_thread.run_sync(
            lambda: _verified_preview(preview, source, self._storage, artifacts)
        )
        raw = request.text.encode("utf-8")
        normalized = unicodedata.normalize("NFC", request.text)
        reason = _reason(request.reason)
        reference = SourceEvaluationReferenceModel(
            id=uuid4(),
            preview_id=preview.id,
            benchmark_id=benchmark_id,
            document_id=document_id,
            page_number=page_number,
            version=version + 1,
            raw_text_utf8=raw,
            normalized_text=normalized,
            text_sha256=hashlib.sha256(raw).hexdigest(),
            normalized_sha256=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            blank_reference=request.blank_reference,
            reason=reason,
            reviewer_id=principal.subject_id,
        )
        self._session.add(reference)
        self._session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=principal.subject_id,
                resource_type="source_evaluation_reference",
                resource_id=reference.id,
                action="source_evaluation.reference_saved",
                payload={
                    "preview_id": str(preview.id),
                    "version": reference.version,
                    "text_sha256": reference.text_sha256,
                    "normalized_sha256": reference.normalized_sha256,
                    "blank_reference": reference.blank_reference,
                    "reason": reason,
                    "human_reviewed": True,
                    "compared_with_original": True,
                    "evaluation_only": True,
                },
            )
        )
        await self._session.commit()
        await self._session.refresh(reference)
        return _reference_response(reference)

    async def history(
        self,
        benchmark_id: UUID,
        document_id: UUID,
        page_number: int,
        *,
        principal: Principal,
        limit: int = 20,
        offset: int = 0,
    ) -> list[EvaluationReferenceResponse]:
        authorize(principal, Permission.SOURCE_READ)
        await load_material_source(self._session, document_id, principal=principal)
        await self._membership(benchmark_id, document_id, page_number)
        if type(limit) is not int or not 1 <= limit <= 20 or type(offset) is not int or offset < 0:
            raise ValueError("invalid evaluation reference pagination")
        references = await self._session.scalars(
            select(SourceEvaluationReferenceModel)
            .where(
                SourceEvaluationReferenceModel.benchmark_id == benchmark_id,
                SourceEvaluationReferenceModel.document_id == document_id,
                SourceEvaluationReferenceModel.page_number == page_number,
            )
            .order_by(SourceEvaluationReferenceModel.version.desc())
            .limit(limit)
            .offset(offset)
        )
        return [_reference_response(reference) for reference in references]

import hashlib
import json
import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol, cast
from uuid import UUID, uuid4, uuid5

from anyio import to_thread
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.core.config import (
    EXTRACTION_ACTOR_MAX_EXECUTION_SECONDS,
    EXTRACTION_NATIVE_STORAGE_HEADROOM_SECONDS,
    OCR_PROVIDER_MAX_EXECUTION_SECONDS,
    TESSERACT_PROBE_COMMAND_COUNT,
)
from exam_guru_api.documents.domain import ExtractionStatus
from exam_guru_api.documents.extraction import (
    KNOWN_CORRUPT_SOURCE_FINGERPRINT,
    ExtractedBlock,
    ExtractedPage,
    ExtractionError,
    ExtractionMode,
    InvalidExtractionTransitionError,
    NativeExtractionResult,
    native_review_config,
    ocr_page_numbers,
    transition_extraction_status,
)
from exam_guru_api.documents.extraction_outbox import (
    SqlAlchemyExtractionOutboxRepository,
    validate_extraction_queue_message_id,
)
from exam_guru_api.documents.models import (
    ExtractedBlockModel,
    SourceDocumentModel,
    SourcePageModel,
)
from exam_guru_api.documents.ocr import (
    MAX_OCR_CONFIG_JSON_BYTES,
    MalformedOCROutputError,
    OCRBlock,
    OCRConfigError,
    OCRContractError,
    OCRInputError,
    OCROutputLimitError,
    OCRPage,
    OCRPort,
    OCRProcessError,
    OCRRequest,
    OCRResult,
    OCRTimeoutError,
    OCRUnavailableError,
)
from exam_guru_api.infrastructure.object_storage import ObjectStorage
from exam_guru_api.observability import OperationalTelemetry, get_operational_telemetry


class NativeDocumentExtractor(Protocol):
    def extract(self, data: bytes) -> NativeExtractionResult: ...


class ExtractionDocumentNotFoundError(LookupError):
    def __init__(self, document_id: UUID) -> None:
        self.document_id = document_id
        super().__init__(str(document_id))


class ExtractionSourceIntegrityError(RuntimeError):
    def __init__(self, document_id: UUID) -> None:
        self.document_id = document_id
        super().__init__(f"source object does not match document {document_id}")


class OCRPipelineError(RuntimeError):
    """Sanitized OCR pipeline failure safe for persistence and audit metadata."""

    def __init__(self, failure_code: str) -> None:
        self.failure_code = failure_code
        super().__init__(failure_code)


class ExtractionTrustBlockedError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class ReviewNotActiveError(RuntimeError):
    pass


class SourcePageNotFoundError(LookupError):
    pass


class ExtractedBlockNotFoundError(LookupError):
    pass


class ConcurrentReviewVersionError(RuntimeError):
    def __init__(self, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"expected review version {expected}, found {actual}")


@dataclass(frozen=True, slots=True)
class ExtractionPersistenceResult:
    document_id: UUID
    status: ExtractionStatus
    page_count: int
    block_count: int
    deduplicated: bool
    queue_message_id: str | None = None


@dataclass(frozen=True, slots=True)
class _ExtractionClaim:
    object_key: str
    checksum_sha256: str
    size_bytes: int


_FINAL_EXTRACTION_STATUSES = frozenset(
    {
        ExtractionStatus.EXTRACTED,
        ExtractionStatus.IN_REVIEW,
        ExtractionStatus.TRUSTED,
    }
)


class DocumentExtractionService:
    def __init__(
        self,
        session: AsyncSession,
        object_storage: ObjectStorage,
        extractor: NativeDocumentExtractor,
        *,
        ocr_port: OCRPort | None = None,
        telemetry: OperationalTelemetry | None = None,
        ocr_max_pages: int = 16,
        ocr_timeout_seconds: float = 10.0,
        execution_deadline: float | None = None,
    ) -> None:
        if (
            isinstance(ocr_max_pages, bool)
            or not isinstance(ocr_max_pages, int)
            or not 1 <= ocr_max_pages <= 1_000
            or isinstance(ocr_timeout_seconds, bool)
            or not isinstance(ocr_timeout_seconds, (int, float))
            or not math.isfinite(ocr_timeout_seconds)
            or not 0 < ocr_timeout_seconds <= 300
            or (
                ocr_port is not None
                and (ocr_max_pages + TESSERACT_PROBE_COMMAND_COUNT) * ocr_timeout_seconds
                > OCR_PROVIDER_MAX_EXECUTION_SECONDS
            )
        ):
            raise ValueError("OCR batch exceeds the execution budget")
        if execution_deadline is not None and not math.isfinite(execution_deadline):
            raise ValueError("execution deadline must be finite")
        self._ocr_max_pages = ocr_max_pages
        self._ocr_timeout_seconds = ocr_timeout_seconds
        actor_deadline = time.monotonic() + EXTRACTION_ACTOR_MAX_EXECUTION_SECONDS
        self._execution_deadline = (
            min(execution_deadline, actor_deadline)
            if execution_deadline is not None
            else actor_deadline
        )
        self._session = session
        self._object_storage = object_storage
        self._extractor = extractor
        self._ocr_port = ocr_port
        self._telemetry = telemetry or get_operational_telemetry()
        self._outbox_repository = SqlAlchemyExtractionOutboxRepository(session)

    async def queue_extraction(
        self,
        document_id: UUID,
        *,
        actor_id: UUID,
    ) -> ExtractionPersistenceResult:
        document = await self._get_locked_document(document_id)
        if document.extraction_status is ExtractionStatus.EXTRACTION_PENDING:
            await self._session.commit()
            return self._result_from_document(document, deduplicated=True)
        if document.extraction_status not in {
            ExtractionStatus.UPLOADED,
            ExtractionStatus.FAILED,
        }:
            raise InvalidExtractionTransitionError(
                document.extraction_status,
                ExtractionStatus.EXTRACTION_PENDING,
            )
        document.extraction_status = transition_extraction_status(
            document.extraction_status,
            ExtractionStatus.EXTRACTION_PENDING,
        )
        document.extraction_attempt_count += 1
        document.extraction_queue_message_id = None
        document.extraction_started_at = datetime.now(UTC)
        document.extraction_completed_at = None
        document.extraction_failure_code = None
        self._clear_result_metadata(document)
        document.updated_by = actor_id
        await self._session.flush()
        await self._delete_persisted_result(document.id)
        self._audit(
            document,
            actor_id=actor_id,
            action="source_document.extraction_queued",
            payload={"attempt": document.extraction_attempt_count},
        )
        await self._session.commit()
        return self._result_from_document(document, deduplicated=False)

    async def attach_queue_message(
        self,
        document_id: UUID,
        message_id: str,
        *,
        actor_id: UUID,
    ) -> ExtractionPersistenceResult:
        validated_message_id = validate_extraction_queue_message_id(message_id)
        try:
            attachment = await self._outbox_repository.attach_queue_message(
                document_id,
                validated_message_id,
            )
        except LookupError as error:
            raise ExtractionDocumentNotFoundError(document_id) from error
        if attachment.attached:
            self._audit(
                attachment.document,
                actor_id=actor_id,
                action="source_document.extraction_dispatched",
                payload={"attempt": attachment.document.extraction_attempt_count},
            )
        await self._session.commit()
        return self._result_from_document(
            attachment.document,
            deduplicated=not attachment.attached,
        )

    async def record_queue_dispatch_failure(
        self,
        document_id: UUID,
        *,
        actor_id: UUID,
    ) -> None:
        await self._session.rollback()
        document = await self._get_locked_document(document_id)
        self._audit(
            document,
            actor_id=actor_id,
            action="source_document.extraction_dispatch_failed",
            payload={
                "attempt": document.extraction_attempt_count,
                "failure_code": "queue_dispatch_failed",
            },
        )
        await self._session.commit()

    async def extract_native(
        self,
        document_id: UUID,
        *,
        actor_id: UUID,
        preclaimed: bool = False,
    ) -> ExtractionPersistenceResult:
        claim_or_result = (
            await self._preclaimed(document_id)
            if preclaimed
            else await self._claim(document_id, actor_id=actor_id)
        )
        if isinstance(claim_or_result, ExtractionPersistenceResult):
            return claim_or_result

        try:
            data = await to_thread.run_sync(
                self._object_storage.get_bytes,
                claim_or_result.object_key,
            )
            if (
                len(data) != claim_or_result.size_bytes
                or hashlib.sha256(data).hexdigest() != claim_or_result.checksum_sha256
            ):
                raise ExtractionSourceIntegrityError(document_id)
            native_extraction = await to_thread.run_sync(self._extractor.extract, data)
            extraction = await self._complete_with_ocr(
                document_id=document_id,
                checksum_sha256=claim_or_result.checksum_sha256,
                data=data,
                native=native_extraction,
            )
            return await self._persist_result(
                document_id,
                extraction,
                actor_id=actor_id,
            )
        except Exception as error:
            await self._session.rollback()
            await self._record_failure(document_id, error, actor_id=actor_id)
            raise

    async def _complete_with_ocr(
        self,
        *,
        document_id: UUID,
        checksum_sha256: str,
        data: bytes,
        native: NativeExtractionResult,
    ) -> NativeExtractionResult:
        native = replace(
            native,
            config={
                **native.config,
                **native_review_config(native.pages, checksum_sha256),
            },
        )
        selected_pages = ocr_page_numbers(native.pages)
        native_pages = tuple(self._with_native_provenance(page, native) for page in native.pages)
        native_manifest = self._bounded_extraction_config(
            {
                "mode": ExtractionMode.NATIVE.value,
                "native": {
                    "config": dict(native.config),
                    "engine": native.engine,
                    "version": native.engine_version,
                },
            }
        )
        native_result = replace(
            native,
            pages=native_pages,
            needs_ocr=bool(selected_pages),
            mode=ExtractionMode.NATIVE,
            ocr_page_count=0,
            ocr_engine=None,
            ocr_engine_version=None,
            ocr_page_numbers=(),
            extraction_config=native_manifest,
        )
        if not selected_pages:
            return native_result
        if self._ocr_port is None:
            return self._with_pending_ocr(
                native_result,
                pending=selected_pages,
                deferred=selected_pages,
                reason="ocr_unconfigured",
            )
        remaining_seconds = min(
            OCR_PROVIDER_MAX_EXECUTION_SECONDS,
            self._execution_deadline
            - time.monotonic()
            - EXTRACTION_NATIVE_STORAGE_HEADROOM_SECONDS,
        )
        full_batch_seconds = (
            self._ocr_max_pages + TESSERACT_PROBE_COMMAND_COUNT
        ) * self._ocr_timeout_seconds
        if remaining_seconds <= 0:
            deadline_page_limit = 0
        elif remaining_seconds >= full_batch_seconds:
            deadline_page_limit = self._ocr_max_pages
        else:
            deadline_page_limit = max(
                0,
                math.floor(remaining_seconds / self._ocr_timeout_seconds)
                - TESSERACT_PROBE_COMMAND_COUNT,
            )
        page_limit = deadline_page_limit
        if page_limit == 0:
            return self._with_pending_ocr(
                native_result,
                pending=selected_pages,
                deferred=selected_pages,
                reason="actor_deadline",
            )
        requested_pages = selected_pages[:page_limit]
        deferred_pages = selected_pages[page_limit:]
        try:
            request = OCRRequest(
                source_document_id=str(document_id),
                source_checksum_sha256=checksum_sha256,
                content=data,
                media_type="application/pdf",
                page_numbers=requested_pages,
            )
            raw_result = await to_thread.run_sync(self._ocr_port.extract, request)
        except Exception as error:
            raise OCRPipelineError(self._ocr_failure_code(error)) from error

        result = self._validated_ocr_result(raw_result)
        result_page_numbers = tuple(page.page_number for page in result.pages)
        if result_page_numbers != requested_pages:
            raise OCRPipelineError("ocr_result_mismatch")
        merged = self._merge_ocr_result(native_result, result)
        missing = tuple(page.page_number for page in result.pages if not page.text.strip())
        pending = tuple(sorted((*deferred_pages, *missing)))
        if not pending:
            return merged
        return self._with_pending_ocr(
            merged,
            pending=pending,
            deferred=deferred_pages,
            missing=missing,
            reason=(
                "actor_deadline"
                if deadline_page_limit < self._ocr_max_pages
                else "page_budget_exceeded"
            )
            if deferred_pages
            else "ocr_empty_pages",
        )

    @classmethod
    def _with_pending_ocr(
        cls,
        result: NativeExtractionResult,
        *,
        pending: tuple[int, ...],
        deferred: tuple[int, ...] = (),
        missing: tuple[int, ...] = (),
        reason: str,
    ) -> NativeExtractionResult:
        manifest = dict(result.extraction_config or {})
        native_manifest = dict(cast(Mapping[str, object], manifest["native"]))
        config = dict(cast(Mapping[str, object], native_manifest["config"]))
        config.update(
            {
                "ocr_pending_page_numbers": ",".join(map(str, pending)),
                "ocr_pending_page_count": len(pending),
                "ocr_deferred_page_numbers": ",".join(map(str, deferred)),
                "ocr_missing_page_numbers": ",".join(map(str, missing)),
                "ocr_pending_reason": reason,
            }
        )
        native_manifest["config"] = config
        manifest["native"] = native_manifest
        return replace(
            result, needs_ocr=True, extraction_config=cls._bounded_extraction_config(manifest)
        )

    @staticmethod
    def _with_native_provenance(
        page: ExtractedPage,
        native: NativeExtractionResult,
    ) -> ExtractedPage:
        page_extractor = page.extractor or native.engine
        page_extractor_version = page.extractor_version or native.engine_version
        page_config = page.extraction_config or native.config
        blocks = tuple(
            replace(
                block,
                extractor=block.extractor or page_extractor,
                extractor_version=block.extractor_version or page_extractor_version,
                extraction_config=block.extraction_config or page_config,
            )
            for block in page.blocks
        )
        return replace(
            page,
            blocks=blocks,
            extractor=page_extractor,
            extractor_version=page_extractor_version,
            extraction_config=page_config,
        )

    @classmethod
    def _merge_ocr_result(
        cls,
        native: NativeExtractionResult,
        ocr: OCRResult,
    ) -> NativeExtractionResult:
        selected_pages = tuple(page.page_number for page in ocr.pages)
        selected_page_set = set(selected_pages)
        ocr_by_page = {page.page_number: page for page in ocr.pages}
        merged_pages: list[ExtractedPage] = []
        for native_page in native.pages:
            if native_page.page_number not in selected_page_set:
                merged_pages.append(native_page)
                continue
            ocr_page = ocr_by_page[native_page.page_number]
            blocks = tuple(
                ExtractedBlock(
                    page_number=block.page_number,
                    reading_order=block.reading_order,
                    bbox=block.bbox,
                    text=block.text,
                    extractor=ocr.engine,
                    extractor_version=ocr.engine_version,
                    extraction_config=ocr.config,
                    confidence=block.confidence,
                )
                for block in ocr_page.blocks
            )
            page_config = dict(ocr.config)
            if native_page.text:
                page_config.update(
                    {
                        "native_text": native_page.text,
                        "native_engine": native_page.extractor or native.engine,
                        "native_engine_version": native_page.extractor_version
                        or native.engine_version,
                    }
                )
            merged_pages.append(
                ExtractedPage(
                    page_number=ocr_page.page_number,
                    text=ocr_page.text,
                    blocks=blocks,
                    largest_image_coverage=native_page.largest_image_coverage,
                    extractor=ocr.engine,
                    extractor_version=ocr.engine_version,
                    extraction_config=page_config,
                    confidence=ocr_page.confidence,
                )
            )

        mode = (
            ExtractionMode.OCR
            if len(selected_pages) == native.page_count
            else ExtractionMode.HYBRID
        )
        engine = ocr.engine if mode is ExtractionMode.OCR else "hybrid"
        engine_version = ocr.engine_version if mode is ExtractionMode.OCR else "1"
        extraction_config = cls._bounded_extraction_config(
            {
                "mode": mode.value,
                "native": {
                    "config": dict(native.config),
                    "engine": native.engine,
                    "version": native.engine_version,
                },
                "ocr": {
                    "config": dict(ocr.config),
                    "engine": ocr.engine,
                    "version": ocr.engine_version,
                },
                "ocr_page_numbers": list(selected_pages),
            }
        )
        merged_page_tuple = tuple(merged_pages)
        return NativeExtractionResult(
            engine=engine,
            engine_version=engine_version,
            pages=merged_page_tuple,
            page_count=native.page_count,
            character_count=sum(len(page.text) for page in merged_page_tuple),
            native_text_page_ratio=native.native_text_page_ratio,
            needs_ocr=bool(set(ocr_page_numbers(native.pages)) - selected_page_set)
            or any(not ocr_by_page[page_number].text.strip() for page_number in selected_pages),
            image_dominant_page_ratio=native.image_dominant_page_ratio,
            config=ocr.config if mode is ExtractionMode.OCR else {},
            mode=mode,
            ocr_page_count=len(selected_pages),
            ocr_engine=ocr.engine,
            ocr_engine_version=ocr.engine_version,
            ocr_page_numbers=selected_pages,
            extraction_config=extraction_config,
        )

    @staticmethod
    def _validated_ocr_result(result: object) -> OCRResult:
        if not isinstance(result, OCRResult):
            raise OCRPipelineError("ocr_malformed_output")
        try:
            pages = tuple(
                OCRPage(
                    page_number=page.page_number,
                    text=page.text,
                    blocks=tuple(
                        OCRBlock(
                            page_number=block.page_number,
                            reading_order=block.reading_order,
                            text=block.text,
                            bbox=block.bbox,
                            confidence=block.confidence,
                        )
                        for block in page.blocks
                    ),
                    confidence=page.confidence,
                )
                for page in result.pages
            )
            return OCRResult(
                engine=result.engine,
                engine_version=result.engine_version,
                config=result.config,
                pages=pages,
            )
        except OCRContractError as error:
            raise OCRPipelineError("ocr_contract") from error
        except (AttributeError, TypeError, ValueError) as error:
            raise OCRPipelineError("ocr_malformed_output") from error

    @staticmethod
    def _bounded_extraction_config(config: Mapping[str, object]) -> dict[str, object]:
        try:
            encoded = json.dumps(
                config,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError, OverflowError) as error:
            raise OCRPipelineError("ocr_contract") from error
        if len(encoded) > MAX_OCR_CONFIG_JSON_BYTES:
            raise OCRPipelineError("ocr_contract")
        decoded = json.loads(encoded)
        if not isinstance(decoded, dict):
            raise OCRPipelineError("ocr_contract")
        return cast(dict[str, object], decoded)

    @staticmethod
    def _ocr_failure_code(error: Exception) -> str:
        if isinstance(error, OCRPipelineError):
            return error.failure_code
        if isinstance(error, OCRConfigError):
            return "ocr_config"
        if isinstance(error, OCRInputError):
            return "ocr_input"
        if isinstance(error, OCRUnavailableError):
            return "ocr_unavailable"
        if isinstance(error, OCRTimeoutError):
            return "ocr_timeout"
        if isinstance(error, OCROutputLimitError):
            return "ocr_output_limit"
        if isinstance(error, MalformedOCROutputError):
            return "ocr_malformed_output"
        if isinstance(error, OCRContractError):
            return "ocr_contract"
        if isinstance(error, OCRProcessError):
            return "ocr_process"
        return "ocr_process"

    async def begin_review(
        self,
        document_id: UUID,
        *,
        actor_id: UUID,
    ) -> ExtractionPersistenceResult:
        document = await self._get_locked_document(document_id)
        if document.extraction_status is ExtractionStatus.IN_REVIEW:
            return self._result_from_document(document, deduplicated=True)

        document.extraction_status = transition_extraction_status(
            document.extraction_status,
            ExtractionStatus.IN_REVIEW,
        )
        document.updated_by = actor_id
        self._audit(
            document,
            actor_id=actor_id,
            action="source_document.extraction_review_started",
            payload={"status": ExtractionStatus.IN_REVIEW.value},
        )
        await self._session.commit()
        return self._result_from_document(document, deduplicated=False)

    async def list_pages(self, document_id: UUID) -> Sequence[SourcePageModel]:
        if await self._session.get(SourceDocumentModel, document_id) is None:
            raise ExtractionDocumentNotFoundError(document_id)
        return (
            await self._session.scalars(
                select(SourcePageModel)
                .where(SourcePageModel.source_document_id == document_id)
                .order_by(SourcePageModel.page_number)
            )
        ).all()

    async def list_blocks(
        self,
        document_id: UUID,
        *,
        page_number: int,
    ) -> Sequence[ExtractedBlockModel]:
        if await self._session.get(SourceDocumentModel, document_id) is None:
            raise ExtractionDocumentNotFoundError(document_id)
        return (
            await self._session.scalars(
                select(ExtractedBlockModel)
                .where(
                    ExtractedBlockModel.source_document_id == document_id,
                    ExtractedBlockModel.page_number == page_number,
                )
                .order_by(ExtractedBlockModel.reading_order)
            )
        ).all()

    async def correct_page(
        self,
        document_id: UUID,
        *,
        page_number: int,
        reviewed_text: str,
        expected_version: int,
        actor_id: UUID,
    ) -> SourcePageModel:
        document = await self._get_locked_document(document_id)
        if document.extraction_status is not ExtractionStatus.IN_REVIEW:
            raise ReviewNotActiveError(document_id)
        page = await self._session.scalar(
            select(SourcePageModel)
            .where(
                SourcePageModel.source_document_id == document_id,
                SourcePageModel.page_number == page_number,
            )
            .with_for_update()
        )
        if page is None:
            raise SourcePageNotFoundError(page_number)
        if page.version != expected_version:
            raise ConcurrentReviewVersionError(expected_version, page.version)
        page.reviewed_text = reviewed_text
        page.version += 1
        page.updated_by = actor_id
        self._audit(
            document,
            actor_id=actor_id,
            action="source_document.page_corrected",
            payload={"page_number": page_number, "version": page.version},
        )
        await self._session.commit()
        await self._session.refresh(page)
        return page

    async def correct_block(
        self,
        document_id: UUID,
        *,
        page_number: int,
        reading_order: int,
        reviewed_text: str,
        expected_version: int,
        actor_id: UUID,
    ) -> ExtractedBlockModel:
        document = await self._get_locked_document(document_id)
        if document.extraction_status is not ExtractionStatus.IN_REVIEW:
            raise ReviewNotActiveError(document_id)
        block = await self._session.scalar(
            select(ExtractedBlockModel)
            .where(
                ExtractedBlockModel.source_document_id == document_id,
                ExtractedBlockModel.page_number == page_number,
                ExtractedBlockModel.reading_order == reading_order,
            )
            .with_for_update()
        )
        if block is None:
            raise ExtractedBlockNotFoundError(reading_order)
        if block.version != expected_version:
            raise ConcurrentReviewVersionError(expected_version, block.version)
        block.reviewed_text = reviewed_text
        block.version += 1
        block.updated_by = actor_id
        self._audit(
            document,
            actor_id=actor_id,
            action="source_document.block_corrected",
            payload={
                "page_number": page_number,
                "reading_order": reading_order,
                "version": block.version,
            },
        )
        await self._session.commit()
        await self._session.refresh(block)
        return block

    async def trust_document(
        self,
        document_id: UUID,
        *,
        actor_id: UUID,
    ) -> ExtractionPersistenceResult:
        document = await self._get_locked_document(document_id)
        transition_extraction_status(document.extraction_status, ExtractionStatus.TRUSTED)
        if document.active_for_ai is False:
            raise ExtractionTrustBlockedError("inactive_source")
        if getattr(document, "metadata_review_required", False):
            raise ExtractionTrustBlockedError("metadata_review_required")
        config = document.extraction_config or {}
        native = config.get("native", {})
        native_config = native.get("config", {}) if isinstance(native, Mapping) else {}
        risk_configs = (config, native_config) if isinstance(native_config, Mapping) else (config,)
        if document.needs_ocr is not False or any(
            item.get("ocr_pending_page_count") for item in risk_configs
        ):
            raise ExtractionTrustBlockedError("needs_ocr")
        if f"sha256:{document.checksum_sha256}" == KNOWN_CORRUPT_SOURCE_FINGERPRINT or any(
            item.get(key)
            for item in risk_configs
            for key in (
                "font_risk",
                "font_risk_page_count",
                "risky_font_names",
                "known_review_warning",
                "private_use_glyph_count",
                "replacement_glyph_count",
            )
        ):
            raise ExtractionTrustBlockedError("font_risk")
        if not document.extracted_page_count or not document.extracted_character_count:
            raise ExtractionTrustBlockedError("empty_extraction")
        if document.extraction_status is ExtractionStatus.TRUSTED:
            return self._result_from_document(document, deduplicated=True)

        document.extraction_status = transition_extraction_status(
            document.extraction_status,
            ExtractionStatus.TRUSTED,
        )
        document.updated_by = actor_id
        self._audit(
            document,
            actor_id=actor_id,
            action="source_document.trusted",
            payload={"status": ExtractionStatus.TRUSTED.value},
        )
        await self._session.commit()
        return self._result_from_document(document, deduplicated=False)

    async def _preclaimed(
        self,
        document_id: UUID,
    ) -> _ExtractionClaim | ExtractionPersistenceResult:
        document = await self._get_locked_document(document_id)
        if document.extraction_status in _FINAL_EXTRACTION_STATUSES:
            result = self._result_from_document(document, deduplicated=True)
            await self._session.commit()
            return result
        if document.extraction_status is not ExtractionStatus.EXTRACTION_PENDING:
            raise InvalidExtractionTransitionError(
                document.extraction_status,
                ExtractionStatus.EXTRACTION_PENDING,
            )
        claim = _ExtractionClaim(
            object_key=document.object_key,
            checksum_sha256=document.checksum_sha256,
            size_bytes=document.size_bytes,
        )
        await self._session.commit()
        return claim

    async def _claim(
        self,
        document_id: UUID,
        *,
        actor_id: UUID,
    ) -> _ExtractionClaim | ExtractionPersistenceResult:
        document = await self._get_locked_document(document_id)
        if document.extraction_status in _FINAL_EXTRACTION_STATUSES:
            return self._result_from_document(document, deduplicated=True)
        if document.extraction_status is ExtractionStatus.EXTRACTION_PENDING:
            claim = _ExtractionClaim(
                object_key=document.object_key,
                checksum_sha256=document.checksum_sha256,
                size_bytes=document.size_bytes,
            )
            await self._session.commit()
            return claim

        document.extraction_status = transition_extraction_status(
            document.extraction_status,
            ExtractionStatus.EXTRACTION_PENDING,
        )
        document.extraction_attempt_count += 1
        document.extraction_queue_message_id = None
        document.extraction_started_at = datetime.now(UTC)
        document.extraction_completed_at = None
        document.extraction_failure_code = None
        self._clear_result_metadata(document)
        document.updated_by = actor_id
        await self._session.flush()
        await self._delete_persisted_result(document.id)
        self._audit(
            document,
            actor_id=actor_id,
            action="source_document.extraction_started",
            payload={"attempt": document.extraction_attempt_count},
        )
        await self._session.commit()
        return _ExtractionClaim(
            object_key=document.object_key,
            checksum_sha256=document.checksum_sha256,
            size_bytes=document.size_bytes,
        )

    async def _persist_result(
        self,
        document_id: UUID,
        extraction: NativeExtractionResult,
        *,
        actor_id: UUID,
    ) -> ExtractionPersistenceResult:
        document = await self._get_locked_document(document_id)
        if document.extraction_status in _FINAL_EXTRACTION_STATUSES:
            return self._result_from_document(document, deduplicated=True)
        if document.extraction_status is ExtractionStatus.FAILED:
            document.extraction_status = transition_extraction_status(
                document.extraction_status,
                ExtractionStatus.EXTRACTION_PENDING,
            )
            document.extraction_completed_at = None
            document.extraction_failure_code = None
            document.updated_by = actor_id
            await self._session.flush()

        transition_extraction_status(
            document.extraction_status,
            ExtractionStatus.EXTRACTED,
        )
        await self._delete_persisted_result(document.id)

        pages: list[SourcePageModel] = []
        blocks: list[ExtractedBlockModel] = []
        for extracted_page in extraction.pages:
            page_id = uuid5(document.id, f"source-page:{extracted_page.page_number}")
            page_extractor = extracted_page.extractor or extraction.engine
            page_extractor_version = extracted_page.extractor_version or extraction.engine_version
            page_config = dict(extracted_page.extraction_config or extraction.config)
            pages.append(
                SourcePageModel(
                    id=page_id,
                    source_document_id=document.id,
                    page_number=extracted_page.page_number,
                    extractor=page_extractor,
                    extractor_version=page_extractor_version,
                    extraction_config=page_config,
                    confidence=extracted_page.confidence,
                    raw_text=extracted_page.text,
                    reviewed_text=None,
                    character_count=len(extracted_page.text),
                    block_count=len(extracted_page.blocks),
                    created_by=actor_id,
                    updated_by=actor_id,
                )
            )
            for extracted_block in extracted_page.blocks:
                block_extractor = extracted_block.extractor or page_extractor
                block_extractor_version = (
                    extracted_block.extractor_version or page_extractor_version
                )
                block_config = dict(extracted_block.extraction_config or page_config)
                bbox = extracted_block.bbox
                blocks.append(
                    ExtractedBlockModel(
                        id=uuid5(
                            document.id,
                            "source-page:"
                            f"{extracted_block.page_number}:block:"
                            f"{extracted_block.reading_order}",
                        ),
                        source_page_id=page_id,
                        source_document_id=document.id,
                        page_number=extracted_block.page_number,
                        reading_order=extracted_block.reading_order,
                        extractor=block_extractor,
                        extractor_version=block_extractor_version,
                        extraction_config=block_config,
                        confidence=extracted_block.confidence,
                        bbox_x0=bbox[0] if bbox is not None else None,
                        bbox_y0=bbox[1] if bbox is not None else None,
                        bbox_x1=bbox[2] if bbox is not None else None,
                        bbox_y1=bbox[3] if bbox is not None else None,
                        raw_text=extracted_block.text,
                        reviewed_text=None,
                        character_count=len(extracted_block.text),
                        created_by=actor_id,
                        updated_by=actor_id,
                    )
                )

        self._session.add_all(pages)
        await self._session.flush()
        self._session.add_all(blocks)
        await self._session.flush()

        document.extractor = extraction.engine
        document.extractor_version = extraction.engine_version
        document.extracted_page_count = extraction.page_count
        document.extracted_block_count = len(blocks)
        document.extracted_character_count = extraction.character_count
        document.native_text_page_ratio = extraction.native_text_page_ratio
        document.needs_ocr = extraction.needs_ocr
        document.ocr_page_count = extraction.ocr_page_count
        document.extraction_config = dict(
            extraction.extraction_config
            or self._bounded_extraction_config(
                {
                    "mode": extraction.mode.value,
                    "native": {
                        "config": dict(extraction.config),
                        "engine": extraction.engine,
                        "version": extraction.engine_version,
                    },
                }
            )
        )
        document.extraction_failure_code = None
        document.extraction_completed_at = datetime.now(UTC)
        document.extraction_status = transition_extraction_status(
            document.extraction_status,
            ExtractionStatus.EXTRACTED,
        )
        document.updated_by = actor_id
        self._audit(
            document,
            actor_id=actor_id,
            action="source_document.extracted",
            payload={
                "attempt": document.extraction_attempt_count,
                "block_count": len(blocks),
                "character_count": extraction.character_count,
                "extractor": extraction.engine,
                "extractor_version": extraction.engine_version,
                "mode": extraction.mode.value,
                "needs_ocr": extraction.needs_ocr,
                "native_text_page_ratio": extraction.native_text_page_ratio,
                "ocr_engine": extraction.ocr_engine,
                "ocr_engine_version": extraction.ocr_engine_version,
                "ocr_page_count": extraction.ocr_page_count,
                "page_count": extraction.page_count,
            },
        )
        await self._session.commit()
        self._telemetry.extraction_terminal(
            status=document.extraction_status.value,
            failure_code=None,
            attempt_count=document.extraction_attempt_count,
            page_count=document.extracted_page_count or 0,
            block_count=document.extracted_block_count or 0,
            ocr_page_count=document.ocr_page_count or 0,
        )
        return self._result_from_document(document, deduplicated=False)

    async def _record_failure(
        self,
        document_id: UUID,
        error: Exception,
        *,
        actor_id: UUID,
    ) -> None:
        document = await self._get_locked_document(document_id)
        if document.extraction_status is not ExtractionStatus.EXTRACTION_PENDING:
            return

        failure_code = self._failure_code(error)
        await self._delete_persisted_result(document.id)
        self._clear_result_metadata(document)
        document.extraction_status = transition_extraction_status(
            document.extraction_status,
            ExtractionStatus.FAILED,
        )
        document.extraction_failure_code = failure_code
        document.extraction_completed_at = datetime.now(UTC)
        document.updated_by = actor_id
        self._audit(
            document,
            actor_id=actor_id,
            action="source_document.extraction_failed",
            payload={
                "attempt": document.extraction_attempt_count,
                "failure_code": failure_code,
            },
        )
        await self._session.commit()
        self._telemetry.extraction_terminal(
            status=document.extraction_status.value,
            failure_code=failure_code,
            attempt_count=document.extraction_attempt_count,
            page_count=0,
            block_count=0,
            ocr_page_count=0,
        )

    async def _get_locked_document(self, document_id: UUID) -> SourceDocumentModel:
        document = await self._session.get(
            SourceDocumentModel,
            document_id,
            with_for_update=True,
        )
        if document is None:
            raise ExtractionDocumentNotFoundError(document_id)
        return document

    async def _delete_persisted_result(self, document_id: UUID) -> None:
        await self._session.execute(
            delete(ExtractedBlockModel).where(ExtractedBlockModel.source_document_id == document_id)
        )
        await self._session.execute(
            delete(SourcePageModel).where(SourcePageModel.source_document_id == document_id)
        )

    @staticmethod
    def _clear_result_metadata(document: SourceDocumentModel) -> None:
        document.extractor = None
        document.extractor_version = None
        document.extracted_page_count = None
        document.extracted_block_count = None
        document.extracted_character_count = None
        document.native_text_page_ratio = None
        document.needs_ocr = None
        document.ocr_page_count = None
        document.extraction_config = None

    @staticmethod
    def _failure_code(error: Exception) -> str:
        if isinstance(error, ExtractionError):
            return error.violation.value
        if isinstance(error, ExtractionSourceIntegrityError):
            return "source_object_integrity"
        if isinstance(error, OCRPipelineError):
            return error.failure_code
        return "unexpected_error"

    @staticmethod
    def _result_from_document(
        document: SourceDocumentModel,
        *,
        deduplicated: bool,
    ) -> ExtractionPersistenceResult:
        return ExtractionPersistenceResult(
            document_id=document.id,
            status=document.extraction_status,
            page_count=document.extracted_page_count or 0,
            block_count=document.extracted_block_count or 0,
            deduplicated=deduplicated,
            queue_message_id=getattr(document, "extraction_queue_message_id", None),
        )

    def _audit(
        self,
        document: SourceDocumentModel,
        *,
        actor_id: UUID,
        action: str,
        payload: dict[str, object],
    ) -> None:
        self._session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=actor_id,
                action=action,
                resource_type="source_document",
                resource_id=document.id,
                payload=payload,
            )
        )

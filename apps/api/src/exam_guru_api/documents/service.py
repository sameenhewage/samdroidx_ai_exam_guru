import hashlib
import hmac
import math
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from time import monotonic
from typing import cast
from uuid import UUID, uuid4

import pymupdf
from anyio import fail_after, to_thread
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.curriculum.models import (
    CurriculumLessonModel,
    CurriculumUnitModel,
    CurriculumVersionModel,
    ExamConfigurationModel,
    MediumModel,
    SubjectModel,
)
from exam_guru_api.documents.domain import (
    ExtractionStatus,
    SourceDocumentType,
    validate_pdf_upload,
)
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.documents.schemas import (
    MaterialGradeSummaryResponse,
    MaterialListItemResponse,
    MaterialStatus,
    SourceIntakeMetadata,
)
from exam_guru_api.infrastructure.object_storage import (
    InvalidObjectKeyError,
    ObjectStorage,
    ObjectStorageOperationError,
    validate_source_object_key,
)
from exam_guru_api.knowledge.models import HistoricalQuestionModel, KnowledgeChunkModel


class SourceCurriculumNotFoundError(LookupError):
    pass


class SourceCurriculumInactiveError(RuntimeError):
    pass


class SourceLearningScopeNotFoundError(LookupError):
    pass


class SourceLearningScopeInactiveError(RuntimeError):
    pass


class SourceLearningScopeMismatchError(ValueError):
    pass


class SourceDocumentNotFoundError(LookupError):
    pass


class SourceDocumentContentUnavailableError(RuntimeError):
    pass


class SourceDocumentPageNotFoundError(LookupError):
    pass


_MAX_PREVIEW_INPUT_BYTES = 256 * 1024 * 1024
_MAX_PREVIEW_PIXELS = 4_000_000
_MAX_PREVIEW_DIMENSION = 4096
_MAX_PREVIEW_PNG_BYTES = 8 * 1024 * 1024
_PREVIEW_TIMEOUT_SECONDS = 5.0
_PREVIEW_RENDER_LOCK = threading.Lock()


def _render_original_page(data: bytes, page_number: int, deadline: float) -> bytes:
    if not _PREVIEW_RENDER_LOCK.acquire(blocking=False):
        raise SourceDocumentContentUnavailableError
    try:
        if monotonic() >= deadline or len(data) > _MAX_PREVIEW_INPUT_BYTES:
            raise SourceDocumentContentUnavailableError
        with pymupdf.open(stream=data, filetype="pdf") as pdf:
            if pdf.needs_pass or not pdf.is_pdf:
                raise SourceDocumentContentUnavailableError
            if page_number > pdf.page_count:
                raise SourceDocumentPageNotFoundError
            page = pdf.load_page(page_number - 1)
            rect = page.rect
            if any(not math.isfinite(value) or value <= 0 for value in (rect.width, rect.height)):
                raise SourceDocumentContentUnavailableError
            scale = min(
                1.5,
                math.sqrt(_MAX_PREVIEW_PIXELS / (rect.width * rect.height)) * 0.99,
                (_MAX_PREVIEW_DIMENSION - 1) / rect.width,
                (_MAX_PREVIEW_DIMENSION - 1) / rect.height,
            )
            matrix = pymupdf.Matrix(scale, scale)
            bounds = (rect * matrix).irect
            if (
                not 0 < bounds.width * bounds.height <= _MAX_PREVIEW_PIXELS
                or max(bounds.width, bounds.height) > _MAX_PREVIEW_DIMENSION
                or monotonic() >= deadline
            ):
                raise SourceDocumentContentUnavailableError
            pixmap = page.get_pixmap(matrix=matrix, colorspace=pymupdf.csRGB, alpha=False)
            if (
                not 0 < pixmap.width * pixmap.height <= _MAX_PREVIEW_PIXELS
                or max(pixmap.width, pixmap.height) > _MAX_PREVIEW_DIMENSION
                or monotonic() >= deadline
            ):
                raise SourceDocumentContentUnavailableError
            png = cast(bytes, pixmap.tobytes("png"))
            if len(png) > _MAX_PREVIEW_PNG_BYTES or monotonic() >= deadline:
                raise SourceDocumentContentUnavailableError
            return png
    except SourceDocumentPageNotFoundError:
        raise
    except Exception:
        raise SourceDocumentContentUnavailableError from None
    finally:
        _PREVIEW_RENDER_LOCK.release()


class ConcurrentMaterialScopeVersionError(RuntimeError):
    def __init__(self, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"expected material metadata version {expected}, found {actual}")


class InvalidMaterialRemovalReasonError(ValueError):
    pass


class MaterialScopeImmutableError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SourceUploadResult:
    document: SourceDocumentModel
    deduplicated: bool
    likely_metadata_duplicate_of_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class SourceDocumentContent:
    filename: str
    data: bytes


class SourceDocumentService:
    def __init__(
        self,
        session: AsyncSession,
        object_storage: ObjectStorage,
        *,
        max_upload_bytes: int,
    ) -> None:
        self._session = session
        self._object_storage = object_storage
        self._max_upload_bytes = max_upload_bytes

    async def list_documents(
        self,
        *,
        document_id: UUID | None = None,
    ) -> Sequence[SourceDocumentModel]:
        statement = select(SourceDocumentModel).order_by(SourceDocumentModel.created_at.desc())
        if document_id is not None:
            statement = statement.where(SourceDocumentModel.id == document_id)
        return (await self._session.scalars(statement)).all()

    async def read_original(
        self, document_id: UUID, *, max_bytes: int | None = None
    ) -> SourceDocumentContent:
        limit = (
            self._max_upload_bytes if max_bytes is None else min(max_bytes, self._max_upload_bytes)
        )
        document = await self._session.get(SourceDocumentModel, document_id)
        if document is None:
            raise SourceDocumentNotFoundError
        if (
            document.content_type != "application/pdf"
            or not 1 <= document.size_bytes <= limit
            or len(document.checksum_sha256) != 64
            or not document.checksum_sha256.isascii()
        ):
            raise SourceDocumentContentUnavailableError
        try:
            validate_source_object_key(document.object_key)
            data = await to_thread.run_sync(self._object_storage.get_bytes, document.object_key)
        except ObjectStorageOperationError as error:
            if error.failure_code == "object_storage_not_found":
                raise SourceDocumentNotFoundError from None
            raise SourceDocumentContentUnavailableError from None
        except InvalidObjectKeyError:
            raise SourceDocumentContentUnavailableError from None
        except Exception:
            raise SourceDocumentContentUnavailableError from None
        if not isinstance(data, bytes) or len(data) != document.size_bytes or len(data) > limit:
            raise SourceDocumentContentUnavailableError
        checksum = await to_thread.run_sync(hashlib.sha256, data)
        if not hmac.compare_digest(checksum.hexdigest(), document.checksum_sha256):
            raise SourceDocumentContentUnavailableError
        return SourceDocumentContent(filename=document.original_filename, data=data)

    async def read_page_preview(self, document_id: UUID, *, page_number: int) -> bytes:
        if not 1 <= page_number <= 1000:
            raise SourceDocumentPageNotFoundError
        content = await self.read_original(document_id, max_bytes=_MAX_PREVIEW_INPUT_BYTES)
        try:
            with fail_after(_PREVIEW_TIMEOUT_SECONDS):
                return await to_thread.run_sync(
                    _render_original_page,
                    content.data,
                    page_number,
                    monotonic() + _PREVIEW_TIMEOUT_SECONDS,
                    abandon_on_cancel=True,
                )
        except TimeoutError:
            raise SourceDocumentContentUnavailableError from None

    async def upload_pdf(
        self,
        *,
        filename: str,
        content_type: str,
        data: bytes,
        document_type: SourceDocumentType,
        actor_id: UUID,
        curriculum_version_id: UUID | None = None,
        unit_id: UUID | None = None,
        lesson_id: UUID | None = None,
        year: int | None = None,
        paper_code: str | None = None,
        intake_metadata: SourceIntakeMetadata | None = None,
    ) -> SourceUploadResult:
        intake = (
            None
            if intake_metadata is None
            else SourceIntakeMetadata.model_validate(intake_metadata).model_dump(mode="json")
        )
        upload = validate_pdf_upload(
            filename=filename,
            content_type=content_type,
            data=data,
            max_bytes=self._max_upload_bytes,
        )
        existing = await self._find_by_checksum(upload.checksum_sha256)
        if existing is not None:
            return SourceUploadResult(existing, deduplicated=True)

        await self._validate_learning_scope(
            curriculum_version_id,
            unit_id,
            lesson_id,
        )
        likely_duplicate = await self._find_likely_metadata_duplicate(
            filename=upload.filename,
            curriculum_version_id=curriculum_version_id,
            document_type=document_type,
            year=year,
            paper_code=paper_code,
        )

        await to_thread.run_sync(
            partial(
                self._object_storage.put_immutable,
                upload.object_key,
                upload.data,
                content_type="application/pdf",
            )
        )
        document = SourceDocumentModel(
            id=uuid4(),
            checksum_sha256=upload.checksum_sha256,
            object_key=upload.object_key,
            original_filename=upload.filename,
            content_type="application/pdf",
            size_bytes=upload.size_bytes,
            document_type=document_type,
            extraction_status=ExtractionStatus.UPLOADED,
            curriculum_version_id=curriculum_version_id,
            unit_id=unit_id,
            lesson_id=lesson_id,
            active_for_ai=True,
            removal_reason=None,
            removed_by=None,
            removed_at=None,
            metadata_scope_version=0,
            intake_metadata=intake,
            metadata_review_required=intake is not None,
            year=year,
            paper_code=paper_code,
            created_by=actor_id,
            updated_by=actor_id,
        )
        self._session.add(document)
        self._session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=actor_id,
                action="source_document.uploaded",
                resource_type="source_document",
                resource_id=document.id,
                payload={
                    "checksum_sha256": upload.checksum_sha256,
                    "document_type": document_type.value,
                    "original_filename": upload.filename,
                    "size_bytes": upload.size_bytes,
                    "intake_metadata": intake,
                    "metadata_review_required": intake is not None,
                    "curriculum_version_id": self._optional_uuid(curriculum_version_id),
                    "unit_id": self._optional_uuid(unit_id),
                    "lesson_id": self._optional_uuid(lesson_id),
                    "likely_metadata_duplicate_of_id": self._optional_uuid(
                        None if likely_duplicate is None else likely_duplicate.id
                    ),
                },
            )
        )
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            raced = await self._find_by_checksum(upload.checksum_sha256)
            if raced is None:
                raise
            return SourceUploadResult(raced, deduplicated=True)
        await self._session.refresh(document)
        return SourceUploadResult(
            document,
            deduplicated=False,
            likely_metadata_duplicate_of_id=(
                None if likely_duplicate is None else likely_duplicate.id
            ),
        )

    async def remove_from_ai_use(
        self,
        document_id: UUID,
        *,
        reason: str,
        expected_version: int,
        actor_id: UUID,
        removed_at: datetime | None = None,
    ) -> SourceDocumentModel:
        if (
            not isinstance(reason, str)
            or not reason
            or reason != reason.strip()
            or len(reason) > 512
            or any(not character.isprintable() for character in reason)
        ):
            raise InvalidMaterialRemovalReasonError
        document = await self._get_for_update(document_id)
        self._require_version(document, expected_version)
        if not document.active_for_ai:
            return document
        previous_version = document.metadata_scope_version
        document.active_for_ai = False
        document.removal_reason = reason
        document.removed_by = actor_id
        document.removed_at = removed_at or datetime.now(UTC)
        document.metadata_scope_version += 1
        document.updated_by = actor_id
        self._audit_use_transition(
            document,
            action="source_document.removed_from_ai_use",
            actor_id=actor_id,
            previous_version=previous_version,
            reason=reason,
        )
        await self._session.commit()
        await self._session.refresh(document)
        return document

    async def restore_to_ai_use(
        self,
        document_id: UUID,
        *,
        expected_version: int,
        actor_id: UUID,
    ) -> SourceDocumentModel:
        document = await self._get_for_update(document_id)
        self._require_version(document, expected_version)
        if document.active_for_ai:
            return document
        await self._validate_learning_scope(
            document.curriculum_version_id,
            document.unit_id,
            document.lesson_id,
        )
        previous_version = document.metadata_scope_version
        previous_reason = document.removal_reason
        document.active_for_ai = True
        document.removal_reason = None
        document.removed_by = None
        document.removed_at = None
        document.metadata_scope_version += 1
        document.updated_by = actor_id
        self._audit_use_transition(
            document,
            action="source_document.restored_to_ai_use",
            actor_id=actor_id,
            previous_version=previous_version,
            reason=previous_reason,
        )
        await self._session.commit()
        await self._session.refresh(document)
        return document

    async def correct_scope(
        self,
        document_id: UUID,
        *,
        curriculum_version_id: UUID | None,
        unit_id: UUID | None,
        lesson_id: UUID | None,
        expected_version: int,
        actor_id: UUID,
        confirm_intake_metadata: bool = False,
    ) -> SourceDocumentModel:
        document = await self._get_for_update(document_id)
        self._require_version(document, expected_version)
        if confirm_intake_metadata and curriculum_version_id is None:
            raise SourceLearningScopeMismatchError
        previous = (
            document.curriculum_version_id,
            document.unit_id,
            document.lesson_id,
        )
        updated = (curriculum_version_id, unit_id, lesson_id)
        confirming = confirm_intake_metadata and document.metadata_review_required
        if previous == updated and not confirming:
            return document
        source_has_knowledge = await self._source_has_knowledge(document.id)
        if document.extraction_status is ExtractionStatus.TRUSTED or source_has_knowledge:
            raise MaterialScopeImmutableError(document.id)
        await self._validate_learning_scope(curriculum_version_id, unit_id, lesson_id)
        if confirm_intake_metadata:
            await self._validate_confirmation_scope(cast(UUID, curriculum_version_id))
        previous_version = document.metadata_scope_version
        previous_year = document.year
        document.curriculum_version_id = curriculum_version_id
        document.unit_id = unit_id
        document.lesson_id = lesson_id
        if document.intake_metadata is not None:
            if confirming and document.year is None:
                document.year = SourceIntakeMetadata.model_validate(document.intake_metadata).year
            document.metadata_review_required = not confirm_intake_metadata
        document.metadata_scope_version += 1
        document.updated_by = actor_id
        self._session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=actor_id,
                action=(
                    "source_document.intake_metadata_confirmed"
                    if confirm_intake_metadata
                    else "source_document.scope_corrected"
                ),
                resource_type="source_document",
                resource_id=document.id,
                payload={
                    "from": self._scope_payload(*previous),
                    "to": self._scope_payload(*updated),
                    "intake_metadata": document.intake_metadata,
                    "metadata_review_required": bool(document.metadata_review_required),
                    "previous_year": previous_year,
                    "year": document.year,
                    "previous_version": previous_version,
                    "version": document.metadata_scope_version,
                },
            )
        )
        await self._session.commit()
        await self._session.refresh(document)
        return document

    async def list_materials(
        self,
        *,
        grade: int | None = None,
        subject_id: UUID | None = None,
        medium_id: UUID | None = None,
        material_type: SourceDocumentType | None = None,
        year: int | None = None,
        status: MaterialStatus | None = None,
        search: str | None = None,
        unassigned_only: bool = False,
        document_id: UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[MaterialListItemResponse, ...]:
        if not 1 <= limit <= 100 or not 0 <= offset <= 100_000:
            raise ValueError("material pagination is out of bounds")
        normalized_search = None if search is None else search.strip()
        if search is not None and (not normalized_search or len(normalized_search) > 200):
            raise ValueError("material search is out of bounds")
        status_conditions = {
            MaterialStatus.READY_FOR_AI: and_(
                SourceDocumentModel.active_for_ai.is_(True),
                SourceDocumentModel.extraction_status == ExtractionStatus.TRUSTED,
                SourceDocumentModel.metadata_review_required.is_(False),
            ),
            MaterialStatus.NEEDS_REVIEW: and_(
                SourceDocumentModel.active_for_ai.is_(True),
                or_(
                    SourceDocumentModel.metadata_review_required.is_(True),
                    SourceDocumentModel.extraction_status.in_(
                        (
                            ExtractionStatus.EXTRACTED,
                            ExtractionStatus.IN_REVIEW,
                            ExtractionStatus.FAILED,
                        )
                    ),
                ),
            ),
            MaterialStatus.PROCESSING: and_(
                SourceDocumentModel.active_for_ai.is_(True),
                SourceDocumentModel.metadata_review_required.is_(False),
                SourceDocumentModel.extraction_status.in_(
                    (ExtractionStatus.UPLOADED, ExtractionStatus.EXTRACTION_PENDING)
                ),
            ),
            MaterialStatus.REMOVED: SourceDocumentModel.active_for_ai.is_(False),
        }
        statement = (
            select(
                SourceDocumentModel,
                ExamConfigurationModel.grade,
                SubjectModel.id.label("subject_id"),
                SubjectModel.name.label("subject_name"),
                MediumModel.name.label("medium_name"),
                CurriculumVersionModel.title.label("curriculum_title"),
                CurriculumUnitModel.title.label("unit_title"),
                CurriculumLessonModel.title.label("lesson_title"),
            )
            .select_from(SourceDocumentModel)
            .outerjoin(
                CurriculumVersionModel,
                CurriculumVersionModel.id == SourceDocumentModel.curriculum_version_id,
            )
            .outerjoin(
                ExamConfigurationModel,
                ExamConfigurationModel.id == CurriculumVersionModel.exam_configuration_id,
            )
            .outerjoin(MediumModel, MediumModel.id == CurriculumVersionModel.medium_id)
            .outerjoin(SubjectModel, SubjectModel.id == CurriculumVersionModel.subject_id)
            .outerjoin(CurriculumUnitModel, CurriculumUnitModel.id == SourceDocumentModel.unit_id)
            .outerjoin(
                CurriculumLessonModel,
                CurriculumLessonModel.id == SourceDocumentModel.lesson_id,
            )
            .order_by(SourceDocumentModel.created_at.desc(), SourceDocumentModel.id.desc())
        )
        if unassigned_only:
            statement = statement.where(SourceDocumentModel.curriculum_version_id.is_(None))
        if document_id is not None:
            statement = statement.where(SourceDocumentModel.id == document_id)
        if grade is not None:
            statement = statement.where(
                or_(
                    ExamConfigurationModel.grade == grade,
                    and_(
                        SourceDocumentModel.curriculum_version_id.is_(None),
                        SourceDocumentModel.intake_metadata["candidate_grade"].as_integer()
                        == grade,
                    ),
                )
            )
        if subject_id is not None:
            statement = statement.where(SubjectModel.id == subject_id)
        if medium_id is not None:
            statement = statement.where(MediumModel.id == medium_id)
        if material_type is not None:
            statement = statement.where(SourceDocumentModel.document_type == material_type)
        if year is not None:
            statement = statement.where(
                or_(
                    SourceDocumentModel.year == year,
                    and_(
                        SourceDocumentModel.year.is_(None),
                        SourceDocumentModel.intake_metadata["year"].as_integer() == year,
                    ),
                )
            )
        if status is not None:
            statement = statement.where(status_conditions[status])
        if normalized_search is not None:
            statement = statement.where(
                SourceDocumentModel.original_filename.icontains(normalized_search, autoescape=True)
            )
        statement = statement.limit(limit).offset(offset)
        rows = (await self._session.execute(statement)).all()
        return tuple(
            MaterialListItemResponse(
                id=document.id,
                title=document.original_filename,
                grade=row_grade if row_grade is not None else candidate.candidate_grade,
                subject_id=row_subject_id,
                subject=subject_name if subject_name is not None else candidate.subject_label,
                medium=medium_name if medium_name is not None else candidate.medium_label,
                curriculum=(
                    curriculum_title if curriculum_title is not None else candidate.curriculum_label
                ),
                unit=unit_title,
                lesson=lesson_title,
                material_type=document.document_type,
                status=self._material_status(document),
                year=(
                    document.year
                    if document.year is not None
                    else intake.year
                    if intake is not None
                    else None
                ),
                intake_metadata=intake,
                metadata_review_required=bool(document.metadata_review_required),
                page_count=document.extracted_page_count,
                uploaded_at=document.created_at,
                metadata_scope_version=document.metadata_scope_version,
            )
            for (
                document,
                row_grade,
                row_subject_id,
                subject_name,
                medium_name,
                curriculum_title,
                unit_title,
                lesson_title,
            ) in rows
            for intake in (
                None
                if document.intake_metadata is None
                else SourceIntakeMetadata.model_validate(document.intake_metadata),
            )
            for candidate in (
                intake
                if intake is not None and document.curriculum_version_id is None
                else SourceIntakeMetadata(),
            )
        )

    async def grade_summary(self) -> tuple[MaterialGradeSummaryResponse, ...]:
        ready = and_(
            SourceDocumentModel.active_for_ai.is_(True),
            SourceDocumentModel.metadata_review_required.is_(False),
            SourceDocumentModel.extraction_status == ExtractionStatus.TRUSTED,
        )
        needs_review = and_(
            SourceDocumentModel.active_for_ai.is_(True),
            or_(
                SourceDocumentModel.metadata_review_required.is_(True),
                SourceDocumentModel.extraction_status.in_(
                    (
                        ExtractionStatus.EXTRACTED,
                        ExtractionStatus.IN_REVIEW,
                        ExtractionStatus.FAILED,
                    )
                ),
            ),
        )
        processing = and_(
            SourceDocumentModel.active_for_ai.is_(True),
            SourceDocumentModel.metadata_review_required.is_(False),
            SourceDocumentModel.extraction_status.in_(
                (ExtractionStatus.UPLOADED, ExtractionStatus.EXTRACTION_PENDING)
            ),
        )
        display_grade = case(
            (
                SourceDocumentModel.curriculum_version_id.is_(None),
                SourceDocumentModel.intake_metadata["candidate_grade"].as_integer(),
            ),
            else_=ExamConfigurationModel.grade,
        )
        display_subject = case(
            (
                SourceDocumentModel.curriculum_version_id.is_(None),
                SourceDocumentModel.intake_metadata["subject_label"].as_string(),
            ),
            else_=SubjectModel.name,
        )
        rows = (
            await self._session.execute(
                select(
                    display_grade,
                    func.count(SourceDocumentModel.id),
                    func.count(func.distinct(func.lower(display_subject))),
                    func.count(SourceDocumentModel.id).filter(ready),
                    func.count(SourceDocumentModel.id).filter(needs_review),
                    func.count(SourceDocumentModel.id).filter(processing),
                    func.count(SourceDocumentModel.id).filter(
                        SourceDocumentModel.active_for_ai.is_(False)
                    ),
                )
                .select_from(SourceDocumentModel)
                .outerjoin(
                    CurriculumVersionModel,
                    CurriculumVersionModel.id == SourceDocumentModel.curriculum_version_id,
                )
                .outerjoin(
                    ExamConfigurationModel,
                    ExamConfigurationModel.id == CurriculumVersionModel.exam_configuration_id,
                )
                .outerjoin(SubjectModel, SubjectModel.id == CurriculumVersionModel.subject_id)
                .group_by(display_grade)
            )
        ).all()
        by_grade = {row[0]: row[1:] for row in rows}
        return tuple(
            MaterialGradeSummaryResponse(
                grade=grade,
                material_count=counts[0],
                subject_count=counts[1],
                ready_count=counts[2],
                needs_review_count=counts[3],
                processing_count=counts[4],
                removed_count=counts[5],
            )
            for grade in (*range(1, 14), *((None,) if None in by_grade else ()))
            for counts in (by_grade.get(grade, (0, 0, 0, 0, 0, 0)),)
        )

    async def _validate_confirmation_scope(self, curriculum_version_id: UUID) -> None:
        curriculum = await self._session.get(
            CurriculumVersionModel,
            curriculum_version_id,
            with_for_update=True,
            populate_existing=True,
        )
        if curriculum is None:
            raise SourceCurriculumNotFoundError
        if not curriculum.active:
            raise SourceCurriculumInactiveError
        for model, identifier in (
            (SubjectModel, curriculum.subject_id),
            (MediumModel, curriculum.medium_id),
            (ExamConfigurationModel, curriculum.exam_configuration_id),
        ):
            scope = cast(
                SubjectModel | MediumModel | ExamConfigurationModel | None,
                await self._session.get(
                    model,
                    identifier,
                    with_for_update=True,
                    populate_existing=True,
                ),
            )
            if scope is None or not scope.active:
                raise SourceCurriculumInactiveError

    async def _validate_learning_scope(
        self,
        curriculum_version_id: UUID | None,
        unit_id: UUID | None,
        lesson_id: UUID | None,
    ) -> None:
        if unit_id is not None and curriculum_version_id is None:
            raise SourceLearningScopeMismatchError
        if lesson_id is not None and unit_id is None:
            raise SourceLearningScopeMismatchError
        if curriculum_version_id is None:
            return
        curriculum = await self._session.get(CurriculumVersionModel, curriculum_version_id)
        if curriculum is None:
            raise SourceCurriculumNotFoundError
        if not curriculum.active:
            raise SourceCurriculumInactiveError
        subject_id = getattr(curriculum, "subject_id", None)
        if subject_id is not None:
            subject = await self._session.get(SubjectModel, subject_id)
            if subject is None or not subject.active:
                raise SourceCurriculumInactiveError
        if unit_id is None:
            return
        unit = await self._session.get(CurriculumUnitModel, unit_id)
        if unit is None:
            raise SourceLearningScopeNotFoundError
        if unit.curriculum_version_id != curriculum_version_id:
            raise SourceLearningScopeMismatchError
        if not unit.active:
            raise SourceLearningScopeInactiveError
        if lesson_id is None:
            return
        lesson = await self._session.get(CurriculumLessonModel, lesson_id)
        if lesson is None:
            raise SourceLearningScopeNotFoundError
        if lesson.curriculum_version_id != curriculum_version_id or lesson.unit_id != unit_id:
            raise SourceLearningScopeMismatchError
        if not lesson.active:
            raise SourceLearningScopeInactiveError

    async def _get_for_update(self, document_id: UUID) -> SourceDocumentModel:
        document = await self._session.get(
            SourceDocumentModel,
            document_id,
            with_for_update=True,
        )
        if document is None:
            raise SourceDocumentNotFoundError(document_id)
        return document

    async def _source_has_knowledge(self, document_id: UUID) -> bool:
        return bool(
            await self._session.scalar(
                select(
                    or_(
                        select(HistoricalQuestionModel.id)
                        .where(HistoricalQuestionModel.source_document_id == document_id)
                        .exists(),
                        select(KnowledgeChunkModel.id)
                        .where(KnowledgeChunkModel.source_document_id == document_id)
                        .exists(),
                    )
                )
            )
        )

    async def _find_by_checksum(self, checksum: str) -> SourceDocumentModel | None:
        return cast(
            SourceDocumentModel | None,
            await self._session.scalar(
                select(SourceDocumentModel).where(SourceDocumentModel.checksum_sha256 == checksum)
            ),
        )

    async def _find_likely_metadata_duplicate(
        self,
        *,
        filename: str,
        curriculum_version_id: UUID | None,
        document_type: SourceDocumentType,
        year: int | None,
        paper_code: str | None,
    ) -> SourceDocumentModel | None:
        return cast(
            SourceDocumentModel | None,
            await self._session.scalar(
                select(SourceDocumentModel)
                .where(
                    func.lower(SourceDocumentModel.original_filename) == filename.casefold(),
                    SourceDocumentModel.curriculum_version_id.is_not_distinct_from(
                        curriculum_version_id
                    ),
                    SourceDocumentModel.document_type == document_type,
                    SourceDocumentModel.year.is_not_distinct_from(year),
                    SourceDocumentModel.paper_code.is_not_distinct_from(paper_code),
                    SourceDocumentModel.active_for_ai.is_(True),
                )
                .order_by(SourceDocumentModel.created_at, SourceDocumentModel.id)
                .limit(1)
            ),
        )

    def _audit_use_transition(
        self,
        document: SourceDocumentModel,
        *,
        action: str,
        actor_id: UUID,
        previous_version: int,
        reason: str | None,
    ) -> None:
        self._session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=actor_id,
                action=action,
                resource_type="source_document",
                resource_id=document.id,
                payload={
                    "active_for_ai": document.active_for_ai,
                    "reason": reason,
                    "previous_version": previous_version,
                    "version": document.metadata_scope_version,
                },
            )
        )

    @staticmethod
    def _require_version(document: SourceDocumentModel, expected: int) -> None:
        if document.metadata_scope_version != expected:
            raise ConcurrentMaterialScopeVersionError(
                expected,
                document.metadata_scope_version,
            )

    @staticmethod
    def _material_status(document: SourceDocumentModel) -> MaterialStatus:
        if not document.active_for_ai:
            return MaterialStatus.REMOVED
        if document.metadata_review_required:
            return MaterialStatus.NEEDS_REVIEW
        if document.extraction_status is ExtractionStatus.TRUSTED:
            return MaterialStatus.READY_FOR_AI
        if document.extraction_status in {
            ExtractionStatus.EXTRACTED,
            ExtractionStatus.IN_REVIEW,
            ExtractionStatus.FAILED,
        }:
            return MaterialStatus.NEEDS_REVIEW
        return MaterialStatus.PROCESSING

    @staticmethod
    def _optional_uuid(value: UUID | None) -> str | None:
        return None if value is None else str(value)

    @classmethod
    def _scope_payload(
        cls,
        curriculum_version_id: UUID | None,
        unit_id: UUID | None,
        lesson_id: UUID | None,
    ) -> dict[str, str | None]:
        return {
            "curriculum_version_id": cls._optional_uuid(curriculum_version_id),
            "unit_id": cls._optional_uuid(unit_id),
            "lesson_id": cls._optional_uuid(lesson_id),
        }

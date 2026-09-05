import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.curriculum.models import (
    CurriculumLessonModel,
    CurriculumUnitModel,
    CurriculumVersionModel,
    SubjectModel,
)
from exam_guru_api.documents.domain import ExtractionStatus, SourceDocumentType
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.documents.schemas import MaterialScopeCorrectionRequest, MaterialStatus
from exam_guru_api.documents.service import (
    ConcurrentMaterialScopeVersionError,
    InvalidMaterialRemovalReasonError,
    MaterialScopeImmutableError,
    SourceCurriculumInactiveError,
    SourceCurriculumNotFoundError,
    SourceDocumentNotFoundError,
    SourceDocumentService,
    SourceLearningScopeInactiveError,
    SourceLearningScopeMismatchError,
    SourceLearningScopeNotFoundError,
)
from exam_guru_api.infrastructure.object_storage import ObjectStorage, StoredObject

VALID_PDF = b"%PDF-1.7\nfixture\n%%EOF"
ACTOR_ID = UUID(int=1)


class StubStorage:
    def __init__(self) -> None:
        self.puts = 0

    def put_immutable(self, key: str, data: bytes, *, content_type: str) -> StoredObject:
        self.puts += 1
        return StoredObject(key, key.split("/")[-1][:-4], len(data), "etag")

    def get_bytes(self, key: str) -> bytes:
        raise AssertionError(key)


class ScalarRows:
    def __init__(self, rows: list[SourceDocumentModel]) -> None:
        self._rows = rows

    def all(self) -> list[SourceDocumentModel]:
        return self._rows


class StubSession:
    def __init__(self) -> None:
        self.scalar_results: list[SourceDocumentModel | None] = []
        self.list_rows: list[SourceDocumentModel] = []
        self.curriculum: object | None = SimpleNamespace(active=True)
        self.added: list[object] = []
        self.fail_commit = False
        self.rolled_back = False

    async def scalar(self, _query: object) -> SourceDocumentModel | None:
        return self.scalar_results.pop(0)

    async def scalars(self, _query: object) -> ScalarRows:
        return ScalarRows(self.list_rows)

    async def get(
        self,
        _model: object,
        _identifier: UUID,
        **_kwargs: object,
    ) -> object | None:
        return self.curriculum

    def add(self, model: object) -> None:
        self.added.append(model)

    async def commit(self) -> None:
        if self.fail_commit:
            raise IntegrityError("INSERT", {}, RuntimeError("race"))

    async def rollback(self) -> None:
        self.rolled_back = True

    async def refresh(self, model: SourceDocumentModel) -> None:
        now = datetime.now(UTC)
        model.created_at = now
        model.updated_at = now


class ExecuteRows:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return self._rows


class MaterialSession:
    def __init__(self) -> None:
        self.objects: dict[tuple[type[object], UUID], object] = {}
        self.scalar_results: list[object | None] = []
        self.execute_results: list[list[object]] = []
        self.executed: list[object] = []
        self.added: list[object] = []
        self.commits = 0

    def put(self, value: object) -> None:
        identifier = cast(Any, value).id
        self.objects[(type(value), identifier)] = value

    async def get(
        self,
        model: type[object],
        identifier: UUID,
        **_kwargs: object,
    ) -> object | None:
        return self.objects.get((model, identifier))

    async def scalar(self, _query: object) -> object | None:
        return self.scalar_results.pop(0)

    async def execute(self, query: object) -> ExecuteRows:
        self.executed.append(query)
        return ExecuteRows(self.execute_results.pop(0))

    def add(self, value: object) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, _value: object) -> None:
        return None


def existing_document() -> SourceDocumentModel:
    now = datetime.now(UTC)
    return SourceDocumentModel(
        id=UUID(int=2),
        checksum_sha256="a" * 64,
        object_key=f"sources/aa/{'a' * 64}.pdf",
        original_filename="existing.pdf",
        content_type="application/pdf",
        size_bytes=10,
        document_type=SourceDocumentType.SYLLABUS,
        extraction_status=ExtractionStatus.UPLOADED,
        curriculum_version_id=None,
        unit_id=None,
        lesson_id=None,
        active_for_ai=True,
        removal_reason=None,
        removed_by=None,
        removed_at=None,
        metadata_scope_version=0,
        year=None,
        paper_code=None,
        created_by=ACTOR_ID,
        updated_by=ACTOR_ID,
        created_at=now,
        updated_at=now,
    )


def service(session: StubSession, storage: StubStorage) -> SourceDocumentService:
    return SourceDocumentService(
        cast(AsyncSession, session),
        cast(ObjectStorage, storage),
        max_upload_bytes=1_024,
    )


def test_source_document_service_lists_documents() -> None:
    session = StubSession()
    storage = StubStorage()
    document = existing_document()
    session.list_rows = [document]

    assert asyncio.run(service(session, storage).list_documents()) == [document]


def test_source_document_service_uploads_and_deduplicates() -> None:
    session = StubSession()
    storage = StubStorage()
    session.scalar_results = [None, None]

    created = asyncio.run(
        service(session, storage).upload_pdf(
            filename="source.pdf",
            content_type="application/pdf",
            data=VALID_PDF,
            document_type=SourceDocumentType.SYLLABUS,
            actor_id=ACTOR_ID,
            curriculum_version_id=UUID(int=3),
        )
    )

    assert created.deduplicated is False
    assert storage.puts == 1
    assert len(session.added) == 2

    session.scalar_results = [created.document]
    duplicate = asyncio.run(
        service(session, storage).upload_pdf(
            filename="renamed.pdf",
            content_type="application/pdf",
            data=VALID_PDF,
            document_type=SourceDocumentType.SYLLABUS,
            actor_id=ACTOR_ID,
        )
    )

    assert duplicate.deduplicated is True
    assert duplicate.document is created.document
    assert storage.puts == 1


def test_source_document_service_validates_curriculum_scope() -> None:
    session = StubSession()
    storage = StubStorage()
    session.scalar_results = [None]
    session.curriculum = None

    with pytest.raises(SourceCurriculumNotFoundError):
        asyncio.run(
            service(session, storage).upload_pdf(
                filename="source.pdf",
                content_type="application/pdf",
                data=VALID_PDF,
                document_type=SourceDocumentType.SYLLABUS,
                actor_id=ACTOR_ID,
                curriculum_version_id=UUID(int=3),
            )
        )

    session.scalar_results = [None]
    session.curriculum = SimpleNamespace(active=False)
    with pytest.raises(SourceCurriculumInactiveError):
        asyncio.run(
            service(session, storage).upload_pdf(
                filename="source.pdf",
                content_type="application/pdf",
                data=VALID_PDF,
                document_type=SourceDocumentType.SYLLABUS,
                actor_id=ACTOR_ID,
                curriculum_version_id=UUID(int=3),
            )
        )


def test_source_document_service_recovers_unique_insert_race() -> None:
    session = StubSession()
    storage = StubStorage()
    raced = existing_document()
    session.scalar_results = [None, None, raced]
    session.fail_commit = True

    result = asyncio.run(
        service(session, storage).upload_pdf(
            filename="source.pdf",
            content_type="application/pdf",
            data=VALID_PDF,
            document_type=SourceDocumentType.SYLLABUS,
            actor_id=ACTOR_ID,
        )
    )

    assert result.deduplicated is True
    assert result.document is raced
    assert session.rolled_back


def test_source_document_service_reraises_unresolved_insert_race() -> None:
    session = StubSession()
    storage = StubStorage()
    session.scalar_results = [None, None, None]
    session.fail_commit = True

    with pytest.raises(IntegrityError):
        asyncio.run(
            service(session, storage).upload_pdf(
                filename="source.pdf",
                content_type="application/pdf",
                data=VALID_PDF,
                document_type=SourceDocumentType.SYLLABUS,
                actor_id=ACTOR_ID,
            )
        )


def test_material_remove_restore_is_audited_idempotent_and_cas_protected() -> None:
    session = StubSession()
    storage = StubStorage()
    document = existing_document()
    session.curriculum = document
    material_service = service(session, storage)

    removed = asyncio.run(
        material_service.remove_from_ai_use(
            document.id,
            reason="Uploaded to the wrong grade",
            expected_version=0,
            actor_id=ACTOR_ID,
        )
    )
    assert removed.active_for_ai is False
    assert removed.metadata_scope_version == 1
    assert removed.removal_reason == "Uploaded to the wrong grade"

    with pytest.raises(ConcurrentMaterialScopeVersionError):
        asyncio.run(
            material_service.restore_to_ai_use(
                document.id,
                expected_version=0,
                actor_id=ACTOR_ID,
            )
        )

    restored = asyncio.run(
        material_service.restore_to_ai_use(
            document.id,
            expected_version=1,
            actor_id=ACTOR_ID,
        )
    )
    assert restored.active_for_ai is True
    assert restored.metadata_scope_version == 2
    assert restored.removal_reason is None
    assert [item.action for item in session.added if isinstance(item, AdminAuditEventModel)] == [
        "source_document.removed_from_ai_use",
        "source_document.restored_to_ai_use",
    ]


@pytest.mark.parametrize("reason", ["", " padded", "x" * 513, "bad\nreason", cast(str, 1)])
def test_material_remove_rejects_invalid_bounded_reasons(reason: str) -> None:
    session = StubSession()
    session.curriculum = existing_document()
    with pytest.raises(InvalidMaterialRemovalReasonError):
        asyncio.run(
            service(session, StubStorage()).remove_from_ai_use(
                UUID(int=2),
                reason=reason,
                expected_version=0,
                actor_id=ACTOR_ID,
            )
        )


def test_material_use_idempotency_and_missing_resource_paths() -> None:
    session = StubSession()
    storage = StubStorage()
    document = existing_document()
    document.active_for_ai = False
    document.removal_reason = "Already removed"
    document.removed_by = ACTOR_ID
    document.removed_at = datetime.now(UTC)
    session.curriculum = document
    material_service = service(session, storage)

    assert (
        asyncio.run(
            material_service.remove_from_ai_use(
                document.id,
                reason="Repeated remove",
                expected_version=0,
                actor_id=ACTOR_ID,
            )
        )
        is document
    )
    document.active_for_ai = True
    document.removal_reason = None
    document.removed_by = None
    document.removed_at = None
    assert (
        asyncio.run(
            material_service.restore_to_ai_use(
                document.id,
                expected_version=0,
                actor_id=ACTOR_ID,
            )
        )
        is document
    )
    session.curriculum = None
    with pytest.raises(SourceDocumentNotFoundError):
        asyncio.run(
            material_service.remove_from_ai_use(
                document.id,
                reason="Missing",
                expected_version=0,
                actor_id=ACTOR_ID,
            )
        )


def test_material_scope_correction_is_server_validated_audited_and_provenance_safe() -> None:
    session = MaterialSession()
    storage = StubStorage()
    now = datetime.now(UTC)
    subject = SubjectModel(
        id=UUID(int=300),
        code="MATHEMATICS",
        name="Mathematics",
        active=True,
        created_by=ACTOR_ID,
        updated_by=ACTOR_ID,
    )
    curriculum = CurriculumVersionModel(
        id=UUID(int=301),
        exam_configuration_id=UUID(int=302),
        medium_id=UUID(int=303),
        subject_id=subject.id,
        code="G7-MATH",
        title="Grade 7 Mathematics",
        active=True,
        created_by=ACTOR_ID,
        updated_by=ACTOR_ID,
    )
    unit = CurriculumUnitModel(
        id=UUID(int=304),
        curriculum_version_id=curriculum.id,
        code="UNIT-1",
        title="Numbers",
        ordinal=1,
        active=True,
        created_by=ACTOR_ID,
        updated_by=ACTOR_ID,
    )
    lesson = CurriculumLessonModel(
        id=UUID(int=305),
        curriculum_version_id=curriculum.id,
        unit_id=unit.id,
        code="LESSON-1",
        title="Whole numbers",
        ordinal=1,
        active=True,
        created_by=ACTOR_ID,
        updated_by=ACTOR_ID,
    )
    document = existing_document()
    document.created_at = now
    session.put(subject)
    session.put(curriculum)
    session.put(unit)
    session.put(lesson)
    session.put(document)
    session.scalar_results = [False]
    material_service = SourceDocumentService(
        cast(AsyncSession, session),
        cast(ObjectStorage, storage),
        max_upload_bytes=1_024,
    )

    corrected = asyncio.run(
        material_service.correct_scope(
            document.id,
            curriculum_version_id=curriculum.id,
            unit_id=unit.id,
            lesson_id=lesson.id,
            expected_version=0,
            actor_id=ACTOR_ID,
        )
    )
    assert (corrected.curriculum_version_id, corrected.unit_id, corrected.lesson_id) == (
        curriculum.id,
        unit.id,
        lesson.id,
    )
    assert corrected.metadata_scope_version == 1
    assert any(
        isinstance(item, AdminAuditEventModel) and item.action == "source_document.scope_corrected"
        for item in session.added
    )
    assert (
        asyncio.run(
            material_service.correct_scope(
                document.id,
                curriculum_version_id=curriculum.id,
                unit_id=unit.id,
                lesson_id=lesson.id,
                expected_version=1,
                actor_id=ACTOR_ID,
            )
        )
        is document
    )

    document.extraction_status = ExtractionStatus.TRUSTED
    session.scalar_results = [False]
    with pytest.raises(MaterialScopeImmutableError):
        asyncio.run(
            material_service.correct_scope(
                document.id,
                curriculum_version_id=None,
                unit_id=None,
                lesson_id=None,
                expected_version=1,
                actor_id=ACTOR_ID,
            )
        )
    document.extraction_status = ExtractionStatus.UPLOADED
    session.scalar_results = [True]
    with pytest.raises(MaterialScopeImmutableError):
        asyncio.run(
            material_service.correct_scope(
                document.id,
                curriculum_version_id=None,
                unit_id=None,
                lesson_id=None,
                expected_version=1,
                actor_id=ACTOR_ID,
            )
        )


def test_material_learning_scope_validation_covers_every_consistency_boundary() -> None:
    async def validate(
        session: MaterialSession,
        curriculum_id: UUID | None,
        unit_id: UUID | None,
        lesson_id: UUID | None,
    ) -> None:
        await SourceDocumentService(
            cast(AsyncSession, session),
            cast(ObjectStorage, StubStorage()),
            max_upload_bytes=1_024,
        )._validate_learning_scope(curriculum_id, unit_id, lesson_id)

    with pytest.raises(SourceLearningScopeMismatchError):
        asyncio.run(validate(MaterialSession(), None, UUID(int=1), None))
    with pytest.raises(SourceLearningScopeMismatchError):
        asyncio.run(validate(MaterialSession(), UUID(int=1), None, UUID(int=2)))
    asyncio.run(validate(MaterialSession(), None, None, None))

    session = MaterialSession()
    with pytest.raises(SourceCurriculumNotFoundError):
        asyncio.run(validate(session, UUID(int=10), None, None))
    subject = SubjectModel(
        id=UUID(int=11),
        code="SCIENCE",
        name="Science",
        active=True,
        created_by=ACTOR_ID,
        updated_by=ACTOR_ID,
    )
    curriculum = CurriculumVersionModel(
        id=UUID(int=10),
        exam_configuration_id=UUID(int=12),
        medium_id=UUID(int=13),
        subject_id=subject.id,
        code="CURR",
        title="Curriculum",
        active=False,
        created_by=ACTOR_ID,
        updated_by=ACTOR_ID,
    )
    session.put(curriculum)
    with pytest.raises(SourceCurriculumInactiveError):
        asyncio.run(validate(session, curriculum.id, None, None))
    curriculum.active = True
    session.put(subject)
    subject.active = False
    with pytest.raises(SourceCurriculumInactiveError):
        asyncio.run(validate(session, curriculum.id, None, None))
    subject.active = True
    asyncio.run(validate(session, curriculum.id, None, None))

    unit = CurriculumUnitModel(
        id=UUID(int=14),
        curriculum_version_id=UUID(int=999),
        code="UNIT",
        title="Unit",
        ordinal=1,
        active=True,
        created_by=ACTOR_ID,
        updated_by=ACTOR_ID,
    )
    session.put(unit)
    with pytest.raises(SourceLearningScopeMismatchError):
        asyncio.run(validate(session, curriculum.id, unit.id, None))
    unit.curriculum_version_id = curriculum.id
    unit.active = False
    with pytest.raises(SourceLearningScopeInactiveError):
        asyncio.run(validate(session, curriculum.id, unit.id, None))
    unit.active = True
    with pytest.raises(SourceLearningScopeNotFoundError):
        asyncio.run(validate(session, curriculum.id, UUID(int=404), None))
    asyncio.run(validate(session, curriculum.id, unit.id, None))

    lesson = CurriculumLessonModel(
        id=UUID(int=15),
        curriculum_version_id=UUID(int=999),
        unit_id=unit.id,
        code="LESSON",
        title="Lesson",
        ordinal=1,
        active=True,
        created_by=ACTOR_ID,
        updated_by=ACTOR_ID,
    )
    session.put(lesson)
    with pytest.raises(SourceLearningScopeMismatchError):
        asyncio.run(validate(session, curriculum.id, unit.id, lesson.id))
    lesson.curriculum_version_id = curriculum.id
    lesson.unit_id = UUID(int=998)
    with pytest.raises(SourceLearningScopeMismatchError):
        asyncio.run(validate(session, curriculum.id, unit.id, lesson.id))
    lesson.unit_id = unit.id
    lesson.active = False
    with pytest.raises(SourceLearningScopeInactiveError):
        asyncio.run(validate(session, curriculum.id, unit.id, lesson.id))
    lesson.active = True
    with pytest.raises(SourceLearningScopeNotFoundError):
        asyncio.run(validate(session, curriculum.id, unit.id, UUID(int=405)))
    asyncio.run(validate(session, curriculum.id, unit.id, lesson.id))


def test_material_listing_summary_statuses_and_pagination_are_bounded() -> None:
    session = MaterialSession()
    storage = StubStorage()
    now = datetime.now(UTC)
    statuses = (
        (ExtractionStatus.TRUSTED, True, MaterialStatus.READY_FOR_AI),
        (ExtractionStatus.IN_REVIEW, True, MaterialStatus.NEEDS_REVIEW),
        (ExtractionStatus.UPLOADED, True, MaterialStatus.PROCESSING),
        (ExtractionStatus.TRUSTED, False, MaterialStatus.REMOVED),
    )
    rows: list[object] = []
    for index, (extraction_status, active_for_ai, _) in enumerate(statuses, start=1):
        document = existing_document()
        document.id = UUID(int=400 + index)
        document.original_filename = f"material-{index}.pdf"
        document.extraction_status = extraction_status
        document.active_for_ai = active_for_ai
        document.created_at = now
        rows.append(
            (
                document,
                7,
                UUID(int=500),
                "Mathematics",
                "English",
                "Grade 7 Mathematics",
                "Numbers",
                "Whole numbers",
            )
        )
    session.execute_results = [rows, [(7, 4, 1, 1, 1, 1, 1)]]
    material_service = SourceDocumentService(
        cast(AsyncSession, session),
        cast(ObjectStorage, storage),
        max_upload_bytes=1_024,
    )
    listed = asyncio.run(
        material_service.list_materials(
            grade=7,
            subject_id=UUID(int=500),
            limit=4,
            offset=1,
        )
    )
    assert tuple(item.status for item in listed) == tuple(item[2] for item in statuses)
    summary = asyncio.run(material_service.grade_summary())
    grade_seven = next(item for item in summary if item.grade == 7)
    assert grade_seven.model_dump() == {
        "grade": 7,
        "material_count": 4,
        "subject_count": 1,
        "ready_count": 1,
        "needs_review_count": 1,
        "processing_count": 1,
        "removed_count": 1,
    }
    assert next(item for item in summary if item.grade == 1).material_count == 0
    session.execute_results = [[]]
    assert asyncio.run(material_service.list_materials()) == ()
    with pytest.raises(ValueError, match="pagination"):
        asyncio.run(material_service.list_materials(limit=0))


def test_material_listing_applies_teacher_search_and_filters_server_side() -> None:
    session = MaterialSession()
    session.execute_results = [[]]
    service = SourceDocumentService(
        cast(AsyncSession, session),
        cast(ObjectStorage, StubStorage()),
        max_upload_bytes=1_024,
    )
    medium_id = UUID(int=501)

    assert (
        asyncio.run(
            service.list_materials(
                grade=5,
                subject_id=UUID(int=500),
                medium_id=medium_id,
                material_type=SourceDocumentType.PAST_PAPER,
                year=2025,
                status=MaterialStatus.REMOVED,
                search="grade_11%paper",
            )
        )
        == ()
    )
    compiled = str(cast(Any, session.executed[-1]).compile(compile_kwargs={"literal_binds": True}))
    assert "exam_configurations.grade = 5" in compiled
    assert "media.id =" in compiled
    assert "source_documents.document_type =" in compiled
    assert "source_documents.year = 2025" in compiled
    assert "source_documents.active_for_ai IS false" in compiled
    assert "ESCAPE '/'" in compiled

    for invalid_search in ("   ", "x" * 201):
        with pytest.raises(ValueError, match="search"):
            asyncio.run(service.list_materials(search=invalid_search))


@pytest.mark.parametrize(
    "payload",
    [
        "{",
        "null",
        "[]",
        '{"trusted":true}',
        '{"candidate_grade":true}',
        '{"candidate_grade":"5"}',
        '{"candidate_grade":14}',
        '{"candidate_grade":[5,6]}',
        '{"candidate_grade":5,"candidate_grade":6}',
        '{"year":2025.5}',
        '{"subject_label":" "}',
        '{"subject_label":"\u00a0Maths\u00a0"}',
        '{"evidence":["\u200bhidden"]}',
        '{"evidence":[true]}',
        '{"warnings":null}',
        '{"publisher":"bad\\nvalue"}',
    ],
)
def test_intake_metadata_rejects_malformed_forged_or_ambiguous_values(payload: str) -> None:
    from exam_guru_api.documents.schemas import SourceIntakeMetadata

    with pytest.raises(ValueError, match=r".+"):
        SourceIntakeMetadata.from_json(payload)


def test_intake_metadata_is_bounded_and_preserves_unknown_scope() -> None:
    from exam_guru_api.documents.schemas import SourceIntakeMetadata

    metadata = SourceIntakeMetadata.from_json(
        '{"candidate_grade":null,"subject_label":"Mathematics",'
        '"evidence":["Folder says grades 5 and 6"],"warnings":["Ambiguous grade"]}'
    )
    assert metadata.candidate_grade is None
    assert metadata.subject_label == "Mathematics"
    assert metadata.warnings == ["Ambiguous grade"]
    for payload in (
        {"subject_label": "x" * 201},
        {"evidence": ["x"] * 33},
        {"warnings": ["x" * 1025]},
        {"evidence": ["x" * 1024] * 32},
    ):
        with pytest.raises(ValidationError):
            SourceIntakeMetadata.model_validate(payload)
    with pytest.raises(ValueError, match="size"):
        SourceIntakeMetadata.from_json(" " * 16_385)


def test_upload_intake_metadata_is_preserved_unverified_and_audited() -> None:
    from exam_guru_api.documents.schemas import SourceIntakeMetadata

    session = StubSession()
    session.scalar_results = [None, None]
    metadata = SourceIntakeMetadata(candidate_grade=7, subject_label="Mathematics")
    created = asyncio.run(
        service(session, StubStorage()).upload_pdf(
            filename="unassigned.pdf",
            content_type="application/pdf",
            data=VALID_PDF,
            document_type=SourceDocumentType.OTHER_APPROVED,
            actor_id=ACTOR_ID,
            intake_metadata=metadata,
        )
    )
    assert created.document.curriculum_version_id is None
    assert created.document.metadata_review_required is True
    assert created.document.intake_metadata == metadata.model_dump(mode="json")
    audit = next(item for item in session.added if isinstance(item, AdminAuditEventModel))
    assert audit.payload["intake_metadata"] == created.document.intake_metadata
    assert audit.payload["metadata_review_required"] is True
    session.scalar_results = [created.document]
    replay = asyncio.run(
        service(session, StubStorage()).upload_pdf(
            filename="renamed.pdf",
            content_type="application/pdf",
            data=VALID_PDF,
            document_type=SourceDocumentType.OTHER_APPROVED,
            actor_id=ACTOR_ID,
            intake_metadata=SourceIntakeMetadata(candidate_grade=5),
        )
    )
    assert replay.deduplicated
    assert replay.document.intake_metadata == metadata.model_dump(mode="json")
    assert replay.document.metadata_review_required is True


def test_metadata_confirmation_requires_explicit_valid_curriculum_and_preserves_evidence() -> None:
    document = existing_document()
    document.intake_metadata = {"candidate_grade": None, "warnings": ["Unknown grade"]}
    document.metadata_review_required = True
    session = MaterialSession()
    session.put(document)
    material_service = SourceDocumentService(
        cast(AsyncSession, session),
        cast(ObjectStorage, StubStorage()),
        max_upload_bytes=1024,
    )
    with pytest.raises(SourceLearningScopeMismatchError):
        asyncio.run(
            material_service.correct_scope(
                document.id,
                curriculum_version_id=None,
                unit_id=None,
                lesson_id=None,
                expected_version=0,
                actor_id=ACTOR_ID,
                confirm_intake_metadata=True,
            )
        )
    with pytest.raises(ValidationError):
        MaterialScopeCorrectionRequest(
            curriculum_version_id=None,
            expected_version=0,
            confirm_intake_metadata=True,
        )


@pytest.mark.parametrize(("source_year", "intake_year"), [(None, None), (None, 2024), (2023, 2024)])
def test_confirmation_validates_active_scope_and_requires_review_again_after_reassignment(
    source_year: int | None, intake_year: int | None
) -> None:
    from exam_guru_api.curriculum.models import ExamConfigurationModel, MediumModel

    session = MaterialSession()
    material_service = SourceDocumentService(
        cast(AsyncSession, session),
        cast(ObjectStorage, StubStorage()),
        max_upload_bytes=1024,
    )
    curriculum = CurriculumVersionModel(
        id=UUID(int=1001),
        subject_id=UUID(int=1002),
        medium_id=UUID(int=1003),
        exam_configuration_id=UUID(int=1004),
        active=False,
    )
    with pytest.raises(SourceCurriculumNotFoundError):
        asyncio.run(material_service._validate_confirmation_scope(curriculum.id))
    session.put(curriculum)
    with pytest.raises(SourceCurriculumInactiveError):
        asyncio.run(material_service._validate_confirmation_scope(curriculum.id))
    curriculum.active = True
    with pytest.raises(SourceCurriculumInactiveError):
        asyncio.run(material_service._validate_confirmation_scope(curriculum.id))
    scope_objects: tuple[SubjectModel | MediumModel | ExamConfigurationModel, ...] = (
        SubjectModel(id=curriculum.subject_id, active=True),
        MediumModel(id=curriculum.medium_id, active=True),
        ExamConfigurationModel(id=curriculum.exam_configuration_id, active=True),
    )
    for scope in scope_objects:
        session.put(scope)
    for scope in scope_objects:
        scope.active = False
        with pytest.raises(SourceCurriculumInactiveError):
            asyncio.run(material_service._validate_confirmation_scope(curriculum.id))
        scope.active = True
    document = existing_document()
    document.year = source_year
    document.intake_metadata = {
        "candidate_grade": None,
        "evidence": ["Unresolved source"],
        "year": intake_year,
    }
    document.metadata_review_required = True
    session.put(document)
    session.scalar_results = [False, False]
    evidence = dict(document.intake_metadata)
    confirmed = asyncio.run(
        material_service.correct_scope(
            document.id,
            curriculum_version_id=curriculum.id,
            unit_id=None,
            lesson_id=None,
            expected_version=0,
            actor_id=ACTOR_ID,
            confirm_intake_metadata=True,
        )
    )
    expected_year = source_year if source_year is not None else intake_year
    assert confirmed.year == expected_year
    assert confirmed.metadata_review_required is False
    assert confirmed.metadata_scope_version == 1
    assert confirmed.intake_metadata == evidence
    assert confirmed.extraction_status is ExtractionStatus.UPLOADED
    replay = asyncio.run(
        material_service.correct_scope(
            document.id,
            curriculum_version_id=curriculum.id,
            unit_id=None,
            lesson_id=None,
            expected_version=1,
            actor_id=ACTOR_ID,
            confirm_intake_metadata=True,
        )
    )
    assert replay.year == expected_year
    assert replay.metadata_scope_version == 1
    corrected = asyncio.run(
        material_service.correct_scope(
            document.id,
            curriculum_version_id=None,
            unit_id=None,
            lesson_id=None,
            expected_version=1,
            actor_id=ACTOR_ID,
        )
    )
    assert corrected.year == expected_year
    assert corrected.metadata_review_required is True
    assert corrected.intake_metadata == evidence
    assert session.commits == 2
    audits = [item for item in session.added if isinstance(item, AdminAuditEventModel)]
    assert audits[0].action == "source_document.intake_metadata_confirmed"
    assert audits[0].payload["intake_metadata"] == evidence
    assert audits[0].payload["previous_year"] == source_year
    assert audits[0].payload["year"] == expected_year
    assert audits[1].action == "source_document.scope_corrected"


@pytest.mark.parametrize("metadata_review_required", [True, False])
@pytest.mark.parametrize(
    ("source_year", "intake_year", "expected_year"),
    [(None, None, None), (None, 2024, 2024), (2023, 2024, 2023)],
)
def test_assigned_material_year_keeps_intake_fallback_without_overwriting_source_year(
    metadata_review_required: bool,
    source_year: int | None,
    intake_year: int | None,
    expected_year: int | None,
) -> None:
    document = existing_document()
    document.curriculum_version_id = UUID(int=1001)
    document.metadata_review_required = metadata_review_required
    document.intake_metadata = {"year": intake_year}
    document.year = source_year
    session = MaterialSession()
    session.execute_results = [[(document, 7, UUID(int=500), "Maths", "English", "V1", None, None)]]
    material_service = SourceDocumentService(
        cast(AsyncSession, session), cast(ObjectStorage, StubStorage()), max_upload_bytes=1024
    )
    listed = asyncio.run(material_service.list_materials())
    assert listed[0].year == expected_year
    assert listed[0].metadata_review_required is metadata_review_required
    assert document.year == source_year
    assert session.commits == 0


def test_intake_json_deep_nesting_is_rejected_without_recursion_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from exam_guru_api.documents.schemas import SourceIntakeMetadata

    with pytest.raises(ValueError, match=r"bounded object|valid dictionary"):
        SourceIntakeMetadata.from_json("[" * 2000 + "]" * 2000)

    def exceeded_nesting_limit(_value: str, **_kwargs: object) -> object:
        raise RecursionError

    monkeypatch.setattr("exam_guru_api.documents.schemas.json.loads", exceeded_nesting_limit)
    with pytest.raises(ValueError, match="bounded object"):
        SourceIntakeMetadata.from_json("[" * 2000 + "]" * 2000)


def test_material_scope_request_shape_rejects_forged_lesson_relationships() -> None:
    with pytest.raises(ValidationError, match="unit_id requires curriculum_version_id"):
        MaterialScopeCorrectionRequest(
            curriculum_version_id=None,
            unit_id=UUID(int=1),
            expected_version=0,
        )
    with pytest.raises(ValidationError, match="lesson_id requires unit_id"):
        MaterialScopeCorrectionRequest(
            curriculum_version_id=UUID(int=1),
            lesson_id=UUID(int=2),
            expected_version=0,
        )

import importlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import pytest

from exam_guru_api.core.config import EXTRACTION_ACTOR_MAX_EXECUTION_SECONDS, Settings
from exam_guru_api.documents import jobs
from exam_guru_api.documents.jobs import (
    EXTRACTION_MAX_BACKOFF_MS,
    EXTRACTION_MAX_RETRIES,
    EXTRACTION_MIN_BACKOFF_MS,
    EXTRACTION_QUEUE_NAME,
    EXTRACTION_RECOVERY_MAX_RETRIES,
    EXTRACTION_RECOVERY_TIME_LIMIT_MS,
    EXTRACTION_TIME_LIMIT_MS,
    NATIVE_EXTRACTION_MAX_PAGES,
    DeterministicExtractionDispatcher,
    DramatiqExtractionDispatcher,
    ExtractionDispatcher,
    create_extraction_dispatcher,
    create_ocr_port,
)
from exam_guru_api.documents.tesseract_ocr import TesseractCliOCRAdapter

DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000201")
ACTOR_ID = UUID("00000000-0000-0000-0000-000000000202")


class StubMessage:
    message_id = "broker-message-id"


class RecordingActor:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, document_id: str, actor_id: str) -> StubMessage:
        self.sent.append((document_id, actor_id))
        return StubMessage()


class ClosingStorage:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class StubResources:
    def __init__(self, session: object) -> None:
        self.session = session
        self.closed = False

    @asynccontextmanager
    async def session_factory(self) -> AsyncIterator[object]:
        yield self.session

    async def close(self) -> None:
        self.closed = True


def test_dramatiq_dispatcher_only_enqueues_serializable_ids_and_returns_message_id() -> None:
    actor = RecordingActor()
    dispatcher = DramatiqExtractionDispatcher(actor)

    message_id = dispatcher.dispatch(DOCUMENT_ID, actor_id=ACTOR_ID)

    assert message_id == "broker-message-id"
    assert actor.sent == [(str(DOCUMENT_ID), str(ACTOR_ID))]


def test_deterministic_dispatcher_is_a_recording_fake_for_route_tests() -> None:
    dispatcher = DeterministicExtractionDispatcher(message_id="route-test-message-id")
    protocol_dispatcher: ExtractionDispatcher = dispatcher

    first = protocol_dispatcher.dispatch(DOCUMENT_ID, actor_id=ACTOR_ID)
    second = protocol_dispatcher.dispatch(DOCUMENT_ID, actor_id=ACTOR_ID)

    assert first == second == "route-test-message-id"
    assert dispatcher.dispatched == [
        (DOCUMENT_ID, ACTOR_ID),
        (DOCUMENT_ID, ACTOR_ID),
    ]


def test_api_dispatcher_factory_binds_actor_to_settings_valkey_broker() -> None:
    dispatcher = create_extraction_dispatcher(Settings())

    assert isinstance(dispatcher, DramatiqExtractionDispatcher)
    assert jobs.extract_document.actor_name in jobs.extract_document.broker.get_declared_actors()


def test_ocr_factory_is_explicit_and_maps_every_bounded_tesseract_control() -> None:
    assert create_ocr_port(Settings(environment="test")) is None

    adapter = create_ocr_port(
        Settings(
            environment="test",
            ocr_provider="tesseract",
            ocr_tesseract_executable="fixture-tesseract",
            ocr_tesseract_language="sin+eng",
            ocr_tesseract_max_source_bytes=30_000_000,
            ocr_tesseract_max_pages=16,
            ocr_tesseract_dpi=240,
            ocr_tesseract_batch_size=3,
            ocr_tesseract_timeout_seconds=10.0,
            ocr_tesseract_page_segmentation_mode=6,
            ocr_tesseract_max_pixels_per_page=20_000_000,
            ocr_tesseract_max_command_output_bytes=2_000_000,
        )
    )

    assert isinstance(adapter, TesseractCliOCRAdapter)
    assert adapter.config.executable == "fixture-tesseract"
    assert adapter.config.language == "sin+eng"
    assert adapter.config.allowed_languages == ("sin", "eng")
    assert adapter.config.max_source_bytes == 30_000_000
    assert adapter.config.max_pages == 16
    assert adapter.config.dpi == 240
    assert adapter.config.batch_size == 3
    assert adapter.config.timeout_seconds == 10.0
    assert adapter.config.page_segmentation_mode == 6
    assert adapter.config.max_pixels_per_page == 20_000_000
    assert adapter.config.max_command_output_bytes == 2_000_000


def test_extraction_actor_uses_a_dedicated_queue_and_bounded_execution_policy() -> None:
    assert jobs.extract_document.queue_name == EXTRACTION_QUEUE_NAME
    assert jobs.extract_document.queue_name != "default"
    assert jobs.extract_document.options == {
        "max_retries": EXTRACTION_MAX_RETRIES,
        "min_backoff": EXTRACTION_MIN_BACKOFF_MS,
        "max_backoff": EXTRACTION_MAX_BACKOFF_MS,
        "time_limit": EXTRACTION_TIME_LIMIT_MS,
    }
    assert 0 < EXTRACTION_MAX_RETRIES <= 5
    assert 0 < EXTRACTION_MIN_BACKOFF_MS <= EXTRACTION_MAX_BACKOFF_MS
    assert EXTRACTION_TIME_LIMIT_MS == EXTRACTION_ACTOR_MAX_EXECUTION_SECONDS * 1_000
    assert jobs.recover_extraction_jobs.queue_name == EXTRACTION_QUEUE_NAME
    assert jobs.recover_extraction_jobs.options == {
        "max_retries": EXTRACTION_RECOVERY_MAX_RETRIES,
        "time_limit": EXTRACTION_RECOVERY_TIME_LIMIT_MS,
    }
    assert EXTRACTION_RECOVERY_MAX_RETRIES == 0
    assert EXTRACTION_RECOVERY_TIME_LIMIT_MS == EXTRACTION_TIME_LIMIT_MS


def test_extraction_actor_builds_worker_owned_dependencies_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(environment="test")
    session = object()
    storage = ClosingStorage()
    extractor = object()
    expected_ocr_port = object()
    resources = StubResources(session)
    calls: list[tuple[UUID, UUID]] = []

    class StubExtractionService:
        def __init__(
            self,
            actual_session: object,
            actual_storage: object,
            actual_extractor: object,
            *,
            ocr_port: object,
            ocr_max_pages: int,
            ocr_timeout_seconds: float,
            execution_deadline: float,
        ) -> None:
            assert ocr_max_pages == settings.ocr_tesseract_max_pages
            assert ocr_timeout_seconds == settings.ocr_tesseract_timeout_seconds
            assert execution_deadline == 1_300.0
            actual_ocr_port = ocr_port
            assert (actual_session, actual_storage, actual_extractor, actual_ocr_port) == (
                session,
                storage,
                extractor,
                expected_ocr_port,
            )

        async def extract_native(
            self,
            document_id: UUID,
            *,
            actor_id: UUID,
            preclaimed: bool = False,
        ) -> None:
            assert preclaimed
            calls.append((document_id, actor_id))

    def create_extractor(*, max_pages: int) -> object:
        assert max_pages == NATIVE_EXTRACTION_MAX_PAGES
        return extractor

    monkeypatch.setattr(jobs, "Settings", lambda: settings)
    monkeypatch.setattr(
        jobs,
        "create_resources",
        lambda actual_settings: resources if actual_settings is settings else None,
    )
    monkeypatch.setattr(
        jobs,
        "create_object_storage",
        lambda actual_settings: storage if actual_settings is settings else None,
    )
    import time

    def create_worker_ocr(actual_settings: Settings, *, execution_deadline: float) -> object:
        assert actual_settings is settings
        assert execution_deadline == 1_240.0
        return expected_ocr_port

    monkeypatch.setattr(time, "monotonic", lambda: 1_000.0)
    monkeypatch.setattr(jobs, "PyMuPdfExtractor", create_extractor)
    monkeypatch.setattr(jobs, "create_ocr_port", create_worker_ocr)
    monkeypatch.setattr(jobs, "DocumentExtractionService", StubExtractionService)

    jobs.extract_document(str(DOCUMENT_ID), str(ACTOR_ID))

    assert calls == [(DOCUMENT_ID, ACTOR_ID)]
    assert storage.closed is True
    assert resources.closed is True


def test_extraction_actor_closes_resources_when_the_service_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resources = StubResources(object())
    storage = ClosingStorage()

    class FailingExtractionService:
        def __init__(self, *_dependencies: object, **_named_dependencies: object) -> None:
            pass

        async def extract_native(
            self,
            _document_id: UUID,
            *,
            actor_id: UUID,
            preclaimed: bool = False,
        ) -> None:
            del actor_id
            assert preclaimed
            raise RuntimeError("transient extraction failure")

    monkeypatch.setattr(jobs, "create_resources", lambda _settings: resources)
    monkeypatch.setattr(jobs, "create_object_storage", lambda _settings: storage)
    monkeypatch.setattr(jobs, "PyMuPdfExtractor", lambda *, max_pages: object())
    monkeypatch.setattr(jobs, "DocumentExtractionService", FailingExtractionService)

    with pytest.raises(RuntimeError, match="transient extraction failure"):
        jobs.extract_document(str(DOCUMENT_ID), str(ACTOR_ID))

    assert storage.closed is True
    assert resources.closed is True


def test_extraction_actor_closes_resources_when_storage_setup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resources = StubResources(object())
    monkeypatch.setattr(jobs, "create_resources", lambda _settings: resources)

    def fail_storage(_settings: Settings) -> object:
        raise RuntimeError("storage setup failed")

    monkeypatch.setattr(jobs, "create_object_storage", fail_storage)
    with pytest.raises(RuntimeError, match="storage setup failed"):
        jobs.extract_document(str(DOCUMENT_ID), str(ACTOR_ID))
    assert resources.closed is True


def test_extraction_recovery_actor_uses_internal_bounded_settings_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from exam_guru_api.documents import extraction_outbox

    settings = Settings.model_validate(
        {
            "environment": "test",
            "extraction_recovery_batch_size": 7,
            "extraction_outbox_min_age_seconds": 11,
        }
    )
    session = object()
    dispatcher = object()
    resources = StubResources(session)
    policies: list[object] = []

    class StubRecoveryService:
        def __init__(
            self,
            actual_session: object,
            actual_dispatcher: object,
            policy: object,
        ) -> None:
            assert actual_session is session
            assert actual_dispatcher is dispatcher
            policies.append(policy)

        async def recover(self) -> object:
            return object()

    monkeypatch.setattr(jobs, "Settings", lambda: settings)
    monkeypatch.setattr(
        jobs, "create_resources", lambda actual: resources if actual is settings else None
    )
    monkeypatch.setattr(jobs, "DramatiqExtractionDispatcher", lambda: dispatcher)
    monkeypatch.setattr(extraction_outbox, "ExtractionRecoveryService", StubRecoveryService)

    jobs.recover_extraction_jobs()

    assert len(policies) == 1
    policy = policies[0]
    assert policy.batch_size == 7  # type: ignore[attr-defined]
    assert policy.outbox_min_age_seconds == 11  # type: ignore[attr-defined]
    assert resources.closed is True


def test_worker_import_registers_extraction_and_recovery_actors() -> None:
    worker = importlib.import_module("exam_guru_api.worker")

    registered = worker.broker.get_actor(jobs.extract_document.actor_name)
    recovery = worker.broker.get_actor(jobs.recover_extraction_jobs.actor_name)

    assert registered is jobs.extract_document
    assert registered.queue_name == EXTRACTION_QUEUE_NAME
    assert recovery is jobs.recover_extraction_jobs
    assert recovery.queue_name == EXTRACTION_QUEUE_NAME

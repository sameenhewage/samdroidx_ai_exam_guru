import importlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import pytest

from exam_guru_api.core.config import Settings
from exam_guru_api.documents import jobs
from exam_guru_api.documents.jobs import (
    EXTRACTION_MAX_BACKOFF_MS,
    EXTRACTION_MAX_RETRIES,
    EXTRACTION_MIN_BACKOFF_MS,
    EXTRACTION_QUEUE_NAME,
    EXTRACTION_TIME_LIMIT_MS,
    NATIVE_EXTRACTION_MAX_PAGES,
    DeterministicExtractionDispatcher,
    DramatiqExtractionDispatcher,
    ExtractionDispatcher,
    create_extraction_dispatcher,
)

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
    assert EXTRACTION_TIME_LIMIT_MS > 0


def test_extraction_actor_builds_worker_owned_dependencies_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(environment="test")
    session = object()
    storage = object()
    extractor = object()
    resources = StubResources(session)
    calls: list[tuple[UUID, UUID]] = []

    class StubExtractionService:
        def __init__(
            self,
            actual_session: object,
            actual_storage: object,
            actual_extractor: object,
        ) -> None:
            assert (actual_session, actual_storage, actual_extractor) == (
                session,
                storage,
                extractor,
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
    monkeypatch.setattr(jobs, "PyMuPdfExtractor", create_extractor)
    monkeypatch.setattr(jobs, "DocumentExtractionService", StubExtractionService)

    jobs.extract_document(str(DOCUMENT_ID), str(ACTOR_ID))

    assert calls == [(DOCUMENT_ID, ACTOR_ID)]
    assert resources.closed is True


def test_extraction_actor_closes_resources_when_the_service_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resources = StubResources(object())

    class FailingExtractionService:
        def __init__(self, *_dependencies: object) -> None:
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
    monkeypatch.setattr(jobs, "create_object_storage", lambda _settings: object())
    monkeypatch.setattr(jobs, "PyMuPdfExtractor", lambda *, max_pages: object())
    monkeypatch.setattr(jobs, "DocumentExtractionService", FailingExtractionService)

    with pytest.raises(RuntimeError, match="transient extraction failure"):
        jobs.extract_document(str(DOCUMENT_ID), str(ACTOR_ID))

    assert resources.closed is True


def test_worker_import_registers_the_extraction_actor() -> None:
    worker = importlib.import_module("exam_guru_api.worker")

    registered = worker.broker.get_actor(jobs.extract_document.actor_name)

    assert registered is jobs.extract_document
    assert registered.queue_name == EXTRACTION_QUEUE_NAME

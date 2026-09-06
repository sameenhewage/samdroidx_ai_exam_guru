import asyncio
import hashlib
import threading
import time
from collections.abc import AsyncIterator, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, BinaryIO, Never, cast
from uuid import UUID

import dramatiq
import pymupdf
import pytest

from exam_guru_api.core.config import Settings
from exam_guru_api.documents import page_images, page_reading
from exam_guru_api.documents import page_reading_jobs as jobs
from exam_guru_api.documents.fidelity import MAX_TEXT_CHARACTERS
from exam_guru_api.documents.ocr import (
    MalformedOCROutputError,
    OCRContractError,
    OCRInputError,
    OCROutputLimitError,
    OCRPage,
    OCRProcessError,
    OCRResult,
    OCRUnavailableError,
)
from exam_guru_api.documents.page_images import PageImageArtifacts, PageImageError, PageImageLimits
from exam_guru_api.documents.page_reading import (
    FilePageReader,
    NativePageText,
    PageReadingConfiguration,
    PageReadingResult,
)
from exam_guru_api.documents.tesseract_ocr import RenderedPageImage
from exam_guru_api.infrastructure.object_storage import LocalFileObjectStorage
from tests.test_tesseract_file_input import FileCommandRunner, file_config, source_pdf


def test_clean_native_page_is_an_unverified_candidate_without_ocr(tmp_path: Path) -> None:
    path = source_pdf(tmp_path / "native.pdf")
    runner = FileCommandRunner(tmp_path / "models")
    reader = FilePageReader(command_runner=runner)
    config = PageReadingConfiguration(ocr=file_config(runner.models))
    with path.open("rb") as source, reader.open(source, configuration=config) as document:
        result = document.read_page(1)
    assert [candidate.method for candidate in result.candidates] == ["native"]
    assert "Read the question" in result.candidates[0].raw_text
    assert result.candidates[0].provenance["engine"] == "pymupdf"
    assert result.candidates[0].provenance["page_number"] == 1
    assert result.candidates[0].provenance["automatic_verification"] is False
    assert result.failure_code is None
    assert runner.calls == []


@pytest.mark.parametrize(
    ("font", "language"),
    [
        ("NIEsin1", "sin+eng"),
        ("FMAbhaya", "sin+eng"),
        ("NIEtml", "tam+eng"),
        ("Bamini", "tam+eng"),
    ],
)
def test_suspicious_nonempty_legacy_fonts_route_per_page_without_mappings(
    tmp_path: Path, font: str, language: str
) -> None:
    path = source_pdf(tmp_path / "legacy.pdf", font_name=font)
    runner = FileCommandRunner(tmp_path / "models")
    reader = FilePageReader(command_runner=runner)
    with (
        path.open("rb") as source,
        reader.open(
            source, configuration=PageReadingConfiguration(ocr=file_config(runner.models))
        ) as document,
    ):
        result = document.read_page(1)
    assert [candidate.method for candidate in result.candidates] == ["native", "ocr"]
    assert result.candidates[0].raw_text.strip()
    assert result.candidates[0].provenance["mappings_applied"] == []
    assert result.candidates[1].provenance["ocr_languages"] == language.split("+")
    ocr_calls = [call for call in runner.calls if "tsv" in call]
    assert len(ocr_calls) == 1
    assert ocr_calls[0][ocr_calls[0].index("-l") + 1] == language
    assert result.candidates[1].provenance["traineddata_sha256"] == {
        code: hashlib.sha256(f"fixture {code}".encode()).hexdigest() for code in language.split("+")
    }
    assert result.candidates[1].provenance["available_languages"] == ["eng", "sin", "tam"]
    assert result.failure_code is None


def test_explicit_reread_forces_ocr_even_for_clean_native_text(tmp_path: Path) -> None:
    path = source_pdf(tmp_path / "native.pdf")
    runner = FileCommandRunner(tmp_path / "models")
    reader = FilePageReader(command_runner=runner)
    config = PageReadingConfiguration(ocr=file_config(runner.models), force_ocr=True)
    with path.open("rb") as source, reader.open(source, configuration=config) as document:
        result = document.read_page(1)
    assert [candidate.method for candidate in result.candidates] == ["native", "ocr"]
    assert len(runner.image_hashes) == 1


def test_ocr_timeout_preserves_native_and_separate_failed_attempt(tmp_path: Path) -> None:
    path = source_pdf(tmp_path / "legacy.pdf", font_name="FMAbhaya")
    runner = FileCommandRunner(tmp_path / "models", fail_ocr=True)
    reader = FilePageReader(command_runner=runner)
    config = PageReadingConfiguration(ocr=file_config(runner.models))
    with path.open("rb") as source, reader.open(source, configuration=config) as document:
        result = document.read_page(1)
    assert result.failure_code == "ocr_timeout"
    native, ocr = result.candidates
    assert native.raw_text.strip()
    assert ocr.method == "ocr"
    assert ocr.raw_text == ""
    assert ocr.provenance["failure_code"] == "ocr_timeout"
    image = ocr.provenance["page_image"]
    assert isinstance(image, dict)
    assert image["sha256"] == runner.image_hashes[0]
    assert ocr.provenance["engine_version"] == "5.4.1"
    assert "PRIVATE" not in repr(result)


def test_missing_tamil_is_explicitly_blocked_with_full_requested_language_evidence(
    tmp_path: Path,
) -> None:
    path = source_pdf(tmp_path / "legacy.pdf", font_name="Bamini")
    runner = FileCommandRunner(tmp_path / "models", languages=("eng", "sin"))
    reader = FilePageReader(command_runner=runner)
    config = PageReadingConfiguration(ocr=file_config(runner.models))
    with path.open("rb") as source, reader.open(source, configuration=config) as document:
        result = document.read_page(1)
    assert result.failure_code == "ocr_languages_unavailable"
    assert result.candidates[0].raw_text.strip()
    failed = result.candidates[-1]
    assert failed.provenance["ocr_languages"] == ["tam", "eng"]
    assert failed.provenance["missing_languages"] == ["tam"]
    assert failed.provenance["available_languages"] == ["eng", "sin"]
    assert not runner.image_hashes


def test_unsafe_native_codepoints_are_preserved_verbatim_for_bytea_not_normalized_away(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    raw = "broken\x00\ud800\ue000source"
    monkeypatch.setattr(
        page_reading,
        "extract_native_page",
        lambda *_args: NativePageText(raw_text=raw, font_names=("FMAbhaya",), image_coverage=0.0),
    )
    runner = FileCommandRunner(tmp_path / "models", fail_ocr=True)
    reader = FilePageReader(command_runner=runner)
    with (
        path.open("rb") as source,
        reader.open(
            source, configuration=PageReadingConfiguration(ocr=file_config(runner.models))
        ) as document,
    ):
        result = document.read_page(1)
    assert result.candidates[0].raw_text.encode("utf-8", errors="surrogatepass") == raw.encode(
        "utf-8", errors="surrogatepass"
    )
    assert result.failure_code == "ocr_timeout"


def test_native_reader_has_no_thousand_page_product_cap(tmp_path: Path) -> None:
    path = source_pdf(tmp_path / "large.pdf", pages=1001)
    reader = FilePageReader()
    with (
        path.open("rb") as source,
        reader.open(source, configuration=PageReadingConfiguration()) as document,
    ):
        assert document.page_count == 1001
        assert "1001" in document.read_page(1001).candidates[0].raw_text


def test_reading_configuration_is_json_roundtrippable_and_cannot_enable_legacy_mappings(
    tmp_path: Path,
) -> None:
    runner = FileCommandRunner(tmp_path / "best")
    configuration = PageReadingConfiguration(
        force_ocr=True,
        expected_languages=("ta",),
        ocr=replace(file_config(runner.models), model_family="best"),
    )
    assert PageReadingConfiguration.from_dict(configuration.to_dict()) == configuration
    with pytest.raises(ValueError, match="unsupported source page reading configuration"):
        PageReadingConfiguration.from_dict({"mapping": "unlicensed-font-map"})


@pytest.mark.parametrize("languages", [("de",), ("sin",), ("si", "si")])
def test_invalid_expected_languages_fail_before_work(languages: tuple[str, ...]) -> None:
    with pytest.raises(ValueError, match="invalid source page reading configuration"):
        PageReadingConfiguration(expected_languages=languages)


def test_language_configuration_failure_still_preserves_native_and_failed_ocr(
    tmp_path: Path,
) -> None:
    path = source_pdf(tmp_path / "tamil.pdf", font_name="Bamini")
    runner = FileCommandRunner(tmp_path / "models")
    configuration = PageReadingConfiguration(
        ocr=replace(file_config(runner.models), allowed_languages=("eng",))
    )
    with (
        path.open("rb") as source,
        FilePageReader(command_runner=runner).open(source, configuration=configuration) as document,
    ):
        result = document.read_page(1)
    assert result.failure_code == "ocr_configuration_invalid"
    native, ocr = result.candidates
    assert native.raw_text.strip()
    assert ocr.raw_text == ""
    assert ocr.provenance["ocr_languages"] == ["tam", "eng"]
    assert ocr.provenance["failure_code"] == "ocr_configuration_invalid"
    assert not runner.calls


def test_native_failure_still_attempts_ocr_and_keeps_failure_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_native(*_args: object) -> NativePageText:
        raise RuntimeError("PRIVATE SOURCE")

    monkeypatch.setattr(page_reading, "extract_native_page", fail_native)
    path = source_pdf(tmp_path / "source.pdf")
    runner = FileCommandRunner(tmp_path / "models")
    configuration = PageReadingConfiguration(
        expected_languages=("en",), ocr=file_config(runner.models)
    )
    with (
        path.open("rb") as source,
        FilePageReader(command_runner=runner).open(source, configuration=configuration) as document,
    ):
        result = document.read_page(1)
    native, ocr = result.candidates
    assert native.raw_text == ""
    assert native.provenance["failure_code"] == "native_extraction_failed"
    assert ocr.raw_text == "Question"
    assert result.failure_code is None
    assert "PRIVATE" not in repr(result)


def test_mixed_source_routes_each_page_independently_of_document_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    texts = (
        "Read the question and choose the answer",
        "தமிழ் மொழி வினா",
        "සිංහල ප්‍රශ්නය",
        "සිංහල தமிழ் Read the question",
    )
    monkeypatch.setattr(
        page_reading,
        "extract_native_page",
        lambda _document, number: NativePageText(raw_text=texts[number - 1]),
    )
    path = source_pdf(tmp_path / "mixed.pdf", pages=4)
    runner = FileCommandRunner(tmp_path / "models")
    configuration = PageReadingConfiguration(
        expected_languages=("si",), force_ocr=True, ocr=file_config(runner.models)
    )
    with (
        path.open("rb") as source,
        FilePageReader(command_runner=runner).open(source, configuration=configuration) as document,
    ):
        results = [document.read_page(number) for number in range(1, 5)]
    assert [result.candidates[-1].provenance["ocr_languages"] for result in results] == [
        ["eng"],
        ["tam", "eng"],
        ["sin", "eng"],
        ["sin", "tam", "eng"],
    ]
    assert all(result.failure_code is None for result in results)
    assert all(
        result.candidates[0].raw_text == text for result, text in zip(results, texts, strict=True)
    )


def test_undetermined_page_with_no_available_languages_is_a_durable_failed_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        page_reading, "extract_native_page", lambda *_args: NativePageText(raw_text="")
    )
    path = source_pdf(tmp_path / "unknown.pdf")
    runner = FileCommandRunner(tmp_path / "models", languages=())
    with (
        path.open("rb") as source,
        FilePageReader(command_runner=runner).open(
            source, configuration=PageReadingConfiguration(ocr=file_config(runner.models))
        ) as document,
    ):
        result = document.read_page(1)
    assert result.failure_code == "ocr_languages_unavailable"
    native, ocr = result.candidates
    assert native.method == "native"
    assert ocr.provenance["missing_languages"] == ["sin", "tam", "eng"]
    assert ocr.provenance["available_languages"] == []
    assert not runner.image_hashes


class ReadMessage:
    message_id = "source-read-message"


class ReadActor:
    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[str] = []
        self.fail = fail

    def send(self, job_id: str) -> ReadMessage:
        self.sent.append(job_id)
        if self.fail:
            raise ConnectionError("PRIVATE SOURCE")
        return ReadMessage()


@pytest.mark.parametrize("fail", [False, True])
def test_dispatch_helper_sends_only_durable_id_and_sanitizes_failures(
    fail: bool, caplog: pytest.LogCaptureFixture
) -> None:
    actor = ReadActor(fail=fail)
    job_id = UUID(int=91)
    dispatched = asyncio.run(
        jobs.dispatch_source_read(job_id, jobs.DramatiqSourceReadDispatcher(actor))
    )
    assert dispatched is not fail
    assert actor.sent == [str(job_id)]
    assert "PRIVATE" not in caplog.text


def test_reading_actor_entrypoints_use_only_job_id_and_have_bounded_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[UUID | str] = []

    async def read_job(job_id: UUID) -> None:
        seen.append(job_id)

    async def recover() -> None:
        seen.append("recovery")

    monkeypatch.setattr(jobs, "_read_source", read_job)
    monkeypatch.setattr(jobs, "_recover_source_read_jobs", recover)
    job_id = UUID(int=92)
    jobs.read_source(str(job_id))
    jobs.recover_source_read_jobs()
    assert seen == [job_id, "recovery"]
    for actor in (jobs.read_source, jobs.recover_source_read_jobs):
        assert actor.queue_name == "source-page-reading"
        assert actor.options == {"max_retries": 0, "time_limit": jobs.SOURCE_READ_TIME_LIMIT_MS}
    assert jobs.SOURCE_READ_EXECUTION_SECONDS * 1000 < jobs.SOURCE_READ_TIME_LIMIT_MS
    assert jobs.SOURCE_READ_TIME_LIMIT_MS < jobs.SOURCE_READ_LEASE_SECONDS * 1000


def test_dispatcher_factory_registers_both_read_and_recovery_actors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Broker:
        def __init__(self) -> None:
            self.actors: list[object] = []

        def declare_actor(self, actor: object) -> None:
            self.actors.append(actor)

    broker = Broker()
    selected: list[object] = []
    monkeypatch.setattr(jobs, "RedisBroker", lambda **_kwargs: broker)
    monkeypatch.setattr(dramatiq, "set_broker", selected.append)
    for actor in (jobs.read_source, jobs.recover_source_read_jobs):
        monkeypatch.setattr(actor, "broker", actor.broker)
    dispatcher = jobs.create_source_read_dispatcher(Settings(environment="test"))
    assert isinstance(dispatcher, jobs.DramatiqSourceReadDispatcher)
    assert selected == [broker]
    assert broker.actors == [jobs.read_source, jobs.recover_source_read_jobs]
    assert cast(object, jobs.read_source.broker) is broker
    assert cast(object, jobs.recover_source_read_jobs.broker) is broker


@pytest.mark.parametrize("status", ["queued", "completed"])
@pytest.mark.parametrize("failure", [None, "create", "run", "close"])
def test_actor_runtime_closes_resources_and_dispatches_only_continuations(
    monkeypatch: pytest.MonkeyPatch, status: str, failure: str | None
) -> None:
    sentinel = object()
    job_id = UUID(int=93)
    actor = ReadActor()

    class Resources:
        closed = False

        @asynccontextmanager
        async def session_factory(self) -> AsyncIterator[object]:
            yield sentinel

        async def close(self) -> None:
            self.closed = True

    class Storage:
        closed = False

        def close(self) -> None:
            self.closed = True
            if failure == "close":
                raise RuntimeError("fixture close failure")

    resources = Resources()
    storage = Storage()

    def create_storage(_settings: object) -> Storage:
        if failure == "create":
            raise RuntimeError("fixture create failure")
        return storage

    async def run_job(
        session: object,
        actual_id: UUID,
        *,
        storage: object,
        image_artifacts: object,
    ) -> jobs.SourceReadResult:
        assert session is sentinel
        assert actual_id == job_id
        assert storage is not None
        assert image_artifacts is sentinel
        if failure == "run":
            raise RuntimeError("fixture run failure")
        return jobs.SourceReadResult(job_id, True, status, 9, 8, None)

    monkeypatch.setattr(jobs, "Settings", lambda: object())
    monkeypatch.setattr(jobs, "create_resources", lambda _settings: resources)
    monkeypatch.setattr(jobs, "create_object_storage", create_storage)
    monkeypatch.setattr(
        jobs, "create_page_image_artifacts", lambda _settings: sentinel, raising=False
    )
    monkeypatch.setattr(jobs, "run_source_read", run_job)
    dispatcher = jobs.DramatiqSourceReadDispatcher(actor)
    monkeypatch.setattr(jobs, "DramatiqSourceReadDispatcher", lambda: dispatcher)
    if failure is None:
        asyncio.run(jobs._read_source(job_id))
    else:
        with pytest.raises(RuntimeError, match=f"fixture {failure} failure"):
            asyncio.run(jobs._read_source(job_id))
    assert resources.closed
    assert storage.closed is (failure != "create")
    assert actor.sent == (
        [str(job_id)] if status == "queued" and failure in {None, "close"} else []
    )


def test_recovery_runtime_closes_resources_when_recovery_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Resources:
        closed = False

        @asynccontextmanager
        async def session_factory(self) -> AsyncIterator[object]:
            yield object()

        async def close(self) -> None:
            self.closed = True

    resources = Resources()

    async def recover(_session: object, _dispatcher: object) -> None:
        raise RuntimeError("fixture recovery failure")

    monkeypatch.setattr(jobs, "Settings", lambda: object())
    monkeypatch.setattr(jobs, "create_resources", lambda _settings: resources)
    monkeypatch.setattr(jobs, "recover_source_reads", recover)
    with pytest.raises(RuntimeError, match="fixture recovery failure"):
        asyncio.run(jobs._recover_source_read_jobs())
    assert resources.closed


@pytest.mark.parametrize(
    ("configuration", "message"),
    [
        ({"ocr": {"executable": "x" * 65536}}, "configuration exceeds its bound"),
        ({"expected_languages": "si"}, "invalid source page reading configuration"),
        ({"expected_languages": [1]}, "invalid source page reading configuration"),
        ({"ocr": []}, "invalid source page reading configuration"),
        ({"ocr": {"unknown": True}}, "invalid source page reading configuration"),
        ({"ocr": {"allowed_languages": None}}, "invalid OCR languages"),
        ({"ocr": {"tessdata_directory": 42}}, "invalid OCR model directory"),
        ({"force_ocr": "yes"}, "invalid source page reading configuration"),
    ],
)
def test_stored_reading_configuration_rejects_malformed_and_oversized_values(
    configuration: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        PageReadingConfiguration.from_dict(configuration)


@pytest.mark.parametrize("number", [0, -1, True, "1", 2])
def test_reader_rejects_invalid_or_missing_page_before_ocr(tmp_path: Path, number: object) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    runner = FileCommandRunner(tmp_path / "models")
    with (
        path.open("rb") as source,
        FilePageReader(command_runner=runner).open(
            source, configuration=PageReadingConfiguration()
        ) as reader,
    ):
        with pytest.raises(ValueError, match="source page is out of range"):
            reader.read_page(cast(int, number))
        assert reader.read_page(1).failure_code is None
    assert runner.calls == []


def test_native_image_coverage_tracks_actual_page_image_bounds(tmp_path: Path) -> None:
    from tests.test_page_images import png_bytes

    path = tmp_path / "half-image.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=180, height=180)
        page.insert_image(pymupdf.Rect(0, 0, 90, 180), stream=png_bytes(), keep_proportion=False)
        page.insert_text((100, 30), "Read this question", fontsize=5)
        document.save(path)
    with pymupdf.open(path) as document:
        native = page_reading.extract_native_page(document, 1)
    assert native.image_coverage == pytest.approx(0.5)
    assert "Read this question" in native.raw_text
    assert native.font_names


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (OCRUnavailableError("PRIVATE"), "ocr_unavailable"),
        (OCRInputError("PRIVATE"), "ocr_input_rejected"),
        (OCROutputLimitError("PRIVATE"), "ocr_output_limit"),
        (OCRProcessError("PRIVATE"), "ocr_process_failed"),
        (OCRContractError("PRIVATE"), "ocr_output_invalid"),
        (MalformedOCROutputError("PRIVATE"), "ocr_output_invalid"),
        (OSError("PRIVATE"), "ocr_unavailable"),
        (RuntimeError("PRIVATE"), "ocr_failed"),
    ],
)
def test_reader_failure_codes_are_allowlisted_and_do_not_echo_provider_details(
    error: Exception, code: str
) -> None:
    assert page_reading._failure_code(error) == code


def test_native_text_over_limit_records_hash_and_recovers_on_the_next_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = "A" * MAX_TEXT_CHARACTERS + "\ud800"
    path = source_pdf(tmp_path / "source.pdf")
    runner = FileCommandRunner(tmp_path / "models")
    with (
        path.open("rb") as source,
        FilePageReader(command_runner=runner).open(
            source, configuration=PageReadingConfiguration()
        ) as reader,
    ):
        with monkeypatch.context() as context:
            context.setattr(page_reading, "extract_native_page", lambda *_args: NativePageText(raw))
            result = reader.read_page(1)
        recovered = reader.read_page(1)
    assert result.failure_code == "native_text_limit"
    (candidate,) = result.candidates
    assert candidate.raw_text == ""
    assert candidate.provenance["raw_character_count"] == len(raw)
    assert (
        candidate.provenance["raw_sha256"]
        == hashlib.sha256(raw.encode("utf-8", errors="surrogatepass")).hexdigest()
    )
    assert candidate.provenance["automatic_verification"] is False
    assert recovered.failure_code is None
    assert "Read the question" in recovered.candidates[0].raw_text
    assert not runner.calls


@pytest.mark.parametrize("extra", [0, 1])
def test_ocr_text_limit_preserves_exact_boundary_and_hashes_rejected_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: int
) -> None:
    from exam_guru_api.documents.tesseract_ocr import TesseractCliOCRAdapter

    raw = "Q" * (MAX_TEXT_CHARACTERS + extra)
    result = OCRResult(
        engine="tesseract-cli", engine_version="5.4.1", config={}, pages=(OCRPage(1, raw),)
    )
    monkeypatch.setattr(TesseractCliOCRAdapter, "extract_file", lambda *_args, **_kwargs: result)
    path = source_pdf(tmp_path / "source.pdf")
    runner = FileCommandRunner(tmp_path / "models")
    with (
        path.open("rb") as source,
        FilePageReader(command_runner=runner).open(
            source,
            configuration=PageReadingConfiguration(force_ocr=True, ocr=file_config(runner.models)),
        ) as reader,
    ):
        read = reader.read_page(1)
    native, ocr = read.candidates
    assert native.raw_text
    assert ocr.provenance["automatic_verification"] is False
    if extra:
        assert read.failure_code == "ocr_text_limit"
        assert ocr.raw_text == ""
        assert ocr.provenance["raw_character_count"] == len(raw)
        assert ocr.provenance["raw_sha256"] == hashlib.sha256(raw.encode()).hexdigest()
    else:
        assert read.failure_code is None
        assert ocr.raw_text == raw


@pytest.mark.parametrize("failure", ["expired", "timeout", "raster"])
def test_native_render_failure_is_explicit_without_discarding_native_text_or_invoking_ocr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    runner = FileCommandRunner(tmp_path / "models")
    captures: list[object] = []

    def render_failure(*_args: Any, **_kwargs: Any) -> Never:
        raise PageImageError(
            "source_page_image_timeout"
            if failure == "timeout"
            else "source_page_image_raster_limit"
        )

    if failure != "expired":
        monkeypatch.setattr(page_reading, "render_page_image", render_failure)
    with (
        path.open("rb") as source,
        FilePageReader(
            command_runner=runner,
            on_render=captures.append,
            execution_deadline=0 if failure == "expired" else None,
        ).open(source, configuration=PageReadingConfiguration()) as reader,
    ):
        result = reader.read_page(1)
    assert result.failure_code == (
        "page_image_render_failed" if failure == "raster" else "page_image_timeout"
    )
    assert "Read the question" in result.candidates[0].raw_text
    assert result.candidates[0].provenance["failure_code"] == result.failure_code
    assert result.candidates[0].provenance["page_image"] == {
        "page_number": 1,
        "failure_code": result.failure_code,
    }
    assert not captures
    assert not runner.calls


@pytest.mark.parametrize("layer_warning", [False, True])
def test_four_worker_threads_persist_each_native_page_without_exceeding_two_render_processes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, layer_warning: bool
) -> None:
    from tests.test_page_images import image_source, native_layer_pdf

    path = (
        native_layer_pdf(tmp_path / "source.pdf")
        if layer_warning
        else source_pdf(tmp_path / "source.pdf", pages=2)
    )
    source = image_source(path, pages=2)
    storage = LocalFileObjectStorage(root=tmp_path / "originals", max_object_bytes=1024 * 1024)
    storage.put_immutable(source.object_key, path.read_bytes(), content_type="application/pdf")
    artifacts = PageImageArtifacts(root=tmp_path / "images")
    runner = FileCommandRunner(tmp_path / "models")
    configuration = PageReadingConfiguration(ocr=file_config(runner.models))
    barrier = threading.Barrier(4)
    lock = threading.Lock()
    arrived: set[int] = set()
    active = 0
    maximum_active = 0
    children: list[asyncio.subprocess.Process] = []
    render = page_images.render_page_image
    run_renderer = page_images._run_renderer
    spawn = asyncio.create_subprocess_exec

    @contextmanager
    def simultaneous_render(
        stream: BinaryIO, number: int, **kwargs: Any
    ) -> Iterator[RenderedPageImage]:
        identifier = threading.get_ident()
        with lock:
            first = identifier not in arrived
            arrived.add(identifier)
        if first:
            barrier.wait(timeout=30)
        with render(stream, number, **kwargs) as image:
            yield image

    async def tracked_renderer(
        source_fd: int, output_fd: int, number: int, limits: PageImageLimits, count: int | None
    ) -> dict[str, object]:
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        try:
            return await run_renderer(source_fd, output_fd, number, limits, count)
        finally:
            with lock:
                active -= 1

    async def tracked_spawn(*args: str, **kwargs: Any) -> asyncio.subprocess.Process:
        child = await spawn(*args, **kwargs)
        with lock:
            children.append(child)
        return child

    monkeypatch.setattr(page_reading, "render_page_image", simultaneous_render)
    monkeypatch.setattr(page_images, "_run_renderer", tracked_renderer)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", tracked_spawn)

    async def read_document() -> list[PageReadingResult]:
        reader = FilePageReader(
            command_runner=runner,
            on_render=artifacts.observer(source),
            execution_deadline=time.monotonic() + 120,
        )
        async with jobs._open_reading(storage, source.object_key, reader, configuration) as (
            opened,
            count,
            executor,
        ):
            assert count == 2
            return [
                await asyncio.get_running_loop().run_in_executor(executor, opened.read_page, number)
                for number in (1, 2)
            ]

    def read_job(_number: int) -> list[PageReadingResult]:
        return asyncio.run(read_document())

    try:
        with ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="worker-reading-test"
        ) as executor:
            results = [page for document in executor.map(read_job, range(4)) for page in document]
    finally:
        storage.close()
    assert len(results) == 8
    assert [result.failure_code for result in results] == [None] * 8
    assert maximum_active == 2
    assert active == 0
    assert len(children) == 8
    assert all(child.returncode == 0 for child in children)
    assert not runner.calls
    per_page: dict[int, str] = {}
    for result in results:
        (candidate,) = result.candidates
        assert candidate.method == "native"
        assert f"answer {result.page_number}" in candidate.raw_text
        assert candidate.provenance["automatic_verification"] is False
        metadata = cast(dict[str, object], candidate.provenance["page_image"])
        png = artifacts.read(metadata, source=source, page_number=result.page_number)
        digest = hashlib.sha256(png).hexdigest()
        assert digest == metadata["sha256"]
        if result.page_number in per_page:
            assert per_page[result.page_number] == digest
        per_page[result.page_number] = digest
    assert per_page[1] != per_page[2]

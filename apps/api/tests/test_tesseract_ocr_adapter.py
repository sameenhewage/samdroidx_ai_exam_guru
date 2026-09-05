import csv
import hashlib
import io
import json
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pymupdf
import pytest

from exam_guru_api.documents.ocr import OCRPort, OCRRequest
from exam_guru_api.documents.tesseract_ocr import (
    CommandResult,
    SubprocessCommandRunner,
    TesseractCliOCRAdapter,
    TesseractConfigError,
    TesseractInputError,
    TesseractInputViolation,
    TesseractMalformedOutputError,
    TesseractOCRConfig,
    TesseractOutputLimitError,
    TesseractProcessError,
    TesseractTimeoutError,
    TesseractUnavailableError,
)

TSV_HEADER = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
    "left\ttop\twidth\theight\tconf\ttext\n"
)
VALID_TSV = (
    TSV_HEADER
    + "1\t1\t0\t0\t0\t0\t0\t0\t100\t120\t-1\t\n"
    + "2\t1\t1\t0\t0\t0\t10\t20\t60\t30\t-1\t\n"
    + "5\t1\t1\t1\t1\t1\t10\t20\t30\t10\t90\tQuestion\n"
    + "5\t1\t1\t1\t1\t2\t45\t20\t10\t10\t80\t1\n"
    + "5\t1\t1\t1\t2\t1\t10\t40\t50\t10\t70\tChoose\n"
    + "2\t1\t2\t0\t0\t0\t5\t80\t80\t10\t-1\t\n"
    + "5\t1\t2\t1\t1\t1\t5\t80\t15\t10\t60\t(A)\n"
    + "5\t1\t2\t1\t1\t2\t25\t80\t40\t10\t100\tanswer\n"
).encode()
EMPTY_TSV = (TSV_HEADER + "1\t1\t0\t0\t0\t0\t0\t0\t100\t120\t-1\t\n").encode()


def oversized_field_tsv() -> bytes:
    prefix = TSV_HEADER.encode() + b"5\t1\t1\t1\t1\t1\t0\t0\t1\t1\t90\t"
    return prefix + (b"x" * 200_000) + b"\n"


def single_word_tsv(text: str) -> bytes:
    output = io.StringIO()
    writer = csv.writer(output, delimiter="\t", lineterminator="\n")
    writer.writerow(TSV_HEADER.rstrip("\n").split("\t"))
    writer.writerow((5, 1, 1, 1, 1, 1, 0, 0, 10, 10, 90, text))
    return output.getvalue().encode()


def pdf_bytes(page_count: int = 1, *, encrypted: bool = False) -> bytes:
    document = pymupdf.open()
    for page_index in range(page_count):
        page = document.new_page(width=200, height=200)
        page.insert_text((20, 30), f"synthetic page {page_index + 1}")
    options: dict[str, object] = {"garbage": 4, "deflate": True}
    if encrypted:
        options.update(
            encryption=cast(int, pymupdf.PDF_ENCRYPT_AES_256),  # type: ignore[attr-defined]
            owner_pw="owner-fixture",
            user_pw="user-fixture",
        )
    data = cast(bytes, document.tobytes(**options))
    document.close()
    return data


def request_for(
    data: bytes,
    *,
    media_type: str = "application/pdf",
    page_numbers: tuple[int, ...] = (),
    checksum: str | None = None,
) -> OCRRequest:
    return OCRRequest(
        source_document_id="document-fixture",
        source_checksum_sha256=checksum or hashlib.sha256(data).hexdigest(),
        content=data,
        media_type=media_type,
        page_numbers=page_numbers,
    )


@dataclass(frozen=True, slots=True)
class RecordedCall:
    argv: tuple[str, ...]
    cwd: Path
    timeout_seconds: float
    max_output_bytes: int
    image_paths: tuple[Path, ...]


class RecordingRunner:
    def __init__(
        self,
        *,
        tsv: bytes = VALID_TSV,
        version: bytes = b"tesseract 5.4.1\n leptonica-1.84.1\n",
        languages: bytes = b'List of available languages in "/models" (2):\neng\nsin\n',
    ) -> None:
        self.tsv = tsv
        self.version = version
        self.languages = languages
        self.calls: list[RecordedCall] = []
        self._lock = threading.Lock()

    def __call__(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        timeout_seconds: float,
        max_output_bytes: int,
    ) -> CommandResult:
        call = RecordedCall(
            argv=argv,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
            image_paths=tuple(sorted(cwd.glob("*.png"))),
        )
        with self._lock:
            self.calls.append(call)
        if argv[1:] == ("--version",):
            return CommandResult(returncode=0, stdout=self.version, stderr=b"")
        if argv[1:] == ("--list-langs",):
            return CommandResult(returncode=0, stdout=self.languages, stderr=b"")
        return CommandResult(returncode=0, stdout=self.tsv, stderr=b"")


def adapter_with(
    runner: Callable[..., CommandResult],
    **config_overrides: object,
) -> TesseractCliOCRAdapter:
    config_values: dict[str, object] = {
        "executable": "fixture-tesseract",
        "max_source_bytes": 1_000_000,
        "max_pages": 10,
        "dpi": 144,
        "batch_size": 2,
        "timeout_seconds": 7.5,
        "max_pixels_per_page": 2_000_000,
        "max_command_output_bytes": 4_096,
    }
    config_values.update(config_overrides)
    config = TesseractOCRConfig(**cast(Any, config_values))
    return TesseractCliOCRAdapter(config=config, command_runner=runner)


def test_adapter_source_ceiling_accepts_256_mib_but_not_unbounded() -> None:
    ceiling = 256 * 1024 * 1024
    assert TesseractOCRConfig(max_source_bytes=ceiling).max_source_bytes == ceiling
    with pytest.raises(TesseractConfigError):
        TesseractOCRConfig(max_source_bytes=ceiling + 1)


@pytest.mark.parametrize("deadline", [True, "105.0", float("nan"), float("inf"), float("-inf")])
def test_adapter_rejects_invalid_absolute_deadlines_before_commands(deadline: object) -> None:
    runner = RecordingRunner()

    with pytest.raises(TesseractConfigError, match=r"^execution deadline must be finite$"):
        TesseractCliOCRAdapter(
            command_runner=runner,
            execution_deadline=cast(float, deadline),
        )

    assert runner.calls == []


def test_absolute_ocr_deadline_caps_commands_and_stops_before_next_probe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    clock = [100.0]
    runner = RecordingRunner()

    def advancing_runner(
        argv: tuple[str, ...],
        *,
        cwd: Path,
        timeout_seconds: float,
        max_output_bytes: int,
    ) -> CommandResult:
        result = runner(
            argv, cwd=cwd, timeout_seconds=timeout_seconds, max_output_bytes=max_output_bytes
        )
        clock[0] = 106.0
        return result

    monkeypatch.setattr("exam_guru_api.documents.tesseract_ocr.time.monotonic", lambda: clock[0])
    adapter = TesseractCliOCRAdapter(
        config=TesseractOCRConfig(timeout_seconds=10),
        command_runner=advancing_runner,
        execution_deadline=105.0,
    )
    with pytest.raises(TesseractTimeoutError):
        adapter.extract(request_for(pdf_bytes()), temporary_directory=tmp_path)
    assert len(runner.calls) == 1
    assert runner.calls[0].timeout_seconds == 5.0
    assert list(tmp_path.iterdir()) == []


def assert_port(_port: OCRPort) -> None:
    return None


def test_adapter_implements_port_and_maps_tsv_blocks_with_reproducible_provenance(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner()
    adapter = adapter_with(runner)
    assert_port(adapter)

    result = adapter.extract(request_for(pdf_bytes()), temporary_directory=tmp_path)

    assert result.engine == "tesseract-cli"
    assert result.engine_version == "5.4.1"
    assert result.config == {
        "batch_size": 2,
        "dpi": 144,
        "language": "sin+eng",
        "max_command_output_bytes": 4_096,
        "max_pages": 10,
        "max_pixels_per_page": 2_000_000,
        "max_source_bytes": 1_000_000,
        "output_format": "tsv",
        "page_segmentation_mode": 3,
        "rasterizer": "pymupdf",
        "rasterizer_version": pymupdf.VersionBind,
        "timeout_seconds": 7.5,
    }
    assert len(result.pages) == 1
    page = result.pages[0]
    assert page.page_number == 1
    assert page.text == "Question 1\nChoose\n(A) answer"
    assert [(block.reading_order, block.text) for block in page.blocks] == [
        (0, "Question 1\nChoose"),
        (1, "(A) answer"),
    ]
    assert page.blocks[0].bbox == (10.0, 20.0, 60.0, 50.0)
    assert page.blocks[0].confidence == pytest.approx(0.8)
    assert page.blocks[1].bbox == (5.0, 80.0, 65.0, 90.0)
    assert page.blocks[1].confidence == pytest.approx(0.8)

    assert [call.argv for call in runner.calls[:2]] == [
        ("fixture-tesseract", "--version"),
        ("fixture-tesseract", "--list-langs"),
    ]
    ocr_call = runner.calls[2]
    assert ocr_call.argv == (
        "fixture-tesseract",
        str(ocr_call.image_paths[0]),
        "stdout",
        "-l",
        "sin+eng",
        "--dpi",
        "144",
        "--psm",
        "3",
        "tsv",
    )
    assert ocr_call.timeout_seconds == 7.5
    assert ocr_call.max_output_bytes == 4_096
    assert ocr_call.cwd.is_relative_to(tmp_path)
    assert not ocr_call.cwd.exists()
    assert not ocr_call.image_paths[0].exists()
    assert list(tmp_path.iterdir()) == []


def test_probe_reports_engine_and_sorted_available_languages_and_checks_selection(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner(
        languages=b'List of available languages in "/models" (3):\nsin\nosd\neng\n'
    )
    adapter = adapter_with(runner)

    probe = adapter.probe(temporary_directory=tmp_path)

    assert probe.engine == "tesseract-cli"
    assert probe.engine_version == "5.4.1"
    assert probe.available_languages == ("eng", "osd", "sin")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("version", "languages"),
    [
        (b"", b'List of available languages in "/models" (2):\neng\nsin\n'),
        (
            b"not a tesseract version\n",
            b'List of available languages in "/models" (2):\neng\nsin\n',
        ),
        (b"\xff", b'List of available languages in "/models" (2):\neng\nsin\n'),
        (b"tesseract 5.4.1\n", b""),
        (b"tesseract 5.4.1\n", b"not a language listing\n"),
        (b"tesseract 5.4.1\n", b'List of available languages in "/models" (2):\neng\n'),
        (b"tesseract 5.4.1\n", b'List of available languages in "/models" (2):\neng\neng\n'),
        (b"tesseract 5.4.1\n", b'List of available languages in "/models" (2):\neng\nsin+eng\n'),
        (b"tesseract 5.4.1\n", b"\xff"),
    ],
)
def test_probe_rejects_malformed_version_or_language_output_without_retaining_it(
    version: bytes,
    languages: bytes,
    tmp_path: Path,
) -> None:
    runner = RecordingRunner(version=version, languages=languages)

    with pytest.raises(TesseractMalformedOutputError) as raised:
        adapter_with(runner).probe(temporary_directory=tmp_path)

    assert raised.value.__context__ is None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "result",
    [
        cast(CommandResult, object()),
        CommandResult(returncode=cast(int, "0"), stdout=b"", stderr=b""),
        CommandResult(returncode=cast(int, True), stdout=b"", stderr=b""),
        CommandResult(returncode=0, stdout=cast(bytes, "text"), stderr=b""),
        CommandResult(returncode=0, stdout=b"", stderr=cast(bytes, "text")),
    ],
)
def test_probe_rejects_malformed_injected_command_result(
    result: CommandResult,
    tmp_path: Path,
) -> None:
    def runner(
        _argv: tuple[str, ...],
        *,
        cwd: Path,
        timeout_seconds: float,
        max_output_bytes: int,
    ) -> CommandResult:
        del cwd, timeout_seconds, max_output_bytes
        return result

    with pytest.raises(TesseractMalformedOutputError):
        adapter_with(runner).probe(temporary_directory=tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_all_pages_are_rasterized_in_bounded_batches_and_temp_files_are_cleaned(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner(tsv=EMPTY_TSV)
    adapter = adapter_with(runner)

    result = adapter.extract(request_for(pdf_bytes(5)), temporary_directory=tmp_path)

    assert tuple(page.page_number for page in result.pages) == (1, 2, 3, 4, 5)
    ocr_calls = runner.calls[2:]
    assert len(ocr_calls) == 5
    assert max(len(call.image_paths) for call in ocr_calls) == 2
    assert len({call.cwd for call in ocr_calls}) == 3
    assert [Path(call.argv[1]).name for call in ocr_calls] == [
        "page-000001.png",
        "page-000002.png",
        "page-000003.png",
        "page-000004.png",
        "page-000005.png",
    ]
    assert all(not call.cwd.exists() for call in ocr_calls)
    assert list(tmp_path.iterdir()) == []


def test_only_requested_pages_are_rendered_and_keep_source_page_numbers(tmp_path: Path) -> None:
    runner = RecordingRunner(tsv=EMPTY_TSV)
    adapter = adapter_with(runner)

    result = adapter.extract(
        request_for(pdf_bytes(4), page_numbers=(2, 4)),
        temporary_directory=tmp_path,
    )

    assert tuple(page.page_number for page in result.pages) == (2, 4)
    assert [Path(call.argv[1]).name for call in runner.calls[2:]] == [
        "page-000002.png",
        "page-000004.png",
    ]


def test_page_limit_caps_selected_subset_not_total_document_pages(tmp_path: Path) -> None:
    runner = RecordingRunner(tsv=EMPTY_TSV)
    adapter = adapter_with(runner, max_pages=2, batch_size=2)

    result = adapter.extract(
        request_for(pdf_bytes(20), page_numbers=(2, 20)),
        temporary_directory=tmp_path,
    )

    assert tuple(page.page_number for page in result.pages) == (2, 20)
    assert [Path(call.argv[1]).name for call in runner.calls[2:]] == [
        "page-000002.png",
        "page-000020.png",
    ]
    assert list(tmp_path.iterdir()) == []


def test_concurrent_calls_use_isolated_workspaces_and_cleanup(tmp_path: Path) -> None:
    barrier = threading.Barrier(2)
    runner = RecordingRunner(tsv=EMPTY_TSV)

    def concurrent_runner(
        argv: tuple[str, ...],
        *,
        cwd: Path,
        timeout_seconds: float,
        max_output_bytes: int,
    ) -> CommandResult:
        if len(argv) > 2:
            barrier.wait(timeout=5)
        return runner(
            argv,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
        )

    adapter = adapter_with(concurrent_runner)
    ocr_request = request_for(pdf_bytes())
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(
            executor.map(
                lambda _index: adapter.extract(
                    ocr_request,
                    temporary_directory=tmp_path,
                ),
                range(2),
            )
        )

    assert all(result.pages[0].page_number == 1 for result in results)
    ocr_calls = [call for call in runner.calls if len(call.argv) > 2]
    assert len(ocr_calls) == 2
    assert ocr_calls[0].cwd != ocr_calls[1].cwd
    assert ocr_calls[0].image_paths[0] != ocr_calls[1].image_paths[0]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("build_request", "config_overrides", "violation"),
    [
        (
            lambda: request_for(pdf_bytes(), media_type="Application/PDF"),
            {},
            TesseractInputViolation.UNSUPPORTED_MEDIA_TYPE,
        ),
        (
            lambda: request_for(b"not-a-pdf"),
            {},
            TesseractInputViolation.INVALID_PDF_SIGNATURE,
        ),
        (
            lambda: request_for(b"%PDF-not-really-a-pdf"),
            {},
            TesseractInputViolation.MALFORMED_PDF,
        ),
        (
            lambda: request_for(pdf_bytes(), checksum="0" * 64),
            {},
            TesseractInputViolation.CHECKSUM_MISMATCH,
        ),
        (
            lambda: request_for(pdf_bytes(2)),
            {"max_source_bytes": 10},
            TesseractInputViolation.SOURCE_TOO_LARGE,
        ),
        (
            lambda: request_for(pdf_bytes(2)),
            {"max_pages": 1, "batch_size": 1},
            TesseractInputViolation.PAGE_LIMIT_EXCEEDED,
        ),
        (
            lambda: request_for(pdf_bytes(2), page_numbers=(3,)),
            {},
            TesseractInputViolation.PAGE_OUT_OF_RANGE,
        ),
        (
            lambda: request_for(pdf_bytes(encrypted=True)),
            {},
            TesseractInputViolation.ENCRYPTED_PDF,
        ),
    ],
)
def test_adapter_rejects_unsafe_pdf_inputs_before_running_tesseract(
    build_request: Callable[[], OCRRequest],
    config_overrides: dict[str, object],
    violation: TesseractInputViolation,
    tmp_path: Path,
) -> None:
    runner = RecordingRunner()
    adapter = adapter_with(runner, **config_overrides)

    with pytest.raises(TesseractInputError) as raised:
        adapter.extract(build_request(), temporary_directory=tmp_path)

    assert raised.value.violation is violation
    assert runner.calls == []
    assert list(tmp_path.iterdir()) == []


def test_oversized_raster_page_is_rejected_before_tesseract_ocr(tmp_path: Path) -> None:
    runner = RecordingRunner()
    adapter = adapter_with(runner, max_pixels_per_page=10_000)

    with pytest.raises(TesseractInputError) as raised:
        adapter.extract(request_for(pdf_bytes()), temporary_directory=tmp_path)

    assert raised.value.violation is TesseractInputViolation.RASTER_LIMIT_EXCEEDED
    assert [call.argv[1:] for call in runner.calls] == [("--version",), ("--list-langs",)]
    assert list(tmp_path.iterdir()) == []


def test_zero_page_pdf_is_rejected_and_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class ZeroPageDocument:
        needs_pass = False
        page_count = 0

        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    document = ZeroPageDocument()
    monkeypatch.setattr(pymupdf, "open", lambda **_kwargs: document)
    data = b"%PDF-1.7 synthetic zero-page fixture"
    runner = RecordingRunner()

    with pytest.raises(TesseractInputError) as raised:
        adapter_with(runner).extract(request_for(data), temporary_directory=tmp_path)

    assert raised.value.violation is TesseractInputViolation.MALFORMED_PDF
    assert document.closed is True
    assert runner.calls == []


@pytest.mark.parametrize("zero_dimension", [True, False])
def test_invalid_page_geometry_or_render_failure_is_typed_and_cleans_temp(
    zero_dimension: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class Rect:
        width = 0.0 if zero_dimension else 100.0
        height = 100.0

    class Page:
        rect = Rect()

        @staticmethod
        def get_pixmap(**_kwargs: object) -> object:
            raise RuntimeError("synthetic renderer failure")

    class Document:
        needs_pass = False
        page_count = 1

        def __init__(self) -> None:
            self.closed = False

        @staticmethod
        def __getitem__(_index: int) -> Page:
            return Page()

        def close(self) -> None:
            self.closed = True

    document = Document()
    monkeypatch.setattr(pymupdf, "open", lambda **_kwargs: document)
    data = b"%PDF-1.7 synthetic rendering fixture"
    runner = RecordingRunner()

    with pytest.raises(TesseractInputError) as raised:
        adapter_with(runner).extract(request_for(data), temporary_directory=tmp_path)

    assert raised.value.violation is TesseractInputViolation.MALFORMED_PDF
    assert document.closed is True
    assert [call.argv[1:] for call in runner.calls] == [("--version",), ("--list-langs",)]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "build",
    [
        lambda: TesseractOCRConfig(max_source_bytes=0),
        lambda: TesseractOCRConfig(max_source_bytes=257 * 1024 * 1024),
        lambda: TesseractOCRConfig(max_pages=0),
        lambda: TesseractOCRConfig(max_pages=1_001),
        lambda: TesseractOCRConfig(dpi=71),
        lambda: TesseractOCRConfig(dpi=601),
        lambda: TesseractOCRConfig(batch_size=0),
        lambda: TesseractOCRConfig(batch_size=17),
        lambda: TesseractOCRConfig(max_pages=1, batch_size=2),
        lambda: TesseractOCRConfig(timeout_seconds=0),
        lambda: TesseractOCRConfig(timeout_seconds=301),
        lambda: TesseractOCRConfig(timeout_seconds=float("inf")),
        lambda: TesseractOCRConfig(max_pixels_per_page=0),
        lambda: TesseractOCRConfig(max_pixels_per_page=100_000_001),
        lambda: TesseractOCRConfig(max_command_output_bytes=0),
        lambda: TesseractOCRConfig(max_command_output_bytes=(64 * 1024 * 1024) + 1),
        lambda: TesseractOCRConfig(language=cast(str, 1)),
        lambda: TesseractOCRConfig(language=""),
        lambda: TesseractOCRConfig(language="sin;rm -rf /"),
        lambda: TesseractOCRConfig(language="sin+fra"),
        lambda: TesseractOCRConfig(
            language="sin+eng+osd+equ+script",
            allowed_languages=("sin", "eng", "osd", "equ", "script"),
        ),
        lambda: TesseractOCRConfig(language="sin+sin"),
        lambda: TesseractOCRConfig(allowed_languages=()),
        lambda: TesseractOCRConfig(allowed_languages=("sin", "sin", "eng")),
        lambda: TesseractOCRConfig(allowed_languages=("sin+eng",)),
        lambda: TesseractOCRConfig(allowed_languages=tuple(f"lang{index}" for index in range(33))),
        lambda: TesseractOCRConfig(executable=" "),
        lambda: TesseractOCRConfig(executable="tesseract\0other"),
        lambda: TesseractOCRConfig(page_segmentation_mode=0),
        lambda: TesseractOCRConfig(page_segmentation_mode=14),
    ],
)
def test_configuration_rejects_unbounded_or_unsafe_values(build: Callable[[], object]) -> None:
    with pytest.raises(TesseractConfigError):
        build()


def test_configured_allowlisted_language_is_passed_as_one_argv_value(tmp_path: Path) -> None:
    runner = RecordingRunner(languages=b'List of available languages in "/models" (1):\neng\n')
    adapter = adapter_with(
        runner,
        language="eng",
        allowed_languages=("eng",),
    )

    adapter.extract(request_for(pdf_bytes()), temporary_directory=tmp_path)

    ocr_argv = runner.calls[2].argv
    assert ocr_argv[ocr_argv.index("-l") + 1] == "eng"


def test_missing_executable_or_selected_traineddata_is_typed_unavailable(tmp_path: Path) -> None:
    def missing_executable(
        _argv: tuple[str, ...],
        *,
        cwd: Path,
        timeout_seconds: float,
        max_output_bytes: int,
    ) -> CommandResult:
        del cwd, timeout_seconds, max_output_bytes
        raise FileNotFoundError

    with pytest.raises(TesseractUnavailableError) as missing_binary:
        adapter_with(missing_executable).probe(temporary_directory=tmp_path)
    assert missing_binary.value.missing_languages == ()

    runner = RecordingRunner(languages=b'List of available languages in "/models" (1):\neng\n')
    with pytest.raises(TesseractUnavailableError) as missing_traineddata:
        adapter_with(runner).probe(temporary_directory=tmp_path)
    assert missing_traineddata.value.missing_languages == ("sin",)
    assert "sin" in str(missing_traineddata.value)
    assert list(tmp_path.iterdir()) == []


def test_timeout_is_typed_and_temp_files_are_cleaned(tmp_path: Path) -> None:
    runner = RecordingRunner()
    timed_out_path: Path | None = None

    def timeout_runner(
        argv: tuple[str, ...],
        *,
        cwd: Path,
        timeout_seconds: float,
        max_output_bytes: int,
    ) -> CommandResult:
        nonlocal timed_out_path
        if len(argv) > 2:
            timed_out_path = Path(argv[1])
            raise subprocess.TimeoutExpired(argv, timeout_seconds, output=b"private OCR text")
        return runner(
            argv,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
        )

    with pytest.raises(TesseractTimeoutError) as raised:
        adapter_with(timeout_runner).extract(
            request_for(pdf_bytes()),
            temporary_directory=tmp_path,
        )

    assert raised.value.operation == "ocr"
    assert raised.value.timeout_seconds == 7.5
    assert "private OCR text" not in str(raised.value)
    assert raised.value.__context__ is None
    assert timed_out_path is not None
    assert not timed_out_path.exists()
    assert list(tmp_path.iterdir()) == []


def test_combined_command_output_limit_is_typed_and_does_not_echo_output(
    tmp_path: Path,
) -> None:
    observed_limits: list[int] = []
    private_stdout = b"private stdout material" * 3
    private_stderr = b"private stderr material" * 3

    def oversized_runner(
        _argv: tuple[str, ...],
        *,
        cwd: Path,
        timeout_seconds: float,
        max_output_bytes: int,
    ) -> CommandResult:
        del cwd, timeout_seconds
        observed_limits.append(max_output_bytes)
        return CommandResult(returncode=0, stdout=private_stdout, stderr=private_stderr)

    with pytest.raises(TesseractOutputLimitError) as raised:
        adapter_with(oversized_runner, max_command_output_bytes=100).probe(
            temporary_directory=tmp_path
        )

    assert observed_limits == [100]
    assert raised.value.operation == "version probe"
    assert raised.value.max_output_bytes == 100
    assert private_stdout.decode() not in str(raised.value)
    assert private_stderr.decode() not in str(raised.value)
    assert raised.value.__context__ is None
    assert list(tmp_path.iterdir()) == []


def test_nonzero_process_error_is_typed_without_exposing_process_output(tmp_path: Path) -> None:
    runner = RecordingRunner()

    def failed_runner(
        argv: tuple[str, ...],
        *,
        cwd: Path,
        timeout_seconds: float,
        max_output_bytes: int,
    ) -> CommandResult:
        if len(argv) > 2:
            return CommandResult(
                returncode=9,
                stdout=b"private source text",
                stderr=b"private diagnostic text",
            )
        return runner(
            argv,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
        )

    with pytest.raises(TesseractProcessError) as raised:
        adapter_with(failed_runner).extract(
            request_for(pdf_bytes()),
            temporary_directory=tmp_path,
        )

    assert raised.value.operation == "ocr"
    assert raised.value.returncode == 9
    assert "private source text" not in str(raised.value)
    assert "private diagnostic text" not in str(raised.value)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "unsafe_character",
    [
        "\x00",
        "\x01",
        "\x08",
        "\x0b",
        "\x0c",
        "\x1f",
        "\x7f",
        "\x80",
        "\x85",
        "\x9f",
        "\u061c",
        "\u200e",
        "\u200f",
        "\u202a",
        "\u202b",
        "\u202c",
        "\u202d",
        "\u202e",
        "\u2066",
        "\u2067",
        "\u2068",
        "\u2069",
    ],
    ids=lambda character: f"U+{ord(character):04X}",
)
def test_tsv_rejects_control_and_bidi_text_without_echoing_raw_output(
    unsafe_character: str,
    tmp_path: Path,
) -> None:
    raw_text = f"untrusted{unsafe_character}private"
    tsv = single_word_tsv(raw_text)

    with pytest.raises(TesseractMalformedOutputError) as raised:
        adapter_with(RecordingRunner(tsv=tsv)).extract(
            request_for(pdf_bytes()),
            temporary_directory=tmp_path,
        )

    assert raw_text not in str(raised.value)
    assert raw_text not in repr(raised.value)
    assert raised.value.__context__ is None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("safe_whitespace", ["\t", "\n", "\r"])
def test_tsv_allows_safe_text_whitespace(
    safe_whitespace: str,
    tmp_path: Path,
) -> None:
    text = f"first{safe_whitespace}second"

    result = adapter_with(RecordingRunner(tsv=single_word_tsv(text))).extract(
        request_for(pdf_bytes()),
        temporary_directory=tmp_path,
    )

    assert result.pages[0].text == text
    assert result.pages[0].blocks[0].text == text


def test_tsv_preserves_sinhala_zero_width_joiner(tmp_path: Path) -> None:
    sinhala_with_zwj = "\u0d9a\u0dca\u200d\u0dbb"

    result = adapter_with(RecordingRunner(tsv=single_word_tsv(sinhala_with_zwj))).extract(
        request_for(pdf_bytes()),
        temporary_directory=tmp_path,
    )

    assert result.pages[0].text == sinhala_with_zwj
    assert result.pages[0].blocks[0].text == sinhala_with_zwj


@pytest.mark.parametrize(
    "tsv",
    [
        b"",
        b"not\ttesseract\ttsv\n",
        TSV_HEADER.encode() + b"5\t1\ttoo\tshort\n",
        TSV_HEADER.encode() + b"5\t1\t1\t1\t1\t1\tleft\t0\t1\t1\t90\tword\n",
        TSV_HEADER.encode() + b"5\t2\t1\t1\t1\t1\t0\t0\t1\t1\t90\tword\n",
        TSV_HEADER.encode() + b"5\t1\t1\t1\t1\t1\t0\t0\t-1\t1\t90\tword\n",
        TSV_HEADER.encode() + b"5\t1\t1\t1\t1\t1\t0\t0\t0\t1\t90\tword\n",
        TSV_HEADER.encode() + b"5\t1\t1\t1\t1\t1\t0\t0\t1\t1\t101\tword\n",
        oversized_field_tsv(),
        b"\xff\xfe",
    ],
)
def test_malformed_tsv_is_rejected_without_echoing_output(tsv: bytes, tmp_path: Path) -> None:
    runner = RecordingRunner(tsv=tsv)

    with pytest.raises(TesseractMalformedOutputError) as raised:
        adapter_with(runner, max_command_output_bytes=1_000_000).extract(
            request_for(pdf_bytes()),
            temporary_directory=tmp_path,
        )

    decoded_output = tsv.decode("utf-8", errors="ignore")
    if decoded_output:
        assert decoded_output not in str(raised.value)
    assert raised.value.__context__ is None
    assert list(tmp_path.iterdir()) == []


def test_default_runner_streams_to_a_combined_output_ceiling_and_reaps_process(
    tmp_path: Path,
) -> None:
    pid_path = tmp_path / "output-limit.pid"
    script = (
        "import os, pathlib, sys, time; "
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); "
        "os.write(1, b'x' * 4096); os.write(2, b'y' * 4096); time.sleep(30)"
    )

    result = SubprocessCommandRunner()(
        (sys.executable, "-c", script, str(pid_path)),
        cwd=tmp_path,
        timeout_seconds=2.0,
        max_output_bytes=1_024,
    )

    child_pid = int(pid_path.read_text())
    assert result.output_limit_exceeded is True
    assert result.stdout == b""
    assert result.stderr == b""
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


def test_default_runner_accepts_combined_output_at_exact_ceiling(tmp_path: Path) -> None:
    script = "import os; os.write(1, b'x' * 600); os.write(2, b'y' * 424)"

    result = SubprocessCommandRunner()(
        (sys.executable, "-c", script),
        cwd=tmp_path,
        timeout_seconds=2.0,
        max_output_bytes=1_024,
    )

    assert result.output_limit_exceeded is False
    assert result.stdout == b"x" * 600
    assert result.stderr == b"y" * 424


def test_default_runner_timeout_kills_and_reaps_process(tmp_path: Path) -> None:
    pid_path = tmp_path / "timeout.pid"
    script = (
        "import os, pathlib, sys, time; "
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)"
    )

    with pytest.raises(subprocess.TimeoutExpired) as raised:
        SubprocessCommandRunner()(
            (sys.executable, "-c", script, str(pid_path)),
            cwd=tmp_path,
            timeout_seconds=0.5,
            max_output_bytes=1_024,
        )

    child_pid = int(pid_path.read_text())
    assert raised.value.output is None
    assert raised.value.stderr is None
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


def test_default_runner_does_not_inherit_secret_bearing_parent_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-reach-child")
    monkeypatch.setenv("EXAM_GURU_PRIVATE_TOKEN", "must-not-reach-child")
    monkeypatch.setenv("PATH", "/secret-bearing-parent-path")
    script = "import json, os; print(json.dumps(dict(os.environ), sort_keys=True))"

    result = SubprocessCommandRunner()(
        (sys.executable, "-c", script),
        cwd=tmp_path,
        timeout_seconds=2.0,
        max_output_bytes=4_096,
    )

    assert json.loads(result.stdout) == {
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": os.defpath,
    }


def test_default_runner_uses_argv_without_shell_and_closes_standard_input(
    tmp_path: Path,
) -> None:
    shell_payload = f"; touch {tmp_path / 'shell-was-used'}"
    script = (
        "import json, sys; "
        "print(json.dumps({'argument': sys.argv[1], 'stdin': sys.stdin.buffer.read().decode()}))"
    )

    result = SubprocessCommandRunner()(
        (sys.executable, "-c", script, shell_payload),
        cwd=tmp_path,
        timeout_seconds=2.0,
        max_output_bytes=4_096,
    )

    assert json.loads(result.stdout) == {"argument": shell_payload, "stdin": ""}
    assert not (tmp_path / "shell-was-used").exists()

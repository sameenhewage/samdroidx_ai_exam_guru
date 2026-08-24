"""Bounded Tesseract CLI adapter for the provider-independent OCR port.

Source PDFs and OCR text are untrusted data. The adapter keeps raster files in a
per-call temporary workspace, captures TSV in memory, never includes process
output in exceptions, and makes no claim about real Sinhala OCR quality.
"""

import csv
import hashlib
import io
import math
import os
import re
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Lock, Thread
from typing import BinaryIO, Protocol, cast

import pymupdf

from exam_guru_api.documents.ocr import (
    MalformedOCROutputError,
    OCRBlock,
    OCRConfigError,
    OCRInputError,
    OCROutputLimitError,
    OCRPage,
    OCRProcessError,
    OCRRequest,
    OCRResult,
    OCRTimeoutError,
    OCRUnavailableError,
)

_MAX_CONFIG_SOURCE_BYTES = 100 * 1024 * 1024
_MAX_CONFIG_PAGES = 1_000
_MIN_CONFIG_DPI = 72
_MAX_CONFIG_DPI = 600
_MAX_CONFIG_BATCH_SIZE = 16
_MAX_CONFIG_TIMEOUT_SECONDS = 300.0
_MAX_CONFIG_PIXELS_PER_PAGE = 100_000_000
_MAX_CONFIG_COMMAND_OUTPUT_BYTES = 64 * 1024 * 1024
_MAX_CONFIG_LANGUAGE_COUNT = 4
_MAX_CONFIG_ALLOWED_LANGUAGES = 32
_COMMAND_OUTPUT_CHUNK_BYTES = 64 * 1024
_COMMAND_POLL_SECONDS = 0.01
_SAFE_TEXT_CONTROLS = frozenset("\t\n\r")
_BIDI_CONTROL_CHARACTERS = frozenset(
    {
        "\u061c",  # Arabic letter mark
        "\u200e",  # left-to-right mark
        "\u200f",  # right-to-left mark
        "\u202a",  # left-to-right embedding
        "\u202b",  # right-to-left embedding
        "\u202c",  # pop directional formatting
        "\u202d",  # left-to-right override
        "\u202e",  # right-to-left override
        "\u2066",  # left-to-right isolate
        "\u2067",  # right-to-left isolate
        "\u2068",  # first-strong isolate
        "\u2069",  # pop directional isolate
    }
)
_LANGUAGE_CODE = re.compile(r"[A-Za-z0-9_]+")
_VERSION_LINE = re.compile(r"tesseract\s+([A-Za-z0-9_.+~-]+)", re.IGNORECASE)
_LANGUAGE_HEADER = re.compile(r"List of available languages in .+ \((\d+)\):")
_TSV_COLUMNS = (
    "level",
    "page_num",
    "block_num",
    "par_num",
    "line_num",
    "word_num",
    "left",
    "top",
    "width",
    "height",
    "conf",
    "text",
)


class TesseractConfigError(OCRConfigError):
    """Raised when an adapter configuration exceeds its production bounds."""


class TesseractInputViolation(StrEnum):
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    SOURCE_TOO_LARGE = "source_too_large"
    INVALID_PDF_SIGNATURE = "invalid_pdf_signature"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    MALFORMED_PDF = "malformed_pdf"
    ENCRYPTED_PDF = "encrypted_pdf"
    PAGE_LIMIT_EXCEEDED = "page_limit_exceeded"
    PAGE_OUT_OF_RANGE = "page_out_of_range"
    RASTER_LIMIT_EXCEEDED = "raster_limit_exceeded"


class TesseractInputError(OCRInputError):
    """Typed rejection of an unsafe or out-of-policy OCR source PDF."""

    def __init__(self, violation: TesseractInputViolation) -> None:
        self.violation = violation
        super().__init__(violation.value)


class TesseractUnavailableError(OCRUnavailableError):
    """Raised when Tesseract or selected traineddata is unavailable."""

    def __init__(self, *, missing_languages: tuple[str, ...] = ()) -> None:
        self.missing_languages = missing_languages
        message = "tesseract executable is unavailable"
        if missing_languages:
            message = f"tesseract traineddata unavailable: {','.join(missing_languages)}"
        super().__init__(message)


class TesseractTimeoutError(OCRTimeoutError):
    """Raised when a bounded Tesseract command exceeds its deadline."""

    def __init__(self, *, operation: str, timeout_seconds: float) -> None:
        self.operation = operation
        self.timeout_seconds = timeout_seconds
        super().__init__(f"tesseract {operation} timed out after {timeout_seconds:g} seconds")


class TesseractProcessError(OCRProcessError):
    """Raised for a non-zero Tesseract process without retaining its output."""

    def __init__(self, *, operation: str, returncode: int) -> None:
        self.operation = operation
        self.returncode = returncode
        super().__init__(f"tesseract {operation} failed with exit code {returncode}")


class TesseractOutputLimitError(OCROutputLimitError):
    """Raised when combined command output exceeds its configured byte ceiling."""

    def __init__(self, *, operation: str, max_output_bytes: int) -> None:
        self.operation = operation
        self.max_output_bytes = max_output_bytes
        super().__init__(
            f"tesseract {operation} exceeded the {max_output_bytes}-byte command output limit"
        )


class TesseractMalformedOutputError(MalformedOCROutputError):
    """Raised when version, language, or TSV output violates its expected shape."""


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Minimal subprocess result used by injected deterministic command runners."""

    returncode: int
    stdout: bytes
    stderr: bytes
    output_limit_exceeded: bool = False


class CommandRunner(Protocol):
    def __call__(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        timeout_seconds: float,
        max_output_bytes: int,
    ) -> CommandResult: ...


def _minimal_command_environment() -> dict[str, str]:
    return {
        "PATH": os.defpath,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }


class _BoundedCommandOutput:
    def __init__(self, max_output_bytes: int) -> None:
        self._max_output_bytes = max_output_bytes
        self._size = 0
        self._stdout = bytearray()
        self._stderr = bytearray()
        self._lock = Lock()
        self._exceeded = Event()

    @property
    def exceeded(self) -> bool:
        return self._exceeded.is_set()

    def wait(self, timeout_seconds: float) -> None:
        self._exceeded.wait(timeout_seconds)

    def consume_stdout(self, stream: BinaryIO) -> None:
        self._consume(stream, self._stdout)

    def consume_stderr(self, stream: BinaryIO) -> None:
        self._consume(stream, self._stderr)

    def _consume(self, stream: BinaryIO, destination: bytearray) -> None:
        while True:
            chunk: bytes | None = None
            with suppress(OSError):
                chunk = os.read(stream.fileno(), _COMMAND_OUTPUT_CHUNK_BYTES)
            if not chunk:
                return
            with self._lock:
                if self._exceeded.is_set():
                    return
                if self._size + len(chunk) > self._max_output_bytes:
                    self._stdout.clear()
                    self._stderr.clear()
                    self._exceeded.set()
                    return
                destination.extend(chunk)
                self._size += len(chunk)

    def outputs(self) -> tuple[bytes, bytes]:
        return bytes(self._stdout), bytes(self._stderr)


def _kill_and_reap(process: subprocess.Popen[bytes]) -> None:
    with suppress(ProcessLookupError):
        process.kill()
    process.wait()


class SubprocessCommandRunner:
    """Stream one argv-only command into a bounded in-memory collector."""

    def __call__(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        timeout_seconds: float,
        max_output_bytes: int = 8 * 1024 * 1024,
    ) -> CommandResult:
        process: subprocess.Popen[bytes] = subprocess.Popen(  # noqa: S603
            argv,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            env=_minimal_command_environment(),
            close_fds=True,
            bufsize=0,
        )
        stdout_stream = cast(BinaryIO, process.stdout)
        stderr_stream = cast(BinaryIO, process.stderr)
        output = _BoundedCommandOutput(max_output_bytes)
        readers = (
            Thread(target=output.consume_stdout, args=(stdout_stream,), daemon=True),
            Thread(target=output.consume_stderr, args=(stderr_stream,), daemon=True),
        )
        for reader in readers:
            reader.start()

        timed_out = False
        deadline = time.monotonic() + timeout_seconds
        try:
            while process.poll() is None and not output.exceeded:
                remaining_seconds = deadline - time.monotonic()
                if remaining_seconds <= 0:
                    timed_out = True
                    break
                output.wait(min(remaining_seconds, _COMMAND_POLL_SECONDS))

            if timed_out or output.exceeded:
                _kill_and_reap(process)
            else:
                process.wait()
            for reader in readers:
                reader.join()

            if output.exceeded:
                return CommandResult(
                    returncode=process.returncode,
                    stdout=b"",
                    stderr=b"",
                    output_limit_exceeded=True,
                )
            if timed_out:
                raise subprocess.TimeoutExpired(argv, timeout_seconds)
            stdout, stderr = output.outputs()
            return CommandResult(
                returncode=process.returncode,
                stdout=stdout,
                stderr=stderr,
            )
        finally:
            _kill_and_reap(process)
            for reader in readers:
                reader.join()
            stdout_stream.close()
            stderr_stream.close()


def _bounded_integer(value: object, *, name: str, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise TesseractConfigError(f"{name} must be between {minimum} and {maximum}")
    return value


def _language_code(value: object) -> bool:
    return isinstance(value, str) and _LANGUAGE_CODE.fullmatch(value) is not None


def _has_unsafe_text_control(text: str) -> bool:
    return any(
        (
            (ord(character) < 0x20 or 0x7F <= ord(character) <= 0x9F)
            and character not in _SAFE_TEXT_CONTROLS
        )
        or character in _BIDI_CONTROL_CHARACTERS
        for character in text
    )


def _decode_tesseract_output(output: bytes, *, kind: str) -> str:
    decoded: str | None = None
    with suppress(UnicodeDecodeError):
        decoded = output.decode("utf-8", errors="strict")
    if decoded is None:
        raise TesseractMalformedOutputError(f"tesseract {kind} output is malformed")
    return decoded


@dataclass(frozen=True, slots=True)
class TesseractOCRConfig:
    """Immutable OCR policy with hard ceilings against accidental unbounded work."""

    executable: str = "tesseract"
    language: str = "sin+eng"
    allowed_languages: tuple[str, ...] = ("sin", "eng")
    max_source_bytes: int = 25 * 1024 * 1024
    max_pages: int = 100
    dpi: int = 300
    batch_size: int = 4
    timeout_seconds: float = 30.0
    page_segmentation_mode: int = 3
    max_pixels_per_page: int = 40_000_000
    max_command_output_bytes: int = 8 * 1024 * 1024

    def __post_init__(self) -> None:
        if (
            not isinstance(self.executable, str)
            or not self.executable.strip()
            or self.executable != self.executable.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in self.executable)
        ):
            raise TesseractConfigError("executable must be a non-blank control-free string")

        _bounded_integer(
            self.max_source_bytes,
            name="max_source_bytes",
            minimum=1,
            maximum=_MAX_CONFIG_SOURCE_BYTES,
        )
        _bounded_integer(
            self.max_pages,
            name="max_pages",
            minimum=1,
            maximum=_MAX_CONFIG_PAGES,
        )
        _bounded_integer(
            self.dpi,
            name="dpi",
            minimum=_MIN_CONFIG_DPI,
            maximum=_MAX_CONFIG_DPI,
        )
        _bounded_integer(
            self.batch_size,
            name="batch_size",
            minimum=1,
            maximum=_MAX_CONFIG_BATCH_SIZE,
        )
        if self.batch_size > self.max_pages:
            raise TesseractConfigError("batch_size cannot exceed max_pages")
        _bounded_integer(
            self.page_segmentation_mode,
            name="page_segmentation_mode",
            minimum=1,
            maximum=13,
        )
        _bounded_integer(
            self.max_pixels_per_page,
            name="max_pixels_per_page",
            minimum=1,
            maximum=_MAX_CONFIG_PIXELS_PER_PAGE,
        )
        _bounded_integer(
            self.max_command_output_bytes,
            name="max_command_output_bytes",
            minimum=1,
            maximum=_MAX_CONFIG_COMMAND_OUTPUT_BYTES,
        )
        if (
            not isinstance(self.timeout_seconds, (int, float))
            or isinstance(self.timeout_seconds, bool)
            or not math.isfinite(self.timeout_seconds)
            or not 0 < self.timeout_seconds <= _MAX_CONFIG_TIMEOUT_SECONDS
        ):
            raise TesseractConfigError(
                f"timeout_seconds must be greater than zero and at most "
                f"{_MAX_CONFIG_TIMEOUT_SECONDS:g}"
            )

        if (
            not isinstance(self.allowed_languages, tuple)
            or not 1 <= len(self.allowed_languages) <= _MAX_CONFIG_ALLOWED_LANGUAGES
            or any(not _language_code(language) for language in self.allowed_languages)
            or len(set(self.allowed_languages)) != len(self.allowed_languages)
        ):
            raise TesseractConfigError("allowed_languages must contain unique language codes")

        if not isinstance(self.language, str):
            raise TesseractConfigError("language must be an allowlisted language expression")
        selected_languages = tuple(self.language.split("+"))
        if (
            not 1 <= len(selected_languages) <= _MAX_CONFIG_LANGUAGE_COUNT
            or any(not _language_code(language) for language in selected_languages)
            or len(set(selected_languages)) != len(selected_languages)
            or any(language not in self.allowed_languages for language in selected_languages)
        ):
            raise TesseractConfigError("language must be an allowlisted language expression")

    @property
    def selected_languages(self) -> tuple[str, ...]:
        return tuple(self.language.split("+"))


@dataclass(frozen=True, slots=True)
class TesseractProbe:
    engine: str
    engine_version: str
    available_languages: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _RenderedPage:
    page_number: int
    path: Path
    width: int
    height: int


@dataclass(slots=True)
class _BlockAccumulator:
    lines: dict[tuple[int, int], list[str]]
    confidences: list[float]
    x_min: int
    y_min: int
    x_max: int
    y_max: int

    @classmethod
    def from_word(
        cls,
        *,
        line_key: tuple[int, int],
        text: str,
        confidence: float,
        left: int,
        top: int,
        width: int,
        height: int,
    ) -> "_BlockAccumulator":
        return cls(
            lines={line_key: [text]},
            confidences=[confidence],
            x_min=left,
            y_min=top,
            x_max=left + width,
            y_max=top + height,
        )

    def add_word(
        self,
        *,
        line_key: tuple[int, int],
        text: str,
        confidence: float,
        left: int,
        top: int,
        width: int,
        height: int,
    ) -> None:
        self.lines.setdefault(line_key, []).append(text)
        self.confidences.append(confidence)
        self.x_min = min(self.x_min, left)
        self.y_min = min(self.y_min, top)
        self.x_max = max(self.x_max, left + width)
        self.y_max = max(self.y_max, top + height)


class TesseractCliOCRAdapter:
    """Rasterize bounded PDF pages and map Tesseract TSV into ``OCRResult``."""

    def __init__(
        self,
        *,
        config: TesseractOCRConfig | None = None,
        command_runner: CommandRunner | None = None,
    ) -> None:
        self._config = config or TesseractOCRConfig()
        self._command_runner = command_runner or SubprocessCommandRunner()

    @property
    def config(self) -> TesseractOCRConfig:
        return self._config

    def probe(self, *, temporary_directory: Path | None = None) -> TesseractProbe:
        """Verify the executable, version shape, and selected traineddata languages."""

        with TemporaryDirectory(
            prefix="exam-guru-tesseract-probe-",
            dir=temporary_directory,
        ) as workspace_name:
            return self._probe(Path(workspace_name))

    def extract(
        self,
        request: OCRRequest,
        *,
        temporary_directory: Path | None = None,
    ) -> OCRResult:
        document, page_numbers = self._open_pdf(request)
        try:
            with TemporaryDirectory(
                prefix="exam-guru-tesseract-",
                dir=temporary_directory,
            ) as workspace_name:
                workspace = Path(workspace_name)
                probe = self._probe(workspace)
                pages: list[OCRPage] = []
                for batch_start in range(0, len(page_numbers), self._config.batch_size):
                    batch_page_numbers = page_numbers[
                        batch_start : batch_start + self._config.batch_size
                    ]
                    with TemporaryDirectory(prefix="batch-", dir=workspace) as batch_name:
                        rendered_pages = tuple(
                            self._render_page(
                                document,
                                page_number=page_number,
                                directory=Path(batch_name),
                            )
                            for page_number in batch_page_numbers
                        )
                        pages.extend(
                            self._ocr_page(rendered_page) for rendered_page in rendered_pages
                        )
        finally:
            document.close()

        return OCRResult(
            engine=probe.engine,
            engine_version=probe.engine_version,
            config={
                "batch_size": self._config.batch_size,
                "dpi": self._config.dpi,
                "language": self._config.language,
                "max_command_output_bytes": self._config.max_command_output_bytes,
                "max_pages": self._config.max_pages,
                "max_pixels_per_page": self._config.max_pixels_per_page,
                "max_source_bytes": self._config.max_source_bytes,
                "output_format": "tsv",
                "page_segmentation_mode": self._config.page_segmentation_mode,
                "rasterizer": "pymupdf",
                "rasterizer_version": pymupdf.VersionBind,
                "timeout_seconds": self._config.timeout_seconds,
            },
            pages=tuple(pages),
        )

    def _open_pdf(self, request: OCRRequest) -> tuple[pymupdf.Document, tuple[int, ...]]:
        if request.media_type != "application/pdf":
            raise TesseractInputError(TesseractInputViolation.UNSUPPORTED_MEDIA_TYPE)
        if len(request.content) > self._config.max_source_bytes:
            raise TesseractInputError(TesseractInputViolation.SOURCE_TOO_LARGE)
        if not request.content.startswith(b"%PDF-"):
            raise TesseractInputError(TesseractInputViolation.INVALID_PDF_SIGNATURE)
        if hashlib.sha256(request.content).hexdigest() != request.source_checksum_sha256:
            raise TesseractInputError(TesseractInputViolation.CHECKSUM_MISMATCH)

        # Page count is available only after PyMuPDF opens the already byte-bounded stream.
        try:
            document = pymupdf.open(stream=request.content, filetype="pdf")
        except Exception:
            raise TesseractInputError(TesseractInputViolation.MALFORMED_PDF) from None

        try:
            if document.needs_pass:
                raise TesseractInputError(TesseractInputViolation.ENCRYPTED_PDF)
            if document.page_count < 1:
                raise TesseractInputError(TesseractInputViolation.MALFORMED_PDF)
            page_numbers = request.page_numbers or tuple(range(1, document.page_count + 1))
            if any(page_number > document.page_count for page_number in page_numbers):
                raise TesseractInputError(TesseractInputViolation.PAGE_OUT_OF_RANGE)
            if len(page_numbers) > self._config.max_pages:
                raise TesseractInputError(TesseractInputViolation.PAGE_LIMIT_EXCEEDED)
        except Exception:
            document.close()
            raise
        return document, page_numbers

    def _probe(self, workspace: Path) -> TesseractProbe:
        version_result = self._execute(
            (self._config.executable, "--version"),
            cwd=workspace,
            operation="version probe",
        )
        version = self._parse_version(version_result.stdout)
        language_result = self._execute(
            (self._config.executable, "--list-langs"),
            cwd=workspace,
            operation="language probe",
        )
        available_languages = self._parse_languages(language_result.stdout)
        missing_languages = tuple(
            language
            for language in self._config.selected_languages
            if language not in available_languages
        )
        if missing_languages:
            raise TesseractUnavailableError(missing_languages=missing_languages)
        return TesseractProbe(
            engine="tesseract-cli",
            engine_version=version,
            available_languages=available_languages,
        )

    def _execute(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        operation: str,
    ) -> CommandResult:
        result: object | None = None
        failure: str | None = None
        try:
            result = self._command_runner(
                argv,
                cwd=cwd,
                timeout_seconds=self._config.timeout_seconds,
                max_output_bytes=self._config.max_command_output_bytes,
            )
        except subprocess.TimeoutExpired:
            failure = "timeout"
        except OSError:
            failure = "unavailable"
        if failure == "timeout":
            raise TesseractTimeoutError(
                operation=operation,
                timeout_seconds=self._config.timeout_seconds,
            )
        if failure == "unavailable":
            raise TesseractUnavailableError()
        if (
            not isinstance(result, CommandResult)
            or not isinstance(result.returncode, int)
            or isinstance(result.returncode, bool)
            or not isinstance(result.stdout, bytes)
            or not isinstance(result.stderr, bytes)
            or not isinstance(result.output_limit_exceeded, bool)
        ):
            raise TesseractMalformedOutputError("tesseract command result is malformed")
        if (
            result.output_limit_exceeded
            or len(result.stdout) + len(result.stderr) > self._config.max_command_output_bytes
        ):
            raise TesseractOutputLimitError(
                operation=operation,
                max_output_bytes=self._config.max_command_output_bytes,
            )
        if result.returncode != 0:
            raise TesseractProcessError(operation=operation, returncode=result.returncode)
        return result

    @staticmethod
    def _parse_version(output: bytes) -> str:
        lines = _decode_tesseract_output(output, kind="version").splitlines()
        if not lines:
            raise TesseractMalformedOutputError("tesseract version output is malformed")
        match = _VERSION_LINE.fullmatch(lines[0].strip())
        if match is None:
            raise TesseractMalformedOutputError("tesseract version output is malformed")
        return match.group(1)

    @staticmethod
    def _parse_languages(output: bytes) -> tuple[str, ...]:
        lines = _decode_tesseract_output(output, kind="language").splitlines()
        if not lines:
            raise TesseractMalformedOutputError("tesseract language output is malformed")
        header_match = _LANGUAGE_HEADER.fullmatch(lines[0].strip())
        languages = tuple(line.strip() for line in lines[1:] if line.strip())
        if (
            header_match is None
            or int(header_match.group(1)) != len(languages)
            or any(not _language_code(language) for language in languages)
            or len(set(languages)) != len(languages)
        ):
            raise TesseractMalformedOutputError("tesseract language output is malformed")
        return tuple(sorted(languages))

    def _render_page(
        self,
        document: pymupdf.Document,
        *,
        page_number: int,
        directory: Path,
    ) -> _RenderedPage:
        try:
            page = document[page_number - 1]
            scale = self._config.dpi / 72.0
            width = math.ceil(float(page.rect.width) * scale)
            height = math.ceil(float(page.rect.height) * scale)
            if width <= 0 or height <= 0:
                raise TesseractInputError(TesseractInputViolation.MALFORMED_PDF)
            if width * height > self._config.max_pixels_per_page:
                raise TesseractInputError(TesseractInputViolation.RASTER_LIMIT_EXCEEDED)
            path = directory / f"page-{page_number:06d}.png"
            pixmap = page.get_pixmap(
                matrix=pymupdf.Matrix(scale, scale),
                colorspace=pymupdf.csRGB,
                alpha=False,
            )
            pixmap.save(path)
        except TesseractInputError:
            raise
        except Exception:
            raise TesseractInputError(TesseractInputViolation.MALFORMED_PDF) from None
        return _RenderedPage(
            page_number=page_number,
            path=path,
            width=width,
            height=height,
        )

    def _ocr_page(self, rendered_page: _RenderedPage) -> OCRPage:
        result = self._execute(
            (
                self._config.executable,
                str(rendered_page.path),
                "stdout",
                "-l",
                self._config.language,
                "--dpi",
                str(self._config.dpi),
                "--psm",
                str(self._config.page_segmentation_mode),
                "tsv",
            ),
            cwd=rendered_page.path.parent,
            operation="ocr",
        )
        return self._parse_tsv(result.stdout, rendered_page=rendered_page)

    @staticmethod
    def _parse_tsv(output: bytes, *, rendered_page: _RenderedPage) -> OCRPage:
        decoded = _decode_tesseract_output(output, kind="TSV")
        rows: list[list[str]] | None = None
        with suppress(csv.Error):
            rows = list(csv.reader(io.StringIO(decoded), delimiter="\t"))
        if not rows or tuple(rows[0]) != _TSV_COLUMNS:
            raise TesseractMalformedOutputError("tesseract TSV output is malformed")

        blocks: dict[int, _BlockAccumulator] = {}
        for row in rows[1:]:
            if len(row) != len(_TSV_COLUMNS):
                raise TesseractMalformedOutputError("tesseract TSV output is malformed")
            parsed_numbers: tuple[int, int, int, int, int, int, int, int, int, int, float] | None
            try:
                parsed_numbers = (
                    int(row[0]),
                    int(row[1]),
                    int(row[2]),
                    int(row[3]),
                    int(row[4]),
                    int(row[5]),
                    int(row[6]),
                    int(row[7]),
                    int(row[8]),
                    int(row[9]),
                    float(row[10]),
                )
            except ValueError:
                parsed_numbers = None
            if parsed_numbers is None:
                raise TesseractMalformedOutputError("tesseract TSV output is malformed")
            (
                level,
                page_number,
                block_number,
                paragraph_number,
                line_number,
                word_number,
                left,
                top,
                width,
                height,
                confidence,
            ) = parsed_numbers

            if (
                not 1 <= level <= 5
                or page_number != 1
                or min(
                    block_number,
                    paragraph_number,
                    line_number,
                    word_number,
                    left,
                    top,
                    width,
                    height,
                )
                < 0
                or left + width > rendered_page.width
                or top + height > rendered_page.height
                or not math.isfinite(confidence)
                or not -1 <= confidence <= 100
            ):
                raise TesseractMalformedOutputError("tesseract TSV output is malformed")

            raw_text = row[11]
            if level == 5 and _has_unsafe_text_control(raw_text):
                raise TesseractMalformedOutputError("tesseract TSV output is malformed")
            text = raw_text.strip()
            if level != 5 or not text:
                continue
            if (
                min(block_number, paragraph_number, line_number, word_number, width, height) < 1
                or confidence < 0
            ):
                raise TesseractMalformedOutputError("tesseract TSV output is malformed")

            line_key = (paragraph_number, line_number)
            accumulator = blocks.get(block_number)
            if accumulator is None:
                blocks[block_number] = _BlockAccumulator.from_word(
                    line_key=line_key,
                    text=text,
                    confidence=confidence,
                    left=left,
                    top=top,
                    width=width,
                    height=height,
                )
            else:
                accumulator.add_word(
                    line_key=line_key,
                    text=text,
                    confidence=confidence,
                    left=left,
                    top=top,
                    width=width,
                    height=height,
                )

        ocr_blocks = tuple(
            OCRBlock(
                page_number=rendered_page.page_number,
                reading_order=reading_order,
                text="\n".join(" ".join(words) for words in accumulator.lines.values()),
                bbox=(
                    float(accumulator.x_min),
                    float(accumulator.y_min),
                    float(accumulator.x_max),
                    float(accumulator.y_max),
                ),
                confidence=sum(accumulator.confidences) / (100.0 * len(accumulator.confidences)),
            )
            for reading_order, accumulator in enumerate(blocks.values())
        )
        page_confidences = tuple(
            block.confidence for block in ocr_blocks if block.confidence is not None
        )
        return OCRPage(
            page_number=rendered_page.page_number,
            text="\n".join(block.text for block in ocr_blocks),
            blocks=ocr_blocks,
            confidence=(
                sum(page_confidences) / len(page_confidences) if page_confidences else None
            ),
        )

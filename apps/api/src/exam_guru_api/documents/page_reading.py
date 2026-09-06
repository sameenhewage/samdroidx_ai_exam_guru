import hashlib
import json
import math
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Any, BinaryIO, Protocol, cast

import pymupdf

from exam_guru_api.documents.fidelity import (
    MAX_TEXT_CHARACTERS,
    MissingOCRLanguagesError,
    assess_page,
    select_ocr_languages,
)
from exam_guru_api.documents.ocr import (
    MalformedOCROutputError,
    OCRConfigError,
    OCRContractError,
    OCRInputError,
    OCROutputLimitError,
    OCRProcessError,
    OCRTimeoutError,
    OCRUnavailableError,
)
from exam_guru_api.documents.page_images import (
    MAX_IMAGE_PIXELS,
    PageImageError,
    PageImageLimits,
    block_provenance,
    native_block_provenance,
    observe_page_image,
    render_page_image,
)
from exam_guru_api.documents.tesseract_ocr import (
    CommandRunner,
    RenderedPageImage,
    TesseractCliOCRAdapter,
    TesseractOCRConfig,
    TesseractProbe,
    TesseractUnavailableError,
    open_pdf_file,
)


def _default_ocr_config() -> TesseractOCRConfig:
    return TesseractOCRConfig(
        language="eng", allowed_languages=("sin", "tam", "eng"), max_pages=1, batch_size=1
    )


@dataclass(frozen=True, slots=True)
class PageReadingConfiguration:
    expected_languages: tuple[str, ...] = ()
    force_ocr: bool = False
    ocr: TesseractOCRConfig = field(default_factory=_default_ocr_config)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.expected_languages, tuple)
            or any(
                language not in {"si", "ta", "en", "und"} for language in self.expected_languages
            )
            or len(set(self.expected_languages)) != len(self.expected_languages)
            or not isinstance(self.force_ocr, bool)
            or not isinstance(self.ocr, TesseractOCRConfig)
        ):
            raise ValueError("invalid source page reading configuration")

    def to_dict(self) -> dict[str, object]:
        ocr = asdict(self.ocr)
        ocr["allowed_languages"] = list(self.ocr.allowed_languages)
        ocr["tessdata_directory"] = (
            str(self.ocr.tessdata_directory) if self.ocr.tessdata_directory is not None else None
        )
        return {
            "expected_languages": list(self.expected_languages),
            "force_ocr": self.force_ocr,
            "ocr": ocr,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "PageReadingConfiguration":
        if not isinstance(value, dict) or set(value) - {"expected_languages", "force_ocr", "ocr"}:
            raise ValueError("unsupported source page reading configuration")
        if len(json.dumps(value, allow_nan=False).encode()) > 65536:
            raise ValueError("source page reading configuration exceeds its bound")
        languages = value.get("expected_languages", [])
        ocr_values = value.get("ocr", {})
        if (
            not isinstance(languages, list)
            or any(not isinstance(language, str) for language in languages)
            or not isinstance(ocr_values, dict)
            or set(ocr_values) - {item.name for item in fields(TesseractOCRConfig)}
        ):
            raise ValueError("invalid source page reading configuration")
        selected = asdict(_default_ocr_config())
        selected.update(ocr_values)
        allowed = selected.get("allowed_languages")
        if not isinstance(allowed, (list, tuple)):
            raise ValueError("invalid OCR languages")
        selected["allowed_languages"] = tuple(allowed)
        directory = selected.get("tessdata_directory")
        if directory is not None:
            if not isinstance(directory, str):
                raise ValueError("invalid OCR model directory")
            selected["tessdata_directory"] = Path(directory)
        return cls(
            expected_languages=tuple(languages),
            force_ocr=cast(bool, value.get("force_ocr", False)),
            ocr=TesseractOCRConfig(**cast(Any, selected)),
        )


@dataclass(frozen=True, slots=True)
class NativePageText:
    raw_text: str = field(repr=False)
    font_names: tuple[str, ...] = ()
    image_coverage: float = 0.0


@dataclass(frozen=True, slots=True)
class PageReadingCandidate:
    raw_text: str = field(repr=False)
    method: str
    provenance: dict[str, object]


@dataclass(frozen=True, slots=True)
class PageReadingResult:
    page_number: int
    candidates: tuple[PageReadingCandidate, ...]
    failure_code: str | None = None


class PageReadingSession(Protocol):
    @property
    def page_count(self) -> int: ...

    def read_page(self, page_number: int) -> PageReadingResult: ...


class PageReader(Protocol):
    def open(
        self, source: BinaryIO, *, configuration: PageReadingConfiguration
    ) -> AbstractContextManager[PageReadingSession]: ...


def extract_native_page(document: pymupdf.Document, page_number: int) -> NativePageText:
    page = document[page_number - 1]
    text = cast(str, page.get_text("text", sort=True))
    font_names = tuple(
        sorted(
            {
                str(font[3])[:256]
                .encode("utf-8", errors="backslashreplace")
                .decode()
                .replace("\x00", "\\u0000")
                for font in page.get_fonts()
            }
        )
    )[:128]
    area = float(page.rect.width) * float(page.rect.height)
    image_area = 0.0
    for image in page.get_image_info():
        rectangle = pymupdf.Rect(image["bbox"]) & page.rect
        image_area += max(0.0, float(rectangle.width) * float(rectangle.height))
    coverage = min(1.0, image_area / area) if area > 0 and math.isfinite(image_area) else 0.0
    return NativePageText(raw_text=text, font_names=font_names, image_coverage=coverage)


def _failure_code(error: Exception) -> str:
    if (
        isinstance(error, (MissingOCRLanguagesError, TesseractUnavailableError))
        and error.missing_languages
    ):
        return "ocr_languages_unavailable"
    for kind, code in (
        (OCRTimeoutError, "ocr_timeout"),
        (OCRUnavailableError, "ocr_unavailable"),
        (OCRInputError, "ocr_input_rejected"),
        (OCROutputLimitError, "ocr_output_limit"),
        (OCRProcessError, "ocr_process_failed"),
        (OCRConfigError, "ocr_configuration_invalid"),
        (OCRContractError, "ocr_output_invalid"),
        (MalformedOCROutputError, "ocr_output_invalid"),
        (OSError, "ocr_unavailable"),
    ):
        if isinstance(error, kind):
            return code
    return "ocr_failed"


class _OpenPageReader:
    def __init__(
        self,
        source: BinaryIO,
        document: pymupdf.Document,
        configuration: PageReadingConfiguration,
        command_runner: CommandRunner | None,
        on_render: Callable[[RenderedPageImage], dict[str, object] | None] | None,
        execution_deadline: float | None,
    ) -> None:
        self._source = source
        self._document = document
        self._configuration = configuration
        self._command_runner = command_runner
        self._on_render = on_render
        self._execution_deadline = execution_deadline

    @property
    def page_count(self) -> int:
        return cast(int, self._document.page_count)

    def _adapter(self, configuration: TesseractOCRConfig) -> TesseractCliOCRAdapter:
        return TesseractCliOCRAdapter(
            config=configuration,
            command_runner=self._command_runner,
            execution_deadline=self._execution_deadline,
        )

    def _native_comparison_image(
        self, page_number: int, provenance: dict[str, object]
    ) -> str | None:
        if self._on_render is None:
            return None
        try:
            timeout = min(self._configuration.ocr.timeout_seconds, 60.0)
            if self._execution_deadline is not None:
                timeout = min(timeout, self._execution_deadline - time.monotonic())
            if timeout <= 0:
                raise PageImageError("source_page_image_timeout")
            limits = PageImageLimits(
                dpi=self._configuration.ocr.dpi,
                max_pixels=min(self._configuration.ocr.max_pixels_per_page, MAX_IMAGE_PIXELS),
                timeout_seconds=timeout,
            )
            with render_page_image(
                self._source,
                page_number,
                limits=limits,
                expected_page_count=self.page_count,
                wait_for_slot=True,
            ) as image:
                metadata, failure = observe_page_image(image, self._on_render)
                provenance["page_image"] = metadata
                provenance["page_image_use"] = "source_comparison"
                return failure
        except PageImageError as error:
            code = (
                "page_image_timeout"
                if error.code == "source_page_image_timeout"
                else "page_image_render_failed"
            )
            provenance["page_image"] = {"page_number": page_number, "failure_code": code}
            return code

    def read_page(self, page_number: int) -> PageReadingResult:
        if (
            isinstance(page_number, bool)
            or not isinstance(page_number, int)
            or not 1 <= page_number <= self.page_count
        ):
            raise ValueError("source page is out of range")
        native_provenance: dict[str, object] = {
            "engine": "pymupdf",
            "engine_version": pymupdf.VersionBind,
            "page_number": page_number,
            "config": {"text_mode": "text", "sort": True, "input_mode": "file"},
            "mappings_applied": [],
            "automatic_verification": False,
        }
        try:
            native = extract_native_page(self._document, page_number)
        except Exception:
            native = NativePageText(raw_text="")
            native_provenance["failure_code"] = "native_extraction_failed"
        if len(native.raw_text) > MAX_TEXT_CHARACTERS:
            return PageReadingResult(
                page_number=page_number,
                candidates=(
                    PageReadingCandidate(
                        "",
                        "native",
                        {
                            **native_provenance,
                            "failure_code": "native_text_limit",
                            "raw_character_count": len(native.raw_text),
                            "raw_sha256": hashlib.sha256(
                                native.raw_text.encode("utf-8", errors="surrogatepass")
                            ).hexdigest(),
                        },
                    ),
                ),
                failure_code="native_text_limit",
            )
        assessment = assess_page(
            native.raw_text, font_names=native.font_names, image_coverage=native.image_coverage
        )
        if assessment.languages == ("und",) and self._configuration.expected_languages:
            assessment = assess_page(
                native.raw_text,
                font_names=native.font_names,
                image_coverage=native.image_coverage,
                expected_languages=self._configuration.expected_languages,
            )
        native_provenance.update(
            fonts=list(native.font_names),
            image_coverage=native.image_coverage,
            languages=list(assessment.languages),
            risk_codes=list(assessment.risk_codes),
            algorithm_version=assessment.algorithm_version,
        )
        native_provenance["blocks"] = native_block_provenance(
            self._document,
            page_number,
            native.raw_text,
            max_bytes=min(
                16 * 1024, max(512, 56 * 1024 - len(json.dumps(native_provenance).encode()))
            ),
        )
        native_candidate = PageReadingCandidate(native.raw_text, "native", native_provenance)
        if assessment.recommended_route != "ocr_review" and not self._configuration.force_ocr:
            image_failure = self._native_comparison_image(page_number, native_provenance)
            if image_failure is not None:
                native_provenance["failure_code"] = image_failure
            return PageReadingResult(page_number, (native_candidate,), failure_code=image_failure)
        requested = select_ocr_languages(assessment, available_languages=("sin", "tam", "eng"))
        config_snapshot = cast(dict[str, object], self._configuration.to_dict()["ocr"])
        config_snapshot["language"] = "+".join(requested)
        provenance: dict[str, object] = {
            "engine": "tesseract-cli",
            "engine_version": None,
            "page_number": page_number,
            "languages": list(assessment.languages),
            "ocr_languages": list(requested),
            "available_languages": [],
            "traineddata_sha256": {},
            "config": config_snapshot,
            "model_family": self._configuration.ocr.model_family,
            "automatic_verification": False,
            "probe_status": "not_completed",
        }
        text = ""
        failure: str | None = None
        probe: TesseractProbe | None = None
        image_failure = None

        def rendered(image: RenderedPageImage) -> None:
            nonlocal image_failure
            metadata, image_failure = observe_page_image(image, self._on_render)
            provenance["page_image"] = metadata
            provenance["page_image_use"] = "ocr_input"
            native_provenance["page_image"] = metadata
            native_provenance["page_image_use"] = "source_comparison"

        try:
            selected_config = replace(self._configuration.ocr, language="+".join(requested))
            adapter = self._adapter(selected_config)
            probe = adapter.probe(check_selected_languages=False)
            provenance.update(
                engine_version=probe.engine_version,
                available_languages=list(probe.available_languages),
                tessdata_directory=probe.tessdata_directory,
                probe_status="languages_probed",
            )
            missing = tuple(
                language for language in requested if language not in probe.available_languages
            )
            if missing:
                raise MissingOCRLanguagesError(missing)
            select_ocr_languages(assessment, available_languages=probe.available_languages)
            probe = adapter.probe(hash_traineddata=True)
            provenance.update(
                traineddata_sha256=dict(probe.traineddata_sha256), probe_status="verified"
            )
            result = adapter.extract_file(
                self._source, page_numbers=(page_number,), probe=probe, on_render=rendered
            )
            text = result.pages[0].text
            if len(text) > MAX_TEXT_CHARACTERS:
                provenance["raw_character_count"] = len(text)
                provenance["raw_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
                text = ""
                failure = "ocr_text_limit"
            provenance["confidence"] = result.pages[0].confidence
            provenance["block_count"] = len(result.pages[0].blocks)
            provenance["blocks"] = block_provenance(
                text,
                [(block.text, block.bbox) for block in result.pages[0].blocks],
                separator="\n",
                coordinate_space="image_pixels",
                max_bytes=min(
                    16 * 1024, max(512, 56 * 1024 - len(json.dumps(provenance).encode()))
                ),
            )
        except (
            MissingOCRLanguagesError,
            OCRConfigError,
            OCRContractError,
            OCRInputError,
            OCRUnavailableError,
            OCRTimeoutError,
            OCRProcessError,
            OCROutputLimitError,
            MalformedOCROutputError,
            OSError,
        ) as error:
            failure = _failure_code(error)
            if isinstance(error, (MissingOCRLanguagesError, TesseractUnavailableError)):
                provenance["missing_languages"] = list(error.missing_languages)
        failure = failure or image_failure
        if failure is not None:
            provenance["failure_code"] = failure
        return PageReadingResult(
            page_number,
            (native_candidate, PageReadingCandidate(text, "ocr", provenance)),
            failure_code=failure,
        )


class FilePageReader:
    def __init__(
        self,
        *,
        command_runner: CommandRunner | None = None,
        on_render: Callable[[RenderedPageImage], dict[str, object] | None] | None = None,
        execution_deadline: float | None = None,
    ) -> None:
        self._command_runner = command_runner
        self._on_render = on_render
        self._execution_deadline = execution_deadline

    @contextmanager
    def open(
        self, source: BinaryIO, *, configuration: PageReadingConfiguration
    ) -> Iterator[PageReadingSession]:
        with open_pdf_file(source) as document:
            yield _OpenPageReader(
                source,
                document,
                configuration,
                self._command_runner,
                self._on_render,
                self._execution_deadline,
            )

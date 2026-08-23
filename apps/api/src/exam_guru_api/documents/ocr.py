"""Provider-independent OCR contracts and deterministic benchmark mechanics.

Extracted text is untrusted data.  Nothing in this module interprets text as a
prompt or instruction.  The deterministic metrics are suitable for fixed test
fixtures, but do not establish OCR quality for Sinhala or any other language.
"""

import math
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol

type OCRConfigValue = str | int | float | bool | None
type OCRBoundingBox = tuple[float, float, float, float]


class OCRContractError(ValueError):
    """Raised when an OCR request, output, or benchmark contract is malformed."""


class MalformedOCROutputError(TypeError):
    """Raised when an adapter crosses the port with an untyped output."""


def _is_non_blank_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_ordered_page_numbers(page_numbers: object, *, field_name: str) -> None:
    if not isinstance(page_numbers, tuple):
        raise OCRContractError(f"{field_name} must be a tuple")
    if any(
        not isinstance(page_number, int) or isinstance(page_number, bool) or page_number < 1
        for page_number in page_numbers
    ):
        raise OCRContractError(f"{field_name} must contain positive integers")
    if page_numbers != tuple(sorted(set(page_numbers))):
        raise OCRContractError(f"{field_name} must be unique and ascending")


@dataclass(frozen=True, slots=True)
class OCRRequest:
    """Binary source input and immutable source identity supplied to an OCR port."""

    source_document_id: str
    source_checksum_sha256: str
    content: bytes = field(repr=False)
    media_type: str
    page_numbers: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not _is_non_blank_string(self.source_document_id):
            raise OCRContractError("source_document_id must be non-blank")
        if (
            not isinstance(self.source_checksum_sha256, str)
            or len(self.source_checksum_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.source_checksum_sha256)
        ):
            raise OCRContractError("source_checksum_sha256 must be a lowercase SHA-256 digest")
        if not isinstance(self.content, bytes) or not self.content:
            raise OCRContractError("content must be non-empty bytes")
        if not _is_non_blank_string(self.media_type):
            raise OCRContractError("media_type must be non-blank")
        _validate_ordered_page_numbers(self.page_numbers, field_name="page_numbers")


@dataclass(frozen=True, slots=True)
class OCRBlock:
    """One OCR text block with source-page and optional layout provenance."""

    page_number: int
    reading_order: int
    text: str
    bbox: OCRBoundingBox | None = None
    confidence: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.page_number, int) or isinstance(self.page_number, bool):
            raise OCRContractError("block page_number must be an integer")
        if self.page_number < 1:
            raise OCRContractError("block page_number must be positive")
        if not isinstance(self.reading_order, int) or isinstance(self.reading_order, bool):
            raise OCRContractError("reading_order must be an integer")
        if self.reading_order < 0:
            raise OCRContractError("reading_order must be non-negative")
        if not _is_non_blank_string(self.text):
            raise OCRContractError("block text must be non-blank")
        if self.bbox is not None:
            if not isinstance(self.bbox, tuple) or len(self.bbox) != 4:
                raise OCRContractError("bbox must contain four coordinates")
            if any(
                not isinstance(coordinate, (int, float))
                or isinstance(coordinate, bool)
                or not math.isfinite(coordinate)
                for coordinate in self.bbox
            ):
                raise OCRContractError("bbox coordinates must be finite numbers")
            x_min, y_min, x_max, y_max = self.bbox
            if x_min > x_max or y_min > y_max:
                raise OCRContractError("bbox coordinates must be ordered")
        if self.confidence is not None and (
            not isinstance(self.confidence, (int, float))
            or isinstance(self.confidence, bool)
            or not math.isfinite(self.confidence)
            or not 0.0 <= self.confidence <= 1.0
        ):
            raise OCRContractError("confidence must be between zero and one")


@dataclass(frozen=True, slots=True)
class OCRPage:
    """OCR text and reading-ordered blocks for one one-based source page."""

    page_number: int
    text: str
    blocks: tuple[OCRBlock, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.page_number, int) or isinstance(self.page_number, bool):
            raise OCRContractError("page_number must be an integer")
        if self.page_number < 1:
            raise OCRContractError("page_number must be positive")
        if not isinstance(self.text, str):
            raise OCRContractError("page text must be a string")
        if not isinstance(self.blocks, tuple) or any(
            not isinstance(block, OCRBlock) for block in self.blocks
        ):
            raise OCRContractError("blocks must contain OCRBlock values")
        if any(block.page_number != self.page_number for block in self.blocks):
            raise OCRContractError("every block must reference its containing page")
        reading_orders = tuple(block.reading_order for block in self.blocks)
        if reading_orders != tuple(sorted(set(reading_orders))):
            raise OCRContractError("block reading_order values must be unique and ascending")


@dataclass(frozen=True, slots=True)
class OCRResult:
    """Typed OCR output with reproducible engine provenance."""

    engine: str
    engine_version: str
    config: Mapping[str, OCRConfigValue]
    pages: tuple[OCRPage, ...]

    def __post_init__(self) -> None:
        if not _is_non_blank_string(self.engine):
            raise OCRContractError("engine must be non-blank")
        if not _is_non_blank_string(self.engine_version):
            raise OCRContractError("engine_version must be non-blank")
        if not isinstance(self.config, Mapping):
            raise OCRContractError("config must be a mapping")

        config_snapshot: dict[str, OCRConfigValue] = {}
        for key, value in self.config.items():
            if not _is_non_blank_string(key):
                raise OCRContractError("config keys must be non-blank strings")
            if value is not None and not isinstance(value, (str, int, float, bool)):
                raise OCRContractError("config values must be scalar")
            if isinstance(value, float) and not math.isfinite(value):
                raise OCRContractError("floating-point config values must be finite")
            config_snapshot[key] = value

        if not isinstance(self.pages, tuple) or any(
            not isinstance(page, OCRPage) for page in self.pages
        ):
            raise OCRContractError("pages must contain OCRPage values")
        page_numbers = tuple(page.page_number for page in self.pages)
        _validate_ordered_page_numbers(page_numbers, field_name="result page numbers")

        object.__setattr__(
            self,
            "config",
            MappingProxyType(dict(sorted(config_snapshot.items()))),
        )


class OCRPort(Protocol):
    """Small first-party OCR boundary; provider SDK types must stay behind it."""

    def extract(self, request: OCRRequest) -> OCRResult: ...


class DeterministicFakeOCRAdapter:
    """In-memory adapter that always returns one immutable, preconfigured result."""

    def __init__(self, result: OCRResult) -> None:
        self._result = result
        self._requests: list[OCRRequest] = []

    @property
    def requests(self) -> tuple[OCRRequest, ...]:
        return tuple(self._requests)

    def extract(self, request: OCRRequest) -> OCRResult:
        self._requests.append(request)
        return self._result


@dataclass(frozen=True, slots=True)
class OCRQuestionStructure:
    """Explicit fixture markers used to score one expected question structure."""

    question_id: str
    page_number: int
    required_markers: tuple[str, ...]

    def __post_init__(self) -> None:
        if not _is_non_blank_string(self.question_id):
            raise OCRContractError("question_id must be non-blank")
        if (
            not isinstance(self.page_number, int)
            or isinstance(self.page_number, bool)
            or self.page_number < 1
        ):
            raise OCRContractError("question page_number must be a positive integer")
        if not isinstance(self.required_markers, tuple) or not self.required_markers:
            raise OCRContractError("required_markers must be a non-empty tuple")
        if any(not _is_non_blank_string(marker) for marker in self.required_markers):
            raise OCRContractError("question markers must be non-blank strings")


@dataclass(frozen=True, slots=True)
class OCRBenchmarkCase:
    """Fixed ground truth for deterministic OCR mechanics and regression evaluation."""

    name: str
    reference_pages: tuple[OCRPage, ...]
    question_structures: tuple[OCRQuestionStructure, ...] = ()

    def __post_init__(self) -> None:
        if not _is_non_blank_string(self.name):
            raise OCRContractError("benchmark name must be non-blank")
        if not isinstance(self.reference_pages, tuple) or any(
            not isinstance(page, OCRPage) for page in self.reference_pages
        ):
            raise OCRContractError("reference_pages must contain OCRPage values")
        reference_page_numbers = tuple(page.page_number for page in self.reference_pages)
        _validate_ordered_page_numbers(
            reference_page_numbers,
            field_name="reference page numbers",
        )
        if not isinstance(self.question_structures, tuple) or any(
            not isinstance(structure, OCRQuestionStructure)
            for structure in self.question_structures
        ):
            raise OCRContractError("question_structures must contain OCRQuestionStructure values")

        reference_page_number_set = set(reference_page_numbers)
        question_ids: set[str] = set()
        for structure in self.question_structures:
            if structure.page_number not in reference_page_number_set:
                raise OCRContractError("question structure references an unknown page")
            if structure.question_id in question_ids:
                raise OCRContractError("question structure identifiers must be unique")
            question_ids.add(structure.question_id)


@dataclass(frozen=True, slots=True)
class OCRBenchmarkMetrics:
    normalized_character_error_rate: float
    page_coverage: float
    question_structure_coverage: float
    edit_distance: int
    normalization_length: int
    expected_page_count: int
    covered_page_count: int
    expected_question_structure_count: int
    covered_question_structure_count: int


@dataclass(frozen=True, slots=True)
class OCRBenchmarkReport:
    case_name: str
    engine: str
    engine_version: str
    config: Mapping[str, OCRConfigValue]
    metrics: OCRBenchmarkMetrics


def normalize_ocr_text(text: str) -> str:
    """Normalize only for scoring; the OCR result remains byte-for-byte untouched."""

    return " ".join(unicodedata.normalize("NFC", text).split())


def _levenshtein_distance(reference: str, candidate: str) -> int:
    if reference == candidate:
        return 0
    if not reference:
        return len(candidate)
    if not candidate:
        return len(reference)
    if len(reference) < len(candidate):
        reference, candidate = candidate, reference

    previous_row = list(range(len(candidate) + 1))
    for reference_index, reference_character in enumerate(reference, start=1):
        current_row = [reference_index]
        for candidate_index, candidate_character in enumerate(candidate, start=1):
            insertion = current_row[candidate_index - 1] + 1
            deletion = previous_row[candidate_index] + 1
            substitution = previous_row[candidate_index - 1] + (
                reference_character != candidate_character
            )
            current_row.append(min(insertion, deletion, substitution))
        previous_row = current_row
    return previous_row[-1]


def _normalized_distance_parts(reference: str, candidate: str) -> tuple[int, int]:
    normalized_reference = normalize_ocr_text(reference)
    normalized_candidate = normalize_ocr_text(candidate)
    return (
        _levenshtein_distance(normalized_reference, normalized_candidate),
        max(len(normalized_reference), len(normalized_candidate)),
    )


def normalized_character_error_rate(reference: str, candidate: str) -> float:
    """Return bounded Unicode/whitespace-normalized edit distance in ``[0, 1]``."""

    edit_distance, normalization_length = _normalized_distance_parts(reference, candidate)
    if normalization_length == 0:
        return 0.0
    return edit_distance / normalization_length


def _coverage(covered_count: int, expected_count: int) -> float:
    if expected_count == 0:
        return 1.0
    return covered_count / expected_count


def evaluate_ocr(case: OCRBenchmarkCase, result: OCRResult) -> OCRBenchmarkMetrics:
    """Evaluate one typed OCR result against deterministic fixture ground truth."""

    reference_by_page = {page.page_number: page.text for page in case.reference_pages}
    candidate_by_page = {page.page_number: page.text for page in result.pages}

    edit_distance = 0
    normalization_length = 0
    for page_number in sorted(reference_by_page.keys() | candidate_by_page.keys()):
        page_edit_distance, page_normalization_length = _normalized_distance_parts(
            reference_by_page.get(page_number, ""),
            candidate_by_page.get(page_number, ""),
        )
        edit_distance += page_edit_distance
        normalization_length += page_normalization_length

    character_error_rate = (
        0.0 if normalization_length == 0 else edit_distance / normalization_length
    )
    covered_page_count = len(reference_by_page.keys() & candidate_by_page.keys())

    covered_question_count = 0
    for structure in case.question_structures:
        candidate_text = normalize_ocr_text(candidate_by_page.get(structure.page_number, ""))
        if all(
            normalize_ocr_text(marker) in candidate_text for marker in structure.required_markers
        ):
            covered_question_count += 1

    return OCRBenchmarkMetrics(
        normalized_character_error_rate=character_error_rate,
        page_coverage=_coverage(covered_page_count, len(reference_by_page)),
        question_structure_coverage=_coverage(
            covered_question_count,
            len(case.question_structures),
        ),
        edit_distance=edit_distance,
        normalization_length=normalization_length,
        expected_page_count=len(reference_by_page),
        covered_page_count=covered_page_count,
        expected_question_structure_count=len(case.question_structures),
        covered_question_structure_count=covered_question_count,
    )


def run_ocr_benchmark(
    *,
    adapter: OCRPort,
    request: OCRRequest,
    case: OCRBenchmarkCase,
) -> OCRBenchmarkReport:
    """Run an adapter and retain its engine/version/config beside deterministic metrics."""

    result = adapter.extract(request)
    if not isinstance(result, OCRResult):
        raise MalformedOCROutputError("OCR adapter must return OCRResult")
    return OCRBenchmarkReport(
        case_name=case.name,
        engine=result.engine,
        engine_version=result.engine_version,
        config=result.config,
        metrics=evaluate_ocr(case, result),
    )

import hashlib
import json
import math
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from copy import deepcopy
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
from exam_guru_api.documents.native_math_recovery import (
    ALGORITHM_VERSION as NATIVE_MATH_RECOVERY_VERSION,
)
from exam_guru_api.documents.native_math_recovery import (
    NativeMathRecovery,
    recover_native_equations,
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
from exam_guru_api.documents.source_math_fidelity import (
    MAX_ENCODED_MATH_REFERENCE_BYTES,
    MAX_ENCODED_MATH_WORD_BYTES,
    assess_math_fidelity,
    decode_math_evidence,
    encode_math_evidence,
    extract_math_layout,
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
    font_metadata: tuple[dict[str, object], ...] = ()


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
    preferred_index: int | None = None


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
    metadata: list[dict[str, object]] = []
    for font in page.get_fonts()[:128]:
        item = {
            "xref": int(font[0]),
            "name": str(font[3])[:256],
            "type": str(font[2])[:64],
            "encoding": str(font[5])[:128],
            "embedded": font[1] != "n/a",
            "has_to_unicode": document.xref_get_key(int(font[0]), "ToUnicode")[0] != "null"
            if font[0]
            else False,
            "mapping_status": "not_applied",
        }
        metadata.append(item)
    if len(json.dumps(metadata, ensure_ascii=True).encode()) > 12 * 1024:
        metadata = [{"failure_code": "font_metadata_limit", **_metadata_summary(metadata)}]
    return NativePageText(
        raw_text=text, font_names=font_names, image_coverage=coverage, font_metadata=tuple(metadata)
    )


def _math_reference(page: pymupdf.Page) -> dict[str, object]:
    reference = extract_math_layout(page)
    try:
        encoded = encode_math_evidence(reference)
        if len(json.dumps(encoded, ensure_ascii=True).encode()) <= MAX_ENCODED_MATH_REFERENCE_BYTES:
            return cast(dict[str, object], encoded)
    except ValueError:
        pass
    digest = hashlib.sha256(json.dumps(reference, sort_keys=True).encode()).hexdigest()
    omitted = {
        key: _metadata_summary(reference[key])
        for key in ("anchors", "tables", "images", "unsupported_layouts")
    }
    reference.update(
        anchors=[],
        tables=[],
        images=[],
        unsupported_layouts=[],
        complete=False,
        risk_codes=["math_metadata_limit"],
        full_evidence_sha256=digest,
        omitted_metadata=omitted,
    )
    return reference


def _recovery_evidence(
    recovered: NativeMathRecovery,
    text: str,
    words: list[tuple[str, tuple[float, float, float, float]]],
    reference: dict[str, object],
    page_number: int,
) -> dict[str, object]:
    def digest(value: object) -> str:
        return hashlib.sha256(
            json.dumps(
                value, ensure_ascii=True, sort_keys=True, allow_nan=False, separators=(",", ":")
            ).encode()
        ).hexdigest()

    metadata = json.loads(json.dumps(recovered.provenance, ensure_ascii=True, allow_nan=False))
    expected = {
        "algorithm_version": NATIVE_MATH_RECOVERY_VERSION,
        "source": "original_pdf_native_nonlegacy",
        "coordinate_space": "unrotated_pdf_points",
        "offset_space": "unicode_codepoints",
        "page_number": page_number,
        "original_ocr_sha256": hashlib.sha256(
            text.encode("utf-8", errors="surrogatepass")
        ).hexdigest(),
        "recovered_text_sha256": hashlib.sha256(
            recovered.raw_text.encode("utf-8", errors="surrogatepass")
        ).hexdigest(),
        "source_reference_sha256": digest(decode_math_evidence(reference)),
        "original_words_sha256": digest(words),
        "evidence_hash_serialization": {
            "version": "python-json-sorted-compact-ascii-v1",
            "input": "decoded_math_evidence",
            "fields": ["source_reference_sha256", "original_words_sha256"],
            "hash_algorithm": "sha256",
            "encoding": "utf-8",
            "ensure_ascii": True,
            "sort_keys": True,
            "allow_nan": False,
            "separators": [",", ":"],
        },
    }
    if (
        digest({key: metadata.get(key) for key in expected}) != digest(expected)
        or not isinstance(metadata.get("edits"), list)
        or not metadata["edits"]
    ):
        raise ValueError("invalid native math recovery evidence")
    return cast(dict[str, object], metadata)


def _candidate_evidence(
    candidate: PageReadingCandidate,
    languages: tuple[str, ...],
    reference: dict[str, object],
    words: list[tuple[str, tuple[float, float, float, float]]] | None,
) -> dict[str, object]:
    provenance = candidate.provenance
    provenance.setdefault("raw_character_count", len(candidate.raw_text))
    provenance.setdefault(
        "raw_sha256",
        hashlib.sha256(candidate.raw_text.encode("utf-8", errors="surrogatepass")).hexdigest(),
    )
    assessment = assess_page(
        candidate.raw_text,
        expected_languages=languages,
        font_names=tuple(cast(list[str], provenance.get("fonts", []))),
        image_coverage=cast(float, provenance.get("image_coverage", 0.0)),
        method=candidate.method,
    )
    maths = assess_math_fidelity(reference, candidate.raw_text, words)
    word_evidence = [[text, list(box)] for text, box in (words or [])]
    try:
        encoded_words = encode_math_evidence(word_evidence)
        if len(json.dumps(encoded_words, ensure_ascii=True).encode()) > MAX_ENCODED_MATH_WORD_BYTES:
            raise ValueError("math word evidence exceeds its storage bound")
    except ValueError:
        provenance["omitted_metadata"] = {"maths_words": _metadata_summary(word_evidence)}
        provenance.setdefault("failure_code", "math_word_evidence_limit")
        encoded_words = []
        maths = {
            **maths,
            "can_confirm": False,
            "risk_codes": sorted(
                set(cast(list[str], maths["risk_codes"])) | {"math_word_evidence_limit"}
            ),
        }
    for font in cast(list[dict[str, object]], provenance.get("font_metadata", [])):
        if font.get("failure_code") == "font_metadata_limit":
            provenance.setdefault("failure_code", "font_metadata_limit")
    provenance["maths_reference"] = reference
    provenance["maths_words"] = encoded_words
    provenance["maths_fidelity"] = {
        "can_confirm": maths["can_confirm"],
        "risk_codes": maths["risk_codes"],
        **{
            f"{key}_count": len(cast(list[object], maths.get(key, [])))
            for key in ("preserved", "lost", "changed", "added")
        },
    }
    readable = assessment.can_confirm and "failure_code" not in provenance
    local = assessment.script_counts["sinhala"] + assessment.script_counts["tamil"]
    info: dict[str, object] = {
        "text_readable": readable,
        "can_confirm": readable and maths["can_confirm"] is True,
        "script_share": local / max(1, local + assessment.script_counts["latin"]),
        "risk_count": len(assessment.risk_codes) + len(cast(list[str], maths["risk_codes"])),
        "algorithm_version": assessment.algorithm_version,
    }
    provenance["languages"] = list(assessment.languages)
    provenance["risk_codes"] = list(assessment.risk_codes)
    provenance["algorithm_version"] = assessment.algorithm_version
    provenance["candidate_selection"] = info
    return info


def _metadata_summary(value: object) -> dict[str, object]:
    # Nonfinite evidence is rejected by the codec; hash its representation only.
    # No invalid float is retained in the persisted omission summary.
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True).encode()
    count = len(value) if isinstance(value, (dict, list, tuple, str)) else 1
    if isinstance(value, dict) and isinstance(value.get("total_count"), int):
        count = value["total_count"]
    return {
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "byte_count": len(encoded),
        "item_count": count,
    }


def _bound_candidate_metadata(candidate: PageReadingCandidate) -> None:
    provenance = candidate.provenance
    # Leave room for the service/job's source checksum, job ID and final selection fields.
    limit = 60 * 1024
    if len(json.dumps(provenance, ensure_ascii=True, allow_nan=False).encode()) <= limit:
        return
    original_summary = _metadata_summary(provenance)
    omitted = dict(cast(dict[str, object], provenance.get("omitted_metadata", {})))
    for key in ("maths_reference", "maths_words", "font_metadata", "fonts", "blocks", "recovery"):
        if key in provenance:
            omitted.setdefault(key, _metadata_summary(provenance.pop(key)))
    provenance["omitted_metadata"] = omitted
    if "failure_code" in provenance:
        provenance["prior_failure_code"] = provenance["failure_code"]
    provenance["failure_code"] = "candidate_metadata_limit"
    provenance["maths_fidelity"] = {
        "can_confirm": False,
        "risk_codes": ["candidate_metadata_limit"],
    }
    cast(dict[str, object], provenance["candidate_selection"]).update(
        can_confirm=False, text_readable=False, qualified_recovery=False
    )
    # Config/probe strings are caller/provider controlled. In the exceptional case that
    # they alone exceed storage, retain their digest identity rather than losing the text.
    for key in sorted(provenance, key=lambda key: len(json.dumps(provenance[key])), reverse=True):
        if len(json.dumps(provenance, ensure_ascii=True, allow_nan=False).encode()) <= limit:
            break
        if key in {"omitted_metadata", "candidate_selection", "maths_fidelity", "derivation"}:
            continue
        value = provenance[key]
        if len(json.dumps(value, ensure_ascii=True).encode()) > 1024:
            omitted[key] = _metadata_summary(value)
            provenance[key] = {"omitted": True, **cast(dict[str, object], omitted[key])}
    if len(json.dumps(provenance, ensure_ascii=True, allow_nan=False).encode()) > limit:
        # Many individually small extensions, or an existing omission/selection payload,
        # can exhaust the loop without fitting. Keep identity and a digest of the full
        # original metadata instead of passing an oversized failed candidate to storage.
        retained: dict[str, object] = {
            key: provenance[key]
            for key in (
                "engine",
                "engine_version",
                "page_number",
                "source_checksum_sha256",
                "source_languages",
                "languages",
                "config",
                "ocr_languages",
                "traineddata_sha256",
                "model_family",
                "page_image",
                "page_image_use",
                "raw_sha256",
                "raw_character_count",
                "prior_failure_code",
                "derivation",
            )
            if key in provenance
        }
        selection = cast(dict[str, object], provenance["candidate_selection"])
        retained["candidate_selection"] = {
            "can_confirm": False,
            "text_readable": False,
            "script_share": selection["script_share"],
            "risk_count": selection["risk_count"],
            "algorithm_version": selection["algorithm_version"],
        }
        retained.update(
            failure_code="candidate_metadata_limit",
            automatic_verification=False,
            mappings_applied=[],
            maths_fidelity={"can_confirm": False, "risk_codes": ["candidate_metadata_limit"]},
            omitted_metadata={"full_provenance": original_summary},
        )
        provenance.clear()
        provenance.update(retained)


def _selected_result(page_number: int, candidates: list[PageReadingCandidate]) -> PageReadingResult:
    for candidate in candidates:
        _bound_candidate_metadata(candidate)

    def rank(index: int) -> tuple[bool, bool, bool, bool, float, int, int, int]:
        selection = cast(dict[str, object], candidates[index].provenance["candidate_selection"])
        return (
            selection["can_confirm"] is True,
            selection["text_readable"] is True,
            candidates[index].method == "native" and selection["can_confirm"] is True,
            selection["can_confirm"] is not True and selection.get("qualified_recovery") is True,
            cast(float, selection.get("prose_script_share", selection["script_share"])),
            -cast(int, selection["risk_count"]),
            cast(int, selection.get("native_math_preserved_gain", 0)),
            index,
        )

    selected = max(range(len(candidates)), key=rank)
    for index, candidate in enumerate(candidates):
        selection = cast(dict[str, object], candidate.provenance["candidate_selection"])
        qualified = selection.get("qualified_recovery") is True
        selection.update(
            selected=index == selected,
            candidate_count=len(candidates),
            strategy="source-fidelity-ranked-v3",
            qualified_recovery=qualified,
            selection_reason="qualified_native_math_recovery"
            if index == selected and qualified
            else "source_fidelity_order",
        )
    chosen = candidates[selected]
    safe = cast(dict[str, object], chosen.provenance["candidate_selection"])["can_confirm"] is True
    failure = (
        None if safe else str(chosen.provenance.get("failure_code") or "source_fidelity_failed")
    )
    return PageReadingResult(page_number, tuple(candidates), failure, selected)


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
            "source_languages": list(self._configuration.expected_languages or ("und",)),
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
                preferred_index=0,
            )
        assessment = assess_page(
            native.raw_text,
            font_names=native.font_names,
            image_coverage=native.image_coverage,
        )
        if assessment.languages == ("und",) and self._configuration.expected_languages:
            assessment = assess_page(
                native.raw_text,
                font_names=native.font_names,
                image_coverage=native.image_coverage,
                expected_languages=self._configuration.expected_languages,
            )
        reference = _math_reference(self._document[page_number - 1])
        native_words = [
            (str(word[4]), tuple(float(value) for value in word[:4]))
            for word in self._document[page_number - 1].get_text("words", sort=True)
        ]
        native_provenance.update(
            fonts=list(native.font_names),
            font_metadata=list(native.font_metadata),
            image_coverage=native.image_coverage,
            source_languages=list(assessment.languages),
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
        native_info = _candidate_evidence(
            native_candidate,
            assessment.languages,
            reference,
            cast(list[tuple[str, tuple[float, float, float, float]]], native_words),
        )
        if (
            assessment.recommended_route != "ocr_review"
            and native_info["can_confirm"]
            and not self._configuration.force_ocr
        ):
            image_failure = self._native_comparison_image(page_number, native_provenance)
            if image_failure is not None:
                native_provenance["failure_code"] = image_failure
                native_info.update(can_confirm=False, text_readable=False)
            return _selected_result(page_number, [native_candidate])
        requested = select_ocr_languages(assessment, available_languages=("sin", "tam", "eng"))
        plans = [requested]
        if "si" in assessment.languages and "ta" not in assessment.languages:
            plans.append(("sin",))
        originals = [
            self._ocr_candidate(
                page_number, languages, assessment.languages, native_provenance, reference
            )
            for languages in plans
        ]
        candidates = [native_candidate, *(candidate for candidate, _ in originals)]
        for candidate in candidates:
            _bound_candidate_metadata(candidate)
        for index, (candidate, words) in enumerate(originals, 1):
            recovered = self._recovered_candidate(
                page_number,
                native_candidate,
                candidate,
                index,
                words,
                assessment.languages,
                reference,
            )
            if recovered is not None:
                candidates.append(recovered)
        # A probe failure can occur before OCR renders anything. Only then render a
        # separate comparison; never retry a declared missing/failed image as quality repair.
        if "page_image" not in native_provenance and native_info["can_confirm"]:
            self._native_comparison_image(page_number, native_provenance)
        comparison = native_provenance.get("page_image")
        if isinstance(comparison, dict) and comparison.get("failure_code"):
            native_provenance["failure_code"] = comparison["failure_code"]
            native_info.update(can_confirm=False, text_readable=False)
        return _selected_result(page_number, candidates)

    def _recovered_candidate(
        self,
        page_number: int,
        native: PageReadingCandidate,
        original: PageReadingCandidate,
        original_index: int,
        words: list[tuple[str, tuple[float, float, float, float]]],
        source_languages: tuple[str, ...],
        reference: dict[str, object],
    ) -> PageReadingCandidate | None:
        if "failure_code" in original.provenance or "failure_code" in native.provenance:
            return None
        try:
            recovered = recover_native_equations(
                self._document[page_number - 1], original.raw_text, list(words)
            )
            if recovered is None or recovered.raw_text == original.raw_text:
                return None
            if (
                not isinstance(recovered.raw_text, str)
                or len(recovered.raw_text) > MAX_TEXT_CHARACTERS
                or not isinstance(recovered.provenance, dict)
                or not recovered.provenance
            ):
                raise ValueError("invalid native math recovery")
            recovery = _recovery_evidence(
                recovered, original.raw_text, words, reference, page_number
            )
            provenance = deepcopy(original.provenance)
            for key in (
                "raw_sha256",
                "raw_character_count",
                "blocks",
                "candidate_selection",
                "maths_fidelity",
                "maths_words",
                "maths_reference",
                "risk_codes",
                "languages",
            ):
                provenance.pop(key, None)
            provenance.update(
                engine="native-math-recovery",
                engine_version="1",
                recovery=recovery,
                derivation={
                    "engine": "native-math-recovery",
                    "engine_version": "1",
                    "ocr_engine": original.provenance["engine"],
                    "ocr_engine_version": original.provenance["engine_version"],
                    "ocr_config_sha256": hashlib.sha256(
                        json.dumps(
                            original.provenance["config"], sort_keys=True, ensure_ascii=True
                        ).encode()
                    ).hexdigest(),
                    "input_candidates": [
                        {
                            "candidate_index": index,
                            "method": candidate.method,
                            "raw_sha256": candidate.provenance["raw_sha256"],
                        }
                        for index, candidate in ((0, native), (original_index, original))
                    ],
                },
            )
            candidate = PageReadingCandidate(recovered.raw_text, "ocr", provenance)
            selection = _candidate_evidence(
                candidate, source_languages, reference, list(recovered.words)
            )
            original_selection = cast(dict[str, object], original.provenance["candidate_selection"])
            original_maths = cast(dict[str, object], original.provenance["maths_fidelity"])
            recovered_maths = cast(dict[str, object], provenance["maths_fidelity"])
            selection.update(
                prose_script_share=original_selection["script_share"],
                prose_source_candidate_index=original_index,
                parent_risk_count=original_selection["risk_count"],
                native_math_preserved_gain=max(
                    0,
                    cast(int, recovered_maths["preserved_count"])
                    - cast(int, original_maths["preserved_count"]),
                ),
            )
            selection["qualified_recovery"] = (
                selection["text_readable"] is True
                and cast(int, selection["native_math_preserved_gain"]) > 0
                and cast(int, selection["risk_count"]) < cast(int, original_selection["risk_count"])
                and not any(
                    "failure_code" in value.provenance or "omitted_metadata" in value.provenance
                    for value in (native, original, candidate)
                )
                and set(cast(list[str], provenance["risk_codes"]))
                <= set(cast(list[str], original.provenance["risk_codes"]))
                and set(cast(list[str], recovered_maths["risk_codes"]))
                <= set(cast(list[str], original_maths["risk_codes"]))
            )
            return candidate
        except Exception:
            original.provenance["recovery_failure_code"] = "native_math_recovery_failed"
            return None

    def _ocr_candidate(
        self,
        page_number: int,
        requested: tuple[str, ...],
        source_languages: tuple[str, ...],
        native_provenance: dict[str, object],
        reference: dict[str, object],
    ) -> tuple[PageReadingCandidate, list[tuple[str, tuple[float, float, float, float]]]]:
        config_snapshot = cast(dict[str, object], self._configuration.to_dict()["ocr"])
        config_snapshot["language"] = "+".join(requested)
        provenance: dict[str, object] = {
            "engine": "tesseract-cli",
            "engine_version": None,
            "page_number": page_number,
            "source_languages": list(source_languages),
            "languages": list(source_languages),
            "fonts": native_provenance.get("fonts", []),
            "font_metadata": native_provenance.get("font_metadata", []),
            "mappings_applied": [],
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
        raster_size: tuple[int, int] | None = None
        words: list[tuple[str, tuple[float, float, float, float]]] = []

        def rendered(image: RenderedPageImage) -> None:
            nonlocal image_failure, raster_size
            raster_size = image.width, image.height
            metadata, image_failure = observe_page_image(image, self._on_render)
            provenance["page_image"] = metadata
            provenance["page_image_use"] = "ocr_input"
            comparison = native_provenance.get("page_image")
            if comparison is None or (
                isinstance(comparison, dict)
                and comparison.get("failure_code")
                and image_failure is None
            ):
                native_provenance["page_image"] = dict(metadata)
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
            probe = adapter.probe(hash_traineddata=True)
            provenance.update(
                traineddata_sha256=dict(probe.traineddata_sha256), probe_status="verified"
            )
            result = adapter.extract_file(
                self._source, page_numbers=(page_number,), probe=probe, on_render=rendered
            )
            text = result.pages[0].text
            if raster_size is not None:
                rectangle = self._document[page_number - 1].rect
                scale_x, scale_y = (
                    rectangle.width / raster_size[0],
                    rectangle.height / raster_size[1],
                )
                words = [
                    (
                        word.text,
                        (
                            word.bbox[0] * scale_x,
                            word.bbox[1] * scale_y,
                            word.bbox[2] * scale_x,
                            word.bbox[3] * scale_y,
                        ),
                    )
                    for word in result.pages[0].words
                    if word.bbox is not None
                ]
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
        candidate = PageReadingCandidate(text, "ocr", provenance)
        _candidate_evidence(candidate, source_languages, reference, words or None)
        return candidate, words


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

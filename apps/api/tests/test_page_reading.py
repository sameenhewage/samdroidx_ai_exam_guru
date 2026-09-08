import asyncio
import hashlib
import json
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
from exam_guru_api.documents.source_math_fidelity import (
    decode_math_evidence,
    encode_math_evidence,
    extract_math_layout,
)
from exam_guru_api.documents.tesseract_ocr import CommandResult, RenderedPageImage
from exam_guru_api.infrastructure.object_storage import LocalFileObjectStorage
from tests.test_tesseract_file_input import FileCommandRunner, file_config, source_pdf
from tests.test_tesseract_ocr_adapter import single_word_tsv


def test_sinhala_reading_compares_bilingual_and_sinhala_only_candidates(tmp_path: Path) -> None:
    class LanguageRunner(FileCommandRunner):
        def __call__(
            self, argv: tuple[str, ...], *, cwd: Path, timeout_seconds: float, max_output_bytes: int
        ) -> CommandResult:
            if "tsv" in argv:
                self.tsv = single_word_tsv(
                    "ගුණ කිරීම" if argv[argv.index("-l") + 1] == "sin" else "ගණිතය wOHdmk m%Yak"
                )
            return super().__call__(
                argv, cwd=cwd, timeout_seconds=timeout_seconds, max_output_bytes=max_output_bytes
            )

    path = source_pdf(tmp_path / "legacy.pdf", font_name="FMBindumathi")
    runner = LanguageRunner(tmp_path / "models")
    with (
        path.open("rb") as source,
        FilePageReader(command_runner=runner).open(
            source, configuration=PageReadingConfiguration(ocr=file_config(runner.models))
        ) as reader,
    ):
        result = reader.read_page(1)
    assert len(result.candidates) == 3
    assert {
        tuple(cast(list[str], candidate.provenance.get("ocr_languages", [])))
        for candidate in result.candidates
        if candidate.method == "ocr"
    } == {("sin", "eng"), ("sin",)}
    assert result.candidates[-1].raw_text == "ගුණ කිරීම"
    selection = cast(dict[str, object], result.candidates[-1].provenance["candidate_selection"])
    assert selection["selected"] is True
    assert selection["text_readable"] is True
    native = next(candidate for candidate in result.candidates if candidate.method == "native")
    assert native.provenance["mappings_applied"] == []
    font_metadata = cast(list[dict[str, object]], native.provenance["font_metadata"])
    assert font_metadata
    assert all("encoding" in record and "has_to_unicode" in record for record in font_metadata)


def test_successful_ocr_command_with_unreadable_sinhala_output_is_a_read_failure(
    tmp_path: Path,
) -> None:
    path = source_pdf(tmp_path / "legacy.pdf", font_name="FMBindumathi")
    runner = FileCommandRunner(tmp_path / "models", tsv=single_word_tsv("ගණිතය wOHdmk m%Yak"))
    with (
        path.open("rb") as source,
        FilePageReader(command_runner=runner).open(
            source, configuration=PageReadingConfiguration(ocr=file_config(runner.models))
        ) as reader,
    ):
        result = reader.read_page(1)
    assert result.failure_code == "source_fidelity_failed"
    assert len(result.candidates) == 3
    assert all(
        not cast(dict[str, object], candidate.provenance["candidate_selection"])["can_confirm"]
        for candidate in result.candidates
    )
    assert all(
        candidate.provenance["automatic_verification"] is False for candidate in result.candidates
    )


def test_wrong_script_ocr_cannot_expand_immutable_source_languages(tmp_path: Path) -> None:
    path = source_pdf(tmp_path / "legacy.pdf", font_name="FMBindumathi")
    runner = FileCommandRunner(tmp_path / "models", tsv=single_word_tsv("தமிழ் மொழி வினா"))
    with (
        path.open("rb") as source,
        FilePageReader(command_runner=runner).open(
            source, configuration=PageReadingConfiguration(ocr=file_config(runner.models))
        ) as reader,
    ):
        result = reader.read_page(1)
    source_languages = result.candidates[0].provenance["source_languages"]
    assert source_languages == ["si", "en"]
    for candidate in result.candidates[1:]:
        assert candidate.provenance["source_languages"] == source_languages
        assert "ta" in cast(list[str], candidate.provenance["languages"])
        assert "ta" not in cast(list[str], candidate.provenance["source_languages"])
        assert "source_script_missing" in cast(list[str], candidate.provenance["risk_codes"])
        assert (
            cast(dict[str, object], candidate.provenance["candidate_selection"])["can_confirm"]
            is False
        )
    assert result.failure_code == "source_fidelity_failed"


@pytest.mark.parametrize("page_count", [1, 40])
def test_dense_native_pages_keep_lossless_math_evidence_without_ocr(
    tmp_path: Path,
    page_count: int,
) -> None:
    path = tmp_path / "dense.pdf"
    expected: list[tuple[str, dict[str, object], list[list[object]]]] = []
    with pymupdf.open() as document:
        for number in range(1, page_count + 1):
            page = document.new_page()
            for index in range(40):
                page.insert_text(
                    (40, 45 + index * 18),
                    f"Synthetic abc123de page {number}, line {index}: 2 + 2 = 4.",
                    fontsize=11,
                )
            reference = extract_math_layout(page)
            words: list[list[object]] = [
                [str(word[4]), [float(value) for value in word[:4]]]
                for word in page.get_text("words", sort=True)
            ]
            assert len(cast(list[object], reference["anchors"])) == 320
            assert len(json.dumps(reference).encode()) > 24 * 1024
            assert len(json.dumps(words).encode()) > 16 * 1024
            expected.append((page.get_text("text", sort=True), reference, words))
        document.save(path)
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    runner = FileCommandRunner(tmp_path / "models")
    with (
        path.open("rb") as source,
        FilePageReader(command_runner=runner).open(
            source,
            configuration=PageReadingConfiguration(
                expected_languages=("en",), ocr=file_config(runner.models)
            ),
        ) as reader,
    ):
        assert reader.page_count == page_count
        for number, (raw, reference, words) in enumerate(expected, start=1):
            result = reader.read_page(number)
            assert result.failure_code is None
            assert result.preferred_index == 0
            (candidate,) = result.candidates
            assert candidate.method == "native"
            assert candidate.raw_text == raw
            provenance = candidate.provenance
            assert provenance["automatic_verification"] is False
            assert provenance["mappings_applied"] == []
            assert provenance["raw_sha256"] == hashlib.sha256(raw.encode()).hexdigest()
            assert provenance["source_languages"] == ["en"]
            assert cast(dict[str, object], provenance["candidate_selection"])["can_confirm"] is True
            maths = cast(dict[str, object], provenance["maths_fidelity"])
            assert maths["can_confirm"] is True
            assert maths["preserved_count"] == 320
            assert "omitted_metadata" not in provenance
            assert decode_math_evidence(provenance["maths_reference"]) == reference
            assert decode_math_evidence(provenance["maths_words"]) == words
            assert len(json.dumps(provenance["maths_reference"]).encode()) <= 24 * 1024
            assert len(json.dumps(provenance["maths_words"]).encode()) <= 16 * 1024
            assert len(json.dumps(provenance).encode()) <= 60 * 1024
    assert runner.calls == []
    assert hashlib.sha256(path.read_bytes()).hexdigest() == source_hash


@pytest.mark.parametrize("method", ["native", "ocr"])
def test_aggregate_metadata_overflow_keeps_bounded_failed_raw_candidate(method: str) -> None:
    from exam_guru_api.documents.fidelity_service import _metadata

    raw = "Read the question and choose the correct answer.\r\n"
    with pymupdf.open() as document:
        reference = page_reading._math_reference(document.new_page())
    reference["fixture_padding"] = "m" * (23 * 1024)
    reference = cast(dict[str, object], encode_math_evidence(reference))
    config = {"language": "sin+eng", "dpi": 72, "timeout_seconds": 30}
    image = {"page_number": 1, "sha256": "b" * 64, "source_checksum_sha256": "a" * 64}
    candidate = page_reading.PageReadingCandidate(
        raw,
        method,
        {
            "engine": "pymupdf" if method == "native" else "tesseract-cli",
            "engine_version": "fixture-1",
            "page_number": 1,
            "source_checksum_sha256": "a" * 64,
            "source_languages": ["en"],
            "config": config,
            "page_image": image,
            "font_metadata": [{"name": "f" * (12 * 1024 - 100)}],
            "fonts": [f"Helvetica{'f' * 230}{index}" for index in range(128)],
            "blocks": {"total_count": 1, "items": [{"fixture": "b" * (16 * 1024 - 100)}]},
            "automatic_verification": False,
            "mappings_applied": [],
        },
    )
    words = [
        (hashlib.sha256(str(index).encode()).hexdigest(), (1.0, 2.0, 3.0, 4.0))
        for index in range(240)
    ]
    encoded_words = encode_math_evidence([[text, list(box)] for text, box in words])
    page_reading._candidate_evidence(candidate, ("en",), reference, words)
    assert len(json.dumps(candidate.provenance).encode()) > 65536
    result = page_reading._selected_result(1, [candidate])
    assert result.failure_code == "candidate_metadata_limit"
    assert result.candidates == (candidate,)
    assert candidate.raw_text == raw
    provenance = _metadata(candidate.provenance)
    assert len(json.dumps(provenance).encode()) <= 60 * 1024
    assert provenance["source_checksum_sha256"] == "a" * 64
    assert provenance["page_image"] == image
    assert provenance["page_number"] == 1
    assert provenance["config"] == config
    assert provenance["raw_sha256"] == hashlib.sha256(raw.encode()).hexdigest()
    assert provenance["raw_character_count"] == len(raw)
    assert provenance["automatic_verification"] is False
    assert provenance["mappings_applied"] == []
    assert cast(dict[str, object], provenance["candidate_selection"])["can_confirm"] is False
    omitted = cast(dict[str, Any], provenance["omitted_metadata"])
    assert omitted["maths_words"]["item_count"] == len(encoded_words)
    assert (
        omitted["maths_words"]["sha256"]
        == hashlib.sha256(
            json.dumps(encoded_words, ensure_ascii=True, sort_keys=True, allow_nan=False).encode()
        ).hexdigest()
    )
    assert (
        omitted["maths_reference"]["sha256"]
        == hashlib.sha256(
            json.dumps(reference, ensure_ascii=True, sort_keys=True, allow_nan=False).encode()
        ).hexdigest()
    )
    assert omitted["font_metadata"]["item_count"] == 1
    assert omitted["blocks"]["item_count"] == 1


@pytest.mark.parametrize("extra", [0, 1])
def test_reader_enforces_reference_byte_boundary_even_if_codec_returns_plain_evidence(
    monkeypatch: pytest.MonkeyPatch,
    extra: int,
) -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        reference = extract_math_layout(page)
        anchors: list[dict[str, object]] = [{"source_evidence": ""}]
        reference["anchors"] = anchors
        anchors[0]["source_evidence"] = "r" * (
            24 * 1024 + extra - len(json.dumps(reference).encode())
        )
        assert len(json.dumps(reference).encode()) == 24 * 1024 + extra
        digest = page_reading._metadata_summary(reference)["sha256"]
        monkeypatch.setattr(page_reading, "extract_math_layout", lambda _page: reference)
        # Exercise the reader's independent storage guard against a codec contract regression.
        monkeypatch.setattr(page_reading, "encode_math_evidence", lambda evidence: evidence)
        bounded = page_reading._math_reference(page)
    assert len(json.dumps(bounded).encode()) <= 24 * 1024
    if extra:
        assert bounded["complete"] is False
        assert bounded["risk_codes"] == ["math_metadata_limit"]
        assert bounded["full_evidence_sha256"] == digest
        assert cast(dict[str, Any], bounded["omitted_metadata"])["anchors"]["item_count"] == 1
    else:
        assert bounded["anchors"] == anchors
        assert "omitted_metadata" not in bounded


@pytest.mark.parametrize("extra", [0, 1])
def test_reader_enforces_word_byte_boundary_even_if_codec_returns_plain_evidence(
    monkeypatch: pytest.MonkeyPatch,
    extra: int,
) -> None:
    raw = "Read the question and choose the correct answer"
    with pymupdf.open() as document:
        reference = extract_math_layout(document.new_page())
    word_evidence: list[list[object]] = [["", [1.0, 2.0, 3.0, 4.0]]]
    text = "w" * (16 * 1024 + extra - len(json.dumps(word_evidence).encode()))
    words = [(text, (1.0, 2.0, 3.0, 4.0))]
    word_evidence[0][0] = text
    assert len(json.dumps(word_evidence).encode()) == 16 * 1024 + extra
    monkeypatch.setattr(page_reading, "encode_math_evidence", lambda evidence: evidence)
    candidate = page_reading.PageReadingCandidate(raw, "ocr", {"automatic_verification": False})
    page_reading._candidate_evidence(candidate, ("en",), reference, words)
    result = page_reading._selected_result(1, [candidate])
    assert candidate.raw_text == raw
    assert candidate.provenance["automatic_verification"] is False
    assert len(json.dumps(candidate.provenance["maths_words"]).encode()) <= 16 * 1024
    if extra:
        assert result.failure_code == "math_word_evidence_limit"
        assert candidate.provenance["maths_words"] == []
        assert cast(dict[str, Any], candidate.provenance["omitted_metadata"])["maths_words"] == (
            page_reading._metadata_summary(word_evidence)
        )
    else:
        assert candidate.provenance["maths_words"] == word_evidence
        assert "omitted_metadata" not in candidate.provenance


@pytest.mark.parametrize("overflow_kind", ["small_fields", "selection_details", "prior_omissions"])
def test_residual_metadata_overflow_keeps_bounded_failure_and_original_manifest(
    overflow_kind: str,
) -> None:
    from exam_guru_api.documents.fidelity_service import _metadata

    raw = "Read the question and choose the correct answer\r\n"
    config = {"language": "eng", "dpi": 72}
    image = {"page_number": 1, "sha256": "b" * 64, "source_checksum_sha256": "a" * 64}
    candidate = page_reading.PageReadingCandidate(
        raw,
        "native",
        {
            "engine": "pymupdf",
            "engine_version": "fixture-1",
            "page_number": 1,
            "source_checksum_sha256": "a" * 64,
            "source_languages": ["en"],
            "config": config,
            "page_image": image,
            "automatic_verification": False,
            "mappings_applied": [],
        },
    )
    with pymupdf.open() as document:
        reference = extract_math_layout(document.new_page())
    page_reading._candidate_evidence(candidate, ("en",), reference, None)
    details = {f"provider_detail_{index}": "d" * 1000 for index in range(80)}
    if overflow_kind == "small_fields":
        candidate.provenance.update(details)
    elif overflow_kind == "selection_details":
        cast(dict[str, object], candidate.provenance["candidate_selection"])["details"] = details
    else:
        candidate.provenance["omitted_metadata"] = details
    original_summary = page_reading._metadata_summary(candidate.provenance)
    assert cast(int, original_summary["byte_count"]) > 65536
    result = page_reading._selected_result(1, [candidate])
    assert candidate.raw_text == raw
    assert result.failure_code == "candidate_metadata_limit"
    assert result.preferred_index == 0
    provenance = _metadata(candidate.provenance)
    assert len(json.dumps(provenance).encode()) <= 60 * 1024
    assert provenance["config"] == config
    assert provenance["page_image"] == image
    assert provenance["source_checksum_sha256"] == "a" * 64
    assert provenance["source_languages"] == ["en"]
    assert provenance["page_number"] == 1
    assert provenance["raw_sha256"] == hashlib.sha256(raw.encode()).hexdigest()
    assert provenance["raw_character_count"] == len(raw)
    assert provenance["automatic_verification"] is False
    assert provenance["mappings_applied"] == []
    assert (
        cast(dict[str, Any], provenance["omitted_metadata"])["full_provenance"] == original_summary
    )
    selection = cast(dict[str, object], provenance["candidate_selection"])
    assert selection["can_confirm"] is False
    assert selection["text_readable"] is False
    assert selection["candidate_count"] == 1


@pytest.mark.parametrize("limit_kind", ["decompressed", "encoded", "invalid"])
def test_math_reference_limit_retains_digest_and_omitted_counts(
    monkeypatch: pytest.MonkeyPatch,
    limit_kind: str,
) -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        reference = page_reading._math_reference(page)
        reference["anchors"] = [
            {
                "token": "1" * 2048
                if limit_kind == "decompressed"
                else hashlib.sha256(str(index).encode()).hexdigest(),
                "bbox": [float("nan") if limit_kind == "invalid" else 1, 2, 3, 4],
            }
            for index in range(800)
        ]
        with pytest.raises(ValueError, match="invalid math evidence"):
            encode_math_evidence(reference)
        digest = hashlib.sha256(json.dumps(reference, sort_keys=True).encode()).hexdigest()
        monkeypatch.setattr(page_reading, "extract_math_layout", lambda _page: reference)
        bounded = page_reading._math_reference(page)
    assert bounded["complete"] is False
    assert bounded["full_evidence_sha256"] == digest
    assert len(json.dumps(bounded, allow_nan=False).encode()) <= 24 * 1024
    assert cast(dict[str, Any], bounded["omitted_metadata"])["anchors"]["item_count"] == 800


def test_native_font_metadata_limit_retains_omitted_count_and_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((10, 20), "Read the question and choose the correct answer")
        fonts = page.get_fonts() * 128
        monkeypatch.setattr(pymupdf.Page, "get_fonts", lambda _page: fonts)
        native = page_reading.extract_native_page(document, 1)
    marker = native.font_metadata[-1]
    assert marker["failure_code"] == "font_metadata_limit"
    assert marker["item_count"] == 128
    assert len(cast(str, marker["sha256"])) == 64
    assert len(json.dumps(native.font_metadata).encode()) <= 12 * 1024


@pytest.mark.parametrize("aggregate_overflow", [False, True])
@pytest.mark.parametrize("limit_kind", ["decompressed", "encoded", "invalid"])
def test_word_evidence_limit_is_durable_failure_with_omission_identity(
    aggregate_overflow: bool,
    limit_kind: str,
) -> None:
    raw = "Read the question and choose the correct answer"
    with pymupdf.open() as document:
        reference = page_reading._math_reference(document.new_page())
    words = [
        (
            "w" * 2048
            if limit_kind == "decompressed"
            else hashlib.sha256(str(index).encode()).hexdigest(),
            (float("nan") if limit_kind == "invalid" else 1.0, 2.0, 3.0, 4.0),
        )
        for index in range(600)
    ]
    with pytest.raises(ValueError, match="invalid math evidence"):
        encode_math_evidence([[text, list(box)] for text, box in words])
    candidate = page_reading.PageReadingCandidate(raw, "ocr", {"automatic_verification": False})
    page_reading._candidate_evidence(candidate, ("en",), reference, words)
    config = {"executable": "x" * 65536, "dpi": 72}
    if aggregate_overflow:
        candidate.provenance["config"] = config
    result = page_reading._selected_result(1, [candidate])
    assert result.failure_code == (
        "candidate_metadata_limit" if aggregate_overflow else "math_word_evidence_limit"
    )
    assert candidate.raw_text == raw
    if aggregate_overflow:
        assert candidate.provenance["prior_failure_code"] == "math_word_evidence_limit"
        assert (
            cast(dict[str, object], candidate.provenance["config"])["sha256"]
            == hashlib.sha256(
                json.dumps(config, ensure_ascii=True, sort_keys=True, allow_nan=False).encode()
            ).hexdigest()
        )
    else:
        assert candidate.provenance["maths_words"] == []
    assert len(json.dumps(candidate.provenance, allow_nan=False).encode()) <= 60 * 1024
    omitted = cast(dict[str, Any], candidate.provenance["omitted_metadata"])
    assert omitted["maths_words"]["item_count"] == 600
    assert (
        omitted["maths_words"]["sha256"]
        == hashlib.sha256(json.dumps(words, ensure_ascii=True, sort_keys=True).encode()).hexdigest()
    )


def test_truncated_font_metadata_cannot_be_a_confirmable_candidate() -> None:
    raw = "Read the question and choose the correct answer"
    with pymupdf.open() as document:
        reference = page_reading._math_reference(document.new_page())
    candidate = page_reading.PageReadingCandidate(
        raw,
        "native",
        {
            "font_metadata": [{"failure_code": "font_metadata_limit"}],
            "automatic_verification": False,
        },
    )
    page_reading._candidate_evidence(candidate, ("en",), reference, None)
    assert page_reading._selected_result(1, [candidate]).failure_code == "font_metadata_limit"
    assert candidate.raw_text == raw


@pytest.mark.parametrize("failure", ["observer", "no_render"])
def test_forced_ocr_cannot_select_native_with_failed_comparison_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    runner = FileCommandRunner(
        tmp_path / "models", languages=() if failure == "no_render" else ("eng",)
    )
    observed: list[str] = []

    def reject_image(image: RenderedPageImage) -> Never:
        observed.append(image.sha256)
        raise PageImageError("source_page_image_artifact_unavailable")

    def reject_render(*_args: Any, **_kwargs: Any) -> Never:
        raise PageImageError("source_page_image_raster_limit")

    if failure == "no_render":
        monkeypatch.setattr(page_reading, "render_page_image", reject_render)
    with (
        path.open("rb") as source,
        FilePageReader(command_runner=runner, on_render=reject_image).open(
            source,
            configuration=PageReadingConfiguration(force_ocr=True, ocr=file_config(runner.models)),
        ) as reader,
    ):
        result = reader.read_page(1)
    native, ocr = result.candidates
    code = (
        "page_image_artifact_unavailable" if failure == "observer" else "page_image_render_failed"
    )
    assert result.failure_code is not None
    assert native.provenance["failure_code"] == code
    assert cast(dict[str, object], native.provenance["page_image"])["failure_code"] == code
    assert cast(dict[str, object], native.provenance["candidate_selection"])["can_confirm"] is False
    assert native.raw_text.startswith("Read the question")
    assert ocr.raw_text == ("Question" if failure == "observer" else "")
    assert len(observed) == (1 if failure == "observer" else 0)


@pytest.mark.parametrize("failed_attempt", [0, 1, None])
def test_native_retains_first_valid_image_and_each_ocr_keeps_own_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_attempt: int | None
) -> None:
    path = tmp_path / "source.pdf"
    with pymupdf.open() as document:
        document.new_page()
        document.save(path)
    monkeypatch.setattr(
        page_reading, "extract_native_page", lambda *_args: NativePageText("සිංහල පාඩම")
    )
    observed: list[dict[str, object]] = []

    def observe(
        image: RenderedPageImage, _observer: object
    ) -> tuple[dict[str, object], str | None]:
        index = len(observed)
        metadata = {**image.metadata(), "attempt": index}
        failure = "page_image_artifact_unavailable" if index == failed_attempt else None
        if failure:
            metadata["failure_code"] = failure
        observed.append(metadata)
        return metadata, failure

    monkeypatch.setattr(page_reading, "observe_page_image", observe)
    runner = FileCommandRunner(tmp_path / "models", tsv=single_word_tsv("සිංහල පාඩම"))
    with (
        path.open("rb") as source,
        FilePageReader(command_runner=runner, on_render=lambda _image: None).open(
            source,
            configuration=PageReadingConfiguration(force_ocr=True, ocr=file_config(runner.models)),
        ) as reader,
    ):
        result = reader.read_page(1)
    assert len(observed) == 2
    native, bilingual, sinhala = result.candidates
    assert native.provenance["page_image"] == observed[1 if failed_attempt == 0 else 0]
    assert bilingual.provenance["page_image"] == observed[0]
    assert sinhala.provenance["page_image"] == observed[1]
    for index, candidate in enumerate((bilingual, sinhala)):
        if index == failed_attempt:
            assert candidate.provenance["failure_code"] == "page_image_artifact_unavailable"
    assert result.preferred_index == 0
    assert result.failure_code is None


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
    runner = FileCommandRunner(
        tmp_path / "models",
        tsv=single_word_tsv("සිංහල පාඩම" if language.startswith("sin") else "தமிழ் மொழி வினா"),
    )
    reader = FilePageReader(command_runner=runner)
    with (
        path.open("rb") as source,
        reader.open(
            source, configuration=PageReadingConfiguration(ocr=file_config(runner.models))
        ) as document,
    ):
        result = document.read_page(1)
    attempts = 2 if language.startswith("sin") else 1
    assert [candidate.method for candidate in result.candidates] == [
        "native",
        *(["ocr"] * attempts),
    ]
    assert result.candidates[0].raw_text.strip()
    assert result.candidates[0].provenance["mappings_applied"] == []
    assert result.candidates[1].provenance["ocr_languages"] == language.split("+")
    ocr_calls = [call for call in runner.calls if "tsv" in call]
    assert len(ocr_calls) == attempts
    assert ocr_calls[0][ocr_calls[0].index("-l") + 1] == language
    if attempts == 2:
        assert ocr_calls[1][ocr_calls[1].index("-l") + 1] == "sin"
    assert result.candidates[1].provenance["traineddata_sha256"] == {
        code: hashlib.sha256(f"fixture {code}".encode()).hexdigest() for code in language.split("+")
    }
    assert result.candidates[1].provenance["available_languages"] == ["eng", "sin", "tam"]
    assert result.failure_code is None


def test_readable_native_is_preferred_over_a_shorter_ocr_guess(tmp_path: Path) -> None:
    path = tmp_path / "native.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=300, height=300)
        page.insert_text((15, 30), "Read the question and choose the correct answer", fontsize=8)
        pdf.save(path)
    runner = FileCommandRunner(tmp_path / "models")
    with (
        path.open("rb") as source,
        FilePageReader(command_runner=runner).open(
            source,
            configuration=PageReadingConfiguration(force_ocr=True, ocr=file_config(runner.models)),
        ) as reader,
    ):
        result = reader.read_page(1)
    assert result.candidates[1].raw_text == "Question"
    assert result.preferred_index == 0
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
    native, *attempts = result.candidates
    assert native.raw_text.strip()
    assert len(attempts) == 2
    for index, ocr in enumerate(attempts):
        assert ocr.method == "ocr"
        assert ocr.raw_text == ""
        assert ocr.provenance["failure_code"] == "ocr_timeout"
        image = ocr.provenance["page_image"]
        assert isinstance(image, dict)
        assert image["sha256"] == runner.image_hashes[index]
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
    assert result.failure_code == "source_fidelity_failed"
    assert cast(dict[str, object], ocr.provenance["maths_fidelity"])["can_confirm"] is False
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
    path = tmp_path / "mixed.pdf"
    with pymupdf.open() as pdf:
        for _ in texts:
            pdf.new_page(width=180, height=180)
        pdf.save(path)
    runner = FileCommandRunner(tmp_path / "models")
    configuration = PageReadingConfiguration(
        expected_languages=("si",), force_ocr=True, ocr=file_config(runner.models)
    )
    with (
        path.open("rb") as source,
        FilePageReader(command_runner=runner).open(source, configuration=configuration) as document,
    ):
        results = [document.read_page(number) for number in range(1, 5)]
    assert [result.candidates[1].provenance["ocr_languages"] for result in results] == [
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
    assert result.preferred_index == 0
    assert candidate.provenance["source_languages"] == ["und"]
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
    assert read.preferred_index == 0
    if extra:
        assert read.failure_code is None
        assert ocr.provenance["failure_code"] == "ocr_text_limit"
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

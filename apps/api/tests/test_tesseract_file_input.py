import hashlib
import io
import os
import subprocess
from collections.abc import Callable
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, BinaryIO, Never, cast

import pymupdf
import pytest

from exam_guru_api.documents.tesseract_ocr import (
    CommandResult,
    RenderedPageImage,
    TesseractCliOCRAdapter,
    TesseractConfigError,
    TesseractInputError,
    TesseractInputViolation,
    TesseractMalformedOutputError,
    TesseractOCRConfig,
    TesseractTimeoutError,
    TesseractUnavailableError,
    open_pdf_file,
)

TSV = (
    b"level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
    b"left\ttop\twidth\theight\tconf\ttext\n"
    b"5\t1\t1\t1\t1\t1\t1\t1\t10\t10\t95\tQuestion\n"
)


def source_pdf(path: Path, *, pages: int = 1, font_name: str | None = None) -> Path:
    with pymupdf.open() as document:
        for index in range(pages):
            page = document.new_page(width=180, height=180)
            page.insert_text(
                (15, 30), f"Read the question and choose the answer {index + 1}", fontsize=5
            )
        if font_name is not None:
            for font in document[0].get_fonts():
                document.xref_set_key(font[0], "BaseFont", f"/{font_name}")
        document.save(path)
    return path


class FileCommandRunner:
    def __init__(
        self,
        models: Path,
        *,
        languages: tuple[str, ...] = ("eng", "sin", "tam"),
        fail_ocr: bool = False,
        tsv: bytes = TSV,
    ) -> None:
        self.models = models
        models.mkdir(exist_ok=True)
        self.languages = languages
        self.fail_ocr = fail_ocr
        self.tsv = tsv
        self.calls: list[tuple[str, ...]] = []
        self.image_hashes: list[str] = []
        for language in languages:
            (models / f"{language}.traineddata").write_bytes(f"fixture {language}".encode())

    def __call__(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        timeout_seconds: float,
        max_output_bytes: int,
    ) -> CommandResult:
        assert timeout_seconds > 0
        assert max_output_bytes > 0
        assert cwd.is_dir()
        self.calls.append(argv)
        if "--version" in argv:
            return CommandResult(0, b"tesseract 5.4.1\n", b"")
        if "--list-langs" in argv:
            languages = "\n".join(self.languages)
            output = (
                f'List of available languages in "{self.models}" ({len(self.languages)}):\n'
                f"{languages}\n"
            )
            return CommandResult(0, output.encode(), b"")
        image = Path(argv[1])
        assert image.is_file()
        self.image_hashes.append(hashlib.sha256(image.read_bytes()).hexdigest())
        if self.fail_ocr:
            raise subprocess.TimeoutExpired(argv, timeout_seconds, output=b"PRIVATE SOURCE")
        return CommandResult(0, self.tsv, b"")


def file_config(models: Path, *, language: str = "eng") -> TesseractOCRConfig:
    return TesseractOCRConfig(
        language=language,
        allowed_languages=("sin", "tam", "eng"),
        tessdata_directory=models,
        model_family="fast",
        dpi=72,
        max_pages=1,
        batch_size=1,
        max_source_bytes=1,
    )


def test_file_input_reads_page_1001_without_a_whole_pdf_bytes_fallback(tmp_path: Path) -> None:
    path = source_pdf(tmp_path / "large.pdf", pages=1001)
    runner = FileCommandRunner(tmp_path / "models")
    adapter = TesseractCliOCRAdapter(config=file_config(runner.models), command_runner=runner)
    with path.open("rb") as source:
        result = adapter.extract_file(source, page_numbers=(1001,))
        assert not source.closed
    assert result.pages[0].page_number == 1001
    assert result.pages[0].text == "Question"
    assert result.config["input_mode"] == "file"
    assert result.config["traineddata_eng_sha256"] == hashlib.sha256(b"fixture eng").hexdigest()
    assert result.config["available_languages"] == "eng+sin+tam"
    assert result.config["model_family"] == "fast"
    assert any("--tessdata-dir" in call for call in runner.calls)


def test_file_input_uses_actual_render_hash_and_cleans_temporary_artifacts(tmp_path: Path) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    runner = FileCommandRunner(tmp_path / "models")
    adapter = TesseractCliOCRAdapter(config=file_config(runner.models), command_runner=runner)
    images: list[RenderedPageImage] = []

    def capture(image: RenderedPageImage) -> None:
        assert image.path.exists()
        assert image.sha256 == hashlib.sha256(image.path.read_bytes()).hexdigest()
        images.append(image)

    with path.open("rb") as source:
        adapter.extract_file(source, page_numbers=(1,), on_render=capture)
    assert len(images) == 1
    assert (images[0].width, images[0].height, images[0].dpi) == (180, 180, 72)
    assert images[0].sha256 == runner.image_hashes[0]
    assert not images[0].path.exists()


def test_file_timeout_still_exposes_actual_render_and_sanitizes_process_output(
    tmp_path: Path,
) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    runner = FileCommandRunner(tmp_path / "models", fail_ocr=True)
    adapter = TesseractCliOCRAdapter(config=file_config(runner.models), command_runner=runner)
    images: list[RenderedPageImage] = []
    with path.open("rb") as source, pytest.raises(TesseractTimeoutError) as error:
        adapter.extract_file(source, page_numbers=(1,), on_render=images.append)
    assert "PRIVATE" not in str(error.value)
    assert len(images) == 1
    assert not images[0].path.exists()


def test_file_backed_pdf_rejects_memory_stream_and_never_calls_unbounded_read() -> None:
    class NoRead(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            raise AssertionError("source stream must not be copied into RAM")

    with pytest.raises(TesseractInputError), open_pdf_file(NoRead(b"%PDF-fixture")):
        pytest.fail("memory-only input must fail closed")


def test_file_backed_pdf_does_not_trust_source_name(tmp_path: Path) -> None:
    path = source_pdf(tmp_path / "source.pdf")

    class DescriptorOnly:
        name = "https://invalid.example/private-source.pdf"

        def __init__(self, source: BinaryIO) -> None:
            self.source = source

        def fileno(self) -> int:
            return self.source.fileno()

        def read(self, size: int = -1) -> bytes:
            raise AssertionError("must open the local descriptor")

    with path.open("rb") as source, open_pdf_file(cast(BinaryIO, DescriptorOnly(source))) as pdf:
        assert pdf.page_count == 1


@pytest.mark.parametrize("pages", [(), (0,), (True,), (2, 1), (1, 1), (1, 2)])
def test_file_input_requires_explicit_bounded_page_selection(
    tmp_path: Path, pages: tuple[int, ...]
) -> None:
    path = source_pdf(tmp_path / "source.pdf", pages=2)
    runner = FileCommandRunner(tmp_path / "models")
    adapter = TesseractCliOCRAdapter(config=file_config(runner.models), command_runner=runner)
    with path.open("rb") as source, pytest.raises(TesseractInputError):
        adapter.extract_file(source, page_numbers=pages)
    assert not runner.calls


def test_file_input_reuses_the_strict_tsv_parser(tmp_path: Path) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    runner = FileCommandRunner(tmp_path / "models", tsv=TSV.replace(b"Question", b"bad\x00text"))
    adapter = TesseractCliOCRAdapter(config=file_config(runner.models), command_runner=runner)
    with path.open("rb") as source, pytest.raises(TesseractMalformedOutputError):
        adapter.extract_file(source, page_numbers=(1,))


def test_language_probe_and_hashes_follow_explicit_best_model_directory(tmp_path: Path) -> None:
    runner = FileCommandRunner(tmp_path / "best")
    adapter = TesseractCliOCRAdapter(
        config=TesseractOCRConfig(
            language="tam+eng",
            allowed_languages=("eng", "sin", "tam"),
            tessdata_directory=runner.models,
            model_family="best",
        ),
        command_runner=runner,
    )
    probe = adapter.probe(hash_traineddata=True)
    assert dict(probe.traineddata_sha256) == {
        language: hashlib.sha256(f"fixture {language}".encode()).hexdigest()
        for language in ("tam", "eng")
    }
    assert probe.available_languages == ("eng", "sin", "tam")
    assert any(str(runner.models) in call for call in runner.calls)


def test_missing_tamil_pack_does_not_fall_back_to_english(tmp_path: Path) -> None:
    runner = FileCommandRunner(tmp_path / "models", languages=("eng", "sin"))
    adapter = TesseractCliOCRAdapter(
        config=file_config(runner.models, language="tam+eng"), command_runner=runner
    )
    with pytest.raises(TesseractUnavailableError) as error:
        adapter.probe(hash_traineddata=True)
    assert error.value.missing_languages == ("tam",)
    assert not runner.image_hashes


@pytest.mark.parametrize("directory", [Path("relative/models"), Path("/unsafe\nmodels")])
def test_model_configuration_rejects_unsafe_paths(directory: Path) -> None:
    with pytest.raises(TesseractConfigError):
        TesseractOCRConfig(tessdata_directory=directory)


def test_changed_traineddata_is_rehashed_not_reused_as_old_provenance(tmp_path: Path) -> None:
    runner = FileCommandRunner(tmp_path / "models")
    adapter = TesseractCliOCRAdapter(config=file_config(runner.models), command_runner=runner)
    before = adapter.probe(hash_traineddata=True)
    (runner.models / "eng.traineddata").write_bytes(b"replacement model")
    after = adapter.probe(hash_traineddata=True)
    assert before.traineddata_sha256 != after.traineddata_sha256


def fail_if_called(*args: object, **kwargs: object) -> None:
    raise AssertionError("the bytes-only extraction path must not be used")


def test_file_input_does_not_call_existing_bytes_extract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    runner = FileCommandRunner(tmp_path / "models")
    adapter = TesseractCliOCRAdapter(config=file_config(runner.models), command_runner=runner)
    monkeypatch.setattr(adapter, "extract", cast(Callable[..., object], fail_if_called))
    with path.open("rb") as source:
        assert adapter.extract_file(source, page_numbers=(1,)).pages


def test_file_input_rejects_stale_supplied_model_hashes_before_ocr(tmp_path: Path) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    runner = FileCommandRunner(tmp_path / "models")
    adapter = TesseractCliOCRAdapter(config=file_config(runner.models), command_runner=runner)
    probe = adapter.probe(hash_traineddata=True)
    (runner.models / "eng.traineddata").write_bytes(b"changed since probe")
    with path.open("rb") as source, pytest.raises(TesseractConfigError):
        adapter.extract_file(source, page_numbers=(1,), probe=probe)
    assert not runner.image_hashes


def test_file_input_rejects_traineddata_changed_during_ocr(tmp_path: Path) -> None:
    path = source_pdf(tmp_path / "source.pdf")

    class ReplacingRunner(FileCommandRunner):
        def __call__(
            self,
            argv: tuple[str, ...],
            *,
            cwd: Path,
            timeout_seconds: float,
            max_output_bytes: int,
        ) -> CommandResult:
            result = super().__call__(
                argv,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                max_output_bytes=max_output_bytes,
            )
            if "tsv" in argv:
                (self.models / "eng.traineddata").write_bytes(b"replaced during OCR")
            return result

    runner = ReplacingRunner(tmp_path / "models")
    adapter = TesseractCliOCRAdapter(config=file_config(runner.models), command_runner=runner)
    with path.open("rb") as source, pytest.raises(TesseractConfigError):
        adapter.extract_file(source, page_numbers=(1,))


def test_file_input_streams_checksum_and_does_not_move_the_caller_cursor(tmp_path: Path) -> None:
    path = source_pdf(tmp_path / "source.pdf", pages=1001)
    with path.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    with path.open("rb") as source:
        source.seek(17)
        with open_pdf_file(source, source_checksum_sha256=checksum) as document:
            assert document.page_count == 1001
        assert source.tell() == 17
        with (
            pytest.raises(TesseractInputError),
            open_pdf_file(source, source_checksum_sha256="0" * 64),
        ):
            pytest.fail("mismatched checksums must fail closed")
        assert not source.closed


@pytest.mark.parametrize("family", ["", "unknown", "cloud-model"])
def test_file_ocr_rejects_unsupported_model_families(family: str) -> None:
    with pytest.raises(TesseractConfigError, match="unsupported traineddata model family"):
        TesseractOCRConfig(model_family=family)


def test_nonregular_source_descriptor_is_rejected_and_duplicate_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    read_fd, write_fd = os.pipe()
    duplicate = os.dup
    captured: list[int] = []

    def tracking_dup(fd: int) -> int:
        owned = duplicate(fd)
        captured.append(owned)
        return owned

    monkeypatch.setattr(os, "dup", tracking_dup)
    try:
        with os.fdopen(read_fd, "rb") as source:
            with pytest.raises(TesseractInputError) as error, open_pdf_file(source):
                pytest.fail("a pipe must never reach the PDF parser")
            assert error.value.violation is TesseractInputViolation.FILE_DESCRIPTOR_REQUIRED
            assert not source.closed
            assert len(captured) == 1
            with pytest.raises(OSError, match="Bad file descriptor"):
                os.fstat(captured[0])
    finally:
        os.close(write_fd)


def test_file_pdf_signature_rejection_does_not_close_callers_descriptor(tmp_path: Path) -> None:
    path = tmp_path / "source.pdf"
    path.write_bytes(b"PRIVATE not a PDF")
    with path.open("rb") as source:
        with pytest.raises(TesseractInputError) as error, open_pdf_file(source):
            pytest.fail("invalid PDF signature must fail")
        assert error.value.violation is TesseractInputViolation.INVALID_PDF_SIGNATURE
        assert "PRIVATE" not in str(error.value)
        assert not source.closed


@pytest.mark.parametrize("change", ["truncated", "metadata"])
def test_source_changed_during_checksum_verification_is_rejected_without_moving_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    original_pread = os.pread
    reads: list[tuple[int, int]] = []

    def changing_read(fd: int, size: int, offset: int) -> bytes:
        reads.append((size, offset))
        data = original_pread(fd, size, offset)
        if len(reads) == 1 and change == "truncated":
            with path.open("r+b") as writer:
                writer.truncate(0)
        elif len(reads) == 2 and change == "metadata":
            details = path.stat()
            os.utime(path, ns=(details.st_atime_ns, details.st_mtime_ns + 1))
        return data

    monkeypatch.setattr(os, "pread", changing_read)
    with path.open("rb") as source:
        source.seek(17)
        with (
            pytest.raises(TesseractInputError) as error,
            open_pdf_file(source, source_checksum_sha256=digest),
        ):
            pytest.fail("a source changed during verification is not immutable evidence")
        assert error.value.violation is TesseractInputViolation.SOURCE_CHANGED
        assert source.tell() == 17
        assert not source.closed
    assert all(size <= 1024 * 1024 for size, _offset in reads)


def test_file_backed_encrypted_pdf_is_rejected_and_parser_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_tesseract_ocr_adapter import pdf_bytes

    path = tmp_path / "encrypted.pdf"
    path.write_bytes(pdf_bytes(encrypted=True))
    opened: list[pymupdf.Document] = []
    original = cast(Callable[..., pymupdf.Document], pymupdf.open)

    def tracked_open(*args: Any, **kwargs: Any) -> pymupdf.Document:
        document = original(*args, **kwargs)
        opened.append(document)
        return document

    monkeypatch.setattr(pymupdf, "open", tracked_open)
    with path.open("rb") as source:
        with pytest.raises(TesseractInputError) as error, open_pdf_file(source):
            pytest.fail("encrypted originals require an explicit unsupported response")
        assert error.value.violation is TesseractInputViolation.ENCRYPTED_PDF
        assert not source.closed
    assert opened
    assert all(document.is_closed for document in opened)


def test_empty_parser_document_is_rejected_and_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    empty = pymupdf.open()
    monkeypatch.setattr(pymupdf, "open", lambda **_kwargs: empty)
    with (
        path.open("rb") as source,
        pytest.raises(TesseractInputError) as error,
        open_pdf_file(source),
    ):
        pytest.fail("a parser result with zero pages cannot become a source reading")
    assert error.value.violation is TesseractInputViolation.MALFORMED_PDF
    assert empty.is_closed


def test_selected_file_page_outside_actual_document_is_rejected_before_model_probe(
    tmp_path: Path,
) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    runner = FileCommandRunner(tmp_path / "models")
    adapter = TesseractCliOCRAdapter(config=file_config(runner.models), command_runner=runner)
    with path.open("rb") as source, pytest.raises(TesseractInputError) as error:
        adapter.extract_file(source, page_numbers=(2,))
    assert error.value.violation is TesseractInputViolation.PAGE_OUT_OF_RANGE
    assert runner.calls == []


@pytest.mark.parametrize(
    "damage",
    [
        "engine",
        "missing_directory",
        "relative",
        "control",
        "other_directory",
        "missing_hash",
        "missing_language",
    ],
)
def test_supplied_probe_is_bound_to_engine_directory_languages_and_models(
    tmp_path: Path, damage: str
) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    runner = FileCommandRunner(tmp_path / "models")
    adapter = TesseractCliOCRAdapter(config=file_config(runner.models), command_runner=runner)
    probe = adapter.probe(hash_traineddata=True)
    changes: dict[str, dict[str, object]] = {
        "engine": {"engine": "other-engine"},
        "missing_directory": {"tessdata_directory": None},
        "relative": {"tessdata_directory": "relative/models"},
        "control": {"tessdata_directory": "/private\tmodels"},
        "other_directory": {"tessdata_directory": str(tmp_path / "other")},
        "missing_hash": {"traineddata_sha256": ()},
        "missing_language": {"available_languages": ("sin", "tam")},
    }
    probe = replace(probe, **cast(Any, changes[damage]))
    work = tmp_path / "work"
    work.mkdir()
    with (
        path.open("rb") as source,
        pytest.raises((TesseractConfigError, TesseractUnavailableError)) as error,
    ):
        adapter.extract_file(source, page_numbers=(1,), probe=probe, temporary_directory=work)
    if damage.startswith("missing_") and damage != "missing_directory":
        assert isinstance(error.value, TesseractUnavailableError)
        assert error.value.missing_languages == ("eng",)
    assert not runner.image_hashes
    assert len(runner.calls) == 2
    assert not list(work.iterdir())


@pytest.mark.parametrize("damage", ["empty", "oversized", "directory", "symlink", "missing"])
def test_traineddata_file_safety_errors_are_typed_without_exposing_paths(
    tmp_path: Path, damage: str
) -> None:
    runner = FileCommandRunner(tmp_path / "models")
    path = runner.models / "eng.traineddata"
    if damage in {"empty", "oversized"}:
        with path.open("wb") as stream:
            stream.truncate(0 if damage == "empty" else 256 * 1024 * 1024 + 1)
    else:
        path.unlink()
        if damage == "directory":
            path.mkdir()
        elif damage == "symlink":
            path.symlink_to(runner.models / "sin.traineddata")
    adapter = TesseractCliOCRAdapter(config=file_config(runner.models), command_runner=runner)
    with pytest.raises(TesseractUnavailableError) as error:
        adapter.probe(hash_traineddata=True)
    assert error.value.missing_languages == ("eng",)
    assert str(tmp_path) not in str(error.value)


@pytest.mark.parametrize("damage", ["truncated", "metadata", "io_failure"])
def test_traineddata_change_during_hash_is_rejected_and_its_descriptor_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    runner = FileCommandRunner(tmp_path / "models")
    path = runner.models / "eng.traineddata"
    adapter = TesseractCliOCRAdapter(config=file_config(runner.models), command_runner=runner)
    calls = 0
    opened: list[BinaryIO] = []
    fdopen = os.fdopen

    def tracked_open(fd: int, mode: str) -> BinaryIO:
        stream = cast(BinaryIO, fdopen(fd, mode))
        opened.append(stream)
        return stream

    def remaining() -> float:
        nonlocal calls
        calls += 1
        if calls == 2:
            if damage == "truncated":
                path.write_bytes(b"")
            elif damage == "metadata":
                details = path.stat()
                os.utime(path, ns=(details.st_atime_ns, details.st_mtime_ns + 1))
            else:
                raise OSError("PRIVATE traineddata path")
        return 30.0

    monkeypatch.setattr(os, "fdopen", tracked_open)
    monkeypatch.setattr(adapter, "_remaining_command_seconds", remaining)
    with pytest.raises(TesseractUnavailableError) as error:
        adapter._hash_traineddata(runner.models, "eng")
    assert error.value.missing_languages == ("eng",)
    assert "PRIVATE" not in str(error.value)
    assert len(opened) == 1
    assert opened[0].closed


@pytest.mark.parametrize("directory", ["relative/models", "/private\tmodels"])
def test_probed_traineddata_directory_must_be_absolute_and_control_free(
    tmp_path: Path, directory: str
) -> None:
    class BadDirectoryRunner(FileCommandRunner):
        def __call__(self, argv: tuple[str, ...], **kwargs: Any) -> CommandResult:
            if "--list-langs" in argv:
                return CommandResult(
                    0, f'List of available languages in "{directory}" (1):\neng\n'.encode(), b""
                )
            return super().__call__(argv, **kwargs)

    runner = BadDirectoryRunner(tmp_path / "models")
    adapter = TesseractCliOCRAdapter(
        config=replace(file_config(runner.models), tessdata_directory=None), command_runner=runner
    )
    with pytest.raises(TesseractMalformedOutputError, match="traineddata path is malformed"):
        adapter.probe()


def test_actual_pixmap_dimensions_are_rechecked_before_writing_or_ocr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    runner = FileCommandRunner(tmp_path / "models")
    adapter = TesseractCliOCRAdapter(
        config=replace(file_config(runner.models), max_pixels_per_page=40_000),
        command_runner=runner,
    )

    def forbidden_save(*_args: object) -> Never:
        raise AssertionError("oversized actual pixmap must not be written")

    monkeypatch.setattr(
        pymupdf.Page,
        "get_pixmap",
        lambda *_args, **_kwargs: SimpleNamespace(width=1000, height=1000, save=forbidden_save),
    )
    work = tmp_path / "work"
    work.mkdir()
    with path.open("rb") as source, pytest.raises(TesseractInputError) as error:
        adapter.extract_file(source, page_numbers=(1,), temporary_directory=work)
    assert error.value.violation is TesseractInputViolation.RASTER_LIMIT_EXCEEDED
    assert not runner.image_hashes
    assert not list(work.iterdir())


def test_file_backed_malformed_pdf_is_sanitized_and_caller_keeps_its_descriptor(
    tmp_path: Path,
) -> None:
    path = tmp_path / "malformed.pdf"
    path.write_bytes(b"%PDF-1.7\nPRIVATE malformed PDF content\n")
    with path.open("rb") as source:
        with pytest.raises(TesseractInputError) as error, open_pdf_file(source):
            pytest.fail("malformed bytes must not become a parsed document")
        assert error.value.violation is TesseractInputViolation.MALFORMED_PDF
        assert "PRIVATE" not in str(error.value)
        assert not source.closed


def test_ocr_adapter_exposes_the_exact_immutable_configuration_used_for_provenance(
    tmp_path: Path,
) -> None:
    runner = FileCommandRunner(tmp_path / "models")
    configuration = file_config(runner.models)
    adapter = TesseractCliOCRAdapter(config=configuration, command_runner=runner)
    assert adapter.config is configuration
    with pytest.raises(FrozenInstanceError):
        cast(Any, adapter.config).dpi = 144
    assert adapter.config.dpi == 72
    assert runner.calls == []

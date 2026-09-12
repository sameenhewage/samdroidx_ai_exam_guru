import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import exam_guru_api.documents.evaluation_references as references
from exam_guru_api.core.config import Settings
from exam_guru_api.documents.page_images import PageImageArtifacts, PageImageError
from exam_guru_api.documents.understanding_runtime import (
    UnderstandingRuntime,
    create_understanding_runtime,
    prepare_understanding_input,
)
from tests.test_page_images import FileSourceStore, image_source
from tests.test_tesseract_file_input import source_pdf


def runtime() -> UnderstandingRuntime:
    value = create_understanding_runtime(
        Settings(
            environment="test",
            test_runtime_id="ai-exam-guru-e2e-understanding-images",
            document_understanding_provider="deterministic",
        )
    )
    assert value is not None
    return value


def no_render(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("declared image evidence must not be replaced with a fresh render")


def test_understanding_input_preserves_original_and_reuses_declared_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    source = image_source(path)
    storage = FileSourceStore(path)
    artifacts = PageImageArtifacts(root=tmp_path / "images")
    prepared = prepare_understanding_input(source, 1, None, storage, artifacts, runtime())
    assert prepared.request.source.source_sha256 == source.checksum_sha256
    assert (
        prepared.request.source.image_sha256
        == hashlib.sha256(prepared.request.image_png).hexdigest()
    )
    assert prepared.image_metadata.page_number == 1
    assert hashlib.sha256(path.read_bytes()).hexdigest() == source.checksum_sha256
    monkeypatch.setattr(references, "render_page_image", no_render)
    provenance: dict[str, object] = {
        "source_checksum_sha256": source.checksum_sha256,
        "page_image": prepared.image_metadata.model_dump(mode="json"),
    }
    again = prepare_understanding_input(source, 1, provenance, storage, artifacts, runtime())
    assert again == prepared
    assert storage.opens == storage.closes == 2


def test_missing_declared_understanding_image_is_not_a_cache_miss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    source = image_source(path)
    storage = FileSourceStore(path)
    artifacts = PageImageArtifacts(root=tmp_path / "images")
    prepared = prepare_understanding_input(source, 1, None, storage, artifacts, runtime())
    missing = PageImageArtifacts(root=tmp_path / "missing-images")
    monkeypatch.setattr(references, "render_page_image", no_render)
    provenance: dict[str, object] = {
        "source_checksum_sha256": source.checksum_sha256,
        "page_image": prepared.image_metadata.model_dump(mode="json"),
    }
    with pytest.raises(PageImageError, match="artifact_unavailable"):
        prepare_understanding_input(source, 1, provenance, storage, missing, runtime())


def test_persisted_image_does_not_hide_original_corruption(tmp_path: Path) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    source = image_source(path)
    storage = FileSourceStore(path)
    artifacts = PageImageArtifacts(root=tmp_path / "images")
    prepared = prepare_understanding_input(source, 1, None, storage, artifacts, runtime())
    path.write_bytes(path.read_bytes() + b"changed")
    provenance: dict[str, object] = {
        "source_checksum_sha256": source.checksum_sha256,
        "page_image": prepared.image_metadata.model_dump(mode="json"),
    }
    with pytest.raises(PageImageError, match="source_original"):
        prepare_understanding_input(source, 1, provenance, storage, artifacts, runtime())


def test_input_preparation_requires_durable_artifacts_and_bounded_image_bytes(
    tmp_path: Path,
) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    source = image_source(path)
    storage = FileSourceStore(path)
    with pytest.raises(PageImageError, match="artifact_unavailable"):
        prepare_understanding_input(source, 1, None, storage, None, runtime())
    configured = runtime()
    limited = replace(
        configured, budget=configured.budget.model_copy(update={"max_image_bytes": 1})
    )
    with pytest.raises(ValueError, match="image"):
        prepare_understanding_input(
            source, 1, None, storage, PageImageArtifacts(root=tmp_path / "images"), limited
        )

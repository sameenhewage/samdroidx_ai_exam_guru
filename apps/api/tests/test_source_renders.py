import hashlib
from pathlib import Path

import pytest

from exam_guru_api.documents.page_images import PageImageArtifacts, PageImageError
from exam_guru_api.documents.source_consensus import SourceRegionInput
from exam_guru_api.documents.source_reading import SourceLayoutRegion
from exam_guru_api.documents.source_renders import SourcePageRenderer
from exam_guru_api.documents.understanding_contracts import RegionBounds
from exam_guru_api.documents.understanding_verification import PageArtifactIdentity
from tests.test_page_images import FileSourceStore, image_source
from tests.test_tesseract_file_input import source_pdf


def test_source_renders_are_true_versioned_resolution_changes_not_upsampled_pngs(
    tmp_path: Path,
) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    source = image_source(path)
    storage = FileSourceStore(path)
    artifacts = PageImageArtifacts(root=tmp_path / "images")
    renderer = SourcePageRenderer(source, 1, storage, artifacts)
    first = renderer.render(300)
    second = renderer.render(400)
    assert first.metadata.dpi == 300
    assert second.metadata.dpi == 400
    assert first.metadata.source_checksum_sha256 == source.checksum_sha256
    assert second.metadata.width > first.metadata.width
    assert first.metadata.sha256 != second.metadata.sha256
    assert first.metadata.sha256 == hashlib.sha256(first.png).hexdigest()
    opens = storage.opens
    assert renderer.render(300) == first
    assert storage.opens == opens
    bounds = RegionBounds(left=0.1, top=0.05, right=0.8, bottom=0.2)
    normal = renderer.crop(bounds, 400)
    detail = renderer.crop(bounds, 600)
    assert detail.render.metadata.dpi == 600
    assert detail.crop.width > normal.crop.width
    assert detail.crop.parent_sha256 == detail.render.metadata.sha256
    assert detail.crop.sha256 == hashlib.sha256(detail.crop.png).hexdigest()
    assert (
        artifacts.read(first.metadata.model_dump(mode="json"), source=source, page_number=1)
        == first.png
    )


def test_consensus_input_factory_keeps_a_bound_true_resolution_renderer(tmp_path: Path) -> None:
    from exam_guru_api.core.config import Settings
    from exam_guru_api.documents.understanding_runtime import (
        create_understanding_runtime,
        prepare_understanding_input,
    )
    from tests.test_document_understanding_runtime import openai_settings

    path = source_pdf(tmp_path / "source.pdf")
    source = image_source(path)
    runtime = create_understanding_runtime(
        Settings.model_validate(
            {
                **openai_settings(),
                "source_consensus_enabled": True,
                "source_qwen_model_digest": "a" * 64,
            }
        )
    )
    assert runtime is not None
    prepared = prepare_understanding_input(
        source,
        1,
        None,
        FileSourceStore(path),
        PageImageArtifacts(root=tmp_path / "images"),
        runtime,
    )
    assert prepared.image_metadata.dpi == 300
    assert prepared.source_renderer is not None
    assert prepared.source_renderer.source == source
    assert prepared.source_renderer.render(300).metadata == prepared.image_metadata


def test_explicit_new_render_does_not_silently_replace_missing_declared_evidence(
    tmp_path: Path,
) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    source = image_source(path)
    artifacts = PageImageArtifacts(root=tmp_path / "images")
    first = SourcePageRenderer(source, 1, FileSourceStore(path), artifacts).render(300)
    missing = PageImageArtifacts(root=tmp_path / "another-image-store")
    with pytest.raises(PageImageError):
        SourcePageRenderer(
            source, 1, FileSourceStore(path), missing, declared=first.metadata
        ).render(400)


def test_region_input_binds_the_actual_high_resolution_parent_and_crop_geometry(
    tmp_path: Path,
) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    source = image_source(path)
    renderer = SourcePageRenderer(
        source, 1, FileSourceStore(path), PageImageArtifacts(root=tmp_path / "images")
    )
    base = renderer.render(300)
    bounds = RegionBounds(left=0.1, top=0.05, right=0.8, bottom=0.2)
    detail = renderer.crop(bounds, 400)
    value = SourceRegionInput(
        source=PageArtifactIdentity(
            document_id=source.document_id,
            source_sha256=source.checksum_sha256,
            page_number=1,
            image_sha256=base.metadata.sha256,
        ),
        region=SourceLayoutRegion(
            key="text", kind="paragraph", reading_order=0, parent_key=None, bounds=bounds
        ),
        image_png=detail.crop.png,
        image_sha256=detail.crop.sha256,
        render_dpi=400,
        render_metadata=detail.render.metadata,
        purpose="text",
        language_hint="en",
    )
    assert value.render_metadata is not None
    assert value.render_metadata.sha256 == detail.crop.parent_sha256
    assert value.crop_coordinates == (detail.crop.left, detail.crop.top)
    with pytest.raises(ValueError, match="exact render revision"):
        SourceRegionInput.model_validate(value.model_copy(update={"render_dpi": 600}))
    with pytest.raises(ValueError, match="exact render revision"):
        SourceRegionInput.model_validate(
            value.model_copy(update={"image_png": base.png, "image_sha256": base.metadata.sha256})
        )

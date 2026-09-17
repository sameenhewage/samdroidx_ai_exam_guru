from pathlib import Path

import pytest

from exam_guru_api.documents.page_images import PageImageArtifacts, PageImageError
from exam_guru_api.documents.source_consensus import SourceRegionInput
from exam_guru_api.documents.source_reading import SourceLayoutRegion
from exam_guru_api.documents.source_renders import SourcePageRenderer, read_source_crop
from exam_guru_api.documents.understanding_contracts import RegionBounds
from exam_guru_api.documents.understanding_verification import PageArtifactIdentity
from tests.test_page_images import FileSourceStore, image_source
from tests.test_tesseract_file_input import source_pdf


def test_recorded_witness_image_is_exactly_the_supplied_crop(tmp_path: Path) -> None:
    path = source_pdf(tmp_path / "source.pdf")
    original = image_source(path)
    storage = FileSourceStore(path)
    artifacts = PageImageArtifacts(root=tmp_path / "images")
    renderer = SourcePageRenderer(original, 1, storage, artifacts)
    base = renderer.render(300)
    bounds = RegionBounds(left=0.1, top=0.05, right=0.8, bottom=0.2)
    frame = renderer.crop(bounds, 400)
    value = SourceRegionInput(
        source=PageArtifactIdentity(
            document_id=original.document_id,
            source_sha256=original.checksum_sha256,
            page_number=1,
            image_sha256=base.metadata.sha256,
        ),
        region=SourceLayoutRegion(
            key="text", kind="paragraph", reading_order=0, parent_key=None, bounds=bounds
        ),
        image_png=frame.crop.png,
        image_sha256=frame.crop.sha256,
        render_dpi=400,
        render_metadata=frame.render.metadata,
        purpose="text",
        language_hint="en",
    )
    payload = value.model_dump(mode="json")
    assert read_source_crop(payload, original, storage, artifacts) == frame.crop.png
    with pytest.raises(PageImageError, match="source_witness_image_invalid"):
        read_source_crop({**payload, "image_sha256": "a" * 64}, original, storage, artifacts)
    with pytest.raises(PageImageError):
        read_source_crop(payload, original, storage, PageImageArtifacts(root=tmp_path / "missing"))

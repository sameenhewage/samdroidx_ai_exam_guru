from dataclasses import dataclass, field

import pymupdf

from exam_guru_api.documents.page_images import (
    PageImageArtifacts,
    PageImageError,
    PageImageLimits,
    SourceCandidateImageMetadata,
    SourceImageIdentity,
    SourceImageStorage,
    open_verified_original,
    read_rendered_image,
    render_page_image,
)
from exam_guru_api.documents.source_consensus import SourceRegionInput
from exam_guru_api.documents.source_reading import SourceCrop, SourceLayoutRegion, crop_source_image
from exam_guru_api.documents.understanding_contracts import RegionBounds, _canonical_json
from exam_guru_api.documents.understanding_verification import PageArtifactIdentity


@dataclass(frozen=True, slots=True)
class SourceRender:
    metadata: SourceCandidateImageMetadata
    png: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class SourceRegionRender:
    render: SourceRender
    bounds: RegionBounds
    crop: SourceCrop


class SourcePageRenderer:
    def __init__(
        self,
        source: SourceImageIdentity,
        page_number: int,
        storage: SourceImageStorage,
        artifacts: PageImageArtifacts,
        *,
        declared: SourceCandidateImageMetadata | None = None,
    ) -> None:
        source.check_page(page_number)
        self.source = source
        self.page_number = page_number
        self.storage = storage
        self.artifacts = artifacts
        self._cache: dict[int, SourceRender] = {}
        if declared is not None:
            data = artifacts.read(
                declared.model_dump(mode="json"), source=source, page_number=page_number
            )
            if declared.rasterizer_version == pymupdf.VersionBind:
                self._cache[declared.dpi] = SourceRender(declared, data)

    def render(self, dpi: int) -> SourceRender:
        if type(dpi) is not int or dpi not in {300, 400, 600}:
            raise ValueError("source render requires an approved bounded resolution")
        if dpi not in self._cache:
            with (
                open_verified_original(self.storage, self.source) as stream,
                render_page_image(
                    stream,
                    self.page_number,
                    limits=PageImageLimits(dpi=dpi),
                    expected_page_count=self.source.page_count,
                    wait_for_slot=True,
                ) as image,
            ):
                metadata = SourceCandidateImageMetadata.model_validate(
                    self.artifacts.persist(image, source=self.source)
                )
                data = read_rendered_image(image)
            self._cache[dpi] = SourceRender(metadata, data)
        return self._cache[dpi]

    def crop(self, bounds: RegionBounds, dpi: int) -> SourceRegionRender:
        rendered = self.render(dpi)
        crop = crop_source_image(rendered.png, bounds, parent_limits=PageImageLimits(dpi=dpi))
        return SourceRegionRender(rendered, bounds, crop)


def read_source_crop(
    payload: dict[str, object],
    source: SourceImageIdentity,
    storage: SourceImageStorage,
    artifacts: PageImageArtifacts,
) -> bytes:
    try:
        reference = PageArtifactIdentity.model_validate_json(_canonical_json(payload["source"]))
        region = SourceLayoutRegion.model_validate_json(_canonical_json(payload["region"]))
        metadata = SourceCandidateImageMetadata.model_validate(payload["render_metadata"])
        if (
            reference.document_id != source.document_id
            or reference.source_sha256 != source.checksum_sha256
        ):
            raise ValueError("source witness identity changed")
        with open_verified_original(storage, source):
            parent = artifacts.read(
                metadata.model_dump(mode="json"), source=source, page_number=reference.page_number
            )
        crop = crop_source_image(
            parent, region.bounds, parent_limits=PageImageLimits(dpi=metadata.dpi)
        )
        SourceRegionInput.model_validate(
            {
                **payload,
                "source": reference,
                "region": region,
                "render_metadata": metadata,
                "image_png": crop.png,
            }
        )
        return crop.png
    except (KeyError, TypeError, ValueError):
        raise PageImageError("source_witness_image_invalid") from None

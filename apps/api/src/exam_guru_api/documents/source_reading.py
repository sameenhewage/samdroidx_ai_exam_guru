import hashlib
import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Final, Literal, Self

import pymupdf
from pydantic import Field, model_validator

from exam_guru_api.documents.page_images import PageImageLimits, _png_dimensions
from exam_guru_api.documents.understanding_contracts import (
    EducationalUnderstanding,
    Key,
    ObservedCell,
    ObservedRelationship,
    ObservedTable,
    PageObservation,
    PageRegionObservation,
    PageUnderstanding,
    RegionBounds,
    RegionKind,
    ShortText,
    Text,
    UnderstandingModel,
    UnderstandingUncertainty,
    VisualFact,
    _canonical_bytes,
)

SOURCE_READING_PROMPT_VERSION: Final = "visual-source-reading.v3"
SOURCE_READING_SCHEMA_VERSION: Final = "source-read-candidate.v1"


class SourceReadCandidate(UnderstandingModel):
    schema_version: Literal["source-read-candidate.v1"]
    observation: PageObservation
    uncertainties: tuple[UnderstandingUncertainty, ...] = Field(max_length=128)

    @model_validator(mode="after")
    def source_only(self) -> Self:
        self.as_legacy_envelope()
        return self

    def as_legacy_envelope(self) -> PageUnderstanding:
        return PageUnderstanding(
            schema_version="page-understanding.v1",
            observation=self.observation,
            education=EducationalUnderstanding(claims=()),
            uncertainties=self.uncertainties,
        )

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(_canonical_bytes(self)).hexdigest()


class SourceLayoutRegion(UnderstandingModel):
    key: Key
    kind: RegionKind
    reading_order: int = Field(ge=0, le=10000)
    parent_key: Key | None
    bounds: RegionBounds


class SourceLayout(UnderstandingModel):
    schema_version: Literal["source-layout.v1"]
    language: Literal["si", "ta", "en", "mixed", "und"]
    regions: tuple[SourceLayoutRegion, ...] = Field(min_length=1, max_length=32)
    relationships: tuple[ObservedRelationship, ...] = Field(max_length=128)

    @model_validator(mode="after")
    def coherent_layout(self) -> Self:
        PageObservation(
            language=self.language,
            relationships=self.relationships,
            regions=tuple(
                PageRegionObservation(
                    **region.model_dump(),
                    polygon=(),
                    exact_text="",
                    equations=(),
                    table=None,
                    visual_facts=(),
                )
                for region in self.regions
            ),
        )
        return self


class SourceCell(UnderstandingModel):
    state: Literal["visible", "blank", "unreadable"]
    exact_text: Text

    @model_validator(mode="after")
    def literal_cell(self) -> Self:
        ObservedCell(
            row=0,
            column=0,
            row_span=1,
            column_span=1,
            state=self.state,
            exact_text=self.exact_text,
        )
        return self


class SourceMatrix(UnderstandingModel):
    rows: tuple[tuple[SourceCell, ...], ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def complete_matrix(self) -> Self:
        width = len(self.rows[0])
        if not 1 <= width <= 64 or width * len(self.rows) > 512:
            raise ValueError("source matrix exceeds its cell bounds")
        if any(len(row) != width for row in self.rows):
            raise ValueError("source matrix must retain every physical cell")
        return self

    def observed(self) -> ObservedTable:
        return ObservedTable(
            rows=len(self.rows),
            columns=len(self.rows[0]),
            cells=tuple(
                ObservedCell(
                    row=row_index,
                    column=column_index,
                    row_span=1,
                    column_span=1,
                    state=cell.state,
                    exact_text=cell.exact_text,
                )
                for row_index, row in enumerate(self.rows)
                for column_index, cell in enumerate(row)
            ),
        )


class SourceRegionUncertainty(UnderstandingModel):
    field: ShortText
    reason: ShortText
    alternatives: tuple[ShortText, ...] = Field(max_length=8)


class SourceRegionReading(UnderstandingModel):
    exact_text: Text
    equations: tuple[ShortText, ...] = Field(max_length=128)
    table: SourceMatrix | None
    visual_facts: tuple[VisualFact, ...] = Field(max_length=64)
    uncertainties: tuple[SourceRegionUncertainty, ...] = Field(max_length=8)


class SourceTextReading(UnderstandingModel):
    exact_text: Text
    uncertainties: tuple[SourceRegionUncertainty, ...] = Field(max_length=8)

    def as_region(self) -> SourceRegionReading:
        return SourceRegionReading(
            exact_text=self.exact_text,
            equations=(),
            table=None,
            visual_facts=(),
            uncertainties=self.uncertainties,
        )


class SourceReadingBudget(UnderstandingModel):
    max_requests: int = Field(default=48, ge=2, le=64)
    max_total_output_tokens: int = Field(default=131072, ge=1, le=262144)
    total_timeout_ms: int = Field(default=900000, ge=1, le=900000)
    max_region_rereads: int = Field(default=4, ge=0, le=8)


@dataclass(frozen=True, slots=True)
class SourceCrop:
    parent_sha256: str
    sha256: str
    left: int
    top: int
    width: int
    height: int
    png: bytes = field(repr=False)
    transform: str | None = None

    def metadata(self) -> dict[str, str | int]:
        return {
            **({"transform": self.transform} if self.transform is not None else {}),
            "parent_sha256": self.parent_sha256,
            "sha256": self.sha256,
            "left": self.left,
            "top": self.top,
            "width": self.width,
            "height": self.height,
        }


def crop_source_image(
    image: bytes,
    bounds: RegionBounds,
    *,
    parent_limits: PageImageLimits | None = None,
) -> SourceCrop:
    bounds = RegionBounds.model_validate(bounds)
    output_limits = PageImageLimits(max_png_bytes=8 * 1024 * 1024, max_pixels=16_000_000)
    width, height = _png_dimensions(image, parent_limits or output_limits)
    rectangle = pymupdf.IRect(
        math.floor(bounds.left * width),
        math.floor(bounds.top * height),
        math.ceil(bounds.right * width),
        math.ceil(bounds.bottom * height),
    )
    if rectangle == pymupdf.IRect(0, 0, width, height):
        _png_dimensions(image, output_limits)
        checksum = hashlib.sha256(image).hexdigest()
        return SourceCrop(checksum, checksum, 0, 0, width, height, image)
    original = pymupdf.Pixmap(image)
    cropped = pymupdf.Pixmap(original.colorspace, rectangle, original.alpha)
    cropped.copy(original, rectangle)
    cropped.set_dpi(original.xres, original.yres)
    png = cropped.tobytes("png")
    _png_dimensions(png, PageImageLimits(max_png_bytes=8 * 1024 * 1024, max_pixels=16_000_000))
    return SourceCrop(
        hashlib.sha256(image).hexdigest(),
        hashlib.sha256(png).hexdigest(),
        rectangle.x0,
        rectangle.y0,
        cropped.width,
        cropped.height,
        png,
    )


def enhance_source_crop(crop: SourceCrop) -> SourceCrop:
    _png_dimensions(crop.png, PageImageLimits(max_png_bytes=8 * 1024 * 1024, max_pixels=16_000_000))
    original = pymupdf.Pixmap(crop.png)
    gray = pymupdf.Pixmap(pymupdf.csGRAY, original)
    values = gray.samples
    histogram = Counter(values)
    total = len(values)
    weighted = sum(value * count for value, count in histogram.items())
    left_count = 0
    left_sum = 0
    best = -1.0
    threshold = 127
    for value in range(256):
        left_count += histogram.get(value, 0)
        left_sum += value * histogram.get(value, 0)
        right_count = total - left_count
        if not left_count or not right_count:
            continue
        difference = left_sum / left_count - (weighted - left_sum) / right_count
        score = left_count * right_count * difference * difference
        if score > best:
            best = score
            threshold = value
    pixels = values.translate(bytes(0 if value <= threshold else 255 for value in range(256)))
    rendition = pymupdf.Pixmap(pymupdf.csGRAY, gray.width, gray.height, pixels, False)
    rendition.set_dpi(original.xres, original.yres)
    png = rendition.tobytes("png")
    return SourceCrop(
        crop.parent_sha256,
        hashlib.sha256(png).hexdigest(),
        crop.left,
        crop.top,
        crop.width,
        crop.height,
        png,
        "grayscale-otsu.v1",
    )


def assemble_source_reading(
    layout: SourceLayout, readings: dict[str, SourceRegionReading]
) -> SourceReadCandidate:
    layout = SourceLayout.model_validate(layout)
    if set(readings) != {region.key for region in layout.regions}:
        raise ValueError("source readings must match the exact requested regions")
    regions: list[PageRegionObservation] = []
    uncertainties: list[UnderstandingUncertainty] = []
    for region in sorted(layout.regions, key=lambda item: (item.reading_order, item.key)):
        reading = SourceRegionReading.model_validate(readings[region.key])
        regions.append(
            PageRegionObservation(
                **region.model_dump(),
                polygon=(),
                exact_text=reading.exact_text,
                equations=reading.equations,
                table=None if reading.table is None else reading.table.observed(),
                visual_facts=reading.visual_facts,
            )
        )
        for uncertain in reading.uncertainties:
            uncertainties.append(
                UnderstandingUncertainty(
                    key=f"uncertainty_{len(uncertainties)}",
                    region_keys=(region.key,),
                    field=uncertain.field,
                    reason=uncertain.reason,
                    alternatives=uncertain.alternatives,
                )
            )
    return SourceReadCandidate(
        schema_version=SOURCE_READING_SCHEMA_VERSION,
        observation=PageObservation(
            language=layout.language,
            regions=tuple(regions),
            relationships=layout.relationships,
        ),
        uncertainties=tuple(uncertainties),
    )

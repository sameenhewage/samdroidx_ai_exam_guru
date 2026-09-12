import hashlib
import json
import math
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from exam_guru_api.documents.fidelity import normalize_source_text

UNDERSTANDING_SCHEMA_VERSION = "page-understanding.v1"
MAX_UNDERSTANDING_BYTES = 1_048_576
MAX_PAGE_REGIONS = 128
MAX_PAGE_CELLS = 4096
Key = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
Coordinate = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


def _unicode_text(value: str) -> str:
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ValueError("understanding text must be valid Unicode") from None
    if "\x00" in value:
        raise ValueError("understanding text cannot contain a null character")
    return value


Text = Annotated[str, Field(max_length=100_000), AfterValidator(_unicode_text)]
ShortText = Annotated[str, Field(min_length=1, max_length=2000), AfterValidator(_unicode_text)]
RegionKind = Literal[
    "heading",
    "paragraph",
    "instruction",
    "question",
    "worked_example",
    "equation",
    "vertical_arithmetic",
    "table",
    "grid",
    "chart",
    "diagram",
    "illustration",
    "repeated_object_group",
    "label",
    "blank_answer_area",
    "page_number",
    "footer",
    "decorative_image",
]


class UnderstandingModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class NormalizedPoint(UnderstandingModel):
    x: Coordinate
    y: Coordinate


class RegionBounds(UnderstandingModel):
    left: Coordinate
    top: Coordinate
    right: Coordinate
    bottom: Coordinate

    @model_validator(mode="after")
    def nonempty(self) -> Self:
        if self.left >= self.right or self.top >= self.bottom:
            raise ValueError("region bounds must have positive area")
        return self


class ObservedCell(UnderstandingModel):
    row: int = Field(ge=0, le=63)
    column: int = Field(ge=0, le=63)
    row_span: int = Field(ge=1, le=64)
    column_span: int = Field(ge=1, le=64)
    state: Literal["visible", "blank", "unreadable"]
    exact_text: Text

    @model_validator(mode="after")
    def explicit_blank(self) -> Self:
        if (self.state == "visible") != bool(self.exact_text.strip()):
            raise ValueError("cell visibility must match its exact source text")
        if self.state != "visible" and self.exact_text != "":
            raise ValueError("blank or unreadable cell cannot contain an inferred value")
        return self


class ObservedTable(UnderstandingModel):
    rows: int = Field(ge=1, le=64)
    columns: int = Field(ge=1, le=64)
    cells: tuple[ObservedCell, ...] = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def preserve_grid(self) -> Self:
        if self.rows * self.columns > 512:
            raise ValueError("grid exceeds its bounded cell count")
        occupied: set[tuple[int, int]] = set()
        for cell in self.cells:
            if (
                cell.row + cell.row_span > self.rows
                or cell.column + cell.column_span > self.columns
            ):
                raise ValueError("cell extends beyond its grid")
            for row in range(cell.row, cell.row + cell.row_span):
                for column in range(cell.column, cell.column + cell.column_span):
                    if (row, column) in occupied:
                        raise ValueError("grid cells overlap")
                    occupied.add((row, column))
        if len(occupied) != self.rows * self.columns:
            raise ValueError("grid cells must explicitly preserve blank and unreadable positions")
        return self


class VisualFact(UnderstandingModel):
    key: Key
    description: ShortText
    group_count: int | None = Field(ge=0, le=10000)
    items_per_group: int | None = Field(ge=0, le=10000)
    printed_total: ShortText | None


class PageRegionObservation(UnderstandingModel):
    key: Key
    kind: RegionKind
    reading_order: int = Field(ge=0, le=10000)
    parent_key: Key | None
    bounds: RegionBounds | None
    polygon: tuple[NormalizedPoint, ...] = Field(max_length=64)
    exact_text: Text
    equations: tuple[ShortText, ...] = Field(max_length=128)
    table: ObservedTable | None
    visual_facts: tuple[VisualFact, ...] = Field(max_length=64)

    @property
    def nfc_text(self) -> str:
        return normalize_source_text(self.exact_text)

    @model_validator(mode="after")
    def coherent_region(self) -> Self:
        if self.polygon:
            if len(self.polygon) < 3:
                raise ValueError("region polygon must contain at least three points")
            area = math.fsum(
                left.x * right.y - right.x * left.y
                for left, right in zip(
                    self.polygon, (*self.polygon[1:], self.polygon[0]), strict=True
                )
            )
            if math.isclose(area, 0, abs_tol=1e-12):
                raise ValueError("region polygon must have nonzero area")
        if self.table is not None and self.kind not in {"table", "grid", "vertical_arithmetic"}:
            raise ValueError("structured cells require a table, grid or arithmetic region")
        if len({fact.key for fact in self.visual_facts}) != len(self.visual_facts):
            raise ValueError("region visual fact keys must be unique")
        return self


class ObservedRelationship(UnderstandingModel):
    source_key: Key
    target_key: Key
    kind: Literal[
        "label_for",
        "answer_area_for",
        "grouped_with",
        "aligned_with",
        "part_of",
        "reading_next",
        "illustrates",
    ]


class PageObservation(UnderstandingModel):
    language: Literal["si", "ta", "en", "mixed", "und"]
    regions: tuple[PageRegionObservation, ...] = Field(min_length=1, max_length=MAX_PAGE_REGIONS)
    relationships: tuple[ObservedRelationship, ...] = Field(max_length=512)

    @model_validator(mode="after")
    def coherent_page(self) -> Self:
        regions = {region.key: region for region in self.regions}
        if len(regions) != len(self.regions):
            raise ValueError("page region keys must be unique")
        if len({(region.parent_key, region.reading_order) for region in self.regions}) != len(
            regions
        ):
            raise ValueError("region sibling reading order must be unambiguous")
        for region in self.regions:
            visited = {region.key}
            parent = region.parent_key
            while parent is not None:
                if parent not in regions or parent in visited or len(visited) > 8:
                    raise ValueError(
                        "region parent links must remain bounded and acyclic within the page"
                    )
                visited.add(parent)
                parent = regions[parent].parent_key
        if (
            sum(region.table.rows * region.table.columns for region in self.regions if region.table)
            > MAX_PAGE_CELLS
        ):
            raise ValueError("page grids exceed the bounded cell count")
        for relationship in self.relationships:
            if (
                relationship.source_key not in regions
                or relationship.target_key not in regions
                or relationship.source_key == relationship.target_key
            ):
                raise ValueError("relationships must link distinct known page regions")
        identities = {(item.source_key, item.target_key, item.kind) for item in self.relationships}
        if len(identities) != len(self.relationships):
            raise ValueError("duplicate region relationship")
        return self


class EducationalClaim(UnderstandingModel):
    key: Key
    kind: Literal[
        "topic",
        "concept",
        "skill",
        "learning_objective",
        "educational_purpose",
        "activity_type",
        "worked_example",
        "relationship",
        "prerequisite",
    ]
    description: ShortText
    region_keys: tuple[Key, ...] = Field(min_length=1, max_length=MAX_PAGE_REGIONS)


class EducationalUnderstanding(UnderstandingModel):
    claims: tuple[EducationalClaim, ...] = Field(max_length=128)


class UnderstandingUncertainty(UnderstandingModel):
    key: Key
    region_keys: tuple[Key, ...] = Field(min_length=1, max_length=MAX_PAGE_REGIONS)
    field: ShortText
    reason: ShortText
    alternatives: tuple[ShortText, ...] = Field(max_length=8)


class PageUnderstanding(UnderstandingModel):
    schema_version: Literal["page-understanding.v1"]
    observation: PageObservation
    education: EducationalUnderstanding
    uncertainties: tuple[UnderstandingUncertainty, ...] = Field(max_length=128)

    @model_validator(mode="after")
    def separate_grounded_layers(self) -> Self:
        known_regions = {region.key for region in self.observation.regions}
        for records in (self.education.claims, self.uncertainties):
            if len({item.key for item in records}) != len(records):
                raise ValueError("understanding keys must be unique within each layer")
            for item in records:
                if not set(item.region_keys) <= known_regions or len(set(item.region_keys)) != len(
                    item.region_keys
                ):
                    raise ValueError("understanding must cite unique known page regions")
        if len(_canonical_bytes(self)) > MAX_UNDERSTANDING_BYTES:
            raise ValueError("page understanding exceeds the bounded payload size")
        return self


def _canonical_json(value: object) -> str:
    if isinstance(value, dict):
        return (
            "{"
            + ",".join(
                json.dumps(key, ensure_ascii=False) + ":" + _canonical_json(item)
                for key, item in sorted(value.items())
            )
            + "}"
        )
    if isinstance(value, list):
        return "[" + ",".join(_canonical_json(item) for item in value) + "]"
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical source numbers must be finite")
        number = Decimal(str(value))
        return format(abs(number) if number.is_zero() else number, "f")
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _canonical_bytes(value: UnderstandingModel) -> bytes:
    return _canonical_json(value.model_dump(mode="json")).encode("utf-8")


def understanding_fingerprint(value: PageUnderstanding) -> str:
    validated = PageUnderstanding.model_validate(value)
    return hashlib.sha256(_canonical_bytes(validated)).hexdigest()

import hashlib
import time
from collections.abc import Callable
from typing import Literal, Self

from pydantic import Field, model_validator

from exam_guru_api.documents.source_consensus import (
    IndependentReading,
    RegionConsensus,
    SourceExecutionBlockedError,
    SourceRegionInput,
    SourceWitnessRecordingError,
    reconcile_region,
)
from exam_guru_api.documents.source_geometry import SourcePageGeometry
from exam_guru_api.documents.source_reading import (
    SourceLayout,
    SourceLayoutRegion,
    SourceReadCandidate,
)
from exam_guru_api.documents.understanding_contracts import (
    Key,
    ObservedCell,
    ObservedTable,
    PageObservation,
    PageRegionObservation,
    UnderstandingModel,
    UnderstandingUncertainty,
    _canonical_bytes,
)
from exam_guru_api.documents.understanding_verification import PageArtifactIdentity

Purpose = Literal["text", "math", "url", "email", "cell", "visual", "unknown"]
RegionInputFactory = Callable[[SourceLayoutRegion, Purpose, int], SourceRegionInput]
IndependentReader = Callable[[SourceRegionInput], IndependentReading]


class SourceRegionFailure(UnderstandingModel):
    region_key: Key
    reader: Literal["qwen", "openai", "pipeline"]
    code: str = Field(min_length=1, max_length=128)


def _readable_content(content: SourceReadCandidate) -> bool:
    return any(
        region.exact_text.strip()
        or region.equations
        or (
            region.table is not None
            and any(
                cell.state == "visible" and cell.exact_text.strip() for cell in region.table.cells
            )
        )
        for region in content.observation.regions
    )


class MachineSourceCandidate(UnderstandingModel):
    schema_version: Literal["machine-source-candidate.v1"] = "machine-source-candidate.v1"
    source: PageArtifactIdentity
    content: SourceReadCandidate = Field(repr=False)
    geometry: SourcePageGeometry
    consensus: tuple[RegionConsensus, ...] = Field(max_length=512)
    failures: tuple[SourceRegionFailure, ...] = Field(max_length=1024)
    state: Literal["machine_ready", "needs_attention"]
    human_verified: Literal[False] = False
    text_readable: bool

    @model_validator(mode="after")
    def machine_evidence_only(self) -> Self:
        if self.geometry.image_sha256 != self.source.image_sha256:
            raise ValueError("machine geometry belongs to another page render")
        if any(pair.source != self.source for pair in self.consensus):
            raise ValueError("machine source contains a different page witness")
        if len({pair.region_key for pair in self.consensus}) != len(self.consensus):
            raise ValueError("machine source repeats a consensus region")
        pairs = {pair.region_key: pair for pair in self.consensus}
        tables = {table.key: table for table in self.geometry.tables}
        if not set(tables).issubset({region.key for region in self.content.observation.regions}):
            raise ValueError("machine source omitted table cell geometry")
        for region in self.content.observation.regions:
            if region.key in tables:
                geometry = tables[region.key]
                if (
                    region.table is None
                    or (region.table.rows, region.table.columns)
                    != (geometry.rows, geometry.columns)
                    or region.bounds != geometry.bounds
                    or region.exact_text
                    or region.equations
                    or region.visual_facts
                ):
                    raise ValueError("machine source changed table cell geometry")
                cells = {(cell.row + 1, cell.column + 1): cell for cell in region.table.cells}
                for expected in geometry.cells:
                    cell = cells[expected.row, expected.column]
                    pair = pairs.get(cell_region_key(geometry.key, expected.row, expected.column))
                    selected = None if pair is None else pair.selected
                    state = (
                        "blank"
                        if expected.blank
                        else "visible"
                        if selected is not None
                        else "unreadable"
                    )
                    value = (
                        selected.exact_text if selected is not None and not expected.blank else ""
                    )
                    if cell.state != state or cell.exact_text != value:
                        raise ValueError("machine source cell differs from its bound witness")
                continue
            pair = pairs.get(region.key)
            selected = None if pair is None else pair.selected
            if (
                region.exact_text != (selected.exact_text if selected else "")
                or region.equations != (selected.equations if selected else ())
                or region.visual_facts != (selected.visual_facts if selected else ())
                or region.table is not None
            ):
                raise ValueError("machine source text differs from independent evidence")
        ready = (
            bool(self.consensus)
            and not self.failures
            and not self.geometry.findings
            and not self.content.uncertainties
            and all(not pair.requires_reread for pair in self.consensus)
        )
        if (self.state == "machine_ready") != ready or self.text_readable != _readable_content(
            self.content
        ):
            raise ValueError("machine quality state contradicts its evidence")
        if len(_canonical_bytes(self)) > 4 * 1024 * 1024:
            raise ValueError("machine source evidence exceeds its bound")
        return self

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(_canonical_bytes(self)).hexdigest()


def cell_region_key(table_key: str, row: int, column: int) -> str:
    return f"{table_key}_r{row}_c{column}"


class SourceConsensusEngine:
    def __init__(
        self,
        *,
        max_rereads: int = 8,
        max_pairs: int = 48,
        timeout_ms: int = 900000,
        initial_dpi: int = 400,
        detail_dpi: int = 600,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            type(max_rereads) is not int
            or not 0 <= max_rereads <= 32
            or type(max_pairs) is not int
            or not 1 <= max_pairs <= 256
            or type(timeout_ms) is not int
            or not 1 <= timeout_ms <= 900000
            or initial_dpi not in {300, 400}
            or detail_dpi != 600
        ):
            raise ValueError("invalid source consensus recovery budget")
        self.max_rereads = max_rereads
        self.max_pairs = max_pairs
        self.timeout_ms = timeout_ms
        self.initial_dpi = initial_dpi
        self.detail_dpi = detail_dpi
        self.clock = clock

    def read_page(
        self,
        source: PageArtifactIdentity,
        layout: SourceLayout,
        geometry: SourcePageGeometry,
        images: RegionInputFactory,
        qwen: IndependentReader,
        openai: IndependentReader,
    ) -> MachineSourceCandidate:
        source = PageArtifactIdentity.model_validate(source)
        layout = SourceLayout.model_validate(layout)
        geometry = SourcePageGeometry.model_validate(geometry)
        if geometry.image_sha256 != source.image_sha256:
            raise ValueError("source geometry does not match the requested page")
        started = self.clock()
        pair_count = 0
        targets: list[tuple[SourceLayoutRegion, Purpose]] = []
        tables = {table.key: table for table in geometry.tables}
        regions = {region.key: region for region in layout.regions}
        failures: dict[str, list[SourceRegionFailure]] = {}
        outcomes: dict[str, RegionConsensus] = {}
        for region in layout.regions:
            if region.kind in {"table", "grid"}:
                if region.key not in tables:
                    failures[region.key] = [
                        SourceRegionFailure(
                            region_key=region.key,
                            reader="pipeline",
                            code="table_geometry_unavailable",
                        )
                    ]
                continue
            if region.kind == "decorative_image":
                continue
            purpose: Purpose = (
                "visual"
                if region.kind in {"illustration", "diagram", "repeated_object_group", "chart"}
                else "math"
                if region.kind in {"equation", "vertical_arithmetic"}
                else "text"
            )
            targets.append((region, purpose))
        for table in geometry.tables:
            if table.key not in regions:
                regions[table.key] = SourceLayoutRegion(
                    key=table.key,
                    kind="grid",
                    reading_order=len(regions),
                    parent_key=None,
                    bounds=table.bounds,
                )
            if not table.geometry_valid:
                failures[table.key] = [
                    SourceRegionFailure(
                        region_key=table.key, reader="pipeline", code="table_geometry_ambiguous"
                    )
                ]
            targets.extend(
                (
                    SourceLayoutRegion(
                        key=cell_region_key(table.key, cell.row, cell.column),
                        kind="label",
                        reading_order=(cell.row - 1) * table.columns + cell.column - 1,
                        parent_key=table.key,
                        bounds=cell.bounds,
                    ),
                    "cell",
                )
                for cell in table.cells
                if not cell.blank
            )

        def attempt(region: SourceLayoutRegion, purpose: Purpose, dpi: int) -> None:
            nonlocal pair_count
            if pair_count >= self.max_pairs or (self.clock() - started) * 1000 >= self.timeout_ms:
                failures[region.key] = [
                    SourceRegionFailure(
                        region_key=region.key,
                        reader="pipeline",
                        code="source_read_budget_exhausted",
                    )
                ]
                return
            value = SourceRegionInput.model_validate(images(region, purpose, dpi))
            if value.source != source or value.region != region or value.render_dpi != dpi:
                raise ValueError("region input factory changed source/render identity")
            pair_count += 1
            readings: list[IndependentReading] = []
            errors: list[SourceRegionFailure] = []
            witnesses: tuple[tuple[Literal["qwen", "openai"], IndependentReader], ...] = (
                ("qwen", qwen),
                ("openai", openai),
            )
            for name, reader in witnesses:
                try:
                    result = IndependentReading.model_validate(reader(value))
                except (SourceWitnessRecordingError, SourceExecutionBlockedError):
                    raise
                except Exception as error:
                    errors.append(
                        SourceRegionFailure(
                            region_key=region.key, reader=name, code=type(error).__name__
                        )
                    )
                    continue
                if result.reader.reader != name or result.input_fingerprint != value.fingerprint:
                    raise ValueError(
                        "independent reader returned stale or different source evidence"
                    )
                readings.append(result)
            if errors:
                failures[region.key] = errors
                return
            failures.pop(region.key, None)
            outcomes[region.key] = reconcile_region(
                value, readings[0], readings[1], previous=outcomes.get(region.key)
            )

        ordered_targets = sorted(
            targets, key=lambda target: target[1] not in {"cell", "math", "url", "email"}
        )
        reserved = min(self.max_rereads, self.max_pairs // 2)
        initial_limit = self.max_pairs - reserved
        attempted: list[tuple[SourceLayoutRegion, Purpose]] = []
        deferred: list[tuple[SourceLayoutRegion, Purpose]] = []
        for region, purpose in ordered_targets:
            if pair_count >= initial_limit or (
                reserved and (self.clock() - started) * 1000 >= self.timeout_ms * 0.7
            ):
                deferred.append((region, purpose))
                failures[region.key] = [
                    SourceRegionFailure(
                        region_key=region.key, reader="pipeline", code="source_read_budget_reserved"
                    )
                ]
                continue
            attempt(region, purpose, self.initial_dpi)
            attempted.append((region, purpose))
        rereads = 0
        for region, purpose in attempted:
            outcome = outcomes.get(region.key)
            if rereads >= self.max_rereads:
                break
            if region.key in failures or (outcome is not None and outcome.requires_reread):
                rereads += 1
                attempt(region, purpose, self.detail_dpi)
        for region, purpose in deferred:
            if pair_count >= self.max_pairs:
                break
            attempt(region, purpose, self.initial_dpi)

        observations: list[PageRegionObservation] = []
        uncertainties: list[UnderstandingUncertainty] = []
        for order, region in enumerate(
            sorted(regions.values(), key=lambda item: (item.bounds.top, item.bounds.left, item.key))
        ):
            selected = outcomes[region.key].selected if region.key in outcomes else None
            geometry_table = tables.get(region.key)
            observed_table = None
            unresolved = region.key in failures or (
                region.kind != "decorative_image" and geometry_table is None and selected is None
            )
            if geometry_table is not None:
                cells: list[ObservedCell] = []
                for cell in geometry_table.cells:
                    key = cell_region_key(geometry_table.key, cell.row, cell.column)
                    value = outcomes[key].selected if key in outcomes else None
                    state: Literal["visible", "blank", "unreadable"] = (
                        "blank" if cell.blank else "visible" if value is not None else "unreadable"
                    )
                    unresolved = unresolved or state == "unreadable"
                    cells.append(
                        ObservedCell(
                            row=cell.row - 1,
                            column=cell.column - 1,
                            row_span=1,
                            column_span=1,
                            state=state,
                            exact_text=value.exact_text
                            if state == "visible" and value is not None
                            else "",
                        )
                    )
                observed_table = ObservedTable(
                    rows=geometry_table.rows, columns=geometry_table.columns, cells=tuple(cells)
                )
            observations.append(
                PageRegionObservation(
                    key=region.key,
                    kind=region.kind,
                    reading_order=order,
                    parent_key=region.parent_key,
                    bounds=region.bounds,
                    polygon=(),
                    exact_text=selected.exact_text if selected else "",
                    equations=selected.equations if selected else (),
                    table=observed_table,
                    visual_facts=selected.visual_facts if selected else (),
                )
            )
            if unresolved:
                uncertainties.append(
                    UnderstandingUncertainty(
                        key=f"consensus_{region.key}",
                        region_keys=(region.key,),
                        field="source_consensus",
                        reason=(
                            "Independent source evidence is unresolved. Compare this region "
                            "with the original before any confirmation."
                        ),
                        alternatives=(),
                    )
                )
        current_failures = tuple(failure for values in failures.values() for failure in values)
        current_outcomes = tuple(outcomes.values())
        ready = (
            bool(current_outcomes)
            and not current_failures
            and not geometry.findings
            and not uncertainties
            and all(not pair.requires_reread for pair in current_outcomes)
        )
        content = SourceReadCandidate(
            schema_version="source-read-candidate.v1",
            observation=PageObservation(
                language=layout.language,
                regions=tuple(observations),
                relationships=layout.relationships,
            ),
            uncertainties=tuple(uncertainties),
        )
        return MachineSourceCandidate(
            source=source,
            content=content,
            geometry=geometry,
            consensus=current_outcomes,
            failures=current_failures,
            state="machine_ready" if ready else "needs_attention",
            text_readable=_readable_content(content),
        )

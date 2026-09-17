from dataclasses import dataclass, field
from typing import Literal

import pytest

from exam_guru_api.documents.source_consensus import IndependentReading, SourceRegionInput
from exam_guru_api.documents.source_geometry import (
    SourceCellGeometry,
    SourcePageGeometry,
    SourceTableGeometry,
)
from exam_guru_api.documents.source_machine import (
    MachineSourceCandidate,
    Purpose,
    SourceConsensusEngine,
)
from exam_guru_api.documents.source_reading import SourceLayout, SourceLayoutRegion
from exam_guru_api.documents.understanding_contracts import RegionBounds
from tests.test_source_consensus import reading, region_input


@dataclass
class Reader:
    name: Literal["qwen", "openai"]
    initial: str
    detail: str
    fails: bool = False
    inputs: list[SourceRegionInput] = field(default_factory=list)

    def read(self, source: SourceRegionInput) -> IndependentReading:
        self.inputs.append(source)
        if self.fails:
            raise RuntimeError("reader unavailable")
        return reading(source, self.name, self.detail if source.render_dpi == 600 else self.initial)


def run(local: Reader, remote: Reader, *, rereads: int = 1) -> MachineSourceCandidate:
    source = region_input()
    layout = SourceLayout(
        schema_version="source-layout.v1", language="si", regions=(source.region,), relationships=()
    )
    geometry = SourcePageGeometry(
        image_sha256=source.source.image_sha256,
        dpi=300,
        width=32,
        height=24,
        tables=(),
        regions=(source.region,),
        unassigned_ink_pixels=0,
        findings=(),
    )

    def images(region: SourceLayoutRegion, purpose: Purpose, dpi: int) -> SourceRegionInput:
        return source.model_copy(update={"region": region, "purpose": purpose, "render_dpi": dpi})

    return SourceConsensusEngine(max_rereads=rereads).read_page(
        source.source, layout, geometry, images, local.read, remote.read
    )


def test_machine_candidate_requires_independent_agreement_and_remains_unverified() -> None:
    result = run(Reader("qwen", "මව්බස", "මව්බස"), Reader("openai", "මව්බස", "මව්බස"))
    assert result.state == "machine_ready"
    assert result.content.observation.regions[0].exact_text == "මව්බස"
    assert result.human_verified is False
    assert result.text_readable is True
    assert len(result.consensus) == 1


def test_disagreement_rereads_both_witnesses_at_higher_resolution_without_prior_text() -> None:
    local = Reader("qwen", "මව්බස", "මව්බස")
    remote = Reader("openai", "මව්බිම", "මව්බස")
    result = run(local, remote)
    assert result.state == "machine_ready"
    assert [value.render_dpi for value in local.inputs] == [400, 600]
    assert [value.render_dpi for value in remote.inputs] == [400, 600]
    assert [value.fingerprint for value in local.inputs] == [
        value.fingerprint for value in remote.inputs
    ]
    assert result.consensus[0].state == "resolved_by_reread"
    assert result.consensus[0].previous_fingerprint is not None


def test_unresolved_disagreement_is_not_arbitrarily_displayed_as_source_text() -> None:
    result = run(Reader("qwen", "මව්බස", "මව්බස"), Reader("openai", "මව්බිම", "මව්බිම"))
    assert result.state == "needs_attention"
    assert result.content.observation.regions[0].exact_text == ""
    assert result.content.uncertainties
    assert result.text_readable is False


def test_one_available_provider_cannot_be_promoted_to_consensus() -> None:
    result = run(Reader("qwen", "මව්බස", "මව්බස", fails=True), Reader("openai", "මව්බස", "මව්බස"))
    assert result.state == "needs_attention"
    assert result.failures
    assert result.human_verified is False


def test_recovery_is_bounded_and_does_not_repeat_unchanged_full_page_reads() -> None:
    local = Reader("qwen", "මව්බස", "මව්බස")
    remote = Reader("openai", "මව්බිම", "මව්බිම")
    result = run(local, remote, rereads=0)
    assert result.state == "needs_attention"
    assert len(local.inputs) == len(remote.inputs) == 1


def test_page_budget_reserves_a_high_resolution_recovery_pair() -> None:
    source = region_input()
    regions = tuple(
        source.region.model_copy(update={"key": f"text_{index}", "reading_order": index})
        for index in range(5)
    )
    layout = SourceLayout(
        schema_version="source-layout.v1", language="si", regions=regions, relationships=()
    )
    geometry = SourcePageGeometry(
        image_sha256=source.source.image_sha256,
        dpi=300,
        width=32,
        height=24,
        tables=(),
        regions=regions,
        unassigned_ink_pixels=0,
        findings=(),
    )
    local = Reader("qwen", "මව්බස", "මව්බස")
    remote = Reader("openai", "මව්බිම", "මව්බස")

    def images(region: SourceLayoutRegion, purpose: Purpose, dpi: int) -> SourceRegionInput:
        return source.model_copy(update={"region": region, "purpose": purpose, "render_dpi": dpi})

    result = SourceConsensusEngine(max_pairs=4, max_rereads=1).read_page(
        source.source, layout, geometry, images, local.read, remote.read
    )
    assert len(local.inputs) == 4
    assert any(value.render_dpi == 600 for value in local.inputs)
    assert any(value.render_dpi == 600 for value in remote.inputs)
    assert result.state == "needs_attention"


def test_machine_candidate_rejects_text_not_supported_by_its_independent_witnesses() -> None:
    result = run(Reader("qwen", "මව්බස", "මව්බස"), Reader("openai", "මව්බස", "මව්බස"))
    region = result.content.observation.regions[0].model_copy(update={"exact_text": "මව්බිම"})
    observation = result.content.observation.model_copy(update={"regions": (region,)})
    altered = result.model_copy(
        update={"content": result.content.model_copy(update={"observation": observation})}
    )
    with pytest.raises(ValueError, match="differs from independent evidence"):
        MachineSourceCandidate.model_validate(altered)


def test_machine_table_values_cannot_move_away_from_their_read_cell() -> None:
    source = region_input(language="und")
    bounds = RegionBounds(left=0.0, top=0.0, right=1.0, bottom=1.0)
    region = SourceLayoutRegion(
        key="grid", kind="grid", reading_order=0, parent_key=None, bounds=bounds
    )
    table = SourceTableGeometry(
        key="grid",
        bounds=bounds,
        rows=2,
        columns=2,
        geometry_valid=True,
        cells=tuple(
            SourceCellGeometry(
                row=r + 1,
                column=c + 1,
                blank=(r, c) != (0, 0),
                ink_pixels=1 if (r, c) == (0, 0) else 0,
                bounds=RegionBounds(left=c / 2, top=r / 2, right=(c + 1) / 2, bottom=(r + 1) / 2),
            )
            for r in range(2)
            for c in range(2)
        ),
    )
    geometry = SourcePageGeometry(
        image_sha256=source.source.image_sha256,
        dpi=300,
        width=32,
        height=24,
        tables=(table,),
        regions=(),
        unassigned_ink_pixels=0,
        findings=(),
    )

    def images(region: SourceLayoutRegion, purpose: Purpose, dpi: int) -> SourceRegionInput:
        return source.model_copy(update={"region": region, "purpose": purpose, "render_dpi": dpi})

    result = SourceConsensusEngine(max_rereads=0).read_page(
        source.source,
        SourceLayout(
            schema_version="source-layout.v1", language="und", regions=(region,), relationships=()
        ),
        geometry,
        images,
        Reader("qwen", "10", "10").read,
        Reader("openai", "10", "10").read,
    )
    observed = result.content.observation.regions[0]
    assert observed.table is not None
    moved = tuple(
        cell.model_copy(update={"state": "blank", "exact_text": ""})
        if (cell.row, cell.column) == (0, 0)
        else cell.model_copy(update={"state": "visible", "exact_text": "10"})
        if (cell.row, cell.column) == (1, 1)
        else cell
        for cell in observed.table.cells
    )
    altered = observed.model_copy(
        update={"table": observed.table.model_copy(update={"cells": moved})}
    )
    content = result.content.model_copy(
        update={
            "observation": result.content.observation.model_copy(update={"regions": (altered,)})
        }
    )
    with pytest.raises(ValueError, match="cell"):
        MachineSourceCandidate.model_validate(result.model_copy(update={"content": content}))


def test_machine_candidate_cannot_claim_readiness_with_missing_region_evidence() -> None:
    result = run(Reader("qwen", "මව්බස", "මව්බස"), Reader("openai", "මව්බස", "මව්බස"))
    with pytest.raises(ValueError, match="independent evidence"):
        MachineSourceCandidate.model_validate(result.model_copy(update={"consensus": ()}))

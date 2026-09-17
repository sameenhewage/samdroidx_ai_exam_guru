"""Character and token level disagreement mapping across the three locked witnesses.

A whole region must not become uncertain because one provider disagreed about one
operator. These tests pin the exact conflicting token instead.
"""

from exam_guru_api.documents.source_consensus import build_disagreement_map


def test_single_operator_conflict_is_pinned_instead_of_failing_the_whole_region() -> None:
    """The locked architecture's worked example: only the operator may be in conflict."""
    result = build_disagreement_map(
        {"qwen": "476 \u00d7 8", "ornith": "476 \u00d7 8", "openai": "476 x 8"}
    )

    assert result.providers == ("openai", "ornith", "qwen")
    assert len(result.cells) == 1
    cell = result.cells[0]
    assert cell.kind == "operator"
    assert cell.critical is True
    assert cell.token_index == 1
    assert {variant.value: variant.providers for variant in cell.variants} == {
        "\u00d7": ("ornith", "qwen"),
        "x": ("openai",),
    }
    assert result.critical_conflict is True
    # The agreeing numerals must not be dragged into the conflict.
    assert all(c.token_index != 0 for c in result.cells)


def test_full_agreement_produces_no_conflict() -> None:
    result = build_disagreement_map(
        {"qwen": "2 X 8 = 16", "ornith": "2 X 8 = 16", "openai": "2 X 8 = 16"}
    )
    assert result.cells == ()
    assert result.critical_conflict is False
    assert result.agreement_ratio == 1.0


def test_source_faithful_capital_x_is_not_normalised_into_agreement() -> None:
    """The book prints a capital Latin X. A provider that 'improves' it disagrees."""
    result = build_disagreement_map(
        {"qwen": "2 X 8 = 16", "ornith": "2 \u00d7 8 = 16", "openai": "2 X 8 = 16"}
    )
    assert result.critical_conflict is True
    assert [cell.kind for cell in result.cells] == ["operator"]


def test_character_level_difference_is_reported_inside_a_conflicting_number() -> None:
    result = build_disagreement_map({"qwen": "476", "ornith": "470", "openai": "476"})
    cell = result.cells[0]
    assert cell.kind == "number"
    assert cell.critical is True
    assert cell.character_positions == (2,)


def test_a_missing_provider_reading_is_recorded_as_a_gap_not_invented() -> None:
    result = build_disagreement_map(
        {"qwen": "", "ornith": "info @ nie.lk", "openai": "info @ nie.lk"}
    )
    assert result.missing_providers == ("qwen",)
    assert result.critical_conflict is False
    assert result.cells == ()


def test_insertion_by_one_provider_is_pinned_as_an_added_token() -> None:
    result = build_disagreement_map(
        {"qwen": "www.nie.lk", "ornith": "www.nie.lk extra", "openai": "www.nie.lk"}
    )
    assert result.critical_conflict is False
    cell = result.cells[0]
    assert {variant.value: variant.providers for variant in cell.variants} == {
        "": ("openai", "qwen"),
        "extra": ("ornith",),
    }

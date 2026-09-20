"""A visual description is derived knowledge, and it must stay honest.

Two ways it goes wrong in practice: it gets written in English about a Sinhala
page, and it repeats a measurement or a label that is printed elsewhere on the
page but is not visible in the crop it claims to describe.
"""

from __future__ import annotations

import pytest

from exam_guru_api.source_v2.visual_description import (
    DescriptionRefusedError,
    assert_describable,
    validate,
    validate_labels,
)

# "A bar magnet, a paper butterfly and a piece of thread are shown."
SINHALA_DESCRIPTION = "දණ්ඩ චුම්බකයක්, කඩදාසි සමනලයෙක් සහ නූල් කැබැල්ලක් දැක්වේ."


def codes(description: str, *, language: str = "sinhala", visible_text: str = "") -> set[str]:
    return {
        item.code for item in validate(description, language=language, visible_text=visible_text)
    }


# --- language follows the source ---------------------------------------------


def test_a_sinhala_source_described_in_sinhala_is_accepted() -> None:
    assert codes(SINHALA_DESCRIPTION) == set()


def test_a_sinhala_source_described_in_english_is_refused() -> None:
    """Describing Sinhala material in English quietly translates it."""

    assert "wrong-language" in codes("A bar magnet and a paper butterfly are shown.")


def test_a_tamil_source_wants_a_tamil_description() -> None:
    assert "wrong-language" in codes(SINHALA_DESCRIPTION, language="tamil")


def test_an_english_source_is_described_in_english() -> None:
    assert codes("A bar magnet and a paper butterfly.", language="english") == set()


def test_an_empty_description_is_refused() -> None:
    assert "empty-description" in codes("   ")


# --- only what the crop actually shows ---------------------------------------


def test_a_dimension_not_printed_in_the_crop_is_refused() -> None:
    """`(20 cm x 6 cm)` is printed in the materials list, not in the drawing.

    Copying it into the description asserts the picture shows a measurement it
    does not, and a later reader cannot tell that it was imported.
    """

    findings = codes(f"{SINHALA_DESCRIPTION} රෙජිෆෝම් කැබැල්ල 20 cm x 6 cm වේ.")
    assert "unverifiable-measurement" in findings


def test_a_dimension_actually_printed_inside_the_crop_is_allowed() -> None:
    assert "unverifiable-measurement" not in codes(
        f"{SINHALA_DESCRIPTION} එහි 20 cm ලෙස සලකුණු කර ඇත.",
        visible_text="රෙජිෆෝම් කැල්ලක් 20 cm",
    )


def test_a_quoted_label_not_printed_inside_the_crop_is_refused() -> None:
    assert "unverifiable-label" in codes(f'{SINHALA_DESCRIPTION} "චුම්බකය" ලෙස නම් කර ඇත.')


def test_a_quoted_label_printed_inside_the_crop_is_allowed() -> None:
    assert "unverifiable-label" not in codes(
        f'{SINHALA_DESCRIPTION} "චුම්බකය" ලෙස නම් කර ඇත.',
        visible_text="කෝටුව නූල චුම්බකය රෙජිෆෝම්/මැටි",
    )


def test_several_imported_claims_are_all_reported() -> None:
    findings = validate(
        f'{SINHALA_DESCRIPTION} 20 cm x 6 cm වන "රෙජිෆෝම්" කැබැල්ලකි.',
        language="sinhala",
        visible_text="",
    )
    assert {"unverifiable-measurement", "unverifiable-label"} <= {f.code for f in findings}


# --- detected labels claim the same thing, in a list -------------------------


def label_codes(labels: list[str], *, visible_text: str = "") -> set[str]:
    return {item.code for item in validate_labels(labels, visible_text=visible_text)}


def test_a_label_printed_inside_the_crop_is_accepted() -> None:
    assert label_codes(["චුම්බකය"], visible_text="කෝටුව නූල චුම්බකය") == set()


def test_a_label_taken_from_elsewhere_on_the_page_is_refused() -> None:
    """The list format does not make an imported label true."""

    assert "unverifiable-label" in label_codes(["රෙජිෆෝම්"], visible_text="කෝටුව නූල")


def test_a_visual_only_region_cannot_carry_detected_labels() -> None:
    """No crop text at all means nothing in the crop is legible as a label."""

    assert "unverifiable-label" in label_codes(["චුම්බකය"], visible_text="")


def test_no_labels_is_always_fine() -> None:
    assert validate_labels([], visible_text="") == []


def test_a_blank_label_is_refused() -> None:
    assert "empty-label" in label_codes(["  "], visible_text="කෝටුව")


# --- a description is only written about verified source ----------------------


def test_only_a_visual_region_can_be_described() -> None:
    with pytest.raises(DescriptionRefusedError):
        assert_describable(source_kind="text_only", has_canonical_visual=True)


def test_an_unverified_visual_can_still_be_described() -> None:
    """The reviewer has to be able to see the description in order to judge it.

    Requiring verification first would mean a description only ever appears
    after the human has already decided, which is a description nobody can
    review. It arrives as a proposal beside the machine's reading of the text.
    """

    assert_describable(source_kind="visual_only", has_canonical_visual=True)


def test_a_visual_without_its_crop_cannot_be_described() -> None:
    with pytest.raises(DescriptionRefusedError):
        assert_describable(source_kind="visual_only", has_canonical_visual=False)


def test_a_visual_with_its_crop_may_be_described() -> None:
    assert_describable(source_kind="visual_only", has_canonical_visual=True)
    assert_describable(source_kind="visual_with_text", has_canonical_visual=True)

"""D18: a region can be source content without containing any text."""

from __future__ import annotations

import pytest

from exam_guru_api.source_v2.source_kind import (
    EmbeddingRefusedError,
    Modality,
    SourceKind,
    assert_embeddable,
    eligible_modalities,
    propose,
)

# --- A. what the machine may propose ------------------------------------------


def test_a_figure_with_no_printed_text_is_proposed_visual_only() -> None:
    assert propose("figure", has_text=False) is SourceKind.VISUAL_ONLY


def test_a_figure_with_printed_labels_is_proposed_visual_with_text() -> None:
    assert propose("figure", has_text=True) is SourceKind.VISUAL_WITH_TEXT


@pytest.mark.parametrize("region_type", ["text", "heading"])
def test_prose_is_proposed_text_only(region_type: str) -> None:
    assert propose(region_type, has_text=True) is SourceKind.TEXT_ONLY


def test_a_decorative_region_is_proposed_decorative() -> None:
    assert propose("decorative", has_text=True) is SourceKind.DECORATIVE


@pytest.mark.parametrize(
    ("region_type", "has_text"),
    [("unknown", True), ("unknown", False), ("text", False), ("table", False)],
)
def test_an_ambiguous_region_is_never_silently_guessed(
    region_type: str, has_text: bool
) -> None:
    """UNDECIDED is a request for a human, not a classification."""

    assert propose(region_type, has_text=has_text) is SourceKind.UNDECIDED


def test_a_table_keeps_text_semantics_rather_than_being_bent_into_a_visual() -> None:
    assert propose("table", has_text=True) is SourceKind.TEXT_ONLY


def test_figure_does_not_imply_visual_only() -> None:
    """The distinction is printed text, not the region type."""

    assert propose("figure", has_text=True) is not propose("figure", has_text=False)


# --- B. what each kind means --------------------------------------------------


def test_only_decorative_and_undecided_are_not_educational() -> None:
    educational = {k for k in SourceKind if k.educational}
    assert educational == {
        SourceKind.TEXT_ONLY,
        SourceKind.VISUAL_ONLY,
        SourceKind.VISUAL_WITH_TEXT,
    }


def test_only_text_only_requires_text_to_be_present() -> None:
    assert SourceKind.TEXT_ONLY.requires_text
    assert not SourceKind.VISUAL_ONLY.requires_text
    assert not SourceKind.VISUAL_WITH_TEXT.requires_text


# --- C. the embedding gate ----------------------------------------------------


def gate(kind: SourceKind, modality: Modality, **kwargs) -> None:
    defaults = {"verified": True, "has_verified_text": True, "has_canonical_visual": True}
    assert_embeddable(kind=kind, modality=modality, **(defaults | kwargs))


def test_unverified_text_cannot_be_embedded() -> None:
    with pytest.raises(EmbeddingRefusedError):
        gate(SourceKind.TEXT_ONLY, Modality.TEXT, verified=False)


def test_unverified_visual_cannot_be_embedded() -> None:
    with pytest.raises(EmbeddingRefusedError):
        gate(SourceKind.VISUAL_ONLY, Modality.IMAGE, verified=False)


def test_verified_visual_only_may_be_embedded_as_an_image() -> None:
    gate(SourceKind.VISUAL_ONLY, Modality.IMAGE, has_verified_text=False)


def test_verified_visual_only_may_never_be_embedded_as_text() -> None:
    """Describing a picture is derived knowledge, never source truth."""

    with pytest.raises(EmbeddingRefusedError) as error:
        gate(SourceKind.VISUAL_ONLY, Modality.TEXT, has_verified_text=False)
    assert "derived knowledge" in str(error.value)


def test_visual_with_text_is_eligible_for_both_modalities_separately() -> None:
    assert eligible_modalities(
        kind=SourceKind.VISUAL_WITH_TEXT,
        verified=True,
        has_verified_text=True,
        has_canonical_visual=True,
    ) == {Modality.TEXT, Modality.IMAGE}


def test_text_only_is_never_eligible_for_an_image_embedding() -> None:
    assert eligible_modalities(
        kind=SourceKind.TEXT_ONLY,
        verified=True,
        has_verified_text=True,
        has_canonical_visual=True,
    ) == {Modality.TEXT}


@pytest.mark.parametrize("modality", list(Modality))
def test_decorative_is_never_eligible_for_an_educational_embedding(modality) -> None:
    with pytest.raises(EmbeddingRefusedError):
        gate(SourceKind.DECORATIVE, modality)


@pytest.mark.parametrize("modality", list(Modality))
def test_an_undecided_region_is_never_embeddable(modality) -> None:
    with pytest.raises(EmbeddingRefusedError):
        gate(SourceKind.UNDECIDED, modality)


def test_a_visual_without_its_canonical_crop_cannot_be_embedded() -> None:
    with pytest.raises(EmbeddingRefusedError) as error:
        gate(SourceKind.VISUAL_ONLY, Modality.IMAGE, has_canonical_visual=False)
    assert "canonical crop" in str(error.value)


def test_a_verified_visual_with_no_image_vectoriser_is_still_valid() -> None:
    """Eligibility is not creation. A verified figure with no embedding yet
    is a correct state, not a gap to be filled with invented text."""

    assert Modality.IMAGE in eligible_modalities(
        kind=SourceKind.VISUAL_ONLY,
        verified=True,
        has_verified_text=False,
        has_canonical_visual=True,
    )

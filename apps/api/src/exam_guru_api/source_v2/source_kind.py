"""What kind of source a region actually is, and what may be embedded from it.

A page is not only prose. A diagram carries educational meaning with no printed
text at all, and throwing it away because `exact_text` is empty loses real
source content. D18 makes that distinction explicit so a figure can be verified
*as a figure*.

The machine may only ever **propose** a kind from deterministic evidence.
A human decides. See `docs/source-v2/DECISIONS.md` D18.
"""

from __future__ import annotations

from enum import StrEnum


class SourceKind(StrEnum):
    """The educational nature of a region, decided by a human."""

    TEXT_ONLY = "text_only"
    VISUAL_ONLY = "visual_only"
    VISUAL_WITH_TEXT = "visual_with_text"
    DECORATIVE = "decorative"
    #: The machine could not tell from geometry alone. Never auto-verified.
    UNDECIDED = "undecided"

    @property
    def educational(self) -> bool:
        """Does this region carry meaning a learner could be taught from?"""

        return self in {
            SourceKind.TEXT_ONLY,
            SourceKind.VISUAL_ONLY,
            SourceKind.VISUAL_WITH_TEXT,
        }

    @property
    def carries_text(self) -> bool:
        return self in {SourceKind.TEXT_ONLY, SourceKind.VISUAL_WITH_TEXT}

    @property
    def carries_visual(self) -> bool:
        return self in {SourceKind.VISUAL_ONLY, SourceKind.VISUAL_WITH_TEXT}

    @property
    def requires_text(self) -> bool:
        """Kinds where empty verified text would be a mistake, not a fact."""

        return self is SourceKind.TEXT_ONLY


class Modality(StrEnum):
    TEXT = "text"
    IMAGE = "image"


#: Region types the layout emits that are page furniture rather than content.
FURNITURE = frozenset({"decorative"})
#: Region types that are a picture of something.
PICTORIAL = frozenset({"figure"})


def propose(region_type: str, *, has_text: bool) -> SourceKind:
    """The machine's opening suggestion, from geometry and emptiness only.

    Deliberately narrow. `figure` does **not** imply `VISUAL_ONLY`: a labelled
    diagram is `VISUAL_WITH_TEXT`, and only the presence of printed text
    distinguishes them. Anything the geometry cannot settle is `UNDECIDED`,
    which a human must resolve rather than the machine guessing.
    """

    if region_type in {"text", "heading"}:
        # Prose with nothing in it is not a text region the machine understands;
        # ask a person rather than inventing a classification.
        return SourceKind.TEXT_ONLY if has_text else SourceKind.UNDECIDED
    if region_type in FURNITURE:
        return SourceKind.DECORATIVE
    if region_type in PICTORIAL:
        return SourceKind.VISUAL_WITH_TEXT if has_text else SourceKind.VISUAL_ONLY
    if region_type == "table":
        # A table is structured text. It keeps its existing text semantics
        # rather than being bent into a visual class to satisfy the enum.
        return SourceKind.TEXT_ONLY if has_text else SourceKind.UNDECIDED
    return SourceKind.UNDECIDED


class EmbeddingRefusedError(Exception):
    """A modality was requested that this region may not produce."""


def assert_embeddable(
    *,
    kind: SourceKind,
    modality: Modality,
    verified: bool,
    has_verified_text: bool,
    has_canonical_visual: bool,
) -> None:
    """The D18 gate: verification *and* modality must both permit this.

    Text and image are gated separately, because a region can legitimately be
    verified for one and not the other. Nothing here creates an embedding; it
    only decides whether one would be allowed, so a future vectoriser cannot
    quietly bypass the rule.
    """

    if not verified:
        raise EmbeddingRefusedError(
            f"{modality} embedding refused: the region is not verified source content"
        )
    if not kind.educational:
        raise EmbeddingRefusedError(
            f"{modality} embedding refused: {kind} is not educational source content"
        )

    if modality is Modality.TEXT:
        if not kind.carries_text:
            raise EmbeddingRefusedError(
                f"text embedding refused: {kind} has no verified source text. "
                "Describing the visual would be derived knowledge, not source."
            )
        if not has_verified_text:
            raise EmbeddingRefusedError(
                "text embedding refused: no verified text is stored for this region"
            )
        return

    if not kind.carries_visual:
        raise EmbeddingRefusedError(f"image embedding refused: {kind} has no source visual")
    if not has_canonical_visual:
        raise EmbeddingRefusedError(
            "image embedding refused: the canonical crop for this region is missing"
        )


def eligible_modalities(
    *,
    kind: SourceKind,
    verified: bool,
    has_verified_text: bool,
    has_canonical_visual: bool,
) -> frozenset[Modality]:
    """Which modalities this region could be embedded in, right now."""

    allowed: set[Modality] = set()
    for modality in Modality:
        try:
            assert_embeddable(
                kind=kind,
                modality=modality,
                verified=verified,
                has_verified_text=has_verified_text,
                has_canonical_visual=has_canonical_visual,
            )
        except EmbeddingRefusedError:
            continue
        allowed.add(modality)
    return frozenset(allowed)

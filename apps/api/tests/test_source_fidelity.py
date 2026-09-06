import inspect
import unicodedata
from collections.abc import Callable, MutableMapping
from dataclasses import FrozenInstanceError, fields, replace
from typing import cast

import pytest

from exam_guru_api.documents import fidelity
from exam_guru_api.documents.fidelity import (
    ALGORITHM_VERSION,
    MAX_TEXT_CHARACTERS,
    UNICODE_VERSION,
    FidelityLimitError,
    MissingOCRLanguagesError,
    PageAssessment,
    assess_page,
    measure_text_fidelity,
    normalize_source_text,
    select_ocr_languages,
)

SYNTHETIC_SINHALA = "1. ශ්\u200dරී ලංකා: 12 + 3 = 15; 2 \u00d7 4 = 8."
SYNTHETIC_TAMIL = "தமிழ்: 12 + 3 = 15."
SYNTHETIC_ENGLISH = "Choose the correct answer: 12 + 3 = 15."
SYNTHETIC_LATIN_GIBBERISH = "wOHdmk m%Yak m%Odk ms<s;=re"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("cafe\u0301", "café"),
        ("ක\u0dd9\u0dca", "ක\u0dda"),
        ("ක\u0dd9\u0dcf", "ක\u0ddc"),
        ("  a\r\n\t b  ", "  a\r\n\t b  "),
        ("\ufb01 \u2163 \u00b2 \uff11 \u00a0", "\ufb01 \u2163 \u00b2 \uff11 \u00a0"),
        ("අ\u0dcf", "අ\u0dcf"),
        ("\ud800\x00\ufffd\ue000", "\ud800\x00\ufffd\ue000"),
        (SYNTHETIC_SINHALA, SYNTHETIC_SINHALA),
        (SYNTHETIC_TAMIL, SYNTHETIC_TAMIL),
    ],
)
def test_normalization_is_nfc_only_and_idempotent(raw: str, expected: str) -> None:
    assert normalize_source_text(raw) == expected
    assert normalize_source_text(normalize_source_text(raw)) == expected


def test_algorithm_version_identifies_the_actual_unicode_database() -> None:
    assert unicodedata.unidata_version == UNICODE_VERSION
    assert f"source-fidelity-v1/ucd-{unicodedata.unidata_version}" == ALGORITHM_VERSION
    assert assess_page(SYNTHETIC_SINHALA).algorithm_version == ALGORITHM_VERSION
    assert measure_text_fidelity("a", "b").algorithm_version == ALGORITHM_VERSION


def test_assessment_contract_has_no_confidence_or_automatic_trust() -> None:
    page = assess_page(SYNTHETIC_SINHALA)
    assert {field.name for field in fields(page)} == {
        "normalized_text",
        "display_text",
        "languages",
        "script_counts",
        "risk_codes",
        "classifications",
        "recommended_route",
        "can_confirm",
        "algorithm_version",
    }
    assert "confidence" not in inspect.signature(assess_page).parameters
    assert page.recommended_route == "native_review"
    assert page.can_confirm
    assert page.normalized_text == SYNTHETIC_SINHALA
    assert page.display_text == SYNTHETIC_SINHALA
    assert page.risk_codes == ()
    assert page.classifications == ("native_unicode",)
    with pytest.raises(FrozenInstanceError):
        page.__setattr__("can_confirm", False)


def test_script_counts_are_immutable_snapshots_and_cover_normalized_codepoints() -> None:
    page = assess_page("කகA1 + \u0378")
    assert page.script_counts == {"sinhala": 1, "tamil": 1, "latin": 1, "other": 5}
    assert sum(page.script_counts.values()) == len("කகA1 + \u0378")
    source_counts = dict(page.script_counts)
    snapshot = replace(page, script_counts=source_counts)
    source_counts["sinhala"] = 99
    assert snapshot.script_counts["sinhala"] == 1
    with pytest.raises(TypeError):
        cast(MutableMapping[str, int], page.script_counts)["sinhala"] = 99


@pytest.mark.parametrize(
    ("character", "risk", "postgres_safe"),
    [
        ("\x00", "unsafe_control", False),
        ("\x01", "unsafe_control", True),
        ("\x1b", "unsafe_control", True),
        ("\x7f", "unsafe_control", True),
        ("\x85", "unsafe_control", True),
        ("\x9f", "unsafe_control", True),
        ("\ue000", "private_use", True),
        ("\U000f0000", "private_use", True),
        ("\U00100000", "private_use", True),
        ("\ufffd", "replacement_character", True),
        ("\u0378", "unassigned_codepoint", True),
        ("\ufdd0", "unassigned_codepoint", True),
        ("\ud800", "surrogate", False),
        (chr(0xDFFF), "surrogate", False),
    ],
)
def test_unsafe_text_has_risks_without_silent_authoritative_replacement(
    character: str, risk: str, postgres_safe: bool
) -> None:
    raw = f"A{character}B"
    page = assess_page(raw)
    assert page.normalized_text == (raw if postgres_safe else None)
    assert page.display_text == "A\ufffdB"
    assert page.display_text.encode("utf-8")
    assert risk in page.risk_codes
    assert "suspicious_encoding" in page.classifications
    assert page.recommended_route == "ocr_review"
    assert not page.can_confirm
    assert raw == f"A{character}B"


@pytest.mark.parametrize(
    "codepoint",
    [
        0x061C,
        0x200E,
        0x200F,
        0x202A,
        0x202B,
        0x202C,
        0x202D,
        0x202E,
        0x2066,
        0x2067,
        0x2068,
        0x2069,
    ],
)
def test_bidi_controls_are_review_risks_not_removed_from_the_candidate(codepoint: int) -> None:
    raw = f"12{chr(codepoint)}34"
    page = assess_page(raw)
    assert page.normalized_text == raw
    assert page.display_text == "12\ufffd34"
    assert "bidi_control" in page.risk_codes
    assert not page.can_confirm


def test_safe_whitespace_and_western_punctuation_digits_and_math_survive() -> None:
    raw = SYNTHETIC_SINHALA + "\r\n\t(A) 0.5, 25%: x\u00b2 \u2260 4; 8 \u00f7 2 = 4!"
    page = assess_page(raw)
    assert page.normalized_text == raw
    assert page.display_text == raw
    assert page.risk_codes == ()
    assert page.can_confirm


@pytest.mark.parametrize(
    "raw",
    [
        "ක\u0dca\u200dර",
        "ක\u200d\u0dcaක",
        "ර\u0dca\u200dක",
        "ක\u0dca\u200cර",
        "ක\u0dca\u200d",
        "க\u0bcd\u200dக",
        "க\u0bcd\u200cக",
        "\u0915\u094d\u200d\u0937",
        "\u0645\u06cc\u200c\u0631\u0648\u0645",
    ],
)
def test_meaningful_joiners_are_preserved_without_grammar_rewriting(raw: str) -> None:
    page = assess_page(raw)
    assert page.normalized_text == raw
    assert page.display_text == raw
    assert "misplaced_joiner" not in page.risk_codes
    assert page.can_confirm


def test_sinhala_joiner_orders_remain_distinct() -> None:
    after = "ක\u0dca\u200dක"
    before = "ක\u200d\u0dcaක"
    assert normalize_source_text(after) != normalize_source_text(before)
    assert measure_text_fidelity(after, before).nfc.character_edits > 0


@pytest.mark.parametrize(
    "raw",
    [
        "\u200dක",
        "ක\u200d",
        "ක\u200dර",
        "a\u200db",
        "\u200c",
        "1\u200c2",
        "ක \u200d\u0dcaර",
        "ක\u0dca\u200d\u200dර",
    ],
)
def test_clearly_misplaced_joiners_are_flagged_but_never_deleted(raw: str) -> None:
    page = assess_page(raw)
    assert page.normalized_text == raw
    assert "misplaced_joiner" in page.risk_codes
    assert not page.can_confirm


@pytest.mark.parametrize("raw", ["\u0dd2ක", "1\u0dd2", "a\u0dd2", "ක.\u0dd2", "\u0dca", "ක \u0dd2"])
def test_orphan_sinhala_marks_are_flagged(raw: str) -> None:
    page = assess_page(raw)
    assert "orphan_sinhala_mark" in page.risk_codes
    assert page.normalized_text == normalize_source_text(raw)
    assert not page.can_confirm


def test_orphan_tamil_marks_are_flagged() -> None:
    page = assess_page("\u0bc6க")
    assert "orphan_tamil_mark" in page.risk_codes
    assert not page.can_confirm


@pytest.mark.parametrize("raw", ["අ\u0dcf", "උ\u0dd6", "ඉ\u0dd2"])
def test_independent_sinhala_vowels_must_not_be_assembled_with_dependent_signs(raw: str) -> None:
    page = assess_page(raw)
    assert page.normalized_text == raw
    assert "invalid_sinhala_vowel_sequence" in page.risk_codes
    assert not page.can_confirm
    assert normalize_source_text("අ\u0dcf") != "ආ"


@pytest.mark.parametrize("raw", ["ආ", "අං", "ක\u0dd9\u0dca", "ක\u0dd9\u0dcf", "தமிழ்"])
def test_valid_vowels_and_combining_signs_are_not_rejected(raw: str) -> None:
    page = assess_page(raw)
    assert page.risk_codes == ()
    assert page.can_confirm


@pytest.mark.parametrize(
    ("raw", "expected_languages", "languages"),
    [
        (SYNTHETIC_SINHALA, (), ("si",)),
        (SYNTHETIC_TAMIL, (), ("ta",)),
        (SYNTHETIC_ENGLISH, (), ("en",)),
        (SYNTHETIC_SINHALA + SYNTHETIC_ENGLISH + SYNTHETIC_TAMIL, (), ("si", "en", "ta")),
        ("abc 123 + x = y", (), ("und",)),
        ("12. (A) x + y = 42.", (), ("und",)),
        ("Bonjour le monde", (), ("und",)),
        ("", ("ta",), ("ta",)),
        ("x + y = 2", ("si",), ("si",)),
        ("opaque latin words", ("en",), ("en",)),
        (SYNTHETIC_ENGLISH, ("si", "si", "und"), ("si", "en")),
        ("\U000111e1", (), ("si",)),
        ("\U00011fc0", (), ("ta",)),
    ],
)
def test_language_evidence_combines_scripts_metadata_and_conservative_english_cues(
    raw: str, expected_languages: tuple[str, ...], languages: tuple[str, ...]
) -> None:
    page = assess_page(raw, expected_languages=expected_languages)
    assert page.languages == languages
    assert ("mixed_language" in page.classifications) == (len(languages) > 1)
    assert ("language_undetermined" in page.risk_codes) == (languages == ("und",))


@pytest.mark.parametrize("language", ["si", "ta"])
def test_expected_local_language_latin_gibberish_is_soft_routing_evidence(language: str) -> None:
    page = assess_page(SYNTHETIC_LATIN_GIBBERISH, expected_languages=(language,))
    assert "latin_script_mismatch" in page.risk_codes
    assert "suspicious_encoding" in page.classifications
    assert page.recommended_route == "ocr_review"
    assert page.normalized_text == SYNTHETIC_LATIN_GIBBERISH
    assert page.can_confirm


@pytest.mark.parametrize(
    "raw",
    [SYNTHETIC_ENGLISH, SYNTHETIC_SINHALA + SYNTHETIC_ENGLISH, "f(x) = x + y; ABC = 123"],
)
def test_english_and_math_in_local_language_material_are_not_hard_rejected(raw: str) -> None:
    page = assess_page(raw, expected_languages=("si", "ta"))
    assert "latin_script_mismatch" not in page.risk_codes
    assert page.can_confirm
    assert page.recommended_route == "native_review"


@pytest.mark.parametrize(
    ("font_name", "language", "risk"),
    [
        ("FMAbhaya", "si", "legacy_font_sinhala"),
        ("dl-Manel", "si", "legacy_font_sinhala"),
        ("Kaputa", "si", "legacy_font_sinhala"),
        ("AMALEE", "si", "legacy_font_sinhala"),
        ("Thibus", "si", "legacy_font_sinhala"),
        ("NIESin", "si", "legacy_font_sinhala"),
        ("ABCDEF+FMAbhaya", "si", "legacy_font_sinhala"),
        ("abcdef+NIESin-Bold", "si", "legacy_font_sinhala"),
        ("Bamini", "ta", "legacy_font_tamil"),
        ("aBcDeF+Kalaham", "ta", "legacy_font_tamil"),
        ("NIETml-Bold", "ta", "legacy_font_tamil"),
    ],
)
def test_general_legacy_font_risks_are_not_a_claim_of_a_proven_mapping(
    font_name: str, language: str, risk: str
) -> None:
    raw = "opaque text"
    page = assess_page(raw, font_names=(font_name,))
    assert page.languages == (language,)
    assert risk in page.risk_codes
    assert "legacy_encoded" in page.classifications
    assert page.recommended_route == "ocr_review"
    assert page.normalized_text == raw
    assert page.can_confirm


@pytest.mark.parametrize(
    "font_name", ["NotoSansSinhala", "IskoolaPota", "Arial", "ABCDE+FMAbhaya", ""]
)
def test_other_font_names_do_not_prove_legacy_encoding(font_name: str) -> None:
    page = assess_page(SYNTHETIC_SINHALA, font_names=(font_name,))
    assert page.risk_codes == ()
    assert page.recommended_route == "native_review"


def test_font_evidence_is_deduplicated_and_never_rewrites_valid_unicode() -> None:
    page = assess_page(SYNTHETIC_SINHALA, font_names=("FMAbhaya", "Bamini", "FMAbhaya"))
    reordered = assess_page(SYNTHETIC_SINHALA, font_names=("Bamini", "FMAbhaya"))
    assert page == reordered
    assert page.normalized_text == SYNTHETIC_SINHALA
    assert page.languages == ("si", "ta")
    assert page.risk_codes == ("legacy_font_sinhala", "legacy_font_tamil")
    assert page.recommended_route == "ocr_review"
    assert page.can_confirm


@pytest.mark.parametrize("raw", ["", " \r\n\t", "\u200b", "\u0301"])
def test_empty_or_invisible_only_pages_cannot_be_confirmed(raw: str) -> None:
    page = assess_page(raw)
    assert "empty_text" in page.risk_codes
    assert not page.can_confirm
    assert page.recommended_route == "ocr_review"


def test_image_classification_distinguishes_embedded_images_and_sparse_scan_overlays() -> None:
    illustrated = assess_page(SYNTHETIC_SINHALA, image_coverage=0.2)
    assert illustrated.classifications == ("native_image", "native_unicode")
    assert illustrated.recommended_route == "native_review"
    scanned = assess_page("1.", image_coverage=0.8)
    assert "scanned_image" in scanned.classifications
    assert "sparse_native_overlay" in scanned.risk_codes
    assert scanned.recommended_route == "ocr_review"
    dense = assess_page(SYNTHETIC_SINHALA * 10, image_coverage=1.0)
    assert "native_image" in dense.classifications
    assert "scanned_image" not in dense.classifications
    assert dense.recommended_route == "native_review"
    empty = assess_page("", image_coverage=0.1)
    assert empty.classifications == ("scanned_image",)
    assert not empty.can_confirm


@pytest.mark.parametrize(("method", "route"), [("ocr", "ocr_review"), ("manual", "native_review")])
def test_ocr_and_manual_candidates_are_never_automatically_verified(
    method: str, route: str
) -> None:
    page = assess_page(
        SYNTHETIC_SINHALA, font_names=("FMAbhaya",), method=method, image_coverage=0.9
    )
    assert page.recommended_route == route
    assert page.risk_codes == ()
    assert "native_unicode" not in page.classifications
    assert page.can_confirm


@pytest.mark.parametrize(
    ("raw", "expected_languages", "selected"),
    [
        (SYNTHETIC_SINHALA, (), ("sin", "eng")),
        (SYNTHETIC_TAMIL, (), ("tam", "eng")),
        (SYNTHETIC_SINHALA + SYNTHETIC_TAMIL, (), ("sin", "tam", "eng")),
        (SYNTHETIC_ENGLISH, (), ("eng",)),
        ("", ("si", "ta"), ("sin", "tam", "eng")),
        (SYNTHETIC_LATIN_GIBBERISH, ("si",), ("sin", "eng")),
    ],
)
def test_ocr_languages_follow_all_evidence_and_always_include_needed_english(
    raw: str, expected_languages: tuple[str, ...], selected: tuple[str, ...]
) -> None:
    page = assess_page(raw, expected_languages=expected_languages)
    available = ("eng", "osd", "tam", "sin", "eng")
    assert select_ocr_languages(page, available_languages=available) == selected


@pytest.mark.parametrize(
    ("raw", "available", "missing"),
    [
        (SYNTHETIC_SINHALA, ("eng",), ("sin",)),
        (SYNTHETIC_TAMIL, ("eng", "sin"), ("tam",)),
        (SYNTHETIC_SINHALA, ("sin",), ("eng",)),
        (SYNTHETIC_TAMIL, ("tam",), ("eng",)),
        (SYNTHETIC_SINHALA + SYNTHETIC_TAMIL, (), ("sin", "tam", "eng")),
    ],
)
def test_missing_required_ocr_language_packs_raise_instead_of_silent_fallback(
    raw: str, available: tuple[str, ...], missing: tuple[str, ...]
) -> None:
    with pytest.raises(MissingOCRLanguagesError) as failure:
        select_ocr_languages(assess_page(raw), available_languages=available)
    assert failure.value.missing_languages == missing
    assert str(failure.value) == f"missing OCR languages: {', '.join(missing)}"


def test_unknown_language_uses_all_supported_available_packs_and_stays_in_review() -> None:
    page = assess_page("123 + x = y")
    assert page.languages == ("und",)
    assert select_ocr_languages(page, available_languages=("tam", "eng", "sin", "osd")) == (
        "sin",
        "tam",
        "eng",
    )
    assert select_ocr_languages(page, available_languages=("tam", "osd")) == ("tam",)
    assert page.recommended_route.endswith("_review")
    assert page.risk_codes == ("language_undetermined",)
    with pytest.raises(ValueError, match="no supported OCR languages"):
        select_ocr_languages(page, available_languages=("osd", "fra"))


@pytest.mark.parametrize(
    ("reference", "candidate", "character_edits", "word_edits", "cer", "wer"),
    [
        ("kitten", "sitting", 3, 1, 0.5, 1.0),
        ("abc", "abcd", 1, 1, 1 / 3, 1.0),
        ("a", "abcd", 3, 1, 3.0, 1.0),
        ("a", "a b c d", 6, 3, 6.0, 3.0),
        ("a b", "b a", 2, 2, 2 / 3, 1.0),
        ("abc", "", 3, 1, 1.0, 1.0),
        ("", "abc", 3, 1, None, None),
        ("", "", 0, 0, 0.0, 0.0),
        ("   ", "\t", 3, 0, 1.0, 0.0),
        ("ක", "කකකක", 3, 1, 3.0, 1.0),
    ],
)
def test_metrics_use_conventional_reference_denominators_with_explicit_empty_reference(
    reference: str,
    candidate: str,
    character_edits: int,
    word_edits: int,
    cer: float | None,
    wer: float | None,
) -> None:
    report = measure_text_fidelity(reference, candidate)
    assert report.raw == report.nfc
    assert report.raw.character_edits == character_edits
    assert report.raw.reference_characters == len(reference)
    assert report.raw.word_edits == word_edits
    assert report.raw.reference_words == len(reference.split())
    assert report.raw.cer == pytest.approx(cer) if cer is not None else report.raw.cer is None
    assert report.raw.wer == pytest.approx(wer) if wer is not None else report.raw.wer is None


def test_raw_and_nfc_metrics_remain_distinct_for_canonically_equivalent_text() -> None:
    reference = "ක\u0dda cafe\u0301"
    candidate = "ක\u0dd9\u0dca café"
    report = measure_text_fidelity(reference, candidate)
    assert report.raw.character_edits > 0
    assert report.raw.word_edits == 2
    assert report.raw.reference_characters == len(reference)
    assert report.nfc.reference_characters == len(normalize_source_text(reference))
    assert report.nfc.character_edits == 0
    assert report.nfc.word_edits == 0
    assert report.nfc.cer == 0.0
    assert report.nfc.wer == 0.0


@pytest.mark.parametrize(
    ("reference", "candidate"),
    [
        ("12 + 3 = 15", "12 - 3 = 15"),
        ("12", "21"),
        ("2 \u00d7 3 = 6", "2 x 3 = 6"),
        ("\u00bd", "1/2"),
        ("x\u00b2", "x2"),
        ("Answer!", "answer"),
        ("ක\u0dca\u200dර", "ක\u0dcaර"),
        ("தமிழ் 12", "தமிழ் 21"),
    ],
)
def test_numbers_operators_case_punctuation_and_joiners_are_not_normalized_away_in_metrics(
    reference: str, candidate: str
) -> None:
    report = measure_text_fidelity(reference, candidate)
    assert report.raw.character_edits > 0
    assert report.nfc.character_edits > 0
    assert report.nfc.word_edits > 0


def test_cer_preserves_whitespace_while_wer_uses_whitespace_delimited_tokens() -> None:
    report = measure_text_fidelity("a  b\r\n", "a b\n")
    assert report.raw.character_edits == 2
    assert report.nfc.character_edits == 2
    assert report.raw.word_edits == 0
    assert report.raw.wer == 0.0


def test_metrics_are_immutable_and_do_not_sanitize_the_measured_strings() -> None:
    report = measure_text_fidelity("A\x00\ud800B", "A\ufffd\ufffdB")
    assert report.raw.character_edits == 2
    assert report.nfc.character_edits == 2
    with pytest.raises(FrozenInstanceError):
        report.__setattr__("raw", report.nfc)
    with pytest.raises(FrozenInstanceError):
        report.raw.__setattr__("cer", 0.0)


def test_exact_edit_budget_boundary_raises_rather_than_returning_an_approximation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fidelity, "MAX_EDIT_CELLS", 6)
    assert measure_text_fidelity("abc", "ab").raw.character_edits == 1
    with pytest.raises(FidelityLimitError, match="edit-distance cell budget"):
        measure_text_fidelity("abc", "abd")


def test_linear_time_equal_and_empty_cases_do_not_need_a_quadratic_budget() -> None:
    maximum_text = "a" * MAX_TEXT_CHARACTERS
    assert measure_text_fidelity(maximum_text, maximum_text).raw.cer == 0.0
    assert measure_text_fidelity(maximum_text, "").raw.cer == 1.0
    assert measure_text_fidelity("", maximum_text).raw.cer is None


@pytest.mark.parametrize(
    "build",
    [
        lambda: assess_page("a" * (MAX_TEXT_CHARACTERS + 1)),
        lambda: measure_text_fidelity("a" * (MAX_TEXT_CHARACTERS + 1), ""),
        lambda: measure_text_fidelity("", "a" * (MAX_TEXT_CHARACTERS + 1)),
        lambda: assess_page("\u0344" * (MAX_TEXT_CHARACTERS // 2 + 1)),
        lambda: measure_text_fidelity(
            "\u0344" * (MAX_TEXT_CHARACTERS // 2 + 1), "\u0344" * (MAX_TEXT_CHARACTERS // 2 + 1)
        ),
    ],
)
def test_input_and_nfc_expansion_are_bounded(build: Callable[[], object]) -> None:
    with pytest.raises(FidelityLimitError, match="text character limit"):
        build()


@pytest.mark.parametrize("invalid", [None, 1, b"text"])
def test_text_apis_reject_non_strings(invalid: object) -> None:
    with pytest.raises(TypeError):
        normalize_source_text(cast(str, invalid))
    with pytest.raises(TypeError):
        assess_page(cast(str, invalid))
    with pytest.raises(TypeError):
        measure_text_fidelity(cast(str, invalid), "a")
    with pytest.raises(TypeError):
        measure_text_fidelity("a", cast(str, invalid))


@pytest.mark.parametrize("invalid", [-0.01, 1.01, float("nan"), float("inf"), True, "0.5"])
def test_invalid_image_coverage_is_not_silently_clamped(invalid: object) -> None:
    with pytest.raises(ValueError, match="image_coverage"):
        assess_page("a", image_coverage=cast(float, invalid))


@pytest.mark.parametrize(
    "build",
    [
        lambda: assess_page("a", expected_languages=("fra",)),
        lambda: assess_page("a", expected_languages=cast(tuple[str, ...], "si")),
        lambda: assess_page("a", expected_languages=cast(tuple[str, ...], (1,))),
        lambda: assess_page("a", expected_languages=("si",) * 129),
        lambda: assess_page("a", font_names=cast(tuple[str, ...], ["FMAbhaya"])),
        lambda: assess_page("a", font_names=cast(tuple[str, ...], (1,))),
        lambda: assess_page("a", font_names=("x" * 257,)),
        lambda: assess_page("a", font_names=("x",) * 129),
        lambda: assess_page("a", method="unknown"),
        lambda: select_ocr_languages(
            assess_page("a"), available_languages=cast(tuple[str, ...], "eng")
        ),
        lambda: select_ocr_languages(assess_page("a"), available_languages=("x" * 257,)),
        lambda: select_ocr_languages(assess_page("a"), available_languages=("eng",) * 129),
    ],
)
def test_routing_metadata_is_bounded_and_validated(build: Callable[[], object]) -> None:
    with pytest.raises(
        ValueError, match=r"expected_languages|font_names|method|available_languages"
    ):
        build()


def test_ocr_selection_requires_a_page_assessment() -> None:
    with pytest.raises(TypeError, match="PageAssessment"):
        select_ocr_languages(cast(PageAssessment, "si"), available_languages=("eng",))

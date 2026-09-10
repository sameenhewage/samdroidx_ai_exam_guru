import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

UNICODE_VERSION = unicodedata.unidata_version
ALGORITHM_VERSION = f"source-fidelity-v2/rules-2/ucd-{UNICODE_VERSION}"
MAX_TEXT_CHARACTERS = 100_000
MAX_EDIT_CELLS = 4_000_000
_LANGUAGE_ORDER = ("si", "en", "ta")
_OCR_LANGUAGES = (("si", "sin"), ("ta", "tam"), ("en", "eng"))
_SUBSET_PREFIX = re.compile(r"^[A-Za-z]{6}\+")
_LATIN_WORDS = re.compile(r"[A-Za-z]+")
_ENGLISH_CUES = frozenset(
    (
        "a",
        "an",
        "the",
        "and",
        "is",
        "are",
        "of",
        "to",
        "in",
        "for",
        "with",
        "this",
        "that",
        "what",
        "which",
        "how",
        "choose",
        "answer",
        "question",
        "read",
        "write",
        "find",
        "calculate",
        "number",
        "numbers",
        "total",
        "add",
        "subtract",
        "multiply",
        "divide",
        "sum",
        "correct",
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "english",
    )
)
_JOINERS = frozenset("\u200c\u200d")
_VIRAMAS = {"\u0dca": "sinhala", "\u0bcd": "tamil"}
_CONSONANTS = {"sinhala": (0x0D9A, 0x0DC6), "tamil": (0x0B95, 0x0BB9)}
_BIDI_CONTROLS = frozenset(
    {0x061C, 0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069}
)


class FidelityLimitError(ValueError):
    pass


class MissingOCRLanguagesError(ValueError):
    def __init__(self, missing_languages: tuple[str, ...]) -> None:
        self.missing_languages = missing_languages
        super().__init__(f"missing OCR languages: {', '.join(missing_languages)}")


@dataclass(frozen=True, slots=True)
class PageAssessment:
    normalized_text: str | None
    display_text: str
    languages: tuple[str, ...]
    script_counts: Mapping[str, int]
    risk_codes: tuple[str, ...]
    classifications: tuple[str, ...]
    recommended_route: str
    can_confirm: bool
    algorithm_version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "script_counts", MappingProxyType(dict(self.script_counts)))


@dataclass(frozen=True, slots=True)
class ErrorMetrics:
    character_edits: int
    reference_characters: int
    word_edits: int
    reference_words: int
    cer: float | None
    wer: float | None


@dataclass(frozen=True, slots=True)
class FidelityMetrics:
    raw: ErrorMetrics
    nfc: ErrorMetrics
    algorithm_version: str


def normalize_source_text(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _bounded_text(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if len(text) > MAX_TEXT_CHARACTERS:
        raise FidelityLimitError("text character limit exceeded")
    return text


def _validate_strings(values: tuple[str, ...], name: str) -> None:
    if (
        not isinstance(values, tuple)
        or len(values) > 128
        or any(not isinstance(value, str) or len(value) > 256 for value in values)
    ):
        raise ValueError(f"{name} must be a tuple of at most 128 strings of at most 256 characters")


def _script(character: str) -> str:
    codepoint = ord(character)
    if unicodedata.category(character) == "Cn":
        return "other"
    if 0x0D80 <= codepoint <= 0x0DFF or 0x111E0 <= codepoint <= 0x111FF:
        return "sinhala"
    if 0x0B80 <= codepoint <= 0x0BFF or 0x11FC0 <= codepoint <= 0x11FFF:
        return "tamil"
    if unicodedata.name(character, "").startswith("LATIN "):
        return "latin"
    return "other"


def _character_risk(character: str, category: str) -> str | None:
    if character == "\ufffd":
        return "replacement_character"
    if category == "Co":
        return "private_use"
    if category == "Cs":
        return "surrogate"
    if category == "Cn":
        return "unassigned_codepoint"
    if category == "Cc" and character not in "\t\n\r":
        return "unsafe_control"
    if ord(character) in _BIDI_CONTROLS:
        return "bidi_control"
    return None


def _is_consonant(character: str, script: str) -> bool:
    low, high = _CONSONANTS[script]
    return (
        bool(character)
        and low <= ord(character) <= high
        and unicodedata.category(character) == "Lo"
    )


def _misplaced_joiner(text: str, index: int) -> bool:
    previous = text[index - 1] if index else ""
    following = text[index + 1] if index + 1 < len(text) else ""
    if previous in _JOINERS or following in _JOINERS:
        return True
    if previous in _VIRAMAS and index >= 2 and _is_consonant(text[index - 2], _VIRAMAS[previous]):
        return False
    if following == "\u0dca" and text[index] == "\u200d" and _is_consonant(previous, "sinhala"):
        return False
    if not previous or not following:
        return True
    if _script(previous) in {"sinhala", "tamil", "latin"} or _script(following) in {
        "sinhala",
        "tamil",
        "latin",
    }:
        return True
    return any(
        unicodedata.category(character)[0] not in "LMS" for character in (previous, following)
    )


def _inspect_text(text: str) -> tuple[dict[str, int], set[str], str, bool]:
    counts = dict.fromkeys(("sinhala", "tamil", "latin", "other"), 0)
    risks: set[str] = set()
    display: list[str] = []
    base, base_script = "", ""
    has_content = False
    for index, character in enumerate(text):
        category = unicodedata.category(character)
        script = _script(character)
        counts[script] += 1
        risk = _character_risk(character, category)
        if risk:
            risks.add(risk)
        display.append("\ufffd" if risk else character)
        has_content = has_content or category[0] in "LNPS"
        if character in _JOINERS:
            if _misplaced_joiner(text, index):
                risks.add("misplaced_joiner")
        elif category[0] == "M":
            if script in {"sinhala", "tamil"} and base_script != script:
                risks.add(f"orphan_{script}_mark")
            if (
                script == "sinhala"
                and "\u0d85" <= base <= "\u0d96"
                and character not in "\u0d82\u0d83"
            ):
                risks.add("invalid_sinhala_vowel_sequence")
        elif category[0] == "L":
            base, base_script = character, script
        else:
            base, base_script = "", ""
    return counts, risks, "".join(display), has_content


def _font_languages(font_names: tuple[str, ...]) -> set[str]:
    languages: set[str] = set()
    for name in font_names:
        family = _SUBSET_PREFIX.sub("", name).casefold()
        if family.startswith(("fm", "dl", "kaputa", "amalee", "thibus", "niesin")):
            languages.add("si")
        elif family.startswith(("bamini", "kalaham", "nietml")):
            languages.add("ta")
    return languages


def _latin_corruption(text: str) -> bool:
    if re.search(r"(?<![A-Za-z0-9])\.=[A-Za-z]{2,}", text):
        return True
    for carrier in re.findall(r"[A-Za-z\u00c0-\u024f][A-Za-z\u00c0-\u024f%<;^|=]*", text):
        letters = "".join(character for character in carrier if character.isalpha())
        if len(letters) < 3 or letters.isupper():
            continue
        parts = re.split(r"[%<;^|=]+", carrier)
        if (
            re.search(r"[A-Za-z][%;][A-Za-z]", carrier)
            and any(len(part) > 1 for part in parts)
            and any(len(part) == 1 for part in parts)
            and any(part and not set(part.casefold()) & set("aeiouy") for part in parts)
        ):
            return True
        if any(character.isupper() and ord(character) > 127 for character in letters[1:]):
            return True
        if any(len(part) >= 5 and not set(part.casefold()) & set("aeiouy") for part in parts):
            return True
        interior_capitals = re.search(r"[a-z][A-Z]{2,}([a-z]+)$", letters)
        if (
            len(letters) >= 5
            and interior_capitals is not None
            and interior_capitals[1] != "s"
            and not set(interior_capitals[1]) & set("aeiouy")
        ):
            return True
    return False


def assess_page(
    text: str,
    *,
    expected_languages: tuple[str, ...] = (),
    font_names: tuple[str, ...] = (),
    image_coverage: float = 0.0,
    method: str = "native",
) -> PageAssessment:
    _bounded_text(text)
    _validate_strings(expected_languages, "expected_languages")
    _validate_strings(font_names, "font_names")
    if not set(expected_languages) <= {*_LANGUAGE_ORDER, "und"}:
        raise ValueError("expected_languages must use si, en, ta or und")
    if not isinstance(method, str) or method not in {"native", "ocr", "manual"}:
        raise ValueError("method must be native, ocr or manual")
    if (
        not isinstance(image_coverage, (int, float))
        or isinstance(image_coverage, bool)
        or not math.isfinite(image_coverage)
        or not 0.0 <= image_coverage <= 1.0
    ):
        raise ValueError("image_coverage must be finite and between zero and one")
    normalized = _bounded_text(normalize_source_text(text))
    counts, risks, display, has_content = _inspect_text(normalized)
    structurally_safe = not risks
    font_languages = _font_languages(font_names)
    evidence = set(expected_languages) | font_languages
    words = [word.lower() for word in _LATIN_WORDS.findall(normalized)]
    english = ("en" in evidence and counts["latin"] > 0) or len(set(words) & _ENGLISH_CUES) >= 2
    for script, language in (("sinhala", "si"), ("tamil", "ta")):
        if counts[script]:
            evidence.add(language)
    if english:
        evidence.add("en")
    languages = tuple(language for language in _LANGUAGE_ORDER if language in evidence) or ("und",)
    local_source = bool(evidence & {"si", "ta"})
    latin_corruption = _latin_corruption(normalized)
    mixed_corruption = bool(counts["sinhala"] + counts["tamil"]) and latin_corruption
    latin_mismatch = not counts["sinhala"] + counts["tamil"] and (
        latin_corruption
        or (
            local_source
            and counts["latin"] >= 16
            and sum(len(word) >= 3 for word in words) >= 3
            and not english
        )
    )
    expected_local = set(expected_languages) & {"si", "ta"}
    source_script_missing = (
        method in {"ocr", "manual"}
        and bool(expected_local)
        and not any(
            counts["sinhala" if language == "si" else "tamil"] for language in expected_local
        )
        and (
            counts["sinhala"] + counts["tamil"] > 0
            or (counts["latin"] >= 8 and any(len(word) >= 3 for word in words))
        )
    )
    native_font_risk = method == "native" and bool(font_languages)
    sparse_overlay = (
        method == "native"
        and has_content
        and image_coverage >= 0.8
        and len(normalized.strip()) < 200
    )
    classifications: set[str] = set()
    if not has_content:
        risks.add("empty_text")
    if languages == ("und",):
        risks.add("language_undetermined")
    if latin_mismatch:
        risks.add("latin_script_mismatch")
    if mixed_corruption:
        risks.add("mixed_script_corruption")
    if source_script_missing:
        risks.add("source_script_missing")
    if native_font_risk:
        for language, script in (("si", "sinhala"), ("ta", "tamil")):
            if language in font_languages:
                risks.add(f"legacy_font_{script}")
        classifications.add("legacy_encoded")
    fidelity_blocked = (
        native_font_risk
        or latin_mismatch
        or mixed_corruption
        or source_script_missing
        or sparse_overlay
    )
    if not structurally_safe or latin_mismatch or mixed_corruption or source_script_missing:
        classifications.add("suspicious_encoding")
    if sparse_overlay:
        risks.add("sparse_native_overlay")
    if image_coverage > 0:
        scanned = not has_content or sparse_overlay or method == "ocr"
        classifications.add("scanned_image" if scanned else "native_image")
    if method == "native" and has_content and structurally_safe and not fidelity_blocked:
        classifications.add("native_unicode")
    if len(languages) > 1:
        classifications.add("mixed_language")
    needs_ocr_review = (
        method == "ocr" or not has_content or not structurally_safe or fidelity_blocked
    )
    return PageAssessment(
        normalized_text=None if "\x00" in normalized or "surrogate" in risks else normalized,
        display_text=display,
        languages=languages,
        script_counts=counts,
        risk_codes=tuple(sorted(risks)),
        classifications=tuple(sorted(classifications)),
        recommended_route="ocr_review" if needs_ocr_review else "native_review",
        can_confirm=has_content and structurally_safe and not fidelity_blocked,
        algorithm_version=ALGORITHM_VERSION,
    )


def select_ocr_languages(
    assessment: PageAssessment, *, available_languages: tuple[str, ...]
) -> tuple[str, ...]:
    if not isinstance(assessment, PageAssessment):
        raise TypeError("assessment must be a PageAssessment")
    _validate_strings(available_languages, "available_languages")
    evidence = set(assessment.languages) & set(_LANGUAGE_ORDER)
    if evidence:
        required = tuple(
            code for language, code in _OCR_LANGUAGES if language in evidence or code == "eng"
        )
        missing = tuple(code for code in required if code not in available_languages)
        if missing:
            raise MissingOCRLanguagesError(missing)
        return required
    selected = tuple(code for _, code in _OCR_LANGUAGES if code in available_languages)
    if not selected:
        raise ValueError("no supported OCR languages are available")
    return selected


def _edit_distance(reference: Sequence[str], candidate: Sequence[str]) -> int:
    if reference == candidate:
        return 0
    if not reference:
        return len(candidate)
    if not candidate:
        return len(reference)
    if len(reference) * len(candidate) > MAX_EDIT_CELLS:
        raise FidelityLimitError("edit-distance cell budget exceeded")
    if len(reference) < len(candidate):
        reference, candidate = candidate, reference
    previous = list(range(len(candidate) + 1))
    for row, reference_item in enumerate(reference, 1):
        current = [row]
        for column, candidate_item in enumerate(candidate, 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (reference_item != candidate_item),
                )
            )
        previous = current
    return previous[-1]


def _error_rate(edits: int, reference_count: int, candidate_count: int) -> float | None:
    if reference_count:
        return edits / reference_count
    return 0.0 if not candidate_count else None


def _error_metrics(reference: str, candidate: str) -> ErrorMetrics:
    reference_words, candidate_words = reference.split(), candidate.split()
    character_edits = _edit_distance(reference, candidate)
    word_edits = _edit_distance(reference_words, candidate_words)
    return ErrorMetrics(
        character_edits=character_edits,
        reference_characters=len(reference),
        word_edits=word_edits,
        reference_words=len(reference_words),
        cer=_error_rate(character_edits, len(reference), len(candidate)),
        wer=_error_rate(word_edits, len(reference_words), len(candidate_words)),
    )


def measure_text_fidelity(reference: str, candidate: str) -> FidelityMetrics:
    _bounded_text(reference)
    _bounded_text(candidate)
    nfc_reference = _bounded_text(normalize_source_text(reference))
    nfc_candidate = _bounded_text(normalize_source_text(candidate))
    return FidelityMetrics(
        raw=_error_metrics(reference, candidate),
        nfc=_error_metrics(nfc_reference, nfc_candidate),
        algorithm_version=ALGORITHM_VERSION,
    )

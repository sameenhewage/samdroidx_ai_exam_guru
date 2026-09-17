import hashlib
import math
import re
import unicodedata
from collections.abc import Mapping
from difflib import SequenceMatcher
from typing import Literal, Self

from pydantic import Field, model_validator

from exam_guru_api.documents.fidelity import _inspect_text, _latin_corruption
from exam_guru_api.documents.page_images import (
    PageImageLimits,
    SourceCandidateImageMetadata,
    _png_dimensions,
)
from exam_guru_api.documents.source_reading import SourceLayoutRegion, SourceRegionReading
from exam_guru_api.documents.understanding_contracts import (
    Key,
    ShortText,
    UnderstandingModel,
    _canonical_bytes,
)
from exam_guru_api.documents.understanding_verification import Checksum, PageArtifactIdentity
from exam_guru_api.generation.domain import GenerationAccounting

_TOKEN = re.compile(
    r"https?://\S+|www\.\S+|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
    r"|[0-9]+(?:[.,][0-9]+)*|[\u0d80-\u0dff\u0b80-\u0bff]+|[^\W\d_]+|[^\s]"
)
_EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![A-Za-z0-9])"
)
_URL = re.compile(r"(?:https?://|www\.)[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?:/[^\s]*)?")
_OPERATORS = frozenset("\u00d7Xx÷+\u2212-=<>%/")


class SourceWitnessRecordingError(RuntimeError):
    pass


class SourceExecutionBlockedError(RuntimeError):
    def __init__(self, code: str, *, accounting: GenerationAccounting | None = None) -> None:
        if code not in {
            "credit_balance_exhausted",
            "insufficient_quota",
            "qwen_runtime_unavailable",
            "qwen_runtime_mismatch",
            "qwen_model_missing",
            "qwen_model_digest_mismatch",
        }:
            raise ValueError("unsupported source execution blocker")
        self.code = code
        self.accounting = accounting
        super().__init__(code)


class SourceRegionInput(UnderstandingModel):
    source: PageArtifactIdentity
    region: SourceLayoutRegion
    image_png: bytes = Field(min_length=8, max_length=8 * 1024 * 1024, repr=False, exclude=True)
    image_sha256: Checksum
    render_dpi: int = Field(ge=72, le=600)
    render_metadata: SourceCandidateImageMetadata | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    purpose: Literal["text", "math", "url", "email", "cell", "visual", "unknown"]
    language_hint: Literal["si", "ta", "en", "mixed", "und"]
    blank_expected: bool = False

    @model_validator(mode="after")
    def bound_image(self) -> Self:
        if hashlib.sha256(self.image_png).hexdigest() != self.image_sha256:
            raise ValueError("source reading image differs from its identity")
        dimensions = _png_dimensions(
            self.image_png,
            PageImageLimits(max_png_bytes=8 * 1024 * 1024, max_pixels=16_000_000),
        )
        if self.render_metadata is not None:
            metadata = SourceCandidateImageMetadata.model_validate(
                self.render_metadata.model_dump(mode="json")
            )
            left, top = self.crop_coordinates
            if (
                metadata.document_id != str(self.source.document_id)
                or metadata.source_checksum_sha256 != self.source.source_sha256
                or metadata.page_number != self.source.page_number
                or metadata.dpi != self.render_dpi
                or dimensions
                != (
                    math.ceil(self.region.bounds.right * metadata.width) - left,
                    math.ceil(self.region.bounds.bottom * metadata.height) - top,
                )
            ):
                raise ValueError("source crop does not match its exact render revision")
        return self

    @property
    def crop_coordinates(self) -> tuple[int, int]:
        if self.render_metadata is None:
            return 0, 0
        return (
            math.floor(self.region.bounds.left * self.render_metadata.width),
            math.floor(self.region.bounds.top * self.render_metadata.height),
        )

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(_canonical_bytes(self)).hexdigest()


class ReaderIdentity(UnderstandingModel):
    reader: Literal["qwen", "openai"]
    provider: Literal["ollama", "openai"]
    model: str = Field(min_length=1, max_length=128)
    model_version: str = Field(min_length=1, max_length=128)
    prompt_version: str = Field(min_length=1, max_length=128)
    configuration_fingerprint: Checksum

    @model_validator(mode="after")
    def independent_provider(self) -> Self:
        if (self.reader == "qwen") != (self.provider == "ollama"):
            raise ValueError("reader identity does not match its provider")
        return self


class IndependentReading(UnderstandingModel):
    reader: ReaderIdentity
    input_fingerprint: Checksum
    content: SourceRegionReading = Field(repr=False)
    input_tokens: int = Field(ge=0, le=10_000_000)
    output_tokens: int = Field(ge=0, le=10_000_000)
    latency_ms: int = Field(ge=0, le=3_600_000)
    cost_microusd: int = Field(ge=0, le=100_000_000)

    @model_validator(mode="after")
    def local_is_not_billable(self) -> Self:
        if self.reader.reader == "qwen" and self.cost_microusd != 0:
            raise ValueError("local reading must not acquire OpenAI billing")
        return self

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(_canonical_bytes(self)).hexdigest()


class SourceTokenDifference(UnderstandingModel):
    kind: Literal["text", "number", "operator", "url", "email", "structure"]
    line: int = Field(ge=0)
    left: str = Field(max_length=4000)
    right: str = Field(max_length=4000)


class RegionConsensus(UnderstandingModel):
    source: PageArtifactIdentity
    region: SourceLayoutRegion
    region_key: Key
    input_fingerprint: Checksum
    state: Literal["validated", "disagreed", "resolved_by_reread", "unresolved"]
    selected: SourceRegionReading | None = Field(repr=False)
    witness_fingerprints: tuple[Checksum, ...] = Field(min_length=2, max_length=2)
    differences: tuple[SourceTokenDifference, ...] = Field(max_length=2048)
    findings: tuple[ShortText, ...] = Field(max_length=64)
    previous_fingerprint: Checksum | None = None
    human_verified: Literal[False] = False

    @model_validator(mode="after")
    def machine_only(self) -> Self:
        if (self.state in {"validated", "resolved_by_reread"}) != (self.selected is not None):
            raise ValueError("unresolved consensus cannot select source text")
        return self

    @property
    def requires_reread(self) -> bool:
        return self.state in {"disagreed", "unresolved"}

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(_canonical_bytes(self)).hexdigest()


def _tokens(value: str) -> list[tuple[str, int]]:
    if len(value) > 100_000:
        raise ValueError("source alignment text exceeds its bound")
    return [
        (token, line)
        for line, text in enumerate(unicodedata.normalize("NFC", value).splitlines())
        for token in _TOKEN.findall(text)
    ]


def _kind(
    left: str, right: str
) -> Literal["text", "number", "operator", "url", "email", "structure"]:
    values = (left, right)
    if any("@" in value for value in values):
        return "email"
    if any(value.startswith(("www.", "http://", "https://")) for value in values):
        return "url"
    if any(value in _OPERATORS for value in values if value):
        return "operator"
    if any(value and value[0].isdigit() for value in values):
        return "number"
    return "text"


class SourceVariant(UnderstandingModel):
    value: str = Field(max_length=4000)
    providers: tuple[ShortText, ...] = Field(min_length=1, max_length=8)


class SourceDisagreementCell(UnderstandingModel):
    kind: Literal["text", "number", "operator", "url", "email", "structure"]
    line: int = Field(ge=0)
    token_index: int = Field(ge=0)
    variants: tuple[SourceVariant, ...] = Field(min_length=2, max_length=8)
    critical: bool
    character_positions: tuple[int, ...] = Field(max_length=4000)


class SourceDisagreementMap(UnderstandingModel):
    providers: tuple[ShortText, ...] = Field(min_length=1, max_length=8)
    missing_providers: tuple[ShortText, ...] = Field(max_length=8)
    cells: tuple[SourceDisagreementCell, ...] = Field(max_length=2048)
    critical_conflict: bool
    agreement_ratio: float = Field(ge=0.0, le=1.0)


def _columns(
    anchor: list[tuple[str, int]], other: list[tuple[str, int]]
) -> list[tuple[int | None, int | None]]:
    """Aligned columns that keep a substitution paired.

    A replaced token must line up with the token it replaced, otherwise `476` against
    `470` looks like a whole missing token instead of one wrong digit.
    """
    left = [token for token, _ in anchor]
    right = [token for token, _ in other]
    pairs: list[tuple[int | None, int | None]] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(a=left, b=right, autojunk=False).get_opcodes():
        if tag == "equal":
            pairs.extend((i, j) for i, j in zip(range(i1, i2), range(j1, j2), strict=True))
            continue
        if tag == "replace":
            shared = min(i2 - i1, j2 - j1)
            pairs.extend((i1 + k, j1 + k) for k in range(shared))
            pairs.extend((i, None) for i in range(i1 + shared, i2))
            pairs.extend((None, j) for j in range(j1 + shared, j2))
            continue
        pairs.extend((i, None) for i in range(i1, i2))
        pairs.extend((None, j) for j in range(j1, j2))
    return pairs


def build_disagreement_map(readings: Mapping[str, str]) -> SourceDisagreementMap:
    """Align every witness at token level, then at character level inside a conflict.

    One provider disagreeing about one operator must pin that operator, never make the
    whole region uncertain. Empty readings are recorded as missing, never invented.
    """
    if not readings:
        raise ValueError("a disagreement map needs at least one witness")
    providers = tuple(sorted(readings))
    missing = tuple(name for name in providers if not readings[name].strip())
    present = [name for name in providers if name not in missing]
    tokens = {name: _tokens(readings[name]) for name in present}
    if len(present) < 2:
        return SourceDisagreementMap(
            providers=providers,
            missing_providers=missing,
            cells=(),
            critical_conflict=False,
            agreement_ratio=1.0,
        )
    # Anchor on the most common token length so a single runaway reading cannot
    # redefine the region's shape.
    anchor_name = max(present, key=lambda name: (len(tokens[name]), name))
    anchor = tokens[anchor_name]
    slots: list[dict[str, str]] = [{} for _ in range(len(anchor))]
    trailing: dict[int, dict[str, str]] = {}
    for name in present:
        if name == anchor_name:
            for index, (token, _) in enumerate(anchor):
                slots[index][name] = token
            continue
        for left, right in _columns(anchor, tokens[name]):
            if left is not None:
                slots[left][name] = tokens[name][right][0] if right is not None else ""
            elif right is not None:
                trailing.setdefault(len(slots), {})[name] = tokens[name][right][0]
    cells: list[SourceDisagreementCell] = []
    for index, slot in enumerate([*slots, trailing.get(len(slots), {})]):
        if not slot:
            continue
        filled = {name: slot.get(name, "") for name in present}
        if len(set(filled.values())) < 2:
            continue
        grouped: dict[str, list[str]] = {}
        for name, value in sorted(filled.items()):
            grouped.setdefault(value, []).append(name)
        competing = [value for value in grouped if value]
        kind = _kind(competing[0], competing[-1] if len(competing) > 1 else competing[0])
        longest = max(len(value) for value in grouped)
        positions = tuple(
            position
            for position in range(longest)
            if len({value[position : position + 1] for value in grouped}) > 1
        )
        cells.append(
            SourceDisagreementCell(
                kind=kind,
                line=anchor[index][1] if index < len(anchor) else anchor[-1][1],
                token_index=index,
                variants=tuple(
                    SourceVariant(value=value, providers=tuple(names))
                    for value, names in sorted(grouped.items())
                ),
                critical=kind in {"number", "operator", "url", "email"},
                character_positions=positions,
            )
        )
    compared = max(len(slots), 1)
    return SourceDisagreementMap(
        providers=providers,
        missing_providers=missing,
        cells=tuple(cells),
        critical_conflict=any(cell.critical for cell in cells),
        agreement_ratio=round(max(0.0, compared - len(cells)) / compared, 4),
    )


def align_source_tokens(left: str, right: str) -> tuple[SourceTokenDifference, ...]:
    a, b = _tokens(left), _tokens(right)
    if len(a) + len(b) > 20_000:
        raise ValueError("source alignment token budget exceeded")
    differences: list[SourceTokenDifference] = []
    alignment = SequenceMatcher(None, [t[0] for t in a], [t[0] for t in b], autojunk=False)
    for operation, first, end, second, stop in alignment.get_opcodes():
        if operation == "equal":
            if [item[1] for item in a[first:end]] != [item[1] for item in b[second:stop]]:
                differences.append(
                    SourceTokenDifference(
                        kind="structure",
                        line=a[first][1],
                        left="source_line_boundaries",
                        right="different_line_boundaries",
                    )
                )
            continue
        left_text = " ".join(item[0] for item in a[first:end])
        right_text = " ".join(item[0] for item in b[second:stop])
        if max(len(left_text), len(right_text)) > 4000:
            raise ValueError("source disagreement span exceeds its bound")
        differences.append(
            SourceTokenDifference(
                kind=_kind(left_text, right_text),
                line=a[first][1] if first < len(a) else b[second][1] if second < len(b) else 0,
                left=left_text,
                right=right_text,
            )
        )
    return tuple(differences)


def validate_source_region(
    source: SourceRegionInput, reading: SourceRegionReading
) -> tuple[str, ...]:
    findings: list[str] = []
    text = reading.exact_text
    values = [text, *reading.equations]
    if reading.table:
        values.extend(cell.exact_text for row in reading.table.rows for cell in row)
        if any(cell.state == "unreadable" for row in reading.table.rows for cell in row):
            findings.append("unreadable_table_cell")
    if any(_inspect_text(value)[1] or _latin_corruption(value) for value in values):
        findings.append("unsafe_source_text")
    if (
        not text.strip()
        and not reading.equations
        and reading.table is None
        and not source.blank_expected
        and not (source.purpose == "visual" and reading.visual_facts)
    ):
        findings.append("missing_source_content")
    if source.blank_expected and (text.strip() or reading.equations or reading.visual_facts):
        findings.append("blank_source_was_filled")
    if text.strip().lower() in {
        "unreadable",
        "uncertain",
        "cannot read",
        "could not read",
        "unknown",
    }:
        findings.append("placeholder_is_not_transcription")
    if source.purpose == "text" and source.language_hint in {"si", "ta"}:
        counts = _inspect_text(text)[0]
        expected = "sinhala" if source.language_hint == "si" else "tamil"
        contact = (
            _EMAIL.fullmatch(text.strip()) is not None or _URL.fullmatch(text.strip()) is not None
        )
        if counts["latin"] and not counts[expected] and not contact:
            findings.append("source_script_missing")
    if source.purpose == "email" and not _EMAIL.search(text):
        findings.append("invalid_source_email")
    if source.purpose == "url" and not _URL.search(text):
        findings.append("invalid_source_url")
    if source.purpose == "math" and any(re.search(r"\d\s*[xX]\s*\d", value) for value in values):
        findings.append("ambiguous_multiplication_operator")
    if reading.uncertainties:
        findings.append("reader_uncertainty")
    return tuple(dict.fromkeys(findings))


def reconcile_region(
    source: SourceRegionInput,
    qwen: IndependentReading,
    openai: IndependentReading,
    *,
    previous: RegionConsensus | None = None,
) -> RegionConsensus:
    source = SourceRegionInput.model_validate(source)
    qwen = IndependentReading.model_validate(qwen)
    openai = IndependentReading.model_validate(openai)
    if qwen.reader.reader != "qwen" or openai.reader.reader != "openai":
        raise ValueError("consensus requires independent Qwen and OpenAI witnesses")
    if (
        qwen.input_fingerprint != source.fingerprint
        or openai.input_fingerprint != source.fingerprint
    ):
        raise ValueError("source witnesses belong to different page/render inputs")
    if previous is not None:
        previous = RegionConsensus.model_validate(previous)
        if previous.source != source.source or previous.region != source.region:
            raise ValueError("reread belongs to another source page or region")
    differences = list(align_source_tokens(qwen.content.exact_text, openai.content.exact_text))
    a = qwen.content.model_dump(exclude={"exact_text", "uncertainties"})
    b = openai.content.model_dump(exclude={"exact_text", "uncertainties"})
    if a != b:
        differences.append(
            SourceTokenDifference(
                kind="structure", line=0, left="qwen_structure", right="openai_structure"
            )
        )
    findings = tuple(
        dict.fromkeys(
            (
                *validate_source_region(source, qwen.content),
                *validate_source_region(source, openai.content),
            )
        )
    )
    selected = None
    state: Literal["validated", "disagreed", "resolved_by_reread", "unresolved"]
    if differences:
        state = "disagreed"
    elif findings:
        state = "unresolved"
    else:
        state = "validated" if previous is None else "resolved_by_reread"
        selected = SourceRegionReading.model_validate(qwen.content)
    return RegionConsensus(
        source=source.source,
        region=source.region,
        region_key=source.region.key,
        input_fingerprint=source.fingerprint,
        state=state,
        selected=selected,
        witness_fingerprints=(qwen.fingerprint, openai.fingerprint),
        differences=tuple(differences),
        findings=findings,
        previous_fingerprint=None if previous is None else previous.fingerprint,
    )

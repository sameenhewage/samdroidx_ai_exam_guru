import asyncio
import json
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.documents import fidelity_service
from exam_guru_api.documents.fidelity_models import PageReviewStateModel
from exam_guru_api.documents.fidelity_service import (
    FidelitySourceNotFoundError,
    PageFidelityConflictError,
    PageFidelityService,
)
from exam_guru_api.documents.models import SourceDocumentModel

ACTOR = UUID(int=87001)
DOCUMENT = UUID(int=87002)


def source(*, pages: int | None = 2) -> SourceDocumentModel:
    return SourceDocumentModel(
        id=DOCUMENT,
        original_page_count=pages,
        checksum_sha256="a" * 64,
        active_for_ai=True,
    )


@pytest.mark.parametrize("value", [None, 1, "", "  ", "x" * 2001])
def test_review_reason_requires_a_bounded_nonempty_string(value: object) -> None:
    with pytest.raises(ValueError, match="bounded review reason"):
        fidelity_service._reason(cast(str, value))


@pytest.mark.parametrize("character", ["\x00", "\t", "\n", "\r", "\x1b", "\x7f", "\ud800"])
def test_review_reason_cannot_smuggle_control_characters(character: str) -> None:
    with pytest.raises(ValueError, match="unsafe characters"):
        fidelity_service._reason(f"Compared{character}the source")


@pytest.mark.parametrize("reason", ["ග", "ග" * 2000, "  Compared the original  "])
def test_review_reason_keeps_unicode_and_trims_only_outer_spaces(reason: str) -> None:
    assert fidelity_service._reason(reason) == reason.strip()


def test_fidelity_metadata_round_trips_a_detached_json_snapshot_at_the_byte_bound() -> None:
    overhead = len(json.dumps({"evidence": ""}).encode())
    metadata: dict[str, object] = {"evidence": "x" * (65536 - overhead)}
    saved = fidelity_service._metadata(metadata)
    assert saved == metadata
    assert saved is not metadata
    assert len(json.dumps(saved).encode()) == 65536
    with pytest.raises(ValueError, match="metadata exceeds its bound"):
        fidelity_service._metadata({"evidence": cast(str, metadata["evidence"]) + "x"})
    with pytest.raises(ValueError, match="metadata exceeds its bound"):
        fidelity_service._metadata({"evidence": "ග" * 22000})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_fidelity_metadata_rejects_non_json_numbers(value: float) -> None:
    with pytest.raises(ValueError, match="JSON compliant"):
        fidelity_service._metadata({"confidence": value})


def test_fidelity_metadata_does_not_retain_mutable_provider_lists() -> None:
    languages = ["si", "en"]
    saved = fidelity_service._metadata({"languages": languages})
    languages.append("ta")
    assert saved == {"languages": ["si", "en"]}
    assert fidelity_service._strings(saved["languages"]) == ("si", "en")
    assert fidelity_service._strings(["si", 1]) == ()
    assert fidelity_service._strings("si") == ()


@pytest.mark.parametrize(
    "value",
    [None, "si", ["si", 1], ("en", None), ["si", "x" * 257], ["en"] * 128 + ["si"]],
)
def test_string_evidence_rejects_the_complete_malformed_array(value: object) -> None:
    assert fidelity_service._strings(value) == ()


@pytest.mark.parametrize("field", ["source_languages", "languages", "fonts"])
@pytest.mark.parametrize(
    "value",
    [None, "si", ["en", 1], ["en", "x" * 257], ["en"] * 128 + ["si"]],
)
def test_malformed_provided_source_evidence_never_loses_constraints_to_become_confirmable(
    field: str,
    value: object,
) -> None:
    provenance = {"source_languages": ["en"], "languages": ["en"], field: value}
    result = fidelity_service.assess_candidate("Read the original question", "native", provenance)
    assert not result.can_confirm
    assert f"invalid_{field}" in result.risk_codes
    assert result.normalized_text == "Read the original question"


@pytest.mark.parametrize("field", ["source_languages", "languages"])
def test_unknown_language_codes_are_rejected_instead_of_silently_removed(field: str) -> None:
    result = fidelity_service.assess_candidate(
        "Read the original question", "human", {field: ["en", "invalid"]}
    )
    assert not result.can_confirm
    assert f"invalid_{field}" in result.risk_codes


@pytest.mark.parametrize("container", [list, tuple])
def test_complete_evidence_arrays_keep_empty_and_exact_bound_values(container: type) -> None:
    assert fidelity_service._strings(container()) == ()
    fonts = container(["F" * 256] * 128)
    assert fidelity_service._strings(fonts) == tuple(fonts)
    result = fidelity_service.assess_candidate(
        "Read the original question",
        "native",
        {"source_languages": container(["en"] * 128), "languages": container(), "fonts": fonts},
    )
    assert result.can_confirm
    assert result.languages == ("en",)


@pytest.mark.parametrize("reference", [None, "reference", [], {}, {"_math_evidence": "invalid"}])
def test_malformed_math_references_override_cached_pass_and_keep_text_risks(
    reference: object,
) -> None:
    result = fidelity_service.assess_candidate(
        "Read the original question\x00",
        "human",
        {"maths_reference": reference, "maths_fidelity": {"can_confirm": True}},
    )
    assert not result.can_confirm
    assert {"unsafe_control", "invalid_math_reference", "maths_fidelity_unconfirmed"} <= set(
        result.risk_codes
    )


@pytest.mark.parametrize("compressed", [False, True])
def test_wrong_decoded_math_word_kind_cannot_override_the_source_reference(
    compressed: bool,
) -> None:
    import pymupdf

    from exam_guru_api.documents.source_math_fidelity import (
        encode_math_evidence,
        extract_math_layout,
    )

    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 70), "3 X 4 = 12")
        reference = extract_math_layout(page)
    words = encode_math_evidence({"padding": "x" * 5000}) if compressed else {}
    result = fidelity_service.assess_candidate(
        "3 X 4 = 12",
        "ocr",
        {"languages": ["en"], "maths_reference": reference, "maths_words": words},
    )
    assert not result.can_confirm
    assert "invalid_math_word_evidence" in result.risk_codes


@pytest.mark.parametrize("page_number", [0, -1, True, 1.0, "1", None])
def test_fidelity_source_rejects_nonpositive_or_noninteger_pages(page_number: object) -> None:
    session = AsyncMock(spec=AsyncSession)
    session.get.return_value = source()
    with pytest.raises(ValueError, match="page number must be positive"):
        asyncio.run(PageFidelityService(session)._source(DOCUMENT, cast(int, page_number)))
    session.get.assert_awaited_once_with(
        SourceDocumentModel, DOCUMENT, with_for_update=True, populate_existing=True
    )
    session.add.assert_not_called()
    session.commit.assert_not_awaited()


@pytest.mark.parametrize("pages", [None, 1])
def test_fidelity_source_cannot_invent_a_page_without_original_evidence(pages: int | None) -> None:
    session = AsyncMock(spec=AsyncSession)
    session.get.return_value = source(pages=pages)
    with pytest.raises(FidelitySourceNotFoundError, match="source_page_not_found"):
        asyncio.run(PageFidelityService(session)._source(DOCUMENT, 2))
    session.add.assert_not_called()
    session.commit.assert_not_awaited()


def test_fidelity_source_missing_document_has_a_typed_error_before_creating_state() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.get.return_value = None
    with pytest.raises(FidelitySourceNotFoundError, match="source_document_not_found"):
        asyncio.run(
            PageFidelityService(session).edit_page(
                DOCUMENT,
                1,
                text="Read the source",
                reason="Compared the original",
                expected_version=0,
                actor_id=ACTOR,
            )
        )
    session.get.assert_awaited_once()
    session.add.assert_not_called()
    session.commit.assert_not_awaited()


@pytest.mark.parametrize(
    "condition", ["missing", "unknown_pages", "zero_pages", "inactive", "quarantined"]
)
def test_document_verification_rejects_missing_or_ineligible_original_before_sql(
    condition: str,
) -> None:
    document = source(
        pages=None if condition == "unknown_pages" else 0 if condition == "zero_pages" else 2
    )
    document.active_for_ai = condition != "inactive"
    document.quarantined_for_teacher_use = condition == "quarantined"
    session = AsyncMock(spec=AsyncSession)
    session.get.return_value = None if condition == "missing" else document
    assert not asyncio.run(PageFidelityService(session).document_is_verified(DOCUMENT))
    session.scalar.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.parametrize("state_name", ["verified", "excluded", "processing"])
def test_automatic_candidate_cannot_overwrite_a_protected_review_state(state_name: str) -> None:
    state = PageReviewStateModel(
        document_id=DOCUMENT,
        page_number=1,
        version=3,
        state=state_name,
        current_candidate_id=UUID(int=87003),
        event_id=UUID(int=87004),
    )
    session = AsyncMock(spec=AsyncSession)
    session.get.side_effect = [source(), state]
    with pytest.raises(PageFidelityConflictError, match="explicit_page_revision_required"):
        asyncio.run(
            PageFidelityService(session).record_candidate(
                DOCUMENT,
                1,
                raw_text="A machine must not replace this decision",
                method="native",
                actor_id=ACTOR,
                provenance={},
            )
        )
    assert state.version == 3
    assert state.state == state_name
    assert state.current_candidate_id == UUID(int=87003)
    session.add.assert_not_called()
    session.commit.assert_not_awaited()


@pytest.mark.parametrize(
    ("raw_text", "method", "message"),
    [
        ("a" * 4194305, "native", "bounded candidate size"),
        ("ග" * 1398102, "ocr", "bounded candidate size"),
        ("Read the source", "automatically_verified", "unsupported reading method"),
    ],
    ids=["ascii-byte-limit", "unicode-byte-limit", "unsupported-method"],
)
def test_candidate_input_limits_prevent_persisting_unbounded_or_unknown_readings(
    raw_text: str, method: str, message: str
) -> None:
    state = PageReviewStateModel(document_id=DOCUMENT, page_number=1, version=0, state="pending")
    session = AsyncMock(spec=AsyncSession)
    session.get.side_effect = [source(), state]
    with pytest.raises(ValueError, match=message):
        asyncio.run(
            PageFidelityService(session).record_candidate(
                DOCUMENT, 1, raw_text=raw_text, method=method, actor_id=ACTOR, provenance={}
            )
        )
    assert state.version == 0
    assert state.current_candidate_id is None
    session.add.assert_not_called()
    session.commit.assert_not_awaited()


@pytest.mark.parametrize("expected", [True, False, -1, 2])
def test_version_checks_reject_booleans_and_stale_revisions(expected: int) -> None:
    state = PageReviewStateModel(
        document_id=DOCUMENT, page_number=1, version=1, state="needs_review"
    )
    with pytest.raises(PageFidelityConflictError, match="source_page_version_conflict"):
        PageFidelityService._version(state, expected)
    assert state.version == 1


@pytest.mark.parametrize(
    ("name", "pages"),
    [("", 1), (" ", 1), ("x" * 161, 1), ("Comparison", 0), ("Comparison", 1001)],
)
def test_benchmark_bounds_fail_before_any_database_access(name: str, pages: int) -> None:
    with pytest.raises(ValueError, match="bounded name and page selection"):
        asyncio.run(
            PageFidelityService(cast(AsyncSession, object())).create_benchmark(
                name=name,
                pages=tuple((DOCUMENT, number, ("maths",)) for number in range(1, pages + 1)),
                actor_id=ACTOR,
                selection={},
            )
        )


@pytest.mark.parametrize("categories", [(), ("",), ("  ",), ("x" * 121,), ("maths",) * 33])
def test_benchmark_categories_are_bounded_before_any_benchmark_is_written(
    categories: tuple[str, ...],
) -> None:
    session = AsyncMock(spec=AsyncSession)
    session.get.return_value = source()
    with pytest.raises(ValueError, match="categories must be bounded"):
        asyncio.run(
            PageFidelityService(session).create_benchmark(
                name="Comparison",
                pages=((DOCUMENT, 1, categories),),
                actor_id=ACTOR,
                selection={},
            )
        )
    session.add.assert_not_called()
    session.commit.assert_not_awaited()

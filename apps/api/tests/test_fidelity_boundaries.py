import asyncio
import json
from datetime import UTC, datetime, timedelta, timezone
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.documents import fidelity_queries, fidelity_service, page_reading_jobs
from exam_guru_api.documents.fidelity_models import PageReviewStateModel, SourceReadJobModel
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


@pytest.mark.parametrize("coverage", ["0.9", None, True, float("inf"), float("nan"), -0.1, 1.1])
def test_readonly_text_view_distrusts_malformed_coverage_without_weakening_text_risks(
    coverage: object,
) -> None:
    safe = fidelity_queries._text_view(
        "Read the original question",
        method="native",
        provenance={"image_coverage": coverage},
        diagnostics={},
    )
    baseline = fidelity_queries._text_view(
        "Read the original question", method="native", provenance={}, diagnostics={}
    )
    assert safe == baseline
    unsafe = fidelity_queries._text_view(
        "broken\x00text",
        method="native",
        provenance={"image_coverage": coverage},
        diagnostics={"risk_codes": ["provider_warning"]},
    )
    assert not unsafe.can_confirm
    assert {"unsafe_control", "provider_warning"} <= set(unsafe.risk_codes)
    assert "\x00" not in unsafe.system_text


@pytest.mark.parametrize("page_number", [None, "1", 1.0, True, 0, -1, 2147483647])
def test_read_queue_rejects_invalid_selected_page_before_io(page_number: object) -> None:
    message = "a page version requires a selected page" if page_number is None else "out of range"
    with pytest.raises(ValueError, match=message):
        asyncio.run(
            page_reading_jobs.queue_source_read(
                cast(AsyncSession, object()),
                DOCUMENT,
                actor_id=ACTOR,
                page_number=cast(int | None, page_number),
                expected_page_version=0,
            )
        )


@pytest.mark.parametrize("version", [None, True, -1, 1.0, "0", 2147483647])
def test_explicit_reread_requires_a_bounded_integer_revision(version: object) -> None:
    message = "reread requires the current page version" if version is None else "out of range"
    with pytest.raises(ValueError, match=message):
        asyncio.run(
            page_reading_jobs.queue_source_read(
                cast(AsyncSession, object()),
                DOCUMENT,
                actor_id=ACTOR,
                page_number=1,
                expected_page_version=cast(int | None, version),
            )
        )


@pytest.mark.parametrize("parameter", ["batch_size", "outbox_min_age_seconds"])
@pytest.mark.parametrize("value", [0, -1, True, 1.0, "1", 3601])
def test_recovery_refuses_unbounded_or_noninteger_work_before_io(
    parameter: str, value: object
) -> None:
    with pytest.raises(ValueError, match="out of range"):
        asyncio.run(
            page_reading_jobs.recover_source_reads(
                cast(AsyncSession, object()),
                cast(page_reading_jobs.SourceReadDispatcher, object()),
                batch_size=cast(int, value) if parameter == "batch_size" else 100,
                outbox_min_age_seconds=cast(int, value)
                if parameter == "outbox_min_age_seconds"
                else 30,
            )
        )


def test_reading_timestamps_require_an_aware_clock_and_normalize_offsets() -> None:
    session = AsyncMock(spec=AsyncSession)
    with pytest.raises(ValueError, match="timezone aware"):
        asyncio.run(
            page_reading_jobs.claim_source_read(session, UUID(int=87005), now=datetime(2026, 1, 1))
        )
    session.scalar.assert_not_awaited()
    offset = datetime(2026, 1, 1, 5, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    assert page_reading_jobs._now(offset) == datetime(2026, 1, 1, tzinfo=UTC)


def test_missing_job_delivery_does_not_open_the_source_or_create_a_job() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.scalar.return_value = None
    session.get.return_value = None
    job_id = UUID(int=87005)
    with pytest.raises(
        page_reading_jobs.SourceReadJobNotFoundError, match="source_read_job_not_found"
    ):
        asyncio.run(
            page_reading_jobs.run_source_read(
                session, job_id, storage=cast(page_reading_jobs.SourceReadStorage, object())
            )
        )
    session.get.assert_awaited_once_with(SourceReadJobModel, job_id, populate_existing=True)
    session.add.assert_not_called()


def test_missing_source_queue_request_rolls_back_without_creating_a_job() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.get.return_value = None
    with pytest.raises(FidelitySourceNotFoundError, match="source_document_not_found"):
        asyncio.run(page_reading_jobs.queue_source_read(session, DOCUMENT, actor_id=ACTOR))
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
    session.add.assert_not_called()

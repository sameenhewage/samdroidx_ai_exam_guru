import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.api.dependencies import get_database_session
from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.auth.ports import AuthenticationError, AuthenticationFailureCode
from exam_guru_api.main import create_app
from exam_guru_api.operations.service import (
    MAX_OPERATIONS_WINDOW,
    OperationsSummaryService,
    OperationsWindow,
    OperationsWindowError,
)

ADMIN_HEADERS = {"Authorization": "Bearer admin-token"}
REVIEWER_HEADERS = {"Authorization": "Bearer reviewer-token"}
PATH = "/api/v1/admin/operations/summary"
START = datetime(2026, 1, 1, tzinfo=UTC)
END = START + timedelta(hours=24)


class StaticIdentityProvider:
    async def authenticate(self, access_token: str) -> Principal:
        if access_token == "admin-token":
            return Principal(UUID(int=1), frozenset({AdminRole.ADMIN}))
        if access_token == "reviewer-token":
            return Principal(UUID(int=2), frozenset({AdminRole.REVIEWER}))
        raise AuthenticationError(AuthenticationFailureCode.INVALID)


class EmptyResult:
    def __init__(self, *, row: object | None = None, rows: tuple[object, ...] = ()) -> None:
        self._row = row
        self._rows = rows

    def one(self) -> object:
        assert self._row is not None
        return self._row

    def all(self) -> tuple[object, ...]:
        return self._rows


class EmptyOperationsSession:
    def __init__(self) -> None:
        empty_bounds = {
            "earliest_observed_at": None,
            "latest_observed_at": None,
        }
        self.results = iter(
            (
                EmptyResult(
                    row=SimpleNamespace(
                        run_count=0,
                        pending_count=0,
                        running_count=0,
                        succeeded_count=0,
                        failed_count=0,
                        attempt_count=0,
                        input_tokens=0,
                        output_tokens=0,
                        total_tokens=0,
                        cost_microusd=0,
                        total_latency_ms=0,
                        maximum_latency_ms=0,
                        **empty_bounds,
                    )
                ),
                EmptyResult(),
                EmptyResult(
                    row=SimpleNamespace(
                        run_count=0,
                        pass_count=0,
                        warn_count=0,
                        fail_count=0,
                        finding_count=0,
                        **empty_bounds,
                    )
                ),
                EmptyResult(
                    row=SimpleNamespace(
                        pass_count=0,
                        warn_count=0,
                        fail_count=0,
                    )
                ),
                EmptyResult(
                    row=SimpleNamespace(
                        document_count=0,
                        uploaded_count=0,
                        extraction_pending_count=0,
                        extracted_count=0,
                        in_review_count=0,
                        trusted_count=0,
                        failed_count=0,
                        ocr_page_count=0,
                        **empty_bounds,
                    )
                ),
                EmptyResult(),
                EmptyResult(
                    row=SimpleNamespace(
                        job_count=0,
                        queued_count=0,
                        claimed_count=0,
                        succeeded_count=0,
                        failed_count=0,
                        requested_count=0,
                        embedded_count=0,
                        deduplicated_count=0,
                        **empty_bounds,
                    )
                ),
                EmptyResult(),
                EmptyResult(
                    row=SimpleNamespace(
                        run_count=0,
                        scanned_count=0,
                        referenced_count=0,
                        candidate_count=0,
                        resolved_count=0,
                        tagged_count=0,
                        failure_count=0,
                        truncated_run_count=0,
                        current_candidate_count=0,
                        last_completed_at=None,
                        **empty_bounds,
                    )
                ),
                EmptyResult(),
                EmptyResult(
                    row=SimpleNamespace(
                        paper_count=0,
                        draft_count=0,
                        published_count=0,
                        archived_count=0,
                        publication_count=0,
                        archive_count=0,
                        **empty_bounds,
                    )
                ),
            )
        )
        self.statements: list[object] = []

    async def execute(self, statement: object) -> EmptyResult:
        self.statements.append(statement)
        return next(self.results)


async def override_session(session: EmptyOperationsSession) -> AsyncIterator[AsyncSession]:
    yield cast(AsyncSession, session)


def operations_client(session: EmptyOperationsSession) -> TestClient:
    application = create_app(identity_provider=StaticIdentityProvider())

    async def session_dependency() -> AsyncIterator[AsyncSession]:
        async for value in override_session(session):
            yield value

    application.dependency_overrides[get_database_session] = session_dependency
    return TestClient(application)


def test_operations_window_defaults_to_24_hours_and_normalizes_offsets_to_utc() -> None:
    now = datetime(2026, 2, 1, 12, tzinfo=UTC)

    default = OperationsWindow.resolve(start=None, end=None, now=now)
    offset = OperationsWindow.resolve(
        start=datetime(2026, 1, 1, 5, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))),
        end=datetime(2026, 1, 2, 5, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))),
        now=now,
    )

    assert default.start == now - timedelta(hours=24)
    assert default.end == now
    assert default.duration == timedelta(hours=24)
    assert offset.start == datetime(2026, 1, 1, tzinfo=UTC)
    assert offset.end == datetime(2026, 1, 2, tzinfo=UTC)
    assert timedelta(days=31) == MAX_OPERATIONS_WINDOW


@pytest.mark.parametrize(
    ("start", "end", "code"),
    [
        (datetime(2026, 1, 1), END, "operations_window_timezone_required"),
        (START, datetime(2026, 1, 2), "operations_window_timezone_required"),
        (END, START, "operations_window_invalid_order"),
        (START, START, "operations_window_invalid_order"),
        (START, START + timedelta(days=31, microseconds=1), "operations_window_too_large"),
    ],
)
def test_operations_window_rejects_unbounded_or_ambiguous_ranges(
    start: datetime,
    end: datetime,
    code: str,
) -> None:
    with pytest.raises(OperationsWindowError) as raised:
        OperationsWindow.resolve(start=start, end=end, now=END)

    assert raised.value.code == code


def test_operations_service_returns_typed_zeroes_and_null_data_bounds_for_empty_data() -> None:
    session = EmptyOperationsSession()

    summary = asyncio.run(
        OperationsSummaryService(cast(AsyncSession, session)).summarize(
            OperationsWindow(start=START, end=END)
        )
    )

    assert summary.model_dump(mode="json") == {
        "window": {
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-01-02T00:00:00Z",
            "semantics": "start_inclusive_end_exclusive",
        },
        "data_bounds": {
            "earliest_observed_at": None,
            "latest_observed_at": None,
        },
        "units": {
            "counts": "count",
            "tokens": "token",
            "cost": "microusd",
            "latency": "millisecond",
            "timestamps": "UTC",
        },
        "generation": {
            "run_count": 0,
            "status_counts": {"pending": 0, "running": 0, "succeeded": 0, "failed": 0},
            "failure_codes": [],
            "attempt_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cost_microusd": 0,
            "latency_ms": {"total": 0, "average": 0, "maximum": 0},
        },
        "validation": {
            "run_count": 0,
            "run_status_counts": {"pass": 0, "warn": 0, "fail": 0},
            "finding_count": 0,
            "finding_status_counts": {"pass": 0, "warn": 0, "fail": 0},
        },
        "extraction": {
            "document_count": 0,
            "status_counts": {
                "uploaded": 0,
                "extraction_pending": 0,
                "extracted": 0,
                "in_review": 0,
                "trusted": 0,
                "failed": 0,
            },
            "failure_codes": [],
            "ocr_page_count": 0,
        },
        "embedding": {
            "job_count": 0,
            "status_counts": {"queued": 0, "claimed": 0, "succeeded": 0, "failed": 0},
            "failure_codes": [],
            "requested_count": 0,
            "embedded_count": 0,
            "deduplicated_count": 0,
        },
        "object_storage": {
            "reconciliation": {
                "run_count": 0,
                "scanned_count": 0,
                "referenced_count": 0,
                "candidate_count": 0,
                "resolved_count": 0,
                "tagged_count": 0,
                "failure_count": 0,
                "truncated_run_count": 0,
                "current_candidate_count": 0,
                "last_completed_at": None,
                "failure_codes": [],
            }
        },
        "practice_papers": {
            "paper_count": 0,
            "state_counts": {"draft": 0, "published": 0, "archived": 0},
            "publication_count": 0,
            "archive_count": 0,
        },
    }
    assert len(session.statements) == 11


def test_operations_service_aggregates_fixed_statuses_costs_failures_and_data_bounds() -> None:
    session = EmptyOperationsSession()
    session.results = iter(
        (
            EmptyResult(
                row=SimpleNamespace(
                    run_count=2,
                    pending_count=0,
                    running_count=0,
                    succeeded_count=1,
                    failed_count=1,
                    attempt_count=3,
                    input_tokens=30,
                    output_tokens=12,
                    total_tokens=42,
                    cost_microusd=57,
                    total_latency_ms=150,
                    maximum_latency_ms=100,
                    earliest_observed_at=START + timedelta(minutes=1),
                    latest_observed_at=START + timedelta(minutes=2),
                )
            ),
            EmptyResult(rows=(("provider_timeout", 1),)),
            EmptyResult(
                row=SimpleNamespace(
                    run_count=2,
                    pass_count=1,
                    warn_count=0,
                    fail_count=1,
                    finding_count=5,
                    earliest_observed_at=START + timedelta(minutes=3),
                    latest_observed_at=START + timedelta(minutes=4),
                )
            ),
            EmptyResult(row=SimpleNamespace(pass_count=3, warn_count=1, fail_count=1)),
            EmptyResult(
                row=SimpleNamespace(
                    document_count=2,
                    uploaded_count=0,
                    extraction_pending_count=0,
                    extracted_count=1,
                    in_review_count=0,
                    trusted_count=0,
                    failed_count=1,
                    ocr_page_count=4,
                    earliest_observed_at=START + timedelta(minutes=5),
                    latest_observed_at=START + timedelta(minutes=6),
                )
            ),
            EmptyResult(rows=(("ocr_timeout", 1),)),
            EmptyResult(
                row=SimpleNamespace(
                    job_count=2,
                    queued_count=0,
                    claimed_count=0,
                    succeeded_count=1,
                    failed_count=1,
                    requested_count=7,
                    embedded_count=4,
                    deduplicated_count=2,
                    earliest_observed_at=START + timedelta(minutes=7),
                    latest_observed_at=START + timedelta(minutes=8),
                )
            ),
            EmptyResult(rows=(("embedding_provider_unavailable", 1),)),
            EmptyResult(
                row=SimpleNamespace(
                    run_count=2,
                    scanned_count=12,
                    referenced_count=5,
                    candidate_count=4,
                    resolved_count=2,
                    tagged_count=3,
                    failure_count=1,
                    truncated_run_count=1,
                    current_candidate_count=7,
                    last_completed_at=START + timedelta(minutes=10),
                    earliest_observed_at=START + timedelta(minutes=9),
                    latest_observed_at=START + timedelta(minutes=10),
                )
            ),
            EmptyResult(rows=(("object_storage_list_failed", 1),)),
            EmptyResult(
                row=SimpleNamespace(
                    paper_count=3,
                    draft_count=1,
                    published_count=1,
                    archived_count=1,
                    publication_count=4,
                    archive_count=1,
                    earliest_observed_at=START + timedelta(seconds=1),
                    latest_observed_at=START + timedelta(minutes=11),
                )
            ),
        )
    )

    summary = asyncio.run(
        OperationsSummaryService(cast(AsyncSession, session)).summarize(
            OperationsWindow(start=START, end=END)
        )
    )

    assert summary.generation.model_dump() == {
        "run_count": 2,
        "status_counts": {"pending": 0, "running": 0, "succeeded": 1, "failed": 1},
        "failure_codes": [{"code": "provider_timeout", "count": 1}],
        "attempt_count": 3,
        "input_tokens": 30,
        "output_tokens": 12,
        "total_tokens": 42,
        "cost_microusd": 57,
        "latency_ms": {"total": 150, "average": 75, "maximum": 100},
    }
    assert summary.validation.finding_status_counts.model_dump() == {
        "pass": 3,
        "warn": 1,
        "fail": 1,
    }
    assert summary.extraction.failure_codes[0].code == "ocr_timeout"
    assert summary.embedding.model_dump()["requested_count"] == 7
    assert summary.object_storage.model_dump(mode="json") == {
        "reconciliation": {
            "run_count": 2,
            "scanned_count": 12,
            "referenced_count": 5,
            "candidate_count": 4,
            "resolved_count": 2,
            "tagged_count": 3,
            "failure_count": 1,
            "truncated_run_count": 1,
            "current_candidate_count": 7,
            "last_completed_at": "2026-01-01T00:10:00Z",
            "failure_codes": [{"code": "object_storage_list_failed", "count": 1}],
        }
    }
    assert summary.practice_papers.model_dump() == {
        "paper_count": 3,
        "state_counts": {"draft": 1, "published": 1, "archived": 1},
        "publication_count": 4,
        "archive_count": 1,
    }
    assert summary.data_bounds.earliest_observed_at == START + timedelta(seconds=1)
    assert summary.data_bounds.latest_observed_at == START + timedelta(minutes=11)


def test_operations_api_is_admin_only_and_has_stable_window_errors() -> None:
    reviewer_session = EmptyOperationsSession()
    with operations_client(reviewer_session) as client:
        reviewer = client.get(PATH, headers=REVIEWER_HEADERS)

    assert reviewer.status_code == 403
    assert reviewer.json() == {"detail": {"code": "permission_denied"}}
    assert reviewer_session.statements == []

    cases = (
        (
            {"start": START.isoformat(), "end": (START + timedelta(days=32)).isoformat()},
            "operations_window_too_large",
        ),
        (
            {"start": END.isoformat(), "end": START.isoformat()},
            "operations_window_invalid_order",
        ),
        (
            {"start": START.isoformat(), "end": END.isoformat(), "group_by": "model"},
            "unsupported_operations_query_parameter",
        ),
    )
    for params, code in cases:
        session = EmptyOperationsSession()
        with operations_client(session) as client:
            response = client.get(PATH, headers=ADMIN_HEADERS, params=params)
        assert response.status_code == 422
        assert response.json() == {"detail": {"code": code}}
        assert session.statements == []

    duplicate_session = EmptyOperationsSession()
    with operations_client(duplicate_session) as client:
        duplicate = client.get(
            PATH,
            headers=ADMIN_HEADERS,
            params=[
                ("start", START.isoformat()),
                ("start", START.isoformat()),
                ("end", END.isoformat()),
            ],
        )
    assert duplicate.status_code == 422
    assert duplicate.json() == {"detail": {"code": "operations_window_ambiguous"}}
    assert duplicate_session.statements == []


def test_operations_api_returns_empty_summary_without_sensitive_payload_fields() -> None:
    session = EmptyOperationsSession()
    with operations_client(session) as client:
        response = client.get(
            PATH,
            headers=ADMIN_HEADERS,
            params={"start": START.isoformat(), "end": END.isoformat()},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["generation"]["cost_microusd"] == 0
    assert body["data_bounds"] == {
        "earliest_observed_at": None,
        "latest_observed_at": None,
    }
    serialized = str(body).casefold()
    for prohibited in (
        "source_text",
        "object_key",
        "checksum_sha256",
        "continuation_cursor",
        "prompt",
        "vector",
        "secret",
        "query",
    ):
        assert prohibited not in serialized


def test_operations_openapi_is_fixed_read_only_and_explicit_about_units_and_bounds() -> None:
    schema = create_app().openapi()
    operation = schema["paths"][PATH]["get"]

    assert operation["operationId"] == "get_operations_summary"
    assert operation["security"] == [{"HTTPBearer": []}]
    assert {parameter["name"] for parameter in operation["parameters"]} == {"start", "end"}
    assert "31 days" in operation["description"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/OperationsSummaryResponse"
    }
    for status_code in ("403", "422"):
        assert operation["responses"][status_code]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ApiErrorResponse"
        }
    response_schema = schema["components"]["schemas"]["OperationsSummaryResponse"]
    assert response_schema["additionalProperties"] is False
    assert set(response_schema["properties"]) == {
        "window",
        "data_bounds",
        "units",
        "generation",
        "validation",
        "extraction",
        "embedding",
        "object_storage",
        "practice_papers",
    }

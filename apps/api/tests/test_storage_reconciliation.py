import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from exam_guru_api.infrastructure.object_storage import (
    ObjectMetadata,
    ObjectPage,
    ObjectStorageOperationError,
    ObjectTagCapacityError,
    ObjectTagMutation,
)
from exam_guru_api.storage_reconciliation.models import (
    FindingStatus,
    ReconciliationRunStatus,
    TagStatus,
)
from exam_guru_api.storage_reconciliation.repository import (
    MAX_FINDING_UPSERT_BATCH_SIZE,
    _finding_batches,
)
from exam_guru_api.storage_reconciliation.service import (
    FindingRecord,
    ReconciliationExecution,
    ReconciliationLease,
    ReconciliationPolicy,
    ReconciliationRunRecord,
    StorageReconciliationService,
)

NOW = datetime(2026, 2, 2, 12, tzinfo=UTC)
LEASE_TOKEN = UUID("00000000-0000-0000-0000-000000002101")


def source_key(character: str) -> str:
    checksum = character * 64
    return f"sources/{checksum[:2]}/{checksum}.pdf"


def metadata(character: str, *, age_seconds: int, size: int = 10) -> ObjectMetadata:
    return ObjectMetadata(
        key=source_key(character),
        size=size,
        last_modified=NOW - timedelta(seconds=age_seconds),
    )


def finding(
    character: str,
    *,
    status: FindingStatus,
    tag_status: TagStatus = TagStatus.NOT_REQUESTED,
) -> FindingRecord:
    first_seen = NOW - timedelta(days=2)
    return FindingRecord(
        object_key=source_key(character),
        checksum_sha256=character * 64,
        first_seen_at=first_seen,
        last_seen_at=NOW - timedelta(days=1),
        candidate_since=first_seen,
        size_bytes=10,
        object_last_modified_at=NOW - timedelta(days=3),
        status=status,
        tag_status=tag_status,
        tag_updated_at=first_seen if tag_status is not TagStatus.NOT_REQUESTED else None,
        resolved_at=NOW - timedelta(days=1) if status is FindingStatus.RESOLVED else None,
        failure_code=(
            "object_storage_tag_failed"
            if tag_status in {TagStatus.CAPACITY_CONFLICT, TagStatus.FAILED}
            else None
        ),
        created_at=first_seen,
        updated_at=NOW - timedelta(days=1),
    )


class FakeRepository:
    def __init__(
        self,
        *,
        lease_token: UUID | None = LEASE_TOKEN,
        continuation_cursor: str | None = None,
        referenced: set[str] | None = None,
        findings: dict[str, FindingRecord] | None = None,
    ) -> None:
        self.lease_token = lease_token
        self.continuation_cursor = continuation_cursor
        self.referenced = referenced or set()
        self.findings = findings or {}
        self.acquire_calls: list[dict[str, object]] = []
        self.reference_calls: list[tuple[str, ...]] = []
        self.finding_calls: list[tuple[str, ...]] = []
        self.completions: list[
            tuple[UUID, ReconciliationRunRecord, tuple[FindingRecord, ...], str | None]
        ] = []

    async def acquire_lease(
        self,
        *,
        now: datetime,
        interval_seconds: int,
        lease_seconds: int,
    ) -> ReconciliationLease | None:
        self.acquire_calls.append(
            {
                "now": now,
                "interval_seconds": interval_seconds,
                "lease_seconds": lease_seconds,
            }
        )
        if self.lease_token is None:
            return None
        return ReconciliationLease(
            token=self.lease_token,
            continuation_cursor=self.continuation_cursor,
        )

    async def referenced_keys(self, keys: tuple[str, ...]) -> set[str]:
        self.reference_calls.append(keys)
        return self.referenced.intersection(keys)

    async def findings_for_keys(self, keys: tuple[str, ...]) -> dict[str, FindingRecord]:
        self.finding_calls.append(keys)
        return {key: value for key, value in self.findings.items() if key in keys}

    async def complete_run(
        self,
        *,
        lease_token: UUID,
        run: ReconciliationRunRecord,
        findings: tuple[FindingRecord, ...],
        continuation_cursor: str | None,
    ) -> None:
        self.completions.append((lease_token, run, findings, continuation_cursor))
        self.findings.update({item.object_key: item for item in findings})
        self.continuation_cursor = continuation_cursor


class FakeStorage:
    def __init__(
        self,
        pages: list[ObjectPage | Exception],
        *,
        tag_results: dict[str, ObjectTagMutation | Exception] | None = None,
    ) -> None:
        self.pages = pages
        self.tag_results = tag_results or {}
        self.list_calls: list[tuple[int, str | None]] = []
        self.tag_calls: list[tuple[str, datetime | None]] = []

    def list_source_objects(
        self,
        *,
        max_keys: int,
        continuation_token: str | None = None,
    ) -> ObjectPage:
        self.list_calls.append((max_keys, continuation_token))
        result = self.pages.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def merge_reconciliation_tags(
        self,
        key: str,
        *,
        candidate_detected_at: datetime | None,
    ) -> ObjectTagMutation:
        self.tag_calls.append((key, candidate_detected_at))
        result = self.tag_results.get(key, ObjectTagMutation(changed=True, tag_count=2))
        if isinstance(result, Exception):
            raise result
        return result


class RecordingTelemetry:
    def __init__(self) -> None:
        self.runs: list[dict[str, object]] = []

    def storage_reconciliation_terminal(self, **values: object) -> None:
        self.runs.append(values)


def page(*objects: ObjectMetadata, next_token: str | None = None) -> ObjectPage:
    return ObjectPage(
        objects=tuple(objects),
        is_truncated=next_token is not None,
        next_continuation_token=next_token,
    )


def run_service(
    repository: FakeRepository,
    storage: FakeStorage,
    *,
    apply_tags: bool = False,
    max_objects: int = 10_000,
    telemetry: RecordingTelemetry | None = None,
) -> ReconciliationExecution:
    service = StorageReconciliationService(
        repository,
        storage,
        ReconciliationPolicy(
            interval_seconds=3_600,
            grace_seconds=86_400,
            max_objects_per_run=max_objects,
            apply_tags=apply_tags,
        ),
        now=lambda: NOW,
        telemetry=telemetry,
    )
    return asyncio.run(service.reconcile())


def test_reconciliation_policy_is_strictly_bounded() -> None:
    policy = ReconciliationPolicy()

    assert policy == ReconciliationPolicy(
        interval_seconds=3_600,
        grace_seconds=86_400,
        max_objects_per_run=1_000,
        apply_tags=False,
    )

    invalid = (
        {"interval_seconds": 299},
        {"grace_seconds": 3_599},
        {"max_objects_per_run": 0},
        {"max_objects_per_run": 10_001},
        {"apply_tags": 1},
    )
    for changes in invalid:
        with pytest.raises(ValueError, match="reconciliation"):
            ReconciliationPolicy(**changes)  # type: ignore[arg-type]


def test_finding_upserts_are_partitioned_below_postgresql_parameter_limits() -> None:
    values = tuple(range(MAX_FINDING_UPSERT_BATCH_SIZE * 2 + 1))

    batches = _finding_batches(values)

    assert tuple(len(batch) for batch in batches) == (
        MAX_FINDING_UPSERT_BATCH_SIZE,
        MAX_FINDING_UPSERT_BATCH_SIZE,
        1,
    )
    assert tuple(value for batch in batches for value in batch) == values


def test_interval_noop_never_calls_the_object_provider() -> None:
    repository = FakeRepository(lease_token=None)
    storage = FakeStorage([])

    result = run_service(repository, storage)

    assert result == ReconciliationExecution(skipped=True, run=None)
    assert storage.list_calls == []
    assert storage.tag_calls == []
    assert repository.completions == []
    assert repository.acquire_calls == [
        {
            "now": NOW,
            "interval_seconds": 3_600,
            "lease_seconds": 3_600,
        }
    ]


def test_persisted_cursor_progresses_without_starvation_then_clears_and_restarts() -> None:
    repository = FakeRepository()
    storage = FakeStorage(
        [
            page(
                metadata("1", age_seconds=200_000),
                metadata("2", age_seconds=200_000),
                next_token="cursor-2",
            ),
            page(
                metadata("3", age_seconds=200_000),
                metadata("4", age_seconds=200_000),
                next_token="cursor-4",
            ),
            page(metadata("5", age_seconds=200_000)),
            page(
                metadata("1", age_seconds=200_000),
                metadata("2", age_seconds=200_000),
                next_token="cursor-2",
            ),
        ]
    )

    runs = tuple(run_service(repository, storage, max_objects=2).run for _ in range(4))

    assert all(run is not None for run in runs)
    assert storage.list_calls == [
        (2, None),
        (2, "cursor-2"),
        (2, "cursor-4"),
        (2, None),
    ]
    assert repository.reference_calls == [
        (source_key("1"), source_key("2")),
        (source_key("3"), source_key("4")),
        (source_key("5"),),
        (source_key("1"), source_key("2")),
    ]
    assert [completion[3] for completion in repository.completions] == [
        "cursor-2",
        "cursor-4",
        None,
        "cursor-2",
    ]
    assert set(repository.findings) == {
        source_key("1"),
        source_key("2"),
        source_key("3"),
        source_key("4"),
        source_key("5"),
    }


def test_scan_uses_bounded_pages_honors_grace_and_marks_truncation_at_run_limit() -> None:
    old_orphan = metadata("a", age_seconds=86_401, size=17)
    young_orphan = metadata("b", age_seconds=86_399)
    referenced = metadata("c", age_seconds=200_000)
    repository = FakeRepository(referenced={referenced.key})
    storage = FakeStorage(
        [
            page(old_orphan, young_orphan, next_token="page-2"),
            page(referenced, next_token="page-3"),
        ]
    )
    telemetry = RecordingTelemetry()

    result = run_service(
        repository,
        storage,
        max_objects=3,
        telemetry=telemetry,
    )

    assert result.skipped is False
    assert result.run is not None
    assert result.run.status is ReconciliationRunStatus.COMPLETED
    assert result.run.scanned_count == 3
    assert result.run.referenced_count == 1
    assert result.run.candidate_count == 1
    assert result.run.resolved_count == 0
    assert result.run.tagged_count == 0
    assert result.run.failure_count == 0
    assert result.run.truncated is True
    assert storage.list_calls == [(3, None), (1, "page-2")]
    assert repository.reference_calls == [
        (old_orphan.key, young_orphan.key),
        (referenced.key,),
    ]
    assert len(repository.completions) == 1
    persisted = repository.completions[0][2]
    assert repository.completions[0][3] == "page-3"
    assert len(persisted) == 1
    assert persisted[0].object_key == old_orphan.key
    assert persisted[0].size_bytes == 17
    assert persisted[0].first_seen_at == NOW
    assert persisted[0].last_seen_at == NOW
    assert persisted[0].status is FindingStatus.CANDIDATE
    assert persisted[0].tag_status is TagStatus.NOT_REQUESTED
    assert storage.tag_calls == []
    assert telemetry.runs == [
        {
            "status": "completed",
            "failure_code": None,
            "scanned_count": 3,
            "referenced_count": 1,
            "candidate_count": 1,
            "resolved_count": 0,
            "tagged_count": 0,
            "failure_count": 0,
            "truncated": True,
        }
    ]
    assert source_key("a") not in str(telemetry.runs)
    assert "cursor" not in str(telemetry.runs).casefold()


def test_dry_run_reopens_resolved_findings_and_resolves_new_references_without_tags() -> None:
    reopened = metadata("d", age_seconds=200_000)
    resolved = metadata("e", age_seconds=200_000)
    repository = FakeRepository(
        referenced={resolved.key},
        findings={
            reopened.key: finding("d", status=FindingStatus.RESOLVED, tag_status=TagStatus.REMOVED),
            resolved.key: finding(
                "e", status=FindingStatus.CANDIDATE, tag_status=TagStatus.APPLIED
            ),
        },
    )
    storage = FakeStorage([page(reopened, resolved)])

    result = run_service(repository, storage)

    assert result.run is not None
    assert result.run.candidate_count == 1
    assert result.run.resolved_count == 1
    assert result.run.tagged_count == 0
    writes = {item.object_key: item for item in repository.completions[0][2]}
    assert writes[reopened.key].status is FindingStatus.CANDIDATE
    assert writes[reopened.key].candidate_since == NOW
    assert writes[reopened.key].resolved_at is None
    assert writes[reopened.key].first_seen_at == NOW - timedelta(days=2)
    assert writes[reopened.key].tag_status is TagStatus.REMOVED
    assert writes[resolved.key].status is FindingStatus.RESOLVED
    assert writes[resolved.key].resolved_at == NOW
    assert writes[resolved.key].tag_status is TagStatus.APPLIED
    assert writes[resolved.key].tag_updated_at == NOW - timedelta(days=2)
    assert storage.tag_calls == []


def test_dry_run_preserves_failed_tag_outcome_when_candidate_resolves() -> None:
    referenced = metadata("6", age_seconds=200_000)
    previous = finding(
        "6",
        status=FindingStatus.CANDIDATE,
        tag_status=TagStatus.FAILED,
    )
    repository = FakeRepository(
        referenced={referenced.key},
        findings={referenced.key: previous},
    )
    storage = FakeStorage([page(referenced)])

    result = run_service(repository, storage)

    assert result.run is not None
    persisted = repository.completions[0][2][0]
    assert persisted.status is FindingStatus.RESOLVED
    assert persisted.tag_status is TagStatus.FAILED
    assert persisted.tag_updated_at == previous.tag_updated_at
    assert persisted.failure_code == previous.failure_code
    assert storage.tag_calls == []


def test_tag_state_remains_truthful_across_apply_dry_run_apply_toggle() -> None:
    item = metadata("7", age_seconds=200_000)
    repository = FakeRepository()

    first = run_service(repository, FakeStorage([page(item)]), apply_tags=True)
    assert first.run is not None
    assert repository.findings[item.key].tag_status is TagStatus.APPLIED

    repository.referenced.add(item.key)
    dry_storage = FakeStorage([page(item)])
    second = run_service(repository, dry_storage, apply_tags=False)
    assert second.run is not None
    assert repository.findings[item.key].status is FindingStatus.RESOLVED
    assert repository.findings[item.key].tag_status is TagStatus.APPLIED
    assert dry_storage.tag_calls == []

    removal_storage = FakeStorage([page(item)])
    third = run_service(repository, removal_storage, apply_tags=True)
    assert third.run is not None
    assert removal_storage.tag_calls == [(item.key, None)]
    assert repository.findings[item.key].tag_status is TagStatus.REMOVED


def test_apply_mode_merges_candidate_tags_and_removes_only_app_tags_on_resolution() -> None:
    candidate = metadata("f", age_seconds=200_000)
    resolved = metadata("1", age_seconds=200_000)
    repository = FakeRepository(
        referenced={resolved.key},
        findings={resolved.key: finding("1", status=FindingStatus.CANDIDATE)},
    )
    storage = FakeStorage([page(candidate, resolved)])

    result = run_service(repository, storage, apply_tags=True)

    assert result.run is not None
    assert result.run.candidate_count == 1
    assert result.run.resolved_count == 1
    assert result.run.tagged_count == 2
    assert storage.tag_calls == [(candidate.key, NOW), (resolved.key, None)]
    writes = {item.object_key: item for item in repository.completions[0][2]}
    assert writes[candidate.key].tag_status is TagStatus.APPLIED
    assert writes[candidate.key].tag_updated_at == NOW
    assert writes[resolved.key].tag_status is TagStatus.REMOVED
    assert writes[resolved.key].tag_updated_at == NOW


def test_apply_mode_retries_app_tag_removal_for_a_previously_resolved_finding() -> None:
    referenced = metadata("0", age_seconds=200_000)
    repository = FakeRepository(
        referenced={referenced.key},
        findings={referenced.key: finding("0", status=FindingStatus.RESOLVED)},
    )
    storage = FakeStorage([page(referenced)])

    result = run_service(repository, storage, apply_tags=True)

    assert result.run is not None
    assert result.run.resolved_count == 0
    assert result.run.tagged_count == 1
    assert storage.tag_calls == [(referenced.key, None)]
    persisted = repository.completions[0][2]
    assert len(persisted) == 1
    assert persisted[0].status is FindingStatus.RESOLVED
    assert persisted[0].tag_status is TagStatus.REMOVED
    assert persisted[0].resolved_at == NOW - timedelta(days=1)


def test_tag_capacity_and_provider_failures_are_recorded_per_finding_and_scan_continues() -> None:
    capacity_key = source_key("2")
    failed_key = source_key("3")
    repository = FakeRepository()
    storage = FakeStorage(
        [page(metadata("2", age_seconds=200_000), metadata("3", age_seconds=200_000))],
        tag_results={
            capacity_key: ObjectTagCapacityError(),
            failed_key: ObjectStorageOperationError("object_storage_tag_read_failed"),
        },
    )

    result = run_service(repository, storage, apply_tags=True)

    assert result.run is not None
    assert result.run.status is ReconciliationRunStatus.COMPLETED
    assert result.run.candidate_count == 2
    assert result.run.failure_count == 2
    assert result.run.tagged_count == 0
    writes = {item.object_key: item for item in repository.completions[0][2]}
    assert writes[capacity_key].tag_status is TagStatus.CAPACITY_CONFLICT
    assert writes[capacity_key].failure_code == "object_storage_tag_capacity_conflict"
    assert writes[failed_key].tag_status is TagStatus.FAILED
    assert writes[failed_key].failure_code == "object_storage_tag_read_failed"


def test_unchanged_candidate_tags_are_successful_without_counting_a_write() -> None:
    candidate = metadata("4", age_seconds=200_000)
    repository = FakeRepository()
    storage = FakeStorage(
        [page(candidate)],
        tag_results={candidate.key: ObjectTagMutation(changed=False, tag_count=2)},
    )

    result = run_service(repository, storage, apply_tags=True)

    assert result.run is not None
    assert result.run.tagged_count == 0
    assert repository.completions[0][2][0].tag_status is TagStatus.APPLIED


def test_unexpected_and_malformed_tag_errors_are_sanitized_and_recorded() -> None:
    unexpected = metadata("5", age_seconds=200_000)
    malformed = metadata("6", age_seconds=200_000)
    repository = FakeRepository()
    storage = FakeStorage(
        [page(unexpected, malformed)],
        tag_results={
            unexpected.key: RuntimeError("private raw object key diagnostic"),
            malformed.key: ObjectStorageOperationError("unsafe raw failure\ncode"),
        },
    )

    result = run_service(repository, storage, apply_tags=True)

    assert result.run is not None
    assert result.run.failure_count == 2
    writes = {item.object_key: item for item in repository.completions[0][2]}
    assert writes[unexpected.key].failure_code == "object_storage_tag_failed"
    assert writes[malformed.key].failure_code == "object_storage_tag_failed"


def test_fatal_list_error_persists_a_sanitized_failed_immutable_run() -> None:
    repository = FakeRepository(continuation_cursor="stale-failed-cursor")
    storage = FakeStorage([ObjectStorageOperationError("object_storage_list_failed")])
    telemetry = RecordingTelemetry()

    result = run_service(repository, storage, telemetry=telemetry)

    assert result.run is not None
    assert result.run.status is ReconciliationRunStatus.FAILED
    assert result.run.failure_code == "object_storage_list_failed"
    assert result.run.failure_count == 1
    assert result.run.truncated is True
    assert storage.list_calls == [(1_000, "stale-failed-cursor")]
    assert repository.completions[0][2] == ()
    assert repository.completions[0][3] is None
    assert repository.continuation_cursor is None
    assert telemetry.runs[0]["failure_code"] == "object_storage_list_failed"

    restart_storage = FakeStorage([page()])
    restart = run_service(repository, restart_storage)
    assert restart.run is not None
    assert restart.run.status is ReconciliationRunStatus.COMPLETED
    assert restart_storage.list_calls == [(1_000, None)]


def test_invalid_persisted_cursor_fails_closed_clears_without_provider_call() -> None:
    repository = FakeRepository(continuation_cursor="unsafe\nopaque-cursor")
    storage = FakeStorage([])

    result = run_service(repository, storage)

    assert result.run is not None
    assert result.run.status is ReconciliationRunStatus.FAILED
    assert result.run.failure_code == "object_storage_pagination_invalid"
    assert storage.list_calls == []
    assert repository.completions[0][3] is None
    assert repository.continuation_cursor is None


def test_unexpected_list_exception_is_sanitized() -> None:
    repository = FakeRepository()
    storage = FakeStorage([RuntimeError("private provider diagnostic with object key")])

    result = run_service(repository, storage)

    assert result.run is not None
    assert result.run.status is ReconciliationRunStatus.FAILED
    assert result.run.failure_code == "object_storage_list_failed"


@pytest.mark.parametrize(
    "malformed_page",
    [
        page(metadata("7", age_seconds=200_000), metadata("8", age_seconds=200_000)),
        ObjectPage(
            objects=(),
            is_truncated=False,
            next_continuation_token="unexpected-token",
        ),
        ObjectPage(
            objects=(),
            is_truncated=True,
            next_continuation_token="empty-page-token",
        ),
        ObjectPage(
            objects=(metadata("8", age_seconds=200_000),),
            is_truncated=True,
            next_continuation_token="unsafe\nnext-token",
        ),
        page(
            ObjectMetadata(
                key=source_key("9"),
                size=-1,
                last_modified=NOW - timedelta(days=2),
            )
        ),
    ],
)
def test_malformed_storage_pages_fail_closed(malformed_page: ObjectPage) -> None:
    repository = FakeRepository()
    storage = FakeStorage([malformed_page])

    result = run_service(repository, storage, max_objects=1)

    assert result.run is not None
    assert result.run.status is ReconciliationRunStatus.FAILED
    assert result.run.failure_code == "object_storage_list_failed"
    assert result.run.scanned_count == 0


def test_reconciliation_rejects_a_naive_clock_before_acquiring_a_lease() -> None:
    repository = FakeRepository()
    service = StorageReconciliationService(
        repository,
        FakeStorage([]),
        ReconciliationPolicy(),
        now=lambda: datetime(2026, 1, 1),
    )

    with pytest.raises(ValueError, match="timezone"):
        asyncio.run(service.reconcile())

    assert repository.acquire_calls == []


def test_reconciliation_runtime_contains_no_object_delete_capability_or_call() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src" / "exam_guru_api"
    implementation = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            source_root / "infrastructure" / "object_storage.py",
            source_root / "storage_reconciliation" / "service.py",
            source_root / "storage_reconciliation" / "jobs.py",
        )
    )

    assert "delete_object" not in implementation


def test_repeated_pagination_token_fails_closed_without_an_unbounded_loop() -> None:
    repository = FakeRepository()
    storage = FakeStorage(
        [
            page(metadata("a", age_seconds=200_000), next_token="repeat"),
            page(metadata("b", age_seconds=200_000), next_token="repeat"),
        ]
    )

    result = run_service(repository, storage)

    assert result.run is not None
    assert result.run.status is ReconciliationRunStatus.FAILED
    assert result.run.failure_code == "object_storage_pagination_invalid"
    assert result.run.failure_count == 1
    assert storage.list_calls == [(1_000, None), (1_000, "repeat")]
    assert repository.completions[0][3] is None

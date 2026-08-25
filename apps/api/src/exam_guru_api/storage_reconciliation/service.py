from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from exam_guru_api.core.config import STORAGE_RECONCILIATION_ACTOR_MAX_EXECUTION_SECONDS
from exam_guru_api.infrastructure.object_storage import (
    ObjectMetadata,
    ObjectPage,
    ObjectStorageOperationError,
    ObjectTagCapacityError,
    ObjectTagMutation,
    validate_source_object_key,
)
from exam_guru_api.observability import get_operational_telemetry
from exam_guru_api.storage_reconciliation.models import (
    FindingStatus,
    ReconciliationRunStatus,
    TagStatus,
)

_MAX_INTERVAL_SECONDS = 31_536_000
_MAX_GRACE_SECONDS = 31_536_000
_LIST_PAGE_SIZE = 1_000
_MAX_CONTINUATION_CURSOR_LENGTH = 2_048
_FAILURE_CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")


@dataclass(frozen=True, slots=True)
class ReconciliationPolicy:
    interval_seconds: int = 3_600
    grace_seconds: int = 86_400
    max_objects_per_run: int = 1_000
    apply_tags: bool = False

    def __post_init__(self) -> None:
        if not _bounded_integer(self.interval_seconds, minimum=300, maximum=_MAX_INTERVAL_SECONDS):
            raise ValueError("reconciliation interval must be between 300 and 31536000 seconds")
        if not _bounded_integer(self.grace_seconds, minimum=3_600, maximum=_MAX_GRACE_SECONDS):
            raise ValueError("reconciliation grace must be between 3600 and 31536000 seconds")
        if not _bounded_integer(self.max_objects_per_run, minimum=1, maximum=10_000):
            raise ValueError("reconciliation object limit must be between 1 and 10000")
        if not isinstance(self.apply_tags, bool):
            raise ValueError("reconciliation tag mode must be boolean")


@dataclass(frozen=True, slots=True)
class FindingRecord:
    object_key: str
    checksum_sha256: str
    first_seen_at: datetime
    last_seen_at: datetime
    candidate_since: datetime
    size_bytes: int
    object_last_modified_at: datetime
    status: FindingStatus
    tag_status: TagStatus
    tag_updated_at: datetime | None
    resolved_at: datetime | None
    failure_code: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ReconciliationRunRecord:
    id: UUID
    started_at: datetime
    completed_at: datetime
    status: ReconciliationRunStatus
    apply_tags: bool
    grace_seconds: int
    max_objects: int
    scanned_count: int
    referenced_count: int
    candidate_count: int
    resolved_count: int
    tagged_count: int
    failure_count: int
    truncated: bool
    failure_code: str | None


@dataclass(frozen=True, slots=True)
class ReconciliationExecution:
    skipped: bool
    run: ReconciliationRunRecord | None


@dataclass(frozen=True, slots=True)
class ReconciliationLease:
    token: UUID
    continuation_cursor: str | None


class StorageReconciliationRepository(Protocol):
    async def acquire_lease(
        self,
        *,
        now: datetime,
        interval_seconds: int,
        lease_seconds: int,
    ) -> ReconciliationLease | None: ...

    async def referenced_keys(self, keys: tuple[str, ...]) -> set[str]: ...

    async def findings_for_keys(self, keys: tuple[str, ...]) -> dict[str, FindingRecord]: ...

    async def complete_run(
        self,
        *,
        lease_token: UUID,
        run: ReconciliationRunRecord,
        findings: tuple[FindingRecord, ...],
        continuation_cursor: str | None,
    ) -> None: ...


class ReconciliationTelemetry(Protocol):
    def storage_reconciliation_terminal(
        self,
        *,
        status: str,
        failure_code: str | None,
        scanned_count: int,
        referenced_count: int,
        candidate_count: int,
        resolved_count: int,
        tagged_count: int,
        failure_count: int,
        truncated: bool,
    ) -> None: ...


class ReconciliationObjectStorage(Protocol):
    def list_source_objects(
        self,
        *,
        max_keys: int,
        continuation_token: str | None = None,
    ) -> ObjectPage: ...

    def merge_reconciliation_tags(
        self,
        key: str,
        *,
        candidate_detected_at: datetime | None,
    ) -> ObjectTagMutation: ...


@dataclass(slots=True)
class _ScanAccounting:
    scanned_count: int = 0
    referenced_count: int = 0
    candidate_count: int = 0
    resolved_count: int = 0
    tagged_count: int = 0
    failure_count: int = 0
    truncated: bool = False
    failure_code: str | None = None


class StorageReconciliationService:
    def __init__(
        self,
        repository: StorageReconciliationRepository,
        storage: ReconciliationObjectStorage,
        policy: ReconciliationPolicy,
        *,
        now: Callable[[], datetime] | None = None,
        telemetry: ReconciliationTelemetry | None = None,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._policy = policy
        self._now = now or (lambda: datetime.now(UTC))
        self._telemetry = telemetry or get_operational_telemetry()

    async def reconcile(self) -> ReconciliationExecution:
        started_at = self._utc_now()
        lease_seconds = max(
            self._policy.interval_seconds,
            STORAGE_RECONCILIATION_ACTOR_MAX_EXECUTION_SECONDS + 1,
        )
        lease = await self._repository.acquire_lease(
            now=started_at,
            interval_seconds=self._policy.interval_seconds,
            lease_seconds=lease_seconds,
        )
        if lease is None:
            return ReconciliationExecution(skipped=True, run=None)

        accounting, findings, status, continuation_cursor = await self._scan(
            started_at,
            continuation_cursor=lease.continuation_cursor,
        )
        completed_at = self._utc_now()
        run = ReconciliationRunRecord(
            id=lease.token,
            started_at=started_at,
            completed_at=max(started_at, completed_at),
            status=status,
            apply_tags=self._policy.apply_tags,
            grace_seconds=self._policy.grace_seconds,
            max_objects=self._policy.max_objects_per_run,
            scanned_count=accounting.scanned_count,
            referenced_count=accounting.referenced_count,
            candidate_count=accounting.candidate_count,
            resolved_count=accounting.resolved_count,
            tagged_count=accounting.tagged_count,
            failure_count=accounting.failure_count,
            truncated=accounting.truncated,
            failure_code=accounting.failure_code,
        )
        await self._repository.complete_run(
            lease_token=lease.token,
            run=run,
            findings=tuple(findings.values()),
            continuation_cursor=continuation_cursor,
        )
        self._telemetry.storage_reconciliation_terminal(
            status=run.status.value,
            failure_code=run.failure_code,
            scanned_count=run.scanned_count,
            referenced_count=run.referenced_count,
            candidate_count=run.candidate_count,
            resolved_count=run.resolved_count,
            tagged_count=run.tagged_count,
            failure_count=run.failure_count,
            truncated=run.truncated,
        )
        return ReconciliationExecution(skipped=False, run=run)

    async def _scan(
        self,
        observed_at: datetime,
        *,
        continuation_cursor: str | None,
    ) -> tuple[
        _ScanAccounting,
        dict[str, FindingRecord],
        ReconciliationRunStatus,
        str | None,
    ]:
        accounting = _ScanAccounting()
        writes: dict[str, FindingRecord] = {}
        continuation_token = continuation_cursor
        seen_tokens = {continuation_cursor} if continuation_cursor is not None else set()
        seen_keys: set[str] = set()
        next_cursor: str | None = None
        cutoff = observed_at - timedelta(seconds=self._policy.grace_seconds)
        if continuation_cursor is not None and not _valid_continuation_cursor(continuation_cursor):
            self._fail_scan(accounting, "object_storage_pagination_invalid")
            return accounting, writes, ReconciliationRunStatus.FAILED, None

        while True:
            remaining = self._policy.max_objects_per_run - accounting.scanned_count
            page_size = min(_LIST_PAGE_SIZE, remaining)
            try:
                page = await asyncio.to_thread(
                    self._storage.list_source_objects,
                    max_keys=page_size,
                    continuation_token=continuation_token,
                )
                self._validate_page(page, page_size=page_size, seen_keys=seen_keys)
            except ObjectStorageOperationError as error:
                self._fail_scan(accounting, error.failure_code)
                return accounting, writes, ReconciliationRunStatus.FAILED, None
            except Exception:
                self._fail_scan(accounting, "object_storage_list_failed")
                return accounting, writes, ReconciliationRunStatus.FAILED, None

            keys = tuple(item.key for item in page.objects)
            seen_keys.update(keys)
            accounting.scanned_count += len(keys)
            referenced = await self._repository.referenced_keys(keys)
            existing = await self._repository.findings_for_keys(keys)
            for item in page.objects:
                current = existing.get(item.key)
                if item.key in referenced:
                    accounting.referenced_count += 1
                    if current is not None and current.status is FindingStatus.CANDIDATE:
                        accounting.resolved_count += 1
                        writes[item.key] = await self._resolved_finding(
                            current,
                            item,
                            observed_at=observed_at,
                            accounting=accounting,
                        )
                    elif (
                        current is not None
                        and self._policy.apply_tags
                        and current.status is FindingStatus.RESOLVED
                        and current.tag_status is not TagStatus.REMOVED
                    ):
                        writes[item.key] = await self._retry_resolved_tag_removal(
                            current,
                            item,
                            observed_at=observed_at,
                            accounting=accounting,
                        )
                    continue
                if item.last_modified > cutoff:
                    continue

                accounting.candidate_count += 1
                writes[item.key] = await self._candidate_finding(
                    current,
                    item,
                    observed_at=observed_at,
                    accounting=accounting,
                )

            if accounting.scanned_count >= self._policy.max_objects_per_run:
                accounting.truncated = page.is_truncated
                next_cursor = page.next_continuation_token if page.is_truncated else None
                break
            if not page.is_truncated:
                break
            next_token = page.next_continuation_token
            if next_token is None or next_token == continuation_token or next_token in seen_tokens:
                self._fail_scan(accounting, "object_storage_pagination_invalid")
                return accounting, writes, ReconciliationRunStatus.FAILED, None
            seen_tokens.add(next_token)
            continuation_token = next_token

        return accounting, writes, ReconciliationRunStatus.COMPLETED, next_cursor

    async def _candidate_finding(
        self,
        existing: FindingRecord | None,
        item: ObjectMetadata,
        *,
        observed_at: datetime,
        accounting: _ScanAccounting,
    ) -> FindingRecord:
        if existing is None:
            record = FindingRecord(
                object_key=item.key,
                checksum_sha256=_checksum_from_key(item.key),
                first_seen_at=observed_at,
                last_seen_at=observed_at,
                candidate_since=observed_at,
                size_bytes=item.size,
                object_last_modified_at=item.last_modified,
                status=FindingStatus.CANDIDATE,
                tag_status=TagStatus.NOT_REQUESTED,
                tag_updated_at=None,
                resolved_at=None,
                failure_code=None,
                created_at=observed_at,
                updated_at=observed_at,
            )
        else:
            candidate_since = (
                existing.candidate_since
                if existing.status is FindingStatus.CANDIDATE
                else observed_at
            )
            record = replace(
                existing,
                last_seen_at=observed_at,
                candidate_since=candidate_since,
                size_bytes=item.size,
                object_last_modified_at=item.last_modified,
                status=FindingStatus.CANDIDATE,
                resolved_at=None,
                updated_at=observed_at,
            )
        if not self._policy.apply_tags:
            return record
        return await self._apply_tag_result(
            record,
            candidate_detected_at=record.candidate_since,
            success_status=TagStatus.APPLIED,
            accounting=accounting,
            observed_at=observed_at,
        )

    async def _resolved_finding(
        self,
        existing: FindingRecord,
        item: ObjectMetadata,
        *,
        observed_at: datetime,
        accounting: _ScanAccounting,
    ) -> FindingRecord:
        record = replace(
            existing,
            last_seen_at=observed_at,
            size_bytes=item.size,
            object_last_modified_at=item.last_modified,
            status=FindingStatus.RESOLVED,
            resolved_at=observed_at,
            updated_at=observed_at,
        )
        if not self._policy.apply_tags:
            return record
        return await self._apply_tag_result(
            record,
            candidate_detected_at=None,
            success_status=TagStatus.REMOVED,
            accounting=accounting,
            observed_at=observed_at,
        )

    async def _retry_resolved_tag_removal(
        self,
        existing: FindingRecord,
        item: ObjectMetadata,
        *,
        observed_at: datetime,
        accounting: _ScanAccounting,
    ) -> FindingRecord:
        record = replace(
            existing,
            last_seen_at=observed_at,
            size_bytes=item.size,
            object_last_modified_at=item.last_modified,
            updated_at=observed_at,
        )
        return await self._apply_tag_result(
            record,
            candidate_detected_at=None,
            success_status=TagStatus.REMOVED,
            accounting=accounting,
            observed_at=observed_at,
        )

    async def _apply_tag_result(
        self,
        record: FindingRecord,
        *,
        candidate_detected_at: datetime | None,
        success_status: TagStatus,
        accounting: _ScanAccounting,
        observed_at: datetime,
    ) -> FindingRecord:
        try:
            mutation = await asyncio.to_thread(
                self._storage.merge_reconciliation_tags,
                record.object_key,
                candidate_detected_at=candidate_detected_at,
            )
        except ObjectTagCapacityError as error:
            accounting.failure_count += 1
            return replace(
                record,
                tag_status=TagStatus.CAPACITY_CONFLICT,
                tag_updated_at=observed_at,
                failure_code=error.failure_code,
            )
        except ObjectStorageOperationError as error:
            accounting.failure_count += 1
            return replace(
                record,
                tag_status=TagStatus.FAILED,
                tag_updated_at=observed_at,
                failure_code=_safe_failure_code(
                    error.failure_code,
                    fallback="object_storage_tag_failed",
                ),
            )
        except Exception:
            accounting.failure_count += 1
            return replace(
                record,
                tag_status=TagStatus.FAILED,
                tag_updated_at=observed_at,
                failure_code="object_storage_tag_failed",
            )
        if mutation.changed:
            accounting.tagged_count += 1
        return replace(
            record,
            tag_status=success_status,
            tag_updated_at=observed_at,
            failure_code=None,
        )

    @staticmethod
    def _validate_page(
        page: ObjectPage,
        *,
        page_size: int,
        seen_keys: set[str],
    ) -> None:
        if not isinstance(page, ObjectPage) or len(page.objects) > page_size:
            raise ValueError
        if page.is_truncated != (page.next_continuation_token is not None):
            raise ValueError
        if page.is_truncated and (
            not page.objects
            or page.next_continuation_token is None
            or not _valid_continuation_cursor(page.next_continuation_token)
        ):
            raise ValueError
        page_keys: set[str] = set()
        for item in page.objects:
            if (
                not isinstance(item, ObjectMetadata)
                or validate_source_object_key(item.key) != item.key
                or not _bounded_integer(item.size, minimum=0, maximum=5 * 1024**4)
                or item.last_modified.utcoffset() is None
                or item.key in page_keys
                or item.key in seen_keys
            ):
                raise ValueError
            page_keys.add(item.key)

    @staticmethod
    def _fail_scan(accounting: _ScanAccounting, failure_code: str) -> None:
        accounting.failure_count += 1
        accounting.failure_code = _safe_failure_code(
            failure_code,
            fallback="object_storage_list_failed",
        )
        accounting.truncated = True

    def _utc_now(self) -> datetime:
        value = self._now()
        if value.utcoffset() is None:
            raise ValueError("reconciliation clock must include a timezone")
        return value.astimezone(UTC)


def _bounded_integer(value: object, *, minimum: int, maximum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= maximum


def _valid_continuation_cursor(value: str) -> bool:
    return (
        1 <= len(value) <= _MAX_CONTINUATION_CURSOR_LENGTH
        and value.isprintable()
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def _checksum_from_key(key: str) -> str:
    return key.rsplit("/", maxsplit=1)[1][:-4]


def _safe_failure_code(value: str, *, fallback: str) -> str:
    return value if _FAILURE_CODE.fullmatch(value) else fallback

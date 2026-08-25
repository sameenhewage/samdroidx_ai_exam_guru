from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.storage_reconciliation.models import (
    FindingStatus,
    StorageOrphanFindingModel,
    StorageReconciliationRunModel,
    StorageReconciliationStateModel,
    TagStatus,
)
from exam_guru_api.storage_reconciliation.service import (
    FindingRecord,
    ReconciliationLease,
    ReconciliationRunRecord,
)

MAX_FINDING_UPSERT_BATCH_SIZE = 500


def _finding_batches[Value](values: tuple[Value, ...]) -> tuple[tuple[Value, ...], ...]:
    return tuple(
        values[offset : offset + MAX_FINDING_UPSERT_BATCH_SIZE]
        for offset in range(0, len(values), MAX_FINDING_UPSERT_BATCH_SIZE)
    )


class ReconciliationLeaseLostError(RuntimeError):
    pass


class SqlAlchemyStorageReconciliationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def acquire_lease(
        self,
        *,
        now: datetime,
        interval_seconds: int,
        lease_seconds: int,
    ) -> ReconciliationLease | None:
        async with self._session.begin():
            state = (
                await self._session.execute(
                    select(StorageReconciliationStateModel)
                    .where(StorageReconciliationStateModel.singleton_id == 1)
                    .with_for_update()
                )
            ).scalar_one()
            if state.lease_expires_at is not None and state.lease_expires_at > now:
                return None
            if (
                state.last_started_at is not None
                and state.last_started_at + timedelta(seconds=interval_seconds) > now
            ):
                return None

            lease_token = uuid4()
            state.last_started_at = now
            state.lease_token = lease_token
            state.lease_acquired_at = now
            state.lease_expires_at = now + timedelta(seconds=lease_seconds)
            return ReconciliationLease(
                token=lease_token,
                continuation_cursor=state.continuation_cursor,
            )

    async def referenced_keys(self, keys: tuple[str, ...]) -> set[str]:
        if not keys:
            return set()
        return set(
            await self._session.scalars(
                select(SourceDocumentModel.object_key).where(
                    SourceDocumentModel.object_key.in_(keys)
                )
            )
        )

    async def findings_for_keys(self, keys: tuple[str, ...]) -> dict[str, FindingRecord]:
        if not keys:
            return {}
        models = tuple(
            await self._session.scalars(
                select(StorageOrphanFindingModel).where(
                    StorageOrphanFindingModel.object_key.in_(keys)
                )
            )
        )
        return {model.object_key: self._record(model) for model in models}

    async def complete_run(
        self,
        *,
        lease_token: UUID,
        run: ReconciliationRunRecord,
        findings: tuple[FindingRecord, ...],
        continuation_cursor: str | None,
    ) -> None:
        try:
            released = await self._session.scalar(
                update(StorageReconciliationStateModel)
                .where(
                    StorageReconciliationStateModel.singleton_id == 1,
                    StorageReconciliationStateModel.lease_token == lease_token,
                )
                .values(
                    last_completed_at=run.completed_at,
                    continuation_cursor=continuation_cursor,
                    lease_token=None,
                    lease_acquired_at=None,
                    lease_expires_at=None,
                )
                .returning(StorageReconciliationStateModel.singleton_id)
            )
            if released != 1:
                raise ReconciliationLeaseLostError

            if findings:
                for batch in _finding_batches(findings):
                    statement = postgresql_insert(StorageOrphanFindingModel).values(
                        [self._finding_values(item) for item in batch]
                    )
                    excluded = statement.excluded
                    await self._session.execute(
                        statement.on_conflict_do_update(
                            index_elements=[StorageOrphanFindingModel.object_key],
                            set_={
                                "last_seen_at": excluded.last_seen_at,
                                "candidate_since": excluded.candidate_since,
                                "size_bytes": excluded.size_bytes,
                                "object_last_modified_at": excluded.object_last_modified_at,
                                "status": excluded.status,
                                "tag_status": excluded.tag_status,
                                "tag_updated_at": excluded.tag_updated_at,
                                "resolved_at": excluded.resolved_at,
                                "failure_code": excluded.failure_code,
                                "updated_at": excluded.updated_at,
                            },
                        )
                    )

            self._session.add(
                StorageReconciliationRunModel(
                    id=run.id,
                    started_at=run.started_at,
                    completed_at=run.completed_at,
                    status=run.status.value,
                    apply_tags=run.apply_tags,
                    grace_seconds=run.grace_seconds,
                    max_objects=run.max_objects,
                    scanned_count=run.scanned_count,
                    referenced_count=run.referenced_count,
                    candidate_count=run.candidate_count,
                    resolved_count=run.resolved_count,
                    tagged_count=run.tagged_count,
                    failure_count=run.failure_count,
                    truncated=run.truncated,
                    failure_code=run.failure_code,
                )
            )
            await self._session.commit()
        except BaseException:
            await self._session.rollback()
            raise

    @staticmethod
    def _record(model: StorageOrphanFindingModel) -> FindingRecord:
        return FindingRecord(
            object_key=model.object_key,
            checksum_sha256=model.checksum_sha256,
            first_seen_at=model.first_seen_at,
            last_seen_at=model.last_seen_at,
            candidate_since=model.candidate_since,
            size_bytes=model.size_bytes,
            object_last_modified_at=model.object_last_modified_at,
            status=FindingStatus(model.status),
            tag_status=TagStatus(model.tag_status),
            tag_updated_at=model.tag_updated_at,
            resolved_at=model.resolved_at,
            failure_code=model.failure_code,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    @staticmethod
    def _finding_values(item: FindingRecord) -> dict[str, object]:
        return {
            "object_key": item.object_key,
            "checksum_sha256": item.checksum_sha256,
            "first_seen_at": item.first_seen_at,
            "last_seen_at": item.last_seen_at,
            "candidate_since": item.candidate_since,
            "size_bytes": item.size_bytes,
            "object_last_modified_at": item.object_last_modified_at,
            "status": item.status.value,
            "tag_status": item.tag_status.value,
            "tag_updated_at": item.tag_updated_at,
            "resolved_at": item.resolved_at,
            "failure_code": item.failure_code,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
        }

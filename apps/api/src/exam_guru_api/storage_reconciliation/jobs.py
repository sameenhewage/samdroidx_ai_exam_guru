from __future__ import annotations

import asyncio

import dramatiq

from exam_guru_api.core.config import (
    STORAGE_RECONCILIATION_ACTOR_MAX_EXECUTION_SECONDS,
    Settings,
)
from exam_guru_api.infrastructure.object_storage import create_object_storage
from exam_guru_api.infrastructure.resources import create_resources
from exam_guru_api.storage_reconciliation.repository import (
    SqlAlchemyStorageReconciliationRepository,
)
from exam_guru_api.storage_reconciliation.service import (
    ReconciliationPolicy,
    StorageReconciliationService,
)

RECONCILIATION_QUEUE_NAME = "storage-reconciliation"
RECONCILIATION_MAX_RETRIES = 0
RECONCILIATION_TIME_LIMIT_MS = STORAGE_RECONCILIATION_ACTOR_MAX_EXECUTION_SECONDS * 1_000


class StorageReconciliationActorError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("storage_reconciliation_failed")


async def _reconcile_source_objects() -> None:
    resources = None
    storage = None
    failed = False
    try:
        settings = Settings()
        resources = create_resources(settings)
        storage = create_object_storage(settings)
        async with resources.session_factory() as session:
            await StorageReconciliationService(
                SqlAlchemyStorageReconciliationRepository(session),
                storage,
                ReconciliationPolicy(
                    interval_seconds=settings.storage_reconciliation_interval_seconds,
                    grace_seconds=settings.storage_reconciliation_grace_seconds,
                    max_objects_per_run=(settings.storage_reconciliation_max_objects_per_run),
                    apply_tags=settings.storage_reconciliation_apply_tags,
                ),
            ).reconcile()
    except Exception:
        failed = True
    finally:
        if storage is not None:
            try:
                storage.close()
            except Exception:
                failed = True
        if resources is not None:
            try:
                await resources.close()
            except Exception:
                failed = True
    if failed:
        raise StorageReconciliationActorError from None


@dramatiq.actor(
    queue_name=RECONCILIATION_QUEUE_NAME,
    max_retries=RECONCILIATION_MAX_RETRIES,
    time_limit=RECONCILIATION_TIME_LIMIT_MS,
)
def reconcile_source_objects() -> None:
    asyncio.run(_reconcile_source_objects())

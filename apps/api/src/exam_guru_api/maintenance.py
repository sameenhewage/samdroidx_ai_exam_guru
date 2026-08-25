import logging
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import Event
from typing import Protocol, cast

import dramatiq
from dramatiq.brokers.redis import RedisBroker

from exam_guru_api.core.config import Settings
from exam_guru_api.documents.jobs import recover_extraction_jobs
from exam_guru_api.generation.jobs import recover_generation_jobs
from exam_guru_api.knowledge.embedding_jobs import recover_embedding_jobs
from exam_guru_api.storage_reconciliation.jobs import reconcile_source_objects

_logger = logging.getLogger(__name__)


class _QueuedMessage(Protocol):
    message_id: str


class RecoveryActor(Protocol):
    def send(self) -> _QueuedMessage: ...


class StopSignal(Protocol):
    def is_set(self) -> bool: ...

    def set(self) -> None: ...

    def wait(self, timeout: float) -> bool: ...


@dataclass(frozen=True, slots=True)
class MaintenanceTickResult:
    enqueued: int
    failures: int


_RECOVERY_ACTORS = (
    cast(RecoveryActor, recover_extraction_jobs),
    cast(RecoveryActor, recover_generation_jobs),
    cast(RecoveryActor, recover_embedding_jobs),
    cast(RecoveryActor, reconcile_source_objects),
)
_RECOVERY_NAMES = ("extraction", "generation", "embedding", "storage_reconciliation")


def enqueue_recovery_jobs(
    extraction_actor: RecoveryActor = _RECOVERY_ACTORS[0],
    generation_actor: RecoveryActor = _RECOVERY_ACTORS[1],
    embedding_actor: RecoveryActor = _RECOVERY_ACTORS[2],
    reconciliation_actor: RecoveryActor = _RECOVERY_ACTORS[3],
) -> MaintenanceTickResult:
    enqueued = 0
    failures = 0
    for name, actor in zip(
        _RECOVERY_NAMES,
        (extraction_actor, generation_actor, embedding_actor, reconciliation_actor),
        strict=True,
    ):
        try:
            actor.send()
        except Exception:
            failures += 1
            _logger.error("maintenance recovery enqueue failed: %s", name)
        else:
            enqueued += 1
    return MaintenanceTickResult(enqueued=enqueued, failures=failures)


def run_maintenance_loop(
    tick: Callable[[], MaintenanceTickResult],
    *,
    interval_seconds: int,
    stop_signal: StopSignal,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    if (
        not isinstance(interval_seconds, int)
        or isinstance(interval_seconds, bool)
        or not 5 <= interval_seconds <= 3_600
    ):
        raise ValueError("maintenance interval must be between 5 and 3600 seconds")
    if stop_signal.is_set():
        return

    next_deadline = monotonic()
    while not stop_signal.is_set():
        try:
            tick()
        except KeyboardInterrupt:
            return
        except Exception:
            _logger.error("maintenance tick failed")

        next_deadline += interval_seconds
        now = monotonic()
        while next_deadline <= now:
            next_deadline += interval_seconds
        try:
            if stop_signal.wait(next_deadline - now):
                return
        except KeyboardInterrupt:
            return


def create_maintenance_broker(settings: Settings) -> RedisBroker:
    broker = RedisBroker(url=settings.valkey_url.get_secret_value())
    dramatiq.set_broker(broker)
    for actor in _RECOVERY_ACTORS:
        actor.broker = broker  # type: ignore[attr-defined]
        broker.declare_actor(actor)  # type: ignore[arg-type]
    return broker


def main() -> None:
    settings = Settings()
    broker = create_maintenance_broker(settings)
    stop_event = Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    previous_handler = signal.signal(signal.SIGTERM, request_stop)
    try:
        run_maintenance_loop(
            lambda: enqueue_recovery_jobs(*_RECOVERY_ACTORS),
            interval_seconds=settings.maintenance_scheduler_interval_seconds,
            stop_signal=stop_event,
        )
    finally:
        try:
            signal.signal(signal.SIGTERM, previous_handler)
        finally:
            broker.close()

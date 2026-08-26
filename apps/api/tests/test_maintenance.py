import signal
from collections.abc import Callable
from types import FrameType
from typing import cast

import pytest

from exam_guru_api.core.config import Settings
from exam_guru_api.documents.jobs import recover_extraction_jobs
from exam_guru_api.generation.jobs import recover_generation_jobs
from exam_guru_api.knowledge.embedding_jobs import recover_embedding_jobs
from exam_guru_api.maintenance import (
    MaintenanceTickResult,
    create_maintenance_broker,
    enqueue_recovery_jobs,
    main,
    run_maintenance_loop,
)
from exam_guru_api.storage_reconciliation.jobs import reconcile_source_objects
from exam_guru_api.teacher_papers.jobs import recover_teacher_papers


class StubMessage:
    message_id = "maintenance-message"


class RecordingRecoveryActor:
    def __init__(
        self,
        name: str,
        calls: list[str],
        *,
        error: Exception | None = None,
    ) -> None:
        self.name = name
        self.calls = calls
        self.error = error

    def send(self) -> StubMessage:
        self.calls.append(self.name)
        if self.error is not None:
            raise self.error
        return StubMessage()


class RecordingLogger:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def error(self, message: str, *args: object) -> None:
        self.errors.append(message % args if args else message)


class LoopStopSignal:
    def __init__(self, *, stop_after_waits: int) -> None:
        self.stop_after_waits = stop_after_waits
        self.waits: list[float] = []
        self.set_calls = 0

    def is_set(self) -> bool:
        return self.set_calls > 0

    def set(self) -> None:
        self.set_calls += 1

    def wait(self, timeout: float) -> bool:
        self.waits.append(timeout)
        return len(self.waits) >= self.stop_after_waits


def monotonic_clock(*values: float) -> Callable[[], float]:
    remaining = list(values)

    def monotonic() -> float:
        return remaining.pop(0)

    return monotonic


def test_scheduler_tick_enqueues_all_maintenance_actors_with_error_isolation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from exam_guru_api import maintenance

    calls: list[str] = []
    logger = RecordingLogger()
    monkeypatch.setattr(maintenance, "_logger", logger)
    actors = (
        RecordingRecoveryActor("extraction", calls),
        RecordingRecoveryActor(
            "generation",
            calls,
            error=RuntimeError("private valkey transport diagnostic raw-payload"),
        ),
        RecordingRecoveryActor("embedding", calls),
        RecordingRecoveryActor("storage_reconciliation", calls),
        RecordingRecoveryActor("teacher_papers", calls),
    )

    result = enqueue_recovery_jobs(
        extraction_actor=actors[0],
        generation_actor=actors[1],
        embedding_actor=actors[2],
        reconciliation_actor=actors[3],
        teacher_paper_actor=actors[4],
    )

    assert result == MaintenanceTickResult(enqueued=4, failures=1)
    assert calls == [
        "extraction",
        "generation",
        "embedding",
        "storage_reconciliation",
        "teacher_papers",
    ]
    assert logger.errors == ["maintenance recovery enqueue failed: generation"]
    assert "private" not in repr(logger.errors)
    assert "raw-payload" not in repr(logger.errors)


def test_scheduler_loop_uses_monotonic_deadlines_sleeps_after_errors_and_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from exam_guru_api import maintenance

    stop = LoopStopSignal(stop_after_waits=2)
    logger = RecordingLogger()
    monkeypatch.setattr(maintenance, "_logger", logger)
    calls = 0

    def tick() -> MaintenanceTickResult:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("private valkey transport diagnostic raw-payload")
        return MaintenanceTickResult(enqueued=3, failures=0)

    run_maintenance_loop(
        tick,
        interval_seconds=5,
        stop_signal=stop,
        monotonic=monotonic_clock(100.0, 101.0, 120.0),
    )

    assert calls == 2
    assert stop.waits == [4.0, 5.0]
    assert logger.errors == ["maintenance tick failed"]
    assert "private" not in repr(logger.errors)
    assert all(timeout > 0 for timeout in stop.waits)


def test_scheduler_loop_handles_pre_stop_and_keyboard_interrupt_without_busy_loop() -> None:
    already_stopped = LoopStopSignal(stop_after_waits=1)
    already_stopped.set()
    calls = 0

    def should_not_tick() -> MaintenanceTickResult:
        nonlocal calls
        calls += 1
        return MaintenanceTickResult(enqueued=3, failures=0)

    run_maintenance_loop(
        should_not_tick,
        interval_seconds=5,
        stop_signal=already_stopped,
        monotonic=monotonic_clock(0.0),
    )
    assert calls == 0
    assert already_stopped.waits == []

    interrupted = LoopStopSignal(stop_after_waits=1)

    def interrupt_tick() -> MaintenanceTickResult:
        raise KeyboardInterrupt

    run_maintenance_loop(
        interrupt_tick,
        interval_seconds=5,
        stop_signal=interrupted,
        monotonic=monotonic_clock(0.0),
    )
    assert interrupted.waits == []

    class InterruptingWait(LoopStopSignal):
        def wait(self, timeout: float) -> bool:
            self.waits.append(timeout)
            raise KeyboardInterrupt

    wait_interrupted = InterruptingWait(stop_after_waits=1)
    run_maintenance_loop(
        should_not_tick,
        interval_seconds=5,
        stop_signal=wait_interrupted,
        monotonic=monotonic_clock(0.0, 1.0),
    )
    assert wait_interrupted.waits == [4.0]

    with pytest.raises(ValueError, match="interval"):
        run_maintenance_loop(
            should_not_tick,
            interval_seconds=4,
            stop_signal=LoopStopSignal(stop_after_waits=1),
        )


def test_maintenance_broker_registers_only_internal_recovery_actors() -> None:
    broker = create_maintenance_broker(Settings(environment="test"))
    try:
        declared = broker.get_declared_actors()
        assert declared == {
            recover_extraction_jobs.actor_name,
            recover_generation_jobs.actor_name,
            recover_embedding_jobs.actor_name,
            reconcile_source_objects.actor_name,
            recover_teacher_papers.actor_name,
        }
        assert recover_extraction_jobs.broker is broker
        assert recover_generation_jobs.broker is broker
        assert recover_embedding_jobs.broker is broker
        assert reconcile_source_objects.broker is broker
        assert recover_teacher_papers.broker is broker
    finally:
        broker.close()


def test_maintenance_main_installs_sigterm_runs_loop_and_closes_broker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from exam_guru_api import maintenance

    settings = Settings(environment="test", maintenance_scheduler_interval_seconds=17)
    stop = LoopStopSignal(stop_after_waits=1)
    handlers: list[object] = []
    previous_handler = object()

    class StubBroker:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    broker = StubBroker()

    def install_handler(
        signum: signal.Signals,
        handler: object,
    ) -> object:
        assert signum is signal.SIGTERM
        handlers.append(handler)
        return previous_handler

    def run_loop(
        tick: Callable[[], MaintenanceTickResult],
        *,
        interval_seconds: int,
        stop_signal: LoopStopSignal,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        del monotonic
        assert interval_seconds == 17
        assert stop_signal is stop
        result = tick()
        assert result == MaintenanceTickResult(enqueued=5, failures=0)
        handler = cast(Callable[[int, FrameType | None], None], handlers[0])
        handler(signal.SIGTERM, None)
        assert stop.is_set()

    calls: list[str] = []
    actors = (
        RecordingRecoveryActor("extraction", calls),
        RecordingRecoveryActor("generation", calls),
        RecordingRecoveryActor("embedding", calls),
        RecordingRecoveryActor("storage_reconciliation", calls),
        RecordingRecoveryActor("teacher_papers", calls),
    )

    monkeypatch.setattr(maintenance, "Settings", lambda: settings)
    monkeypatch.setattr(maintenance, "Event", lambda: stop)
    monkeypatch.setattr(maintenance, "create_maintenance_broker", lambda actual: broker)
    monkeypatch.setattr(maintenance, "run_maintenance_loop", run_loop)
    monkeypatch.setattr(signal, "signal", install_handler)
    monkeypatch.setattr(maintenance, "_RECOVERY_ACTORS", actors)

    main()

    assert calls == [
        "extraction",
        "generation",
        "embedding",
        "storage_reconciliation",
        "teacher_papers",
    ]
    assert handlers == [handlers[0], previous_handler]
    assert broker.closed is True


def test_scheduler_loop_exits_naturally_when_wait_sets_stop_flag() -> None:
    class NaturalStopSignal:
        def __init__(self) -> None:
            self.stopped = False
            self.waits: list[float] = []

        def is_set(self) -> bool:
            return self.stopped

        def set(self) -> None:
            self.stopped = True

        def wait(self, timeout: float) -> bool:
            self.waits.append(timeout)
            self.stopped = True
            return False

    stop = NaturalStopSignal()
    calls = 0

    def tick() -> MaintenanceTickResult:
        nonlocal calls
        calls += 1
        return MaintenanceTickResult(enqueued=3, failures=0)

    run_maintenance_loop(
        tick,
        interval_seconds=5,
        stop_signal=stop,
        monotonic=monotonic_clock(10.0, 11.0),
    )

    assert calls == 1
    assert stop.waits == [4.0]

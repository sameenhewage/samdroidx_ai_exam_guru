from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, cast

import pytest

from exam_guru_api.observability import OperationalTelemetry


@dataclass
class RecordingSpan:
    name: str
    attributes: dict[str, object]
    events: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value

    def set_attributes(self, attributes: dict[str, object]) -> None:
        self.attributes.update(attributes)

    def add_event(
        self,
        name: str,
        attributes: dict[str, object] | None = None,
        timestamp: int | None = None,
    ) -> None:
        del timestamp
        self.events.append((name, dict(attributes or {})))


class RecordingSpanContext(AbstractContextManager[RecordingSpan]):
    def __init__(self, span: RecordingSpan) -> None:
        self.span = span
        self.exit_values: list[tuple[object, object, object]] = []

    def __enter__(self) -> RecordingSpan:
        return self.span

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.exit_values.append((exc_type, exc_value, traceback))


class RecordingTracer:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.contexts: list[RecordingSpanContext] = []

    def start_as_current_span(self, name: str, **kwargs: object) -> RecordingSpanContext:
        attributes = dict(cast(dict[str, object], kwargs.pop("attributes", {})))
        self.calls.append({"name": name, "attributes": attributes, **kwargs})
        context = RecordingSpanContext(RecordingSpan(name, attributes))
        self.contexts.append(context)
        return context


class RecordingLogger:
    def __init__(self) -> None:
        self.records: list[tuple[str, dict[str, object]]] = []

    def info(self, message: str, *, extra: dict[str, object]) -> None:
        self.records.append((message, dict(extra)))


QUERY_SHA256 = "0" * 64


def telemetry() -> tuple[OperationalTelemetry, RecordingLogger, RecordingTracer]:
    logger = RecordingLogger()
    tracer = RecordingTracer()
    return (
        OperationalTelemetry(logger=logger, tracer=tracer),  # type: ignore[arg-type]
        logger,
        tracer,
    )


def test_retrieval_event_has_exact_sanitized_log_fields_and_trace_only_scope_ids() -> None:
    operational, logger, tracer = telemetry()
    trace_scope = {
        "retrieval.query.sha256": QUERY_SHA256,
        "retrieval.scope.grade": 5,
        "retrieval.scope.medium_id": "medium-id",
        "retrieval.scope.curriculum_version_id": "curriculum-id",
    }

    with operational.span("retrieval.explore", attributes=trace_scope) as span:
        operational.retrieval_completed(
            span=span,
            query_sha256=QUERY_SHA256,
            outcome="succeeded",
            failure_code=None,
            candidate_count=4,
            context_count=2,
            validation_latency_ms=1.0,
            embedding_latency_ms=2.0,
            candidate_retrieval_latency_ms=3.0,
            fusion_latency_ms=4.0,
            context_building_latency_ms=5.0,
            total_latency_ms=15.0,
        )

    expected_fields = {
        "event_name": "retrieval.completed",
        "outcome": "succeeded",
        "failure_code": None,
        "query_sha256": QUERY_SHA256,
        "candidate_count": 4,
        "context_count": 2,
        "validation_latency_ms": 1.0,
        "embedding_latency_ms": 2.0,
        "candidate_retrieval_latency_ms": 3.0,
        "fusion_latency_ms": 4.0,
        "context_building_latency_ms": 5.0,
        "total_latency_ms": 15.0,
    }
    assert logger.records == [("Operational event", expected_fields)]
    assert tracer.calls == [
        {
            "name": "retrieval.explore",
            "attributes": trace_scope,
            "record_exception": False,
            "set_status_on_exception": False,
        }
    ]
    assert tracer.contexts[0].span.events == [
        (
            "retrieval.completed",
            {key: value for key, value in expected_fields.items() if value is not None},
        )
    ]
    assert "medium_id" not in logger.records[0][1]
    assert "curriculum_version_id" not in logger.records[0][1]


def test_operational_spans_never_record_exception_messages() -> None:
    operational, logger, tracer = telemetry()

    with (
        pytest.raises(RuntimeError, match="raw model secret"),
        operational.span("generation.execute", attributes={"safe": True}),
    ):
        raise RuntimeError("raw model secret and source text")

    assert logger.records == []
    assert tracer.calls[0]["record_exception"] is False
    assert tracer.calls[0]["set_status_on_exception"] is False
    assert tracer.contexts[0].span.events == []
    assert "raw model secret" not in str(tracer.contexts[0].span.attributes)


def test_terminal_helpers_emit_fixed_low_cardinality_events_and_sanitize_failure_codes() -> None:
    operational, logger, tracer = telemetry()

    operational.generation_terminal(
        status="failed",
        failure_code="raw provider message\nwith secret",
        attempt_count=2,
        input_tokens=10,
        output_tokens=4,
        total_tokens=14,
        cost_microusd=9,
        latency_ms=20,
    )
    operational.validation_creation(
        outcome="succeeded",
        failure_code=None,
        overall_status="warn",
        finding_count=3,
        deduplicated=False,
    )
    operational.extraction_terminal(
        status="failed",
        failure_code="unexpected_error",
        attempt_count=1,
        page_count=0,
        block_count=0,
        ocr_page_count=0,
    )
    operational.embedding_terminal(
        status="succeeded",
        failure_code=None,
        requested_count=4,
        embedded_count=3,
        deduplicated_count=1,
    )
    operational.paper_transition(
        action="published",
        outcome="succeeded",
        failure_code=None,
        version=2,
        question_count=3,
        deduplicated=False,
    )
    operational.paper_transition(
        action="archived",
        outcome="failed",
        failure_code="paper_state_conflict",
        version=None,
        question_count=None,
        deduplicated=False,
    )

    assert [record[1]["event_name"] for record in logger.records] == [
        "generation.worker_terminal",
        "validation.creation",
        "extraction.terminal",
        "embedding.terminal",
        "paper.published",
        "paper.archived",
    ]
    assert logger.records[0][1] == {
        "event_name": "generation.worker_terminal",
        "outcome": "failed",
        "failure_code": "generation_internal_error",
        "status": "failed",
        "attempt_count": 2,
        "input_tokens": 10,
        "output_tokens": 4,
        "total_tokens": 14,
        "cost_microusd": 9,
        "latency_ms": 20,
    }
    assert logger.records[-1][1] == {
        "event_name": "paper.archived",
        "outcome": "failed",
        "failure_code": "paper_state_conflict",
        "version": None,
        "question_count": None,
        "deduplicated": False,
    }
    serialized = str(logger.records)
    assert "raw provider message" not in serialized
    assert "secret" not in serialized
    assert len(tracer.contexts) == 6
    assert all(context.span.events for context in tracer.contexts)


def test_telemetry_failures_never_break_application_work() -> None:
    class FailingLogger:
        def info(self, message: str, *, extra: dict[str, object]) -> None:
            del message, extra
            raise RuntimeError("logger unavailable")

    class FailingTracer:
        def start_as_current_span(self, name: str, **kwargs: object) -> Any:
            del name, kwargs
            raise RuntimeError("tracer unavailable")

    operational = OperationalTelemetry(
        logger=FailingLogger(),  # type: ignore[arg-type]
        tracer=FailingTracer(),  # type: ignore[arg-type]
    )

    operational.embedding_terminal(
        status="failed",
        failure_code="embedding_internal_error",
        requested_count=1,
        embedded_count=0,
        deduplicated_count=0,
    )
    with operational.span("safe.operation", attributes={}) as span:
        assert span is not None

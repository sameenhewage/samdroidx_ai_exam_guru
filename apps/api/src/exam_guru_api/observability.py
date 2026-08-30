import logging
import re
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import sentry_sdk
from fastapi import FastAPI, Request, Response
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
from opentelemetry.trace import Span, Tracer
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from exam_guru_api.core.config import Settings

REQUEST_ID_HEADER = "X-Request-ID"
logger = logging.getLogger(__name__)
_operational_logger = logging.getLogger("exam_guru_api.operational")
_FAILURE_CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
TelemetryValue = str | bool | int | float


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = resolve_request_id(request.headers.get(REQUEST_ID_HEADER))
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        logger.info(
            "HTTP request completed",
            extra={
                "http_method": request.method,
                "http_path": request.url.path,
                "http_status_code": response.status_code,
                "request_id": request_id,
            },
        )
        return response


def resolve_request_id(value: str | None) -> str:
    if value is not None:
        try:
            return str(UUID(value))
        except ValueError:
            pass
    return str(uuid4())


def _trace_attributes(values: Mapping[str, object]) -> dict[str, TelemetryValue]:
    return {
        key: value for key, value in values.items() if isinstance(value, str | bool | int | float)
    }


def _failure_code(value: str | None, *, fallback: str) -> str | None:
    if value is None:
        return None
    return value if _FAILURE_CODE.fullmatch(value) else fallback


class OperationalTelemetry:
    """Fixed, content-free operational log and manual-span events.

    Logging and tracing failures are deliberately isolated from application state changes.
    Exception recording is disabled on manual spans so provider/source messages cannot be
    attached by the OpenTelemetry context manager.
    """

    def __init__(
        self,
        *,
        logger: logging.Logger | None = None,
        tracer: Tracer | None = None,
    ) -> None:
        self._logger = logger or _operational_logger
        self._tracer = tracer or trace.get_tracer("exam_guru_api.operational")

    @contextmanager
    def span(
        self,
        name: str,
        *,
        attributes: Mapping[str, object],
    ) -> Iterator[Span]:
        try:
            context = self._tracer.start_as_current_span(
                name,
                attributes=_trace_attributes(attributes),
                record_exception=False,
                set_status_on_exception=False,
            )
            active_span = context.__enter__()
        except Exception:
            yield trace.INVALID_SPAN
            return

        try:
            yield active_span
        except BaseException:
            exception = sys.exc_info()
            with suppress(Exception):
                context.__exit__(*exception)
            raise
        else:
            with suppress(Exception):
                context.__exit__(None, None, None)

    def retrieval_completed(
        self,
        *,
        span: Span,
        query_sha256: str,
        outcome: str,
        failure_code: str | None,
        candidate_count: int,
        context_count: int,
        validation_latency_ms: float,
        embedding_latency_ms: float,
        candidate_retrieval_latency_ms: float,
        fusion_latency_ms: float,
        context_building_latency_ms: float,
        total_latency_ms: float,
    ) -> None:
        safe_query_sha256 = query_sha256 if _SHA256.fullmatch(query_sha256) else "0" * 64
        self._emit(
            "retrieval.completed",
            {
                "outcome": outcome,
                "failure_code": _failure_code(
                    failure_code,
                    fallback="retrieval_internal_error",
                ),
                "query_sha256": safe_query_sha256,
                "candidate_count": candidate_count,
                "context_count": context_count,
                "validation_latency_ms": validation_latency_ms,
                "embedding_latency_ms": embedding_latency_ms,
                "candidate_retrieval_latency_ms": candidate_retrieval_latency_ms,
                "fusion_latency_ms": fusion_latency_ms,
                "context_building_latency_ms": context_building_latency_ms,
                "total_latency_ms": total_latency_ms,
            },
            span=span,
        )

    def generation_terminal(
        self,
        *,
        status: str,
        failure_code: str | None,
        attempt_count: int,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        cost_microusd: int,
        latency_ms: int,
    ) -> None:
        self._emit(
            "generation.worker_terminal",
            {
                "outcome": "succeeded" if status == "succeeded" else "failed",
                "failure_code": _failure_code(
                    failure_code,
                    fallback="generation_internal_error",
                ),
                "status": status,
                "attempt_count": attempt_count,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "cost_microusd": cost_microusd,
                "latency_ms": latency_ms,
            },
        )

    def validation_creation(
        self,
        *,
        outcome: str,
        failure_code: str | None,
        overall_status: str | None,
        finding_count: int,
        deduplicated: bool,
    ) -> None:
        self._emit(
            "validation.creation",
            {
                "outcome": outcome,
                "failure_code": _failure_code(
                    failure_code,
                    fallback="validation_internal_error",
                ),
                "overall_status": overall_status,
                "finding_count": finding_count,
                "deduplicated": deduplicated,
            },
        )

    def extraction_terminal(
        self,
        *,
        status: str,
        failure_code: str | None,
        attempt_count: int,
        page_count: int,
        block_count: int,
        ocr_page_count: int,
    ) -> None:
        self._emit(
            "extraction.terminal",
            {
                "outcome": "succeeded" if status == "extracted" else "failed",
                "failure_code": _failure_code(
                    failure_code,
                    fallback="unexpected_error",
                ),
                "status": status,
                "attempt_count": attempt_count,
                "page_count": page_count,
                "block_count": block_count,
                "ocr_page_count": ocr_page_count,
            },
        )

    def embedding_terminal(
        self,
        *,
        status: str,
        failure_code: str | None,
        requested_count: int,
        embedded_count: int,
        deduplicated_count: int,
    ) -> None:
        self._emit(
            "embedding.terminal",
            {
                "outcome": "succeeded" if status == "succeeded" else "failed",
                "failure_code": _failure_code(
                    failure_code,
                    fallback="embedding_internal_error",
                ),
                "status": status,
                "requested_count": requested_count,
                "embedded_count": embedded_count,
                "deduplicated_count": deduplicated_count,
            },
        )

    def embedding_provider_completed(
        self,
        *,
        provider: str,
        model: str,
        dimension: int,
        embedding_version: str,
        input_tokens: int,
        total_tokens: int,
        cost_microusd: int,
        latency_ms: int,
    ) -> None:
        self._emit(
            "embedding.provider_completed",
            {
                "outcome": "succeeded",
                "provider": provider,
                "model": model,
                "dimension": dimension,
                "embedding_version": embedding_version,
                "input_tokens": input_tokens,
                "total_tokens": total_tokens,
                "cost_microusd": cost_microusd,
                "latency_ms": latency_ms,
            },
        )

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
    ) -> None:
        self._emit(
            "storage_reconciliation.terminal",
            {
                "outcome": status,
                "failure_code": _failure_code(
                    failure_code,
                    fallback="storage_reconciliation_internal_error",
                ),
                "status": status,
                "scanned_count": scanned_count,
                "referenced_count": referenced_count,
                "candidate_count": candidate_count,
                "resolved_count": resolved_count,
                "tagged_count": tagged_count,
                "failure_count": failure_count,
                "truncated": truncated,
            },
        )

    def paper_transition(
        self,
        *,
        action: str,
        outcome: str,
        failure_code: str | None,
        version: int | None,
        question_count: int | None,
        deduplicated: bool,
    ) -> None:
        event_name = {
            "published": "paper.published",
            "archived": "paper.archived",
        }.get(action, "paper.transition")
        self._emit(
            event_name,
            {
                "outcome": outcome,
                "failure_code": _failure_code(
                    failure_code,
                    fallback="paper_internal_error",
                ),
                "version": version,
                "question_count": question_count,
                "deduplicated": deduplicated,
            },
        )

    def _emit(
        self,
        event_name: str,
        values: Mapping[str, object],
        *,
        span: Span | None = None,
    ) -> None:
        fields = {"event_name": event_name, **values}
        with suppress(Exception):
            self._logger.info("Operational event", extra=fields)

        attributes = _trace_attributes(fields)
        if span is not None:
            self._add_event(span, event_name, attributes)
            return
        with self.span(f"exam_guru.{event_name}", attributes={}) as event_span:
            self._add_event(event_span, event_name, attributes)

    @staticmethod
    def _add_event(
        span: Span,
        event_name: str,
        attributes: Mapping[str, TelemetryValue],
    ) -> None:
        with suppress(Exception):
            span.add_event(event_name, attributes=attributes)


_default_operational_telemetry = OperationalTelemetry()


def get_operational_telemetry() -> OperationalTelemetry:
    return _default_operational_telemetry


def _set_operational_telemetry(value: OperationalTelemetry) -> None:
    global _default_operational_telemetry
    _default_operational_telemetry = value


@dataclass(slots=True)
class ObservabilityRuntime:
    tracer_provider: TracerProvider
    operational_telemetry: OperationalTelemetry
    service_name: str
    sentry_enabled: bool = False
    is_shutdown: bool = field(default=False, init=False)

    def shutdown(self) -> None:
        if self.is_shutdown:
            return
        self.is_shutdown = True
        self.tracer_provider.shutdown()
        if self.sentry_enabled:
            sentry_sdk.flush(timeout=2.0)


def _create_runtime(settings: Settings, *, service_name: str) -> ObservabilityRuntime:
    tracer_provider = TracerProvider(
        resource=Resource.create({"service.name": service_name}),
        sampler=TraceIdRatioBased(settings.trace_sample_ratio),
    )
    if settings.otel_exporter_otlp_endpoint is not None:
        exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint)
        tracer_provider.add_span_processor(BatchSpanProcessor(exporter))
    telemetry = OperationalTelemetry(tracer=tracer_provider.get_tracer("exam_guru_api.operational"))
    return ObservabilityRuntime(
        tracer_provider=tracer_provider,
        operational_telemetry=telemetry,
        service_name=service_name,
        sentry_enabled=settings.sentry_dsn is not None,
    )


def _configure_sentry(
    settings: Settings,
    *,
    server_name: str | None = None,
) -> None:
    if settings.sentry_dsn is None:
        return
    if server_name is None:
        sentry_sdk.init(
            dsn=settings.sentry_dsn.get_secret_value(),
            enable_tracing=True,
            environment=settings.environment,
            send_default_pii=False,
            traces_sample_rate=settings.trace_sample_ratio,
        )
        return
    sentry_sdk.init(
        dsn=settings.sentry_dsn.get_secret_value(),
        enable_tracing=True,
        environment=settings.environment,
        send_default_pii=False,
        server_name=server_name,
        traces_sample_rate=settings.trace_sample_ratio,
    )


def configure_observability(application: FastAPI, settings: Settings) -> ObservabilityRuntime:
    runtime = _create_runtime(settings, service_name=settings.otel_service_name)
    application.add_middleware(RequestIdMiddleware)
    FastAPIInstrumentor.instrument_app(
        application,
        tracer_provider=runtime.tracer_provider,
        excluded_urls="/api/v1/health/live",
    )
    _configure_sentry(settings)
    _set_operational_telemetry(runtime.operational_telemetry)
    return runtime


def configure_worker_observability(settings: Settings) -> ObservabilityRuntime:
    service_name = (
        settings.otel_service_name
        if settings.otel_service_name.endswith("-worker")
        else f"{settings.otel_service_name}-worker"
    )
    runtime = _create_runtime(settings, service_name=service_name)
    _configure_sentry(settings, server_name=service_name)
    _set_operational_telemetry(runtime.operational_telemetry)
    return runtime

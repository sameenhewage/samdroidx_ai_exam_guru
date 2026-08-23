import logging
from dataclasses import dataclass
from uuid import UUID, uuid4

import sentry_sdk
from fastapi import FastAPI, Request, Response
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from exam_guru_api.core.config import Settings

REQUEST_ID_HEADER = "X-Request-ID"
logger = logging.getLogger(__name__)


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


@dataclass(slots=True)
class ObservabilityRuntime:
    tracer_provider: TracerProvider

    def shutdown(self) -> None:
        self.tracer_provider.shutdown()


def configure_observability(application: FastAPI, settings: Settings) -> ObservabilityRuntime:
    tracer_provider = TracerProvider(
        resource=Resource.create({"service.name": settings.otel_service_name}),
        sampler=TraceIdRatioBased(settings.trace_sample_ratio),
    )
    if settings.otel_exporter_otlp_endpoint is not None:
        exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint)
        tracer_provider.add_span_processor(BatchSpanProcessor(exporter))

    application.add_middleware(RequestIdMiddleware)
    FastAPIInstrumentor.instrument_app(
        application,
        tracer_provider=tracer_provider,
        excluded_urls="/api/v1/health/live",
    )

    if settings.sentry_dsn is not None:
        sentry_sdk.init(
            dsn=settings.sentry_dsn.get_secret_value(),
            enable_tracing=True,
            environment=settings.environment,
            send_default_pii=False,
            traces_sample_rate=settings.trace_sample_ratio,
        )

    return ObservabilityRuntime(tracer_provider=tracer_provider)

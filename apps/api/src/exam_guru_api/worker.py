import dramatiq
from dramatiq.brokers.redis import RedisBroker

from exam_guru_api.core.config import Settings
from exam_guru_api.observability import (
    ObservabilityRuntime,
    configure_worker_observability,
)


class WorkerObservabilityMiddleware(dramatiq.Middleware):
    """Own the worker process observability runtime and flush it at shutdown."""

    def __init__(self, runtime: ObservabilityRuntime) -> None:
        self._runtime = runtime

    def after_worker_shutdown(self, broker: object, worker: object) -> None:
        del broker, worker
        self._runtime.shutdown()


def _register_actors(broker: RedisBroker) -> None:
    from exam_guru_api.documents.jobs import extract_document, recover_extraction_jobs
    from exam_guru_api.generation.jobs import generate_question, recover_generation_jobs
    from exam_guru_api.knowledge.embedding_jobs import ingest_embeddings, recover_embedding_jobs
    from exam_guru_api.storage_reconciliation.jobs import reconcile_source_objects

    extract_document.broker = broker
    recover_extraction_jobs.broker = broker
    generate_question.broker = broker
    recover_generation_jobs.broker = broker
    ingest_embeddings.broker = broker
    recover_embedding_jobs.broker = broker
    reconcile_source_objects.broker = broker
    broker.declare_actor(extract_document)
    broker.declare_actor(recover_extraction_jobs)
    broker.declare_actor(generate_question)
    broker.declare_actor(recover_generation_jobs)
    broker.declare_actor(ingest_embeddings)
    broker.declare_actor(recover_embedding_jobs)
    broker.declare_actor(reconcile_source_objects)


def create_broker(
    settings: Settings | None = None,
    *,
    observability_runtime: ObservabilityRuntime | None = None,
) -> RedisBroker:
    resolved_settings = settings or Settings()
    runtime = observability_runtime or configure_worker_observability(resolved_settings)
    broker = RedisBroker(url=resolved_settings.valkey_url.get_secret_value())
    broker.add_middleware(WorkerObservabilityMiddleware(runtime))
    dramatiq.set_broker(broker)
    _register_actors(broker)
    return broker


broker = create_broker()

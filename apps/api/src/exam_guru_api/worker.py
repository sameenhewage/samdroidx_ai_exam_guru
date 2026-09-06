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
    from exam_guru_api.documents.page_reading_jobs import read_source, recover_source_read_jobs
    from exam_guru_api.documents.upload_jobs import (
        finalize_source_upload,
        recover_source_upload_jobs,
    )
    from exam_guru_api.generation.jobs import generate_question, recover_generation_jobs
    from exam_guru_api.knowledge.embedding_jobs import ingest_embeddings, recover_embedding_jobs
    from exam_guru_api.storage_reconciliation.jobs import reconcile_source_objects
    from exam_guru_api.teacher_papers.jobs import advance_teacher_paper, recover_teacher_papers

    for actor in (
        extract_document,
        recover_extraction_jobs,
        read_source,
        recover_source_read_jobs,
        finalize_source_upload,
        recover_source_upload_jobs,
        generate_question,
        recover_generation_jobs,
        ingest_embeddings,
        recover_embedding_jobs,
        reconcile_source_objects,
        advance_teacher_paper,
        recover_teacher_papers,
    ):
        actor.broker = broker
        broker.declare_actor(actor)


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

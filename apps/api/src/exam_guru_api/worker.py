import dramatiq
from dramatiq.brokers.redis import RedisBroker

from exam_guru_api.core.config import Settings


def _register_actors(broker: RedisBroker) -> None:
    from exam_guru_api.documents.jobs import extract_document
    from exam_guru_api.generation.jobs import generate_question, recover_generation_jobs
    from exam_guru_api.knowledge.embedding_jobs import ingest_embeddings, recover_embedding_jobs

    extract_document.broker = broker
    generate_question.broker = broker
    recover_generation_jobs.broker = broker
    ingest_embeddings.broker = broker
    recover_embedding_jobs.broker = broker
    broker.declare_actor(extract_document)
    broker.declare_actor(generate_question)
    broker.declare_actor(recover_generation_jobs)
    broker.declare_actor(ingest_embeddings)
    broker.declare_actor(recover_embedding_jobs)


def create_broker(settings: Settings | None = None) -> RedisBroker:
    resolved_settings = settings or Settings()
    broker = RedisBroker(url=resolved_settings.valkey_url.get_secret_value())
    dramatiq.set_broker(broker)
    _register_actors(broker)
    return broker


broker = create_broker()

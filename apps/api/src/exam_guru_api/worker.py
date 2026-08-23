import dramatiq
from dramatiq.brokers.redis import RedisBroker

from exam_guru_api.core.config import Settings


def _register_actors(broker: RedisBroker) -> None:
    from exam_guru_api.documents.jobs import extract_document

    extract_document.broker = broker
    broker.declare_actor(extract_document)


def create_broker(settings: Settings | None = None) -> RedisBroker:
    resolved_settings = settings or Settings()
    broker = RedisBroker(url=resolved_settings.valkey_url.get_secret_value())
    dramatiq.set_broker(broker)
    _register_actors(broker)
    return broker


broker = create_broker()

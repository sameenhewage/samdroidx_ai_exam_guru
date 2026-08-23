import dramatiq
from dramatiq.brokers.redis import RedisBroker

from exam_guru_api.core.config import Settings


def create_broker(settings: Settings | None = None) -> RedisBroker:
    resolved_settings = settings or Settings()
    broker = RedisBroker(url=resolved_settings.valkey_url.get_secret_value())
    dramatiq.set_broker(broker)
    return broker


broker = create_broker()

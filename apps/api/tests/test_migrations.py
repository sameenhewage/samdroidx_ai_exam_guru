from alembic.config import Config

from exam_guru_api.infrastructure.migrations import configure_database_url_from_environment


def test_alembic_keeps_configured_url_without_environment_override() -> None:
    config = Config()
    config.set_main_option("sqlalchemy.url", "postgresql+asyncpg://configured/app")

    configure_database_url_from_environment(config, {})

    assert config.get_main_option("sqlalchemy.url") == "postgresql+asyncpg://configured/app"


def test_alembic_uses_database_url_from_environment() -> None:
    config = Config()

    configure_database_url_from_environment(
        config,
        {"EXAM_GURU_DATABASE_URL": "postgresql+asyncpg://service:p%40ss@postgres/app"},
    )

    assert (
        config.get_main_option("sqlalchemy.url")
        == "postgresql+asyncpg://service:p%40ss@postgres/app"
    )

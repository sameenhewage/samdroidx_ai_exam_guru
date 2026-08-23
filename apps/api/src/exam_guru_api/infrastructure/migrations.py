import os
from collections.abc import Mapping
from pathlib import Path

from alembic import command
from alembic.config import Config

ALEMBIC_CONFIG_PATH = Path(__file__).resolve().parents[3] / "alembic.ini"
DATABASE_URL_ENVIRONMENT_VARIABLE = "EXAM_GURU_DATABASE_URL"


def _set_database_url(config: Config, database_url: str) -> None:
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))


def configure_database_url_from_environment(
    config: Config,
    environment: Mapping[str, str] = os.environ,
) -> None:
    database_url = environment.get(DATABASE_URL_ENVIRONMENT_VARIABLE)
    if database_url is not None:
        _set_database_url(config, database_url)


def upgrade_database(database_url: str) -> None:
    config = Config(str(ALEMBIC_CONFIG_PATH))
    _set_database_url(config, database_url)
    command.upgrade(config, "head")

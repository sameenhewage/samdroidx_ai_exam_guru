from typing import cast

from fastapi import Request

from exam_guru_api.core.config import Settings
from exam_guru_api.infrastructure.resources import ApplicationResources


def get_resources(request: Request) -> ApplicationResources:
    return cast(ApplicationResources, request.app.state.resources)


def get_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)

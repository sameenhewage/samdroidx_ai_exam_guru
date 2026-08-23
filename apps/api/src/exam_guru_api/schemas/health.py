from typing import Literal

from pydantic import BaseModel

DependencyStatus = Literal["ok", "unavailable"]


class HealthResponse(BaseModel):
    status: Literal["ok"]


class DependencyChecks(BaseModel):
    database: DependencyStatus
    valkey: DependencyStatus


class ReadinessResponse(BaseModel):
    status: DependencyStatus
    checks: DependencyChecks

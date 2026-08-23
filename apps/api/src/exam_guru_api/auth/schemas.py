from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AdminAuditEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    actor_id: UUID
    action: str
    resource_type: str
    resource_id: UUID
    payload: dict[str, Any]
    created_at: datetime

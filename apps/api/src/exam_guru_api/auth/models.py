from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Index, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from exam_guru_api.infrastructure.database import Base


class AdminAuditEventModel(Base):
    __tablename__ = "admin_audit_events"
    __table_args__ = (
        CheckConstraint(
            "action = btrim(action) AND length(action) > 0",
            name="ck_admin_audit_event_action",
        ),
        CheckConstraint(
            "resource_type = btrim(resource_type) AND length(resource_type) > 0",
            name="ck_admin_audit_event_resource_type",
        ),
        Index("ix_admin_audit_events_actor_created", "actor_id", "created_at"),
        Index(
            "ix_admin_audit_events_resource_created",
            "resource_type",
            "resource_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    actor_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

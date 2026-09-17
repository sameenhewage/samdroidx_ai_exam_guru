import hashlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.documents.source_verification_models import SourceReadingEventModel
from exam_guru_api.documents.understanding_contracts import _canonical_json
from exam_guru_api.documents.understanding_service import UnderstandingConflictError


async def record_source_reading_event(
    session: AsyncSession,
    job_id: UUID,
    lease_token: UUID,
    event: dict[str, object],
) -> None:
    from exam_guru_api.documents.understanding_jobs import _locked

    if event.get("schema_version") == "source-witness-event.v1":
        from exam_guru_api.documents.source_machine_service import record_source_witness_event

        await record_source_witness_event(session, job_id, lease_token, event)
        return
    job, page, source = await _locked(session, job_id)
    if (
        job.status != "running"
        or job.lease_token != lease_token
        or job.lease_expires_at is None
        or job.lease_expires_at <= datetime.now(UTC)
        or page.active_job_id != job_id
        or page.version != job.expected_page_version
        or not source.active_for_ai
        or source.quarantined_for_teacher_use
    ):
        await session.rollback()
        raise UnderstandingConflictError("source_reading_lease_changed")
    number, event_type = event.get("pass_number"), event.get("event")
    if (
        type(number) is not int
        or not 0 <= number < 64
        or event_type not in {"requested", "provider_completed", "failed"}
    ):
        raise ValueError("invalid source reading pass event")
    encoded = _canonical_json(event).encode("utf-8")
    if len(encoded) > 2 * 1024 * 1024:
        raise ValueError("source reading event exceeds its bound")
    fingerprint = hashlib.sha256(encoded).hexdigest()
    existing = await session.scalar(
        select(SourceReadingEventModel).where(
            SourceReadingEventModel.job_id == job_id,
            SourceReadingEventModel.pass_number == number,
            SourceReadingEventModel.event == event_type,
        )
    )
    if existing is not None:
        if existing.fingerprint != fingerprint:
            raise UnderstandingConflictError("source_reading_event_changed")
        await session.commit()
        return
    audit = AdminAuditEventModel(
        id=uuid4(),
        actor_id=job.created_by,
        resource_type="source_reading_pass",
        resource_id=job.document_id,
        action=f"source_reading.{event_type}",
        payload={"job_id": str(job_id), "pass_number": number, "fingerprint": fingerprint},
    )
    session.add(audit)
    await session.flush()
    session.add(
        SourceReadingEventModel(
            id=uuid4(),
            job_id=job_id,
            lease_token=lease_token,
            pass_number=number,
            event=event_type,
            payload=event,
            fingerprint=fingerprint,
            created_by=job.created_by,
            audit_event_id=audit.id,
        )
    )
    await session.commit()

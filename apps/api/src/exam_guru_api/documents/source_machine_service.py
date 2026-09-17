import hashlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

import anyio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.domain import Principal
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.documents.page_images import (
    PageImageArtifacts,
    PageImageError,
    SourceImageStorage,
    load_material_source,
)
from exam_guru_api.documents.source_consensus import IndependentReading
from exam_guru_api.documents.source_machine import MachineSourceCandidate
from exam_guru_api.documents.source_machine_models import (
    MachineSourceCandidateModel,
    SourceWitnessEventModel,
)
from exam_guru_api.documents.source_renders import read_source_crop
from exam_guru_api.documents.understanding_contracts import _canonical_json
from exam_guru_api.documents.understanding_service import UnderstandingConflictError
from exam_guru_api.documents.understanding_verification import ObservationCandidate


async def record_source_witness_event(
    session: AsyncSession,
    job_id: UUID,
    lease_token: UUID,
    event: dict[str, object],
) -> None:
    from exam_guru_api.documents.understanding_jobs import _locked

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
        raise UnderstandingConflictError("source_witness_lease_changed")
    number, kind, reader, input_fingerprint = (
        event.get("pass_number"),
        event.get("event"),
        event.get("reader"),
        event.get("input_fingerprint"),
    )
    if (
        type(number) is not int
        or not 0 <= number < 512
        or kind not in {"requested", "provider_completed", "parsed", "failed", "cache_hit"}
        or reader not in {"qwen", "openai", "layout"}
        or not isinstance(input_fingerprint, str)
        or len(input_fingerprint) != 64
    ):
        raise ValueError("invalid source witness event")
    encoded = _canonical_json(event).encode("utf-8")
    if len(encoded) > 2 * 1024 * 1024:
        raise ValueError("source witness event exceeds its bound")
    if kind in {"parsed", "cache_hit"} and reader != "layout":
        reading = IndependentReading.model_validate_json(_canonical_json(event.get("reading")))
        if (
            reading.fingerprint != event.get("reading_fingerprint")
            or reading.input_fingerprint != input_fingerprint
            or reading.reader.reader != reader
        ):
            raise ValueError("source witness content differs from its identity")
    fingerprint = hashlib.sha256(encoded).hexdigest()
    existing = await session.scalar(
        select(SourceWitnessEventModel).where(
            SourceWitnessEventModel.job_id == job_id,
            SourceWitnessEventModel.pass_number == number,
            SourceWitnessEventModel.event == kind,
        )
    )
    if existing is not None:
        if existing.fingerprint != fingerprint:
            raise UnderstandingConflictError("source_witness_event_changed")
        await session.commit()
        return
    audit = AdminAuditEventModel(
        id=uuid4(),
        actor_id=job.created_by,
        resource_type="source_witness",
        resource_id=job.document_id,
        action=f"source_witness.{kind}",
        payload={
            "job_id": str(job_id),
            "pass_number": number,
            "reader": reader,
            "input_fingerprint": input_fingerprint,
            "fingerprint": fingerprint,
        },
    )
    session.add(audit)
    await session.flush()
    session.add(
        SourceWitnessEventModel(
            id=uuid4(),
            job_id=job_id,
            lease_token=lease_token,
            pass_number=number,
            event=kind,
            reader=reader,
            input_fingerprint=input_fingerprint,
            payload=event,
            fingerprint=fingerprint,
            created_by=job.created_by,
            audit_event_id=audit.id,
        )
    )
    await session.commit()


async def store_machine_source(
    session: AsyncSession,
    *,
    candidate: ObservationCandidate,
    job_id: UUID,
    actor_id: UUID,
    machine: MachineSourceCandidate,
) -> None:
    machine = MachineSourceCandidate.model_validate(machine)
    if (
        machine.source != candidate.source
        or machine.content.as_legacy_envelope() != candidate.content
    ):
        raise ValueError("machine source differs from the current candidate")
    audit = AdminAuditEventModel(
        id=uuid4(),
        actor_id=actor_id,
        resource_type="source_machine_candidate",
        resource_id=candidate.source.document_id,
        action="source_machine_candidate.created",
        payload={
            "job_id": str(job_id),
            "candidate_id": str(candidate.id),
            "fingerprint": machine.fingerprint,
        },
    )
    session.add(audit)
    await session.flush()
    session.add(
        MachineSourceCandidateModel(
            candidate_id=candidate.id,
            job_id=job_id,
            payload=machine.model_dump(mode="json"),
            fingerprint=machine.fingerprint,
            created_by=actor_id,
            audit_event_id=audit.id,
        )
    )
    await session.flush()


async def load_machine_source(
    session: AsyncSession, candidate: ObservationCandidate
) -> MachineSourceCandidate | None:
    row = await session.get(MachineSourceCandidateModel, candidate.id)
    if row is None:
        return None
    machine = MachineSourceCandidate.model_validate_json(_canonical_json(row.payload))
    if (
        machine.fingerprint != row.fingerprint
        or machine.source != candidate.source
        or machine.content.as_legacy_envelope() != candidate.content
    ):
        raise ValueError("stored machine source evidence is inconsistent")
    return machine


async def list_source_witness_events(
    session: AsyncSession,
    *,
    principal: Principal,
    job_id: UUID,
    offset: int,
    limit: int,
) -> tuple[list[SourceWitnessEventModel], int | None]:
    from exam_guru_api.documents.understanding_jobs import UnderstandingJobService

    if (
        type(offset) is not int
        or not 0 <= offset <= 2560
        or type(limit) is not int
        or not 1 <= limit <= 40
    ):
        raise ValueError("invalid source witness page")
    await UnderstandingJobService(session).get(principal=principal, job_id=job_id)
    rows = list(
        (
            await session.scalars(
                select(SourceWitnessEventModel)
                .where(SourceWitnessEventModel.job_id == job_id)
                .order_by(
                    SourceWitnessEventModel.pass_number,
                    SourceWitnessEventModel.created_at,
                    SourceWitnessEventModel.id,
                )
                .offset(offset)
                .limit(limit + 1)
            )
        ).all()
    )
    for row in rows:
        if (
            hashlib.sha256(_canonical_json(row.payload).encode("utf-8")).hexdigest()
            != row.fingerprint
        ):
            raise ValueError("source witness history fingerprint mismatch")
    return rows[:limit], offset + limit if len(rows) > limit else None


async def source_witness_image(
    session: AsyncSession,
    *,
    principal: Principal,
    job_id: UUID,
    event_id: UUID,
    storage: SourceImageStorage,
    artifacts: PageImageArtifacts | None,
) -> bytes:
    from exam_guru_api.documents.understanding_jobs import UnderstandingJobService

    job = await UnderstandingJobService(session).get(principal=principal, job_id=job_id)
    row = await session.get(SourceWitnessEventModel, event_id)
    if row is None or row.job_id != job_id:
        raise PageImageError("source_witness_image_not_found", 404)
    if (
        artifacts is None
        or hashlib.sha256(_canonical_json(row.payload).encode("utf-8")).hexdigest()
        != row.fingerprint
    ):
        raise PageImageError("source_witness_image_invalid")
    source = await load_material_source(session, job.document_id, principal=principal)
    payload = row.payload.get("input")
    if not isinstance(payload, dict):
        raise PageImageError("source_witness_image_invalid")
    return await anyio.to_thread.run_sync(read_source_crop, payload, source, storage, artifacts)

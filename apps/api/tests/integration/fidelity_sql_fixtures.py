"""Direct-SQL page fidelity fixtures shared by surviving Postgres suites.

Extracted from the removed V1 source-fidelity Postgres test module. Nothing in
here reads a source: every helper writes immutable evidence rows directly.
"""

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from exam_guru_api.documents.domain import SourceDocumentType
from exam_guru_api.documents.fidelity_models import (
    PageReviewEventModel,
    PageReviewStateModel,
    PageTextCandidateModel,
)
from exam_guru_api.documents.models import SourceDocumentModel

ACTOR = UUID(int=81001)


async def add_source(session: AsyncSession, *, page_count: int = 2) -> UUID:
    identifier = uuid4()
    checksum = hashlib.sha256(identifier.bytes).hexdigest()
    document = SourceDocumentModel(
        id=identifier,
        checksum_sha256=checksum,
        object_key=f"sources/{checksum[:2]}/{checksum}.pdf",
        original_filename="fidelity.pdf",
        content_type="application/pdf",
        size_bytes=100,
        document_type=SourceDocumentType.TEACHER_GUIDE,
        created_by=ACTOR,
        updated_by=ACTOR,
        original_page_count=page_count,
    )
    session.add(document)
    await session.commit()
    return identifier


@asynccontextmanager
async def fidelity_session(url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


async def add_sql_candidate(
    session: AsyncSession,
    document_id: UUID,
    *,
    method: str,
    diagnostics: dict[str, object],
    normalized_text: str | None = "Read the original",
    can_confirm: bool = True,
    provenance: dict[str, object] | None = None,
) -> PageTextCandidateModel:
    raw = (normalized_text if normalized_text is not None else "Unsafe original").encode()
    candidate = PageTextCandidateModel(
        id=uuid4(),
        document_id=document_id,
        page_number=1,
        method=method,
        raw_text_utf8=raw,
        normalized_text=normalized_text,
        text_sha256=hashlib.sha256(raw).hexdigest(),
        can_confirm=can_confirm,
        provenance=provenance if provenance is not None else {"engine": "sql-fixture"},
        diagnostics=diagnostics,
        created_by=ACTOR,
    )
    session.add(candidate)
    await session.commit()
    return candidate


async def confirm_sql_candidate(
    session: AsyncSession,
    candidate: PageTextCandidateModel,
    *,
    action: str = "confirmed",
    payload: dict[str, object] | None = None,
) -> PageReviewEventModel:
    state = await session.get(PageReviewStateModel, (candidate.document_id, candidate.page_number))
    if state is None:
        state = PageReviewStateModel(
            document_id=candidate.document_id,
            page_number=candidate.page_number,
            version=0,
            state="pending",
        )
        session.add(state)
        await session.flush()
    event = PageReviewEventModel(
        id=uuid4(),
        document_id=candidate.document_id,
        page_number=candidate.page_number,
        version=state.version + 1,
        candidate_id=candidate.id,
        action=action,
        state="verified",
        actor_id=ACTOR,
        reason="Explicit original comparison in an isolated SQL fixture",
        payload=payload
        if payload is not None
        else {"compared_with_original": True, "text_sha256": candidate.text_sha256},
    )
    session.add(event)
    await session.flush()
    state.version = event.version
    state.state = event.state
    state.current_candidate_id = candidate.id
    state.event_id = event.id
    await session.commit()
    return event

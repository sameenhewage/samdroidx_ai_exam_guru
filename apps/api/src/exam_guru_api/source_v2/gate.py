"""The single downstream gate.

Every path that turns source into meaning — educational analysis, knowledge,
embeddings, retrieval, generation — calls `assert_document_usable` first. There
is deliberately one function: a second way in is a hole.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.source_v2.domain import NotVerifiedError
from exam_guru_api.source_v2.repository import document_resolution


class DownstreamPurpose(StrEnum):
    """Everything on the far side of the source-fidelity line."""

    EDUCATIONAL_ANALYSIS = "educational analysis"
    KNOWLEDGE = "knowledge"
    EMBEDDINGS = "embeddings"
    RETRIEVAL = "retrieval"
    GENERATION = "generation"


class LegacyPolicy(StrEnum):
    """What to do with a document that has no Source V2 pages at all.

    Such a document predates this pipeline. Refusing every one of them today
    would stop the Studio before V2 has replaced V1, and quietly allowing them
    forever would be a hole in the invariant. So the choice is named, explicit
    and visible at every call site instead of hidden in a config default.

    `CUTOVER` is what Phase 7 switches to once the old source architecture is
    retired; at that point `PRE_CUTOVER` and this enum disappear with it.
    """

    PRE_CUTOVER = "pre-cutover"  # no V2 pages -> allow, V1 still owns this document
    CUTOVER = "cutover"  # no V2 pages -> refuse, V2 owns every document


def evaluate(pages: dict[int, dict[str, int]], *, document_id: str, purpose: str) -> None:
    """Pure form of the gate, so it can be tested without a database."""

    if not pages:
        raise NotVerifiedError(f"{purpose} refused: document {document_id} has no Source V2 pages")
    unresolved = sorted(number for number, counts in pages.items() if counts["unverified"])
    if unresolved:
        raise NotVerifiedError(
            f"{purpose} refused: document {document_id} has unresolved pages {unresolved}; "
            "every page must be verified or explicitly excluded"
        )
    if not any(counts["verified"] for counts in pages.values()):
        raise NotVerifiedError(
            f"{purpose} refused: document {document_id} has no verified page; "
            "excluding everything is not verification"
        )


async def assert_document_usable(
    session: AsyncSession,
    document_id: UUID,
    *,
    purpose: DownstreamPurpose,
    legacy: LegacyPolicy = LegacyPolicy.CUTOVER,
) -> None:
    """Raise `NotVerifiedError` unless the document has current verified source.

    Call this at the entry of every downstream operation, not in the middle of
    one: refusing after a side effect is not a gate.

    Once a document has *any* Source V2 page the strict rule always applies,
    whatever the legacy policy says. A half-reviewed document is never usable.
    """

    pages = await document_resolution(session, document_id)
    if not pages and legacy is LegacyPolicy.PRE_CUTOVER:
        return
    evaluate(pages, document_id=str(document_id), purpose=str(purpose))


async def is_document_usable(
    session: AsyncSession,
    document_id: UUID,
    *,
    purpose: DownstreamPurpose,
    legacy: LegacyPolicy = LegacyPolicy.CUTOVER,
) -> str | None:
    """The same decision as a reason string, for callers that report rather than raise."""

    try:
        await assert_document_usable(session, document_id, purpose=purpose, legacy=legacy)
    except NotVerifiedError as error:
        return str(error)
    return None

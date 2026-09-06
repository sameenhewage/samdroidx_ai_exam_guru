import hashlib
import json
import unicodedata
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.documents.fidelity import assess_page
from exam_guru_api.documents.fidelity_models import (
    PageGroundTruthModel,
    PageReviewEventModel,
    PageReviewStateModel,
    PageTextCandidateModel,
    SourceBenchmarkModel,
    SourceBenchmarkPageModel,
)
from exam_guru_api.documents.models import SourceDocumentModel


class PageFidelityConflictError(ValueError):
    pass


class PageVerificationBlockedError(ValueError):
    pass


class FidelitySourceNotFoundError(LookupError):
    pass


def _reason(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 2000:
        raise ValueError("a bounded review reason is required")
    if any(unicodedata.category(character) in {"Cc", "Cs"} for character in value):
        raise ValueError("review reason contains unsafe characters")
    return value.strip()


def _metadata(value: dict[str, object]) -> dict[str, object]:
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
    if len(encoded.encode()) > 65536:
        raise ValueError("fidelity metadata exceeds its bound")
    return cast(dict[str, object], json.loads(encoded))


def _strings(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
        return tuple(value)
    return ()


class PageFidelityService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _source(self, document_id: UUID, page_number: int) -> SourceDocumentModel:
        document = await self._session.get(
            SourceDocumentModel, document_id, with_for_update=True, populate_existing=True
        )
        if document is None:
            raise FidelitySourceNotFoundError("source_document_not_found")
        if isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 1:
            raise ValueError("page number must be positive")
        if document.original_page_count is None or page_number > document.original_page_count:
            raise FidelitySourceNotFoundError("source_page_not_found")
        return document

    async def _state(self, document_id: UUID, page_number: int) -> PageReviewStateModel:
        state = await self._session.get(
            PageReviewStateModel,
            (document_id, page_number),
            with_for_update=True,
            populate_existing=True,
        )
        if state is None:
            state = PageReviewStateModel(
                document_id=document_id, page_number=page_number, version=0, state="pending"
            )
            self._session.add(state)
            await self._session.flush()
        return state

    @staticmethod
    def _version(state: PageReviewStateModel, expected_version: int) -> None:
        if isinstance(expected_version, bool) or expected_version != state.version:
            raise PageFidelityConflictError("source_page_version_conflict")

    async def _advance(
        self,
        state: PageReviewStateModel,
        *,
        candidate_id: UUID | None,
        action: str,
        target: str,
        actor_id: UUID,
        reason: str,
        payload: dict[str, object],
    ) -> PageReviewEventModel:
        event = PageReviewEventModel(
            id=uuid4(),
            document_id=state.document_id,
            page_number=state.page_number,
            version=state.version + 1,
            candidate_id=candidate_id,
            action=action,
            state=target,
            actor_id=actor_id,
            reason=_reason(reason),
            payload=_metadata(payload),
        )
        self._session.add(event)
        await self._session.flush()
        state.current_candidate_id = candidate_id
        state.event_id = event.id
        state.version = event.version
        state.state = target
        state.updated_at = datetime.now(UTC)
        self._session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=actor_id,
                action=f"source_page.{action}",
                resource_type="source_page_review",
                resource_id=event.id,
                payload={
                    "document_id": str(state.document_id),
                    "page_number": state.page_number,
                    "version": state.version,
                    "candidate_id": str(candidate_id) if candidate_id else None,
                },
            )
        )
        await self._session.flush()
        return event

    async def record_candidate(
        self,
        document_id: UUID,
        page_number: int,
        *,
        raw_text: str,
        method: str,
        actor_id: UUID,
        provenance: dict[str, object],
        expected_version: int | None = None,
        reason: str = "Recorded an unverified source reading candidate",
        action: str = "candidate_recorded",
        commit: bool = True,
    ) -> PageReviewStateModel:
        document = await self._source(document_id, page_number)
        state = await self._state(document_id, page_number)
        if expected_version is not None:
            self._version(state, expected_version)
        elif state.state in {"verified", "excluded", "processing"}:
            raise PageFidelityConflictError("explicit_page_revision_required")
        raw = raw_text.encode("utf-8", errors="surrogatepass")
        if len(raw) > 4194304:
            raise ValueError("page text exceeds the bounded candidate size")
        if method not in {"native", "legacy", "ocr", "human"}:
            raise ValueError("unsupported reading method")
        provenance = _metadata({**provenance, "source_checksum_sha256": document.checksum_sha256})
        coverage = provenance.get("image_coverage", 0.0)
        assessment = assess_page(
            raw_text,
            expected_languages=_strings(provenance.get("languages")),
            font_names=_strings(provenance.get("fonts")),
            image_coverage=float(coverage) if isinstance(coverage, (int, float)) else 0.0,
            method="manual" if method == "human" else "native" if method == "legacy" else method,
        )
        current = (
            await self._session.get(PageTextCandidateModel, state.current_candidate_id)
            if state.current_candidate_id
            else None
        )
        if (
            current is not None
            and state.state == "needs_review"
            and action == "candidate_recorded"
            and current.raw_text_utf8 == raw
            and current.method == method
            and current.provenance == provenance
        ):
            if commit:
                await self._session.commit()
            return state
        normalized = assessment.normalized_text
        candidate = PageTextCandidateModel(
            id=uuid4(),
            document_id=document_id,
            page_number=page_number,
            parent_candidate_id=state.current_candidate_id,
            method=method,
            raw_text_utf8=raw,
            normalized_text=normalized,
            text_sha256=hashlib.sha256(
                normalized.encode() if normalized is not None else raw
            ).hexdigest(),
            can_confirm=assessment.can_confirm,
            provenance=provenance,
            diagnostics=_metadata(
                {
                    "algorithm_version": assessment.algorithm_version,
                    "languages": list(assessment.languages),
                    "script_counts": dict(assessment.script_counts),
                    "risk_codes": list(assessment.risk_codes),
                    "classifications": list(assessment.classifications),
                    "recommended_route": assessment.recommended_route,
                }
            ),
            created_by=actor_id,
        )
        self._session.add(candidate)
        await self._session.flush()
        await self._advance(
            state,
            candidate_id=candidate.id,
            action=action,
            target="needs_review",
            actor_id=actor_id,
            reason=reason,
            payload={
                "previous_candidate_id": str(candidate.parent_candidate_id)
                if candidate.parent_candidate_id
                else None,
                "text_sha256": candidate.text_sha256,
            },
        )
        if commit:
            await self._session.commit()
        return state

    async def confirm_page(
        self,
        document_id: UUID,
        page_number: int,
        *,
        candidate_id: UUID | None,
        expected_version: int,
        actor_id: UUID,
        reason: str,
    ) -> PageReviewStateModel:
        document = await self._source(document_id, page_number)
        state = await self._state(document_id, page_number)
        self._version(state, expected_version)
        if candidate_id is None or state.current_candidate_id != candidate_id:
            raise PageFidelityConflictError("source_candidate_changed")
        candidate = await self._session.get(PageTextCandidateModel, candidate_id)
        if not document.active_for_ai or candidate is None or not candidate.can_confirm:
            raise PageVerificationBlockedError("source_candidate_not_confirmable")
        if state.state == "verified":
            await self._session.commit()
            return state
        if state.state != "needs_review":
            raise PageVerificationBlockedError("source_page_not_awaiting_review")
        event = await self._advance(
            state,
            candidate_id=candidate_id,
            action="confirmed",
            target="verified",
            actor_id=actor_id,
            reason=reason,
            payload={"compared_with_original": True, "text_sha256": candidate.text_sha256},
        )
        memberships = await self._session.scalars(
            select(SourceBenchmarkPageModel).where(
                SourceBenchmarkPageModel.document_id == document_id,
                SourceBenchmarkPageModel.page_number == page_number,
            )
        )
        for membership in memberships:
            self._session.add(
                PageGroundTruthModel(
                    id=uuid4(),
                    benchmark_id=membership.benchmark_id,
                    document_id=document_id,
                    page_number=page_number,
                    candidate_id=candidate_id,
                    review_event_id=event.id,
                    source_checksum_sha256=document.checksum_sha256,
                    text_sha256=candidate.text_sha256,
                    reviewer_id=actor_id,
                )
            )
        await self._session.commit()
        return state

    async def edit_page(
        self,
        document_id: UUID,
        page_number: int,
        *,
        text: str,
        reason: str,
        expected_version: int,
        actor_id: UUID,
    ) -> PageReviewStateModel:
        await self._source(document_id, page_number)
        state = await self._state(document_id, page_number)
        self._version(state, expected_version)
        if state.state == "processing":
            raise PageFidelityConflictError("page_reading_in_progress")
        current = (
            await self._session.get(PageTextCandidateModel, state.current_candidate_id)
            if state.current_candidate_id
            else None
        )
        return await self.record_candidate(
            document_id,
            page_number,
            raw_text=text,
            method="human",
            actor_id=actor_id,
            provenance={
                "languages": current.diagnostics.get("languages", []) if current else [],
                "engine": "human-edit",
                "version": "1",
            },
            expected_version=expected_version,
            reason=_reason(reason),
            action="edited",
        )

    async def exclude_page(
        self,
        document_id: UUID,
        page_number: int,
        *,
        reason: str,
        expected_version: int,
        actor_id: UUID,
    ) -> PageReviewStateModel:
        await self._source(document_id, page_number)
        state = await self._state(document_id, page_number)
        self._version(state, expected_version)
        await self._advance(
            state,
            candidate_id=state.current_candidate_id,
            action="excluded",
            target="excluded",
            actor_id=actor_id,
            reason=reason,
            payload={"excluded_from_retrieval": True},
        )
        await self._session.commit()
        return state

    async def page_is_verified(
        self, document_id: UUID, page_number: int, candidate_id: UUID | None = None
    ) -> bool:
        return bool(
            await self._session.scalar(
                select(func.source_page_fidelity_is_current(document_id, page_number, candidate_id))
            )
        )

    async def document_is_verified(self, document_id: UUID) -> bool:
        document = await self._session.get(SourceDocumentModel, document_id)
        if document is None or not document.original_page_count or not document.active_for_ai:
            return False
        completed, verified = (
            await self._session.execute(
                select(
                    func.count().filter(PageReviewStateModel.state.in_(("verified", "excluded"))),
                    func.count().filter(PageReviewStateModel.state == "verified"),
                ).where(PageReviewStateModel.document_id == document_id)
            )
        ).one()
        return bool(completed == document.original_page_count and verified > 0)

    async def create_benchmark(
        self,
        *,
        name: str,
        pages: tuple[tuple[UUID, int, tuple[str, ...]], ...],
        actor_id: UUID,
        selection: dict[str, object],
    ) -> UUID:
        if not 1 <= len(name.strip()) <= 160 or not 1 <= len(pages) <= 1000:
            raise ValueError("benchmark must have a bounded name and page selection")
        if len({(doc, number) for doc, number, _ in pages}) != len(pages):
            raise ValueError("benchmark pages must be unique")
        for document_id, page_number, categories in sorted(pages):
            await self._source(document_id, page_number)
            if not 1 <= len(categories) <= 32 or any(
                not 1 <= len(value.strip()) <= 120 for value in categories
            ):
                raise ValueError("benchmark categories must be bounded")
        benchmark = SourceBenchmarkModel(
            id=uuid4(), name=name.strip(), selection=_metadata(selection), created_by=actor_id
        )
        self._session.add(benchmark)
        await self._session.flush()
        for document_id, page_number, categories in pages:
            self._session.add(
                SourceBenchmarkPageModel(
                    benchmark_id=benchmark.id,
                    document_id=document_id,
                    page_number=page_number,
                    categories=list(categories),
                )
            )
        self._session.add(
            AdminAuditEventModel(
                id=uuid4(),
                actor_id=actor_id,
                action="source_benchmark.created",
                resource_type="source_benchmark",
                resource_id=benchmark.id,
                payload={"page_count": len(pages), "name": benchmark.name},
            )
        )
        await self._session.commit()
        return benchmark.id

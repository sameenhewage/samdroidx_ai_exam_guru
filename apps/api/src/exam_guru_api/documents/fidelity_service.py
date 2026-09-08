import hashlib
import json
import math
import unicodedata
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import Boolean, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.documents.fidelity import (
    MAX_TEXT_CHARACTERS,
    PageAssessment,
    assess_page,
    normalize_source_text,
)
from exam_guru_api.documents.fidelity_models import (
    PageGroundTruthModel,
    PageReviewEventModel,
    PageReviewStateModel,
    PageTextCandidateModel,
    SourceBenchmarkModel,
    SourceBenchmarkPageModel,
)
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.documents.source_math_fidelity import assess_math_fidelity, decode_math_evidence


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
    if (
        not isinstance(value, (list, tuple))
        or len(value) > 128
        or any(not isinstance(item, str) or len(item) > 256 for item in value)
    ):
        return ()
    return tuple(value)


def assess_candidate(text: str, method: str, provenance: dict[str, object]) -> PageAssessment:
    evidence: dict[str, tuple[str, ...]] = {}
    invalid_evidence: set[str] = set()
    for field in ("source_languages", "languages", "fonts"):
        value = provenance.get(field, ())
        values = _strings(value)
        if (
            not isinstance(value, (list, tuple))
            or len(values) != len(value)
            or (field != "fonts" and not set(values) <= {"si", "ta", "en", "und"})
        ):
            invalid_evidence.add(f"invalid_{field}")
            values = ()
        evidence[field] = values
    coverage = provenance.get("image_coverage", 0.0)
    invalid_coverage = False
    if (
        not isinstance(coverage, (int, float))
        or isinstance(coverage, bool)
        or not math.isfinite(coverage)
        or not 0 <= coverage <= 1
    ):
        coverage = 0.0
        invalid_coverage = True
    assessment = assess_page(
        text,
        expected_languages=evidence[
            "source_languages" if "source_languages" in provenance else "languages"
        ],
        font_names=evidence["fonts"],
        image_coverage=float(coverage),
        method="manual" if method == "human" else "native" if method == "legacy" else method,
    )
    risks = set(assessment.risk_codes) | invalid_evidence
    blocked = invalid_coverage or bool(invalid_evidence)
    if invalid_coverage:
        risks.add("invalid_image_coverage")
    if "failure_code" in provenance:
        blocked = True
        risks.add("source_reading_failed")
        failure = provenance["failure_code"]
        if isinstance(failure, str) and 0 < len(failure) <= 256:
            risks.add(failure)
    if provenance.get("source_reprocessing_required") is True:
        blocked = True
        risks.add("source_reprocessing_required")
    maths = provenance.get("maths_fidelity")
    reference = provenance.get("maths_reference")
    if isinstance(reference, dict):
        try:
            words = None if method == "human" else provenance.get("maths_words")
            if words is not None:
                words = decode_math_evidence(words)
                if not isinstance(words, list):
                    raise ValueError("invalid math word evidence")
            maths = assess_math_fidelity(
                reference,
                text,
                cast(list[tuple[str, tuple[float, float, float, float]]] | None, words),
            )
        except ValueError:
            maths = {"can_confirm": False, "risk_codes": ["invalid_math_word_evidence"]}
    elif "maths_reference" in provenance:
        maths = {"can_confirm": False, "risk_codes": ["invalid_math_reference"]}
    if isinstance(maths, dict):
        risks.update(_strings(maths.get("risk_codes")))
        if maths.get("can_confirm") is False:
            blocked = True
            risks.add("maths_fidelity_unconfirmed")
    return replace(
        assessment,
        can_confirm=assessment.can_confirm and not blocked,
        risk_codes=tuple(sorted(risks)),
        recommended_route="ocr_review" if blocked else assessment.recommended_route,
    )


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
        assessment = assess_candidate(raw_text, method, provenance)
        current = (
            await self._session.get(PageTextCandidateModel, state.current_candidate_id)
            if state.current_candidate_id
            else None
        )
        if (
            current is not None
            and state.state == ("needs_review" if assessment.can_confirm else "failed")
            and current.diagnostics.get("algorithm_version") == assessment.algorithm_version
            and current.can_confirm == assessment.can_confirm
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
            target="needs_review" if assessment.can_confirm else "failed",
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
        if (
            not document.active_for_ai
            or document.quarantined_for_teacher_use
            or candidate is None
            or candidate.document_id != document_id
            or candidate.page_number != page_number
            or not candidate.can_confirm
        ):
            raise PageVerificationBlockedError("source_candidate_not_confirmable")
        try:
            assessment = assess_candidate(
                candidate.raw_text_utf8.decode("utf-8", errors="surrogatepass"),
                candidate.method,
                candidate.provenance,
            )
        except (UnicodeError, ValueError) as error:
            raise PageVerificationBlockedError("source_candidate_not_confirmable") from error
        if not assessment.can_confirm or not await self._session.scalar(
            select(func.public.source_candidate_is_confirmable(candidate.id))
        ):
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
        provenance = dict(current.provenance) if current else {}
        languages = set(_strings(provenance.get("languages")))
        previous_text = ""
        invalid_languages = False
        if current:
            languages.update(_strings(current.diagnostics.get("languages")))
            try:
                previous_text = current.raw_text_utf8.decode("utf-8", errors="surrogatepass")
            except UnicodeDecodeError:
                previous_text = current.raw_text_utf8.decode("utf-8", errors="surrogateescape")
            assessment = assess_candidate(
                normalize_source_text(previous_text[:MAX_TEXT_CHARACTERS])[:MAX_TEXT_CHARACTERS],
                current.method,
                provenance,
            )
            languages.update(assessment.languages)
            invalid_languages = "invalid_languages" in assessment.risk_codes
        if not invalid_languages:
            provenance["languages"] = sorted(languages)
        provenance.update(engine="human-edit", version="1", engine_version="1")
        provenance.pop("maths_words", None)
        provenance.pop("candidate_selection", None)
        reference = provenance.get("maths_reference")
        if isinstance(reference, dict):
            maths = assess_math_fidelity(reference, text)
            provenance["maths_fidelity"] = {
                "can_confirm": maths["can_confirm"],
                "risk_codes": maths["risk_codes"],
            }
        algorithm = current.diagnostics.get("algorithm_version") if current else None
        if not isinstance(algorithm, str) or not algorithm.startswith("source-fidelity-v2/"):
            provenance["source_reprocessing_required"] = True
            provenance.setdefault("failure_code", "source_reprocessing_required")
        elif current is not None and provenance.get("failure_code") in (
            "ocr_timeout",
            "ocr_unavailable",
            "ocr_process_failed",
            "ocr_output_limit",
            "ocr_text_limit",
            "ocr_output_invalid",
            "ocr_failed",
            "ocr_languages_unavailable",
            "ocr_configuration_invalid",
        ):
            if normalize_source_text(text) != normalize_source_text(previous_text):
                provenance["superseded_ocr_failure_code"] = provenance.pop("failure_code")
        return await self.record_candidate(
            document_id,
            page_number,
            raw_text=text,
            method="human",
            actor_id=actor_id,
            provenance=provenance,
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
        if (
            document is None
            or not document.original_page_count
            or not document.active_for_ai
            or document.quarantined_for_teacher_use
        ):
            return False
        return bool(
            await self._session.scalar(
                select(
                    func.public.source_document_fidelity_is_current(document_id, type_=Boolean())
                )
            )
        )

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

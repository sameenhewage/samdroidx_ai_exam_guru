import codecs
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import Boolean, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.auth.domain import AuthorizationError, Permission, Principal, authorize
from exam_guru_api.curriculum.admission import admitted_curriculum_predicate
from exam_guru_api.documents.fidelity import (
    MAX_TEXT_CHARACTERS,
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
from exam_guru_api.documents.fidelity_schemas import (
    PageConfirmRequest,
    PageEditRequest,
    PageExcludeRequest,
    PageReviewMutationResponse,
    PageReviewProgress,
    PageReviewView,
    PageReviewWorkspaceResponse,
    ReviewCandidateSummary,
    SourceBenchmarkCreateRequest,
    SourceBenchmarkPageView,
    SourceBenchmarkResponse,
)
from exam_guru_api.documents.fidelity_service import (
    FidelitySourceNotFoundError,
    PageFidelityService,
    assess_candidate,
)
from exam_guru_api.documents.models import SourceDocumentModel, SourcePageModel

_HISTORY_LIMIT = 20
_TEXT_BYTE_LIMIT = 4 * (MAX_TEXT_CHARACTERS + 1)


@dataclass(frozen=True, slots=True)
class _Candidate:
    id: UUID
    method: str
    raw_text: bytes
    raw_size: int
    can_confirm: bool
    provenance: dict[str, object]
    diagnostics: dict[str, object]


@dataclass(frozen=True, slots=True)
class _TextView:
    system_text: str
    language: str
    can_confirm: bool
    risk_codes: list[str]
    diagnostics: dict[str, object]


def _page_number(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("page number must be positive")


def _escape_display(value: str) -> str:
    parts = []
    for character in value:
        category = unicodedata.category(character)
        if category in {"Cc", "Cf", "Cs", "Co", "Cn"} and character not in "\t\n\r\u200c\u200d":
            codepoint = ord(character)
            parts.append(f"\\u{codepoint:04x}" if codepoint <= 0xFFFF else f"\\U{codepoint:08x}")
        else:
            parts.append(character)
    return "".join(parts)


def _display_metadata(value: object) -> object:
    if isinstance(value, str):
        return _escape_display(value)
    if isinstance(value, dict):
        return {_escape_display(str(key)): _display_metadata(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_display_metadata(item) for item in value]
    return value


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        return ()
    return tuple(item for item in value[:128] if isinstance(item, str) and len(item) <= 256)


def _text_view(
    text: str,
    *,
    method: str,
    provenance: dict[str, object],
    diagnostics: dict[str, object],
    truncated: bool = False,
) -> _TextView:
    normalized = normalize_source_text(text[:MAX_TEXT_CHARACTERS])
    assessment = assess_candidate(normalized[:MAX_TEXT_CHARACTERS], method, provenance)
    display = _escape_display(normalized[:MAX_TEXT_CHARACTERS])
    truncated = truncated or max(len(text), len(normalized), len(display)) > MAX_TEXT_CHARACTERS
    risk_codes = sorted(set(assessment.risk_codes) | set(_strings(diagnostics.get("risk_codes"))))
    details = {
        **diagnostics,
        "stored_diagnostics": diagnostics,
        "algorithm_version": assessment.algorithm_version,
        "languages": list(assessment.languages),
        "script_counts": dict(assessment.script_counts),
        "classifications": list(assessment.classifications),
        "recommended_route": assessment.recommended_route,
        "risk_codes": risk_codes,
        "text_truncated": truncated,
        "text_readable": not truncated
        and assess_candidate(
            normalized[:MAX_TEXT_CHARACTERS],
            method,
            {
                key: value
                for key, value in provenance.items()
                if key not in {"maths_reference", "maths_fidelity", "maths_words"}
            },
        ).can_confirm,
    }
    language = assessment.languages[0] if len(assessment.languages) == 1 else "mul"
    if assessment.script_counts.get("sinhala", 0) and not assessment.script_counts.get("tamil", 0):
        language = "si"
    return _TextView(
        system_text=display[:MAX_TEXT_CHARACTERS],
        language=language,
        can_confirm=assessment.can_confirm and not truncated,
        risk_codes=[_escape_display(code) for code in risk_codes],
        diagnostics=cast(dict[str, object], _display_metadata(details)),
    )


async def _source(
    session: AsyncSession, document_id: UUID, page_number: int
) -> SourceDocumentModel:
    _page_number(page_number)
    document = await session.scalar(
        select(SourceDocumentModel)
        .where(SourceDocumentModel.id == document_id)
        .execution_options(populate_existing=True)
    )
    if document is None:
        raise FidelitySourceNotFoundError("source_document_not_found")
    total = document.original_page_count or document.extracted_page_count or 0
    if page_number > total and not (total == 0 and page_number == 1):
        raise FidelitySourceNotFoundError("source_page_not_found")
    return document


async def _progress(
    session: AsyncSession, document_id: UUID, total: int, page_number: int
) -> tuple[PageReviewProgress, int | None, int | None]:
    numbers = func.generate_series(1, total).table_valued("page_number").render_derived()
    state, legacy = PageReviewStateModel, SourcePageModel
    current = func.public.source_page_fidelity_is_current(
        document_id, numbers.c.page_number, state.current_candidate_id, type_=Boolean()
    )
    flagged = and_(func.coalesce(state.state, "pending") != "excluded", current.is_(False))
    processed, verified, excluded, previous, following = (
        await session.execute(
            select(
                func.count().filter(
                    or_(state.current_candidate_id.is_not(None), legacy.id.is_not(None))
                ),
                func.count().filter(current),
                func.count().filter(state.state == "excluded"),
                func.max(numbers.c.page_number).filter(
                    and_(flagged, numbers.c.page_number < page_number)
                ),
                func.min(numbers.c.page_number).filter(
                    and_(flagged, numbers.c.page_number > page_number)
                ),
            )
            .select_from(numbers)
            .outerjoin(
                state,
                and_(state.document_id == document_id, state.page_number == numbers.c.page_number),
            )
            .outerjoin(
                legacy,
                and_(
                    legacy.source_document_id == document_id,
                    legacy.page_number == numbers.c.page_number,
                ),
            )
        )
    ).one()
    remaining = total - verified - excluded
    return (
        PageReviewProgress(
            total_pages=total,
            processed_pages=processed,
            verified_pages=verified,
            excluded_pages=excluded,
            flagged_pages=remaining,
            remaining_pages=remaining,
        ),
        previous,
        following,
    )


async def _candidate(
    session: AsyncSession, document_id: UUID, page_number: int, candidate_id: UUID
) -> _Candidate | None:
    candidate = PageTextCandidateModel
    record = (
        (
            await session.execute(
                select(
                    candidate.id,
                    candidate.method,
                    func.substr(candidate.raw_text_utf8, 1, _TEXT_BYTE_LIMIT).label("raw_text"),
                    func.octet_length(candidate.raw_text_utf8).label("raw_size"),
                    func.public.source_candidate_is_confirmable(candidate.id).label("can_confirm"),
                    candidate.provenance,
                    candidate.diagnostics,
                )
                .where(
                    candidate.id == candidate_id,
                    candidate.document_id == document_id,
                    candidate.page_number == page_number,
                )
                .limit(1)
            )
        )
        .mappings()
        .one_or_none()
    )
    return _Candidate(**record) if record is not None else None


async def _history(
    session: AsyncSession, document_id: UUID, page_number: int, current_candidate_id: UUID | None
) -> list[ReviewCandidateSummary]:
    candidate = PageTextCandidateModel
    records = await session.execute(
        select(candidate.id, candidate.method, candidate.created_at, candidate.text_sha256)
        .where(candidate.document_id == document_id, candidate.page_number == page_number)
        .order_by(candidate.created_at.desc(), candidate.id.desc())
        .limit(_HISTORY_LIMIT)
    )
    return [
        ReviewCandidateSummary(**record, is_current=record["id"] == current_candidate_id)
        for record in records.mappings()
    ]


async def _legacy_text(
    session: AsyncSession, document_id: UUID, page_number: int
) -> tuple[str, dict[str, object]]:
    legacy = SourcePageModel
    record = (
        await session.execute(
            select(
                func.substr(legacy.raw_text, 1, MAX_TEXT_CHARACTERS + 1).label("text"),
                legacy.extractor,
                legacy.extractor_version,
                legacy.extraction_config,
            )
            .where(legacy.source_document_id == document_id, legacy.page_number == page_number)
            .limit(1)
        )
    ).one_or_none()
    if record is None:
        return "", {}
    return record.text, {
        "method": "legacy",
        "engine": record.extractor,
        "version": record.extractor_version,
        **record.extraction_config,
    }


async def _page_view(
    session: AsyncSession,
    document: SourceDocumentModel,
    page_number: int,
    *,
    principal: Principal,
    candidate_id: UUID | None = None,
) -> PageReviewView:
    state = await session.scalar(
        select(PageReviewStateModel)
        .where(
            PageReviewStateModel.document_id == document.id,
            PageReviewStateModel.page_number == page_number,
        )
        .execution_options(populate_existing=True)
    )
    current_id = state.current_candidate_id if state else None
    selected_id = candidate_id if candidate_id is not None else current_id
    candidate = (
        await _candidate(session, document.id, page_number, selected_id)
        if selected_id is not None
        else None
    )
    if candidate_id is not None and candidate is None:
        raise FidelitySourceNotFoundError("source_candidate_not_found")
    provenance: dict[str, object]
    if candidate is None:
        raw_text, provenance = await _legacy_text(session, document.id, page_number)
        view = _text_view(raw_text, method="legacy", provenance=provenance, diagnostics={})
    else:
        try:
            raw_text = codecs.getincrementaldecoder("utf-8")("surrogatepass").decode(
                candidate.raw_text, final=candidate.raw_size == len(candidate.raw_text)
            )
        except UnicodeDecodeError:
            raw_text = candidate.raw_text.decode("utf-8", errors="surrogateescape")
        provenance = candidate.provenance
        view = _text_view(
            raw_text,
            method=candidate.method,
            provenance=provenance,
            diagnostics=candidate.diagnostics,
            truncated=candidate.raw_size > len(candidate.raw_text),
        )
    try:
        authorize(principal, Permission.SOURCE_TRUST)
        can_trust = True
    except AuthorizationError:
        can_trust = False
    selected_is_current = selected_id == current_id
    page_state = (
        state.state if state and selected_is_current else "needs_review" if candidate else "pending"
    )
    confirmable = bool(candidate and candidate.can_confirm and view.can_confirm)
    current_verified = page_state != "verified" or bool(
        await session.scalar(
            select(
                func.public.source_page_fidelity_is_current(document.id, page_number, current_id)
            )
        )
    )
    if candidate and (not confirmable or not current_verified):
        risks = sorted(set(view.risk_codes) | {"source_reprocessing_required"})
        view = replace(
            view,
            can_confirm=False,
            risk_codes=risks,
            diagnostics={
                **view.diagnostics,
                "risk_codes": risks,
                "source_reprocessing_required": True,
                "recommended_route": "ocr_review",
            },
        )
        if page_state not in {"excluded", "processing"}:
            page_state = "failed"
    return PageReviewView(
        page_number=page_number,
        state=cast(
            Literal["pending", "needs_review", "verified", "excluded", "processing", "failed"],
            page_state,
        ),
        version=state.version if state else 0,
        candidate_id=candidate.id if candidate else None,
        system_text=view.system_text,
        language=view.language,
        can_confirm=bool(
            can_trust
            and document.active_for_ai
            and not document.quarantined_for_teacher_use
            and state
            and state.state == "needs_review"
            and selected_is_current
            and candidate
            and candidate.can_confirm
            and view.can_confirm
        ),
        risk_codes=view.risk_codes,
        preview_url=f"/api/v1/admin/materials/{document.id}/pages/{page_number}/image",
        provenance=cast(dict[str, object], _display_metadata(provenance)),
        diagnostics=view.diagnostics,
        history=await _history(session, document.id, page_number, current_id),
    )


async def get_review_workspace(
    session: AsyncSession,
    document_id: UUID,
    *,
    page_number: int = 1,
    principal: Principal,
) -> PageReviewWorkspaceResponse:
    authorize(principal, Permission.SOURCE_READ)
    with session.no_autoflush:
        document = await _source(session, document_id, page_number)
        total = document.original_page_count or document.extracted_page_count or 0
        progress, previous, following = await _progress(session, document_id, total, page_number)
        page = (
            await _page_view(session, document, page_number, principal=principal) if total else None
        )
        admitted = document.curriculum_version_id is not None and bool(
            await session.scalar(
                select(admitted_curriculum_predicate(document.curriculum_version_id))
            )
        )
        metadata_review_required = document.metadata_review_required or not admitted
        ready = bool(
            document.original_page_count
            and document.active_for_ai
            and not document.quarantined_for_teacher_use
            and not metadata_review_required
            and progress.remaining_pages == 0
            and progress.verified_pages > 0
            and await session.scalar(
                select(func.public.source_document_fidelity_is_current(document.id))
            )
        )
        return PageReviewWorkspaceResponse(
            document_id=document.id,
            document_title=_escape_display(document.original_filename),
            language=page.language if page else "und",
            metadata_review_required=metadata_review_required,
            source_active=document.active_for_ai,
            ready_for_ai=ready,
            progress=progress,
            page=page,
            previous_flagged_page=previous,
            next_flagged_page=following,
        )


async def get_source_page_candidate(
    session: AsyncSession,
    document_id: UUID,
    page_number: int,
    candidate_id: UUID,
    *,
    principal: Principal,
) -> PageReviewView:
    authorize(principal, Permission.SOURCE_READ)
    with session.no_autoflush:
        document = await _source(session, document_id, page_number)
        return await _page_view(
            session, document, page_number, candidate_id=candidate_id, principal=principal
        )


def _mutation_response(state: PageReviewStateModel) -> PageReviewMutationResponse:
    return PageReviewMutationResponse(
        document_id=state.document_id,
        page_number=state.page_number,
        version=state.version,
        state=state.state,
        candidate_id=state.current_candidate_id,
    )


async def confirm_source_page(
    session: AsyncSession,
    document_id: UUID,
    page_number: int,
    request: PageConfirmRequest,
    *,
    principal: Principal,
) -> PageReviewMutationResponse:
    authorize(principal, Permission.SOURCE_TRUST)
    if request.compared_with_original is not True:
        raise ValueError("explicit comparison with the original is required")
    return _mutation_response(
        await PageFidelityService(session).confirm_page(
            document_id,
            page_number,
            candidate_id=request.candidate_id,
            expected_version=request.expected_version,
            actor_id=principal.subject_id,
            reason=request.reason,
        )
    )


async def edit_source_page(
    session: AsyncSession,
    document_id: UUID,
    page_number: int,
    request: PageEditRequest,
    *,
    principal: Principal,
) -> PageReviewMutationResponse:
    authorize(principal, Permission.CONTENT_REVIEW)
    return _mutation_response(
        await PageFidelityService(session).edit_page(
            document_id,
            page_number,
            text=request.text,
            expected_version=request.expected_version,
            actor_id=principal.subject_id,
            reason=request.reason,
        )
    )


async def exclude_source_page(
    session: AsyncSession,
    document_id: UUID,
    page_number: int,
    request: PageExcludeRequest,
    *,
    principal: Principal,
) -> PageReviewMutationResponse:
    authorize(principal, Permission.SOURCE_WRITE)
    if request.confirm_exclusion is not True:
        raise ValueError("explicit exclusion confirmation is required")
    return _mutation_response(
        await PageFidelityService(session).exclude_page(
            document_id,
            page_number,
            expected_version=request.expected_version,
            actor_id=principal.subject_id,
            reason=request.reason,
        )
    )


async def _benchmark_views(
    session: AsyncSession, benchmarks: Sequence[SourceBenchmarkModel]
) -> list[SourceBenchmarkResponse]:
    if not benchmarks:
        return []
    identifiers = [benchmark.id for benchmark in benchmarks]
    membership, truth, event, state = (
        SourceBenchmarkPageModel,
        PageGroundTruthModel,
        PageReviewEventModel,
        PageReviewStateModel,
    )
    references = (
        select(
            truth.benchmark_id,
            truth.document_id,
            truth.page_number,
            func.count().label("versions"),
        )
        .join(
            event,
            and_(
                event.id == truth.review_event_id,
                event.document_id == truth.document_id,
                event.page_number == truth.page_number,
                event.candidate_id == truth.candidate_id,
                event.actor_id == truth.reviewer_id,
                event.action == "confirmed",
                event.state == "verified",
                event.payload["compared_with_original"].as_boolean().is_(True),
                event.payload["text_sha256"].as_string() == truth.text_sha256,
            ),
        )
        .where(truth.benchmark_id.in_(identifiers))
        .group_by(truth.benchmark_id, truth.document_id, truth.page_number)
        .subquery()
    )
    records = await session.execute(
        select(
            membership.benchmark_id,
            membership.document_id,
            SourceDocumentModel.original_filename,
            membership.page_number,
            membership.categories,
            func.coalesce(state.state, "pending").label("state"),
            func.coalesce(references.c.versions, 0).label("versions"),
        )
        .join(SourceDocumentModel, SourceDocumentModel.id == membership.document_id)
        .outerjoin(
            state,
            and_(
                state.document_id == membership.document_id,
                state.page_number == membership.page_number,
            ),
        )
        .outerjoin(
            references,
            and_(
                references.c.benchmark_id == membership.benchmark_id,
                references.c.document_id == membership.document_id,
                references.c.page_number == membership.page_number,
                state.state == "verified",
            ),
        )
        .where(membership.benchmark_id.in_(identifiers))
        .order_by(membership.document_id, membership.page_number)
    )
    pages: dict[UUID, list[SourceBenchmarkPageView]] = {
        identifier: [] for identifier in identifiers
    }
    for record in records:
        pages[record.benchmark_id].append(
            SourceBenchmarkPageView(
                document_id=record.document_id,
                document_title=_escape_display(record.original_filename),
                page_number=record.page_number,
                categories=[_escape_display(category) for category in record.categories],
                state=record.state,
                ground_truth_versions=record.versions,
            )
        )
    responses = []
    for benchmark in benchmarks:
        reviewed = sum(page.ground_truth_versions > 0 for page in pages[benchmark.id])
        responses.append(
            SourceBenchmarkResponse(
                id=benchmark.id,
                name=_escape_display(benchmark.name),
                created_at=benchmark.created_at,
                pages=pages[benchmark.id],
                adjudicated_pages=reviewed,
                pending_pages=len(pages[benchmark.id]) - reviewed,
                accuracy_status="references_available"
                if reviewed
                else "awaiting_human_adjudication",
            )
        )
    return responses


async def get_source_benchmark(
    session: AsyncSession, benchmark_id: UUID, *, principal: Principal
) -> SourceBenchmarkResponse:
    authorize(principal, Permission.SOURCE_READ)
    with session.no_autoflush:
        benchmark = await session.scalar(
            select(SourceBenchmarkModel).where(SourceBenchmarkModel.id == benchmark_id)
        )
        if benchmark is None:
            raise FidelitySourceNotFoundError("source_benchmark_not_found")
        return (await _benchmark_views(session, [benchmark]))[0]


async def list_source_benchmarks(
    session: AsyncSession,
    *,
    principal: Principal,
    limit: int = 20,
    offset: int = 0,
) -> list[SourceBenchmarkResponse]:
    authorize(principal, Permission.SOURCE_READ)
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= 50
        or isinstance(offset, bool)
        or not isinstance(offset, int)
        or not 0 <= offset <= 100000
    ):
        raise ValueError("benchmark pagination exceeds its bounds")
    with session.no_autoflush:
        benchmarks = await session.scalars(
            select(SourceBenchmarkModel)
            .order_by(SourceBenchmarkModel.created_at.desc(), SourceBenchmarkModel.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return await _benchmark_views(session, benchmarks.all())


async def create_source_benchmark(
    session: AsyncSession,
    request: SourceBenchmarkCreateRequest,
    *,
    principal: Principal,
) -> SourceBenchmarkResponse:
    authorize(principal, Permission.SOURCE_WRITE)
    identifier = await PageFidelityService(session).create_benchmark(
        name=request.name,
        pages=tuple(
            (page.document_id, page.page_number, tuple(page.categories)) for page in request.pages
        ),
        actor_id=principal.subject_id,
        selection=request.selection,
    )
    return await get_source_benchmark(session, identifier, principal=principal)

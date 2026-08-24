import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import ColumnElement, func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.documents.domain import ExtractionStatus
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.generation.models import GenerationRunModel, GenerationRunStatus
from exam_guru_api.knowledge.models import EmbeddingJobModel, EmbeddingJobStatus
from exam_guru_api.operations.schemas import (
    EmbeddingOperationsResponse,
    EmbeddingStatusCountsResponse,
    ExtractionOperationsResponse,
    ExtractionStatusCountsResponse,
    FailureCodeCountResponse,
    GenerationOperationsResponse,
    GenerationStatusCountsResponse,
    LatencyMillisecondsResponse,
    OperationsDataBoundsResponse,
    OperationsSummaryResponse,
    OperationsUnitsResponse,
    OperationsWindowResponse,
    PracticePaperOperationsResponse,
    PracticePaperStateCountsResponse,
    ValidationOperationsResponse,
    ValidationStatusCountsResponse,
)
from exam_guru_api.papers.domain import PaperState
from exam_guru_api.papers.publication_models import (
    PaperArchiveEventModel,
    PracticePaperModel,
    PublishedPaperVersionModel,
)
from exam_guru_api.validation.domain import FindingStatus
from exam_guru_api.validation.models import ValidationFindingModel, ValidationRunModel

DEFAULT_OPERATIONS_WINDOW = timedelta(hours=24)
MAX_OPERATIONS_WINDOW = timedelta(days=31)
_FAILURE_CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")


class OperationsWindowError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class OperationsWindow:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.utcoffset() is None or self.end.utcoffset() is None:
            raise OperationsWindowError("operations_window_timezone_required")
        normalized_start = self.start.astimezone(UTC)
        normalized_end = self.end.astimezone(UTC)
        if normalized_end <= normalized_start:
            raise OperationsWindowError("operations_window_invalid_order")
        if normalized_end - normalized_start > MAX_OPERATIONS_WINDOW:
            raise OperationsWindowError("operations_window_too_large")
        object.__setattr__(self, "start", normalized_start)
        object.__setattr__(self, "end", normalized_end)

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    @classmethod
    def resolve(
        cls,
        *,
        start: datetime | None,
        end: datetime | None,
        now: datetime,
    ) -> "OperationsWindow":
        resolved_end = now if end is None else end
        resolved_start = resolved_end - DEFAULT_OPERATIONS_WINDOW if start is None else start
        return cls(start=resolved_start, end=resolved_end)


def _zeroed_sum(column: Any) -> ColumnElement[Any]:
    return func.coalesce(func.sum(column), 0)


def _zeroed_max(column: Any) -> ColumnElement[Any]:
    return func.coalesce(func.max(column), 0)


def _count_when(condition: ColumnElement[bool]) -> ColumnElement[int]:
    return func.count().filter(condition)


def _failure_counts(rows: tuple[tuple[str, int], ...]) -> list[FailureCodeCountResponse]:
    counts: dict[str, int] = {}
    for raw_code, count in rows:
        code = raw_code if _FAILURE_CODE.fullmatch(raw_code) else "unclassified_failure"
        counts[code] = counts.get(code, 0) + int(count)
    return [
        FailureCodeCountResponse(code=code, count=count) for code, count in sorted(counts.items())
    ]


def _observed_bounds(rows: tuple[Any, ...]) -> OperationsDataBoundsResponse:
    starts = tuple(value for row in rows if (value := row.earliest_observed_at) is not None)
    ends = tuple(value for row in rows if (value := row.latest_observed_at) is not None)
    return OperationsDataBoundsResponse(
        earliest_observed_at=min(starts) if starts else None,
        latest_observed_at=max(ends) if ends else None,
    )


class OperationsSummaryService:
    """Aggregate fixed operational dimensions in PostgreSQL without loading content rows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def summarize(self, window: OperationsWindow) -> OperationsSummaryResponse:
        generation = await self._generation(window)
        generation_failures = await self._generation_failures(window)
        validation = await self._validation(window)
        validation_findings = await self._validation_findings(window)
        extraction = await self._extraction(window)
        extraction_failures = await self._extraction_failures(window)
        embedding = await self._embedding(window)
        embedding_failures = await self._embedding_failures(window)
        papers = await self._papers(window)

        run_count = int(generation.run_count)
        return OperationsSummaryResponse(
            window=OperationsWindowResponse(start=window.start, end=window.end),
            data_bounds=_observed_bounds((generation, validation, extraction, embedding, papers)),
            units=OperationsUnitsResponse(),
            generation=GenerationOperationsResponse(
                run_count=run_count,
                status_counts=GenerationStatusCountsResponse(
                    pending=int(generation.pending_count),
                    running=int(generation.running_count),
                    succeeded=int(generation.succeeded_count),
                    failed=int(generation.failed_count),
                ),
                failure_codes=generation_failures,
                attempt_count=int(generation.attempt_count),
                input_tokens=int(generation.input_tokens),
                output_tokens=int(generation.output_tokens),
                total_tokens=int(generation.total_tokens),
                cost_microusd=int(generation.cost_microusd),
                latency_ms=LatencyMillisecondsResponse(
                    total=int(generation.total_latency_ms),
                    average=(int(generation.total_latency_ms) // run_count if run_count else 0),
                    maximum=int(generation.maximum_latency_ms),
                ),
            ),
            validation=ValidationOperationsResponse(
                run_count=int(validation.run_count),
                run_status_counts=ValidationStatusCountsResponse.model_validate(
                    {
                        "pass": int(validation.pass_count),
                        "warn": int(validation.warn_count),
                        "fail": int(validation.fail_count),
                    }
                ),
                finding_count=int(validation.finding_count),
                finding_status_counts=ValidationStatusCountsResponse.model_validate(
                    {
                        "pass": int(validation_findings.pass_count),
                        "warn": int(validation_findings.warn_count),
                        "fail": int(validation_findings.fail_count),
                    }
                ),
            ),
            extraction=ExtractionOperationsResponse(
                document_count=int(extraction.document_count),
                status_counts=ExtractionStatusCountsResponse(
                    uploaded=int(extraction.uploaded_count),
                    extraction_pending=int(extraction.extraction_pending_count),
                    extracted=int(extraction.extracted_count),
                    in_review=int(extraction.in_review_count),
                    trusted=int(extraction.trusted_count),
                    failed=int(extraction.failed_count),
                ),
                failure_codes=extraction_failures,
                ocr_page_count=int(extraction.ocr_page_count),
            ),
            embedding=EmbeddingOperationsResponse(
                job_count=int(embedding.job_count),
                status_counts=EmbeddingStatusCountsResponse(
                    queued=int(embedding.queued_count),
                    claimed=int(embedding.claimed_count),
                    succeeded=int(embedding.succeeded_count),
                    failed=int(embedding.failed_count),
                ),
                failure_codes=embedding_failures,
                requested_count=int(embedding.requested_count),
                embedded_count=int(embedding.embedded_count),
                deduplicated_count=int(embedding.deduplicated_count),
            ),
            practice_papers=PracticePaperOperationsResponse(
                paper_count=int(papers.paper_count),
                state_counts=PracticePaperStateCountsResponse(
                    draft=int(papers.draft_count),
                    published=int(papers.published_count),
                    archived=int(papers.archived_count),
                ),
                publication_count=int(papers.publication_count),
                archive_count=int(papers.archive_count),
            ),
        )

    async def _generation(self, window: OperationsWindow) -> Any:
        timestamp = func.coalesce(
            GenerationRunModel.completed_at,
            GenerationRunModel.created_at,
        )
        statement = select(
            func.count(GenerationRunModel.id).label("run_count"),
            _count_when(GenerationRunModel.status == GenerationRunStatus.PENDING.value).label(
                "pending_count"
            ),
            _count_when(GenerationRunModel.status == GenerationRunStatus.RUNNING.value).label(
                "running_count"
            ),
            _count_when(GenerationRunModel.status == GenerationRunStatus.SUCCEEDED.value).label(
                "succeeded_count"
            ),
            _count_when(GenerationRunModel.status == GenerationRunStatus.FAILED.value).label(
                "failed_count"
            ),
            _zeroed_sum(GenerationRunModel.attempt_count).label("attempt_count"),
            _zeroed_sum(GenerationRunModel.input_tokens).label("input_tokens"),
            _zeroed_sum(GenerationRunModel.output_tokens).label("output_tokens"),
            _zeroed_sum(GenerationRunModel.total_tokens).label("total_tokens"),
            _zeroed_sum(GenerationRunModel.cost_microusd).label("cost_microusd"),
            _zeroed_sum(GenerationRunModel.latency_ms).label("total_latency_ms"),
            _zeroed_max(GenerationRunModel.latency_ms).label("maximum_latency_ms"),
            func.min(timestamp).label("earliest_observed_at"),
            func.max(timestamp).label("latest_observed_at"),
        ).where(timestamp >= window.start, timestamp < window.end)
        return (await self._session.execute(statement)).one()

    async def _generation_failures(
        self, window: OperationsWindow
    ) -> list[FailureCodeCountResponse]:
        timestamp = func.coalesce(
            GenerationRunModel.completed_at,
            GenerationRunModel.created_at,
        )
        statement = (
            select(GenerationRunModel.failure_code, func.count(GenerationRunModel.id))
            .where(
                timestamp >= window.start,
                timestamp < window.end,
                GenerationRunModel.failure_code.is_not(None),
            )
            .group_by(GenerationRunModel.failure_code)
            .order_by(GenerationRunModel.failure_code)
        )
        rows = cast(
            tuple[tuple[str, int], ...],
            tuple((await self._session.execute(statement)).all()),
        )
        return _failure_counts(rows)

    async def _validation(self, window: OperationsWindow) -> Any:
        timestamp = ValidationRunModel.created_at
        statement = select(
            func.count(ValidationRunModel.id).label("run_count"),
            _count_when(ValidationRunModel.overall_status == FindingStatus.PASS.value).label(
                "pass_count"
            ),
            _count_when(ValidationRunModel.overall_status == FindingStatus.WARN.value).label(
                "warn_count"
            ),
            _count_when(ValidationRunModel.overall_status == FindingStatus.FAIL.value).label(
                "fail_count"
            ),
            _zeroed_sum(ValidationRunModel.finding_count).label("finding_count"),
            func.min(timestamp).label("earliest_observed_at"),
            func.max(timestamp).label("latest_observed_at"),
        ).where(timestamp >= window.start, timestamp < window.end)
        return (await self._session.execute(statement)).one()

    async def _validation_findings(self, window: OperationsWindow) -> Any:
        timestamp = ValidationRunModel.created_at
        statement = (
            select(
                _count_when(ValidationFindingModel.status == FindingStatus.PASS.value).label(
                    "pass_count"
                ),
                _count_when(ValidationFindingModel.status == FindingStatus.WARN.value).label(
                    "warn_count"
                ),
                _count_when(ValidationFindingModel.status == FindingStatus.FAIL.value).label(
                    "fail_count"
                ),
            )
            .select_from(ValidationFindingModel)
            .join(
                ValidationRunModel,
                ValidationRunModel.id == ValidationFindingModel.validation_run_id,
            )
            .where(timestamp >= window.start, timestamp < window.end)
        )
        return (await self._session.execute(statement)).one()

    async def _extraction(self, window: OperationsWindow) -> Any:
        timestamp = SourceDocumentModel.updated_at
        statement = select(
            func.count(SourceDocumentModel.id).label("document_count"),
            _count_when(SourceDocumentModel.extraction_status == ExtractionStatus.UPLOADED).label(
                "uploaded_count"
            ),
            _count_when(
                SourceDocumentModel.extraction_status == ExtractionStatus.EXTRACTION_PENDING
            ).label("extraction_pending_count"),
            _count_when(SourceDocumentModel.extraction_status == ExtractionStatus.EXTRACTED).label(
                "extracted_count"
            ),
            _count_when(SourceDocumentModel.extraction_status == ExtractionStatus.IN_REVIEW).label(
                "in_review_count"
            ),
            _count_when(SourceDocumentModel.extraction_status == ExtractionStatus.TRUSTED).label(
                "trusted_count"
            ),
            _count_when(SourceDocumentModel.extraction_status == ExtractionStatus.FAILED).label(
                "failed_count"
            ),
            func.coalesce(func.sum(SourceDocumentModel.ocr_page_count), 0).label("ocr_page_count"),
            func.min(timestamp).label("earliest_observed_at"),
            func.max(timestamp).label("latest_observed_at"),
        ).where(timestamp >= window.start, timestamp < window.end)
        return (await self._session.execute(statement)).one()

    async def _extraction_failures(
        self, window: OperationsWindow
    ) -> list[FailureCodeCountResponse]:
        timestamp = SourceDocumentModel.updated_at
        statement = (
            select(
                SourceDocumentModel.extraction_failure_code,
                func.count(SourceDocumentModel.id),
            )
            .where(
                timestamp >= window.start,
                timestamp < window.end,
                SourceDocumentModel.extraction_failure_code.is_not(None),
            )
            .group_by(SourceDocumentModel.extraction_failure_code)
            .order_by(SourceDocumentModel.extraction_failure_code)
        )
        rows = cast(
            tuple[tuple[str, int], ...],
            tuple((await self._session.execute(statement)).all()),
        )
        return _failure_counts(rows)

    async def _embedding(self, window: OperationsWindow) -> Any:
        timestamp = func.coalesce(
            EmbeddingJobModel.completed_at,
            EmbeddingJobModel.created_at,
        )
        statement = select(
            func.count(EmbeddingJobModel.id).label("job_count"),
            _count_when(EmbeddingJobModel.status == EmbeddingJobStatus.QUEUED.value).label(
                "queued_count"
            ),
            _count_when(EmbeddingJobModel.status == EmbeddingJobStatus.CLAIMED.value).label(
                "claimed_count"
            ),
            _count_when(EmbeddingJobModel.status == EmbeddingJobStatus.SUCCEEDED.value).label(
                "succeeded_count"
            ),
            _count_when(EmbeddingJobModel.status == EmbeddingJobStatus.FAILED.value).label(
                "failed_count"
            ),
            _zeroed_sum(EmbeddingJobModel.requested_count).label("requested_count"),
            _zeroed_sum(EmbeddingJobModel.embedded_count).label("embedded_count"),
            _zeroed_sum(EmbeddingJobModel.deduplicated_count).label("deduplicated_count"),
            func.min(timestamp).label("earliest_observed_at"),
            func.max(timestamp).label("latest_observed_at"),
        ).where(timestamp >= window.start, timestamp < window.end)
        return (await self._session.execute(statement)).one()

    async def _embedding_failures(self, window: OperationsWindow) -> list[FailureCodeCountResponse]:
        timestamp = func.coalesce(
            EmbeddingJobModel.completed_at,
            EmbeddingJobModel.created_at,
        )
        statement = (
            select(EmbeddingJobModel.failure_code, func.count(EmbeddingJobModel.id))
            .where(
                timestamp >= window.start,
                timestamp < window.end,
                EmbeddingJobModel.failure_code.is_not(None),
            )
            .group_by(EmbeddingJobModel.failure_code)
            .order_by(EmbeddingJobModel.failure_code)
        )
        rows = cast(
            tuple[tuple[str, int], ...],
            tuple((await self._session.execute(statement)).all()),
        )
        return _failure_counts(rows)

    async def _papers(self, window: OperationsWindow) -> Any:
        paper_timestamp = PracticePaperModel.updated_at
        publication_timestamp = PublishedPaperVersionModel.published_at
        archive_timestamp = PaperArchiveEventModel.archived_at
        paper_aggregate = (
            select(
                func.count(PracticePaperModel.id).label("paper_count"),
                _count_when(PracticePaperModel.state == PaperState.DRAFT.value).label(
                    "draft_count"
                ),
                _count_when(PracticePaperModel.state == PaperState.PUBLISHED.value).label(
                    "published_count"
                ),
                _count_when(PracticePaperModel.state == PaperState.ARCHIVED.value).label(
                    "archived_count"
                ),
                func.min(paper_timestamp).label("earliest"),
                func.max(paper_timestamp).label("latest"),
            )
            .where(paper_timestamp >= window.start, paper_timestamp < window.end)
            .cte("operations_papers")
        )
        publication_aggregate = (
            select(
                func.count().label("publication_count"),
                func.min(publication_timestamp).label("earliest"),
                func.max(publication_timestamp).label("latest"),
            )
            .select_from(PublishedPaperVersionModel)
            .where(publication_timestamp >= window.start, publication_timestamp < window.end)
            .cte("operations_publications")
        )
        archive_aggregate = (
            select(
                func.count().label("archive_count"),
                func.min(archive_timestamp).label("earliest"),
                func.max(archive_timestamp).label("latest"),
            )
            .select_from(PaperArchiveEventModel)
            .where(archive_timestamp >= window.start, archive_timestamp < window.end)
            .cte("operations_archives")
        )
        statement = select(
            paper_aggregate.c.paper_count,
            paper_aggregate.c.draft_count,
            paper_aggregate.c.published_count,
            paper_aggregate.c.archived_count,
            publication_aggregate.c.publication_count,
            archive_aggregate.c.archive_count,
            func.least(
                paper_aggregate.c.earliest,
                publication_aggregate.c.earliest,
                archive_aggregate.c.earliest,
            ).label("earliest_observed_at"),
            func.greatest(
                paper_aggregate.c.latest,
                publication_aggregate.c.latest,
                archive_aggregate.c.latest,
            ).label("latest_observed_at"),
        ).select_from(
            paper_aggregate.join(publication_aggregate, true()).join(
                archive_aggregate,
                true(),
            )
        )
        return (await self._session.execute(statement)).one()

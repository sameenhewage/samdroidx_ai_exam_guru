from __future__ import annotations

from dataclasses import dataclass
from typing import cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import Subquery

from exam_guru_api.papers.models import QuestionCandidateRevisionModel
from exam_guru_api.subject_quality.models import (
    SubjectQualityEvalCaseVersionModel,
    SubjectQualityEvalResultModel,
    SubjectQualityEvalRunModel,
    SubjectQualityFeedbackModel,
)


class SubjectQualityFeedbackNotFoundError(LookupError):
    pass


class SubjectQualityEvalCaseNotFoundError(LookupError):
    pass


class SubjectQualityEvalRunNotFoundError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class StoredEvalRun:
    run: SubjectQualityEvalRunModel
    results: tuple[SubjectQualityEvalResultModel, ...]


class SubjectQualityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def revision(self, candidate_id: UUID, revision: int) -> QuestionCandidateRevisionModel:
        model = await self.session.get(
            QuestionCandidateRevisionModel,
            {"candidate_id": candidate_id, "revision": revision},
        )
        if model is None:
            raise SubjectQualityFeedbackNotFoundError(candidate_id)
        return model

    async def feedback_by_action(
        self, action_fingerprint: str
    ) -> SubjectQualityFeedbackModel | None:
        return cast(
            SubjectQualityFeedbackModel | None,
            await self.session.scalar(
                select(SubjectQualityFeedbackModel).where(
                    SubjectQualityFeedbackModel.action_fingerprint == action_fingerprint
                )
            ),
        )

    async def add_feedback(self, model: SubjectQualityFeedbackModel) -> SubjectQualityFeedbackModel:
        self.session.add(model)
        await self.session.flush()
        return model

    async def feedback(self, feedback_id: UUID) -> SubjectQualityFeedbackModel:
        model = await self.session.get(SubjectQualityFeedbackModel, feedback_id)
        if model is None:
            raise SubjectQualityFeedbackNotFoundError(feedback_id)
        return model

    async def list_feedback(
        self,
        *,
        candidate_id: UUID | None,
        curriculum_version_id: UUID | None,
        limit: int,
        offset: int,
    ) -> tuple[tuple[SubjectQualityFeedbackModel, ...], int]:
        conditions = []
        if candidate_id is not None:
            conditions.append(SubjectQualityFeedbackModel.candidate_id == candidate_id)
        if curriculum_version_id is not None:
            conditions.append(
                SubjectQualityFeedbackModel.curriculum_version_id == curriculum_version_id
            )
        records = tuple(
            await self.session.scalars(
                select(SubjectQualityFeedbackModel)
                .where(*conditions)
                .order_by(
                    SubjectQualityFeedbackModel.created_at.desc(),
                    SubjectQualityFeedbackModel.id.desc(),
                )
                .limit(limit)
                .offset(offset)
            )
        )
        total = int(
            await self.session.scalar(
                select(func.count(SubjectQualityFeedbackModel.id)).where(*conditions)
            )
            or 0
        )
        return records, total

    async def latest_case_for_feedback(
        self, feedback_id: UUID
    ) -> SubjectQualityEvalCaseVersionModel | None:
        return cast(
            SubjectQualityEvalCaseVersionModel | None,
            await self.session.scalar(
                select(SubjectQualityEvalCaseVersionModel)
                .where(SubjectQualityEvalCaseVersionModel.source_feedback_id == feedback_id)
                .order_by(SubjectQualityEvalCaseVersionModel.version.desc())
                .limit(1)
            ),
        )

    async def insert_draft_case(
        self, values: dict[str, object]
    ) -> SubjectQualityEvalCaseVersionModel | None:
        statement = (
            insert(SubjectQualityEvalCaseVersionModel)
            .values(**values)
            .on_conflict_do_nothing()
            .returning(SubjectQualityEvalCaseVersionModel)
        )
        return cast(
            SubjectQualityEvalCaseVersionModel | None,
            await self.session.scalar(statement),
        )

    async def latest_case(
        self,
        eval_case_id: UUID,
        *,
        for_update: bool = False,
    ) -> SubjectQualityEvalCaseVersionModel:
        statement = (
            select(SubjectQualityEvalCaseVersionModel)
            .where(SubjectQualityEvalCaseVersionModel.eval_case_id == eval_case_id)
            .order_by(SubjectQualityEvalCaseVersionModel.version.desc())
            .limit(1)
        )
        if for_update:
            statement = statement.with_for_update()
        model = await self.session.scalar(statement)
        if model is None:
            raise SubjectQualityEvalCaseNotFoundError(eval_case_id)
        return model

    async def add_case_version(
        self, model: SubjectQualityEvalCaseVersionModel
    ) -> SubjectQualityEvalCaseVersionModel:
        self.session.add(model)
        await self.session.flush()
        return model

    def _latest_case_query(self) -> Subquery:
        return (
            select(
                SubjectQualityEvalCaseVersionModel.eval_case_id.label("eval_case_id"),
                func.max(SubjectQualityEvalCaseVersionModel.version).label("version"),
            )
            .group_by(SubjectQualityEvalCaseVersionModel.eval_case_id)
            .subquery()
        )

    async def list_cases(
        self,
        *,
        state: str | None,
        limit: int,
        offset: int,
    ) -> tuple[tuple[SubjectQualityEvalCaseVersionModel, ...], int]:
        latest = self._latest_case_query()
        state_condition = (
            () if state is None else (SubjectQualityEvalCaseVersionModel.state == state,)
        )
        join_condition = (
            latest.c.eval_case_id == SubjectQualityEvalCaseVersionModel.eval_case_id
        ) & (latest.c.version == SubjectQualityEvalCaseVersionModel.version)
        records = tuple(
            await self.session.scalars(
                select(SubjectQualityEvalCaseVersionModel)
                .join(latest, join_condition)
                .where(*state_condition)
                .order_by(
                    SubjectQualityEvalCaseVersionModel.created_at.desc(),
                    SubjectQualityEvalCaseVersionModel.eval_case_id.desc(),
                )
                .limit(limit)
                .offset(offset)
            )
        )
        count_result = await self.session.execute(
            select(func.count()).select_from(
                select(SubjectQualityEvalCaseVersionModel.eval_case_id)
                .join(latest, join_condition)
                .where(*state_condition)
                .subquery()
            )
        )
        total = int(count_result.scalar_one())
        return records, total

    async def approved_cases(
        self, case_ids: tuple[UUID, ...]
    ) -> tuple[SubjectQualityEvalCaseVersionModel, ...]:
        records = tuple(
            await self.session.scalars(
                select(SubjectQualityEvalCaseVersionModel)
                .where(
                    SubjectQualityEvalCaseVersionModel.eval_case_id.in_(case_ids),
                    SubjectQualityEvalCaseVersionModel.version == 2,
                    SubjectQualityEvalCaseVersionModel.state == "approved",
                )
                .order_by(SubjectQualityEvalCaseVersionModel.eval_case_id)
            )
        )
        if len(records) != len(case_ids):
            found = {record.eval_case_id for record in records}
            missing = next(case_id for case_id in case_ids if case_id not in found)
            raise SubjectQualityEvalCaseNotFoundError(missing)
        return records

    async def run_by_request(self, request_fingerprint: str) -> StoredEvalRun | None:
        model = await self.session.scalar(
            select(SubjectQualityEvalRunModel).where(
                SubjectQualityEvalRunModel.request_fingerprint == request_fingerprint
            )
        )
        if model is None:
            return None
        return StoredEvalRun(model, await self.results(model.id))

    async def add_run(
        self,
        run: SubjectQualityEvalRunModel,
        results: tuple[SubjectQualityEvalResultModel, ...],
    ) -> StoredEvalRun:
        self.session.add(run)
        await self.session.flush()
        self.session.add_all(results)
        await self.session.flush()
        return StoredEvalRun(run, results)

    async def results(self, run_id: UUID) -> tuple[SubjectQualityEvalResultModel, ...]:
        return tuple(
            await self.session.scalars(
                select(SubjectQualityEvalResultModel)
                .where(SubjectQualityEvalResultModel.eval_run_id == run_id)
                .order_by(SubjectQualityEvalResultModel.eval_case_id)
            )
        )

    async def run(self, run_id: UUID) -> StoredEvalRun:
        model = await self.session.get(SubjectQualityEvalRunModel, run_id)
        if model is None:
            raise SubjectQualityEvalRunNotFoundError(run_id)
        return StoredEvalRun(model, await self.results(run_id))

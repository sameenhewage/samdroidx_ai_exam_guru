"""PostgreSQL/pgvector adapter for scope-safe hybrid candidate retrieval."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

from pgvector.sqlalchemy import Vector  # type: ignore[import-untyped]
from sqlalchemy import Float, Text, and_, bindparam, func, literal, or_, select, union
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql import Select
from sqlalchemy.sql.selectable import CTE

from exam_guru_api.curriculum.domain import (
    LEGACY_UNCLASSIFIED_SUBJECT_ID,
    TaxonomyLevel,
    TaxonomyReviewState,
)
from exam_guru_api.curriculum.models import (
    CurriculumLessonModel,
    CurriculumUnitModel,
    CurriculumVersionModel,
    ExamConfigurationModel,
    MediumModel,
    SubjectModel,
    TaxonomyNodeModel,
)
from exam_guru_api.documents.models import SourceDocumentModel
from exam_guru_api.knowledge.domain import ReviewState
from exam_guru_api.knowledge.embeddings import EmbeddingConfig
from exam_guru_api.knowledge.models import (
    EmbeddingConfigurationModel,
    HistoricalQuestionModel,
    KnowledgeChunkModel,
    KnowledgeEmbeddingModel,
)
from exam_guru_api.retrieval.domain import (
    LexicalCandidate,
    RetrievalContractError,
    RetrievalFilters,
    RetrievalRecord,
    RetrievalScope,
    RetrievalScopeSet,
    SourceProvenance,
    TaxonomyScope,
    VectorCandidate,
)

MAX_POSTGRES_CANDIDATES = 1_000
MAX_RETRIEVAL_QUERY_CHARACTERS = 4_096
MAX_QUERY_VECTOR_ABSOLUTE_VALUE = 100.0

_RECORD_COLUMNS = (
    "record_kind",
    "record_id",
    "text",
    "grade",
    "exam_id",
    "medium_id",
    "subject_id",
    "curriculum_version_id",
    "unit_id",
    "lesson_id",
    "competency_id",
    "skill_id",
    "sub_skill_id",
    "learning_concept_id",
    "source_document_id",
    "page_number",
    "source_block_id",
)


@dataclass(frozen=True, slots=True)
class RetrievalCandidateSet:
    """Bounded snapshots from the lexical and vector database channels."""

    lexical_candidates: tuple[LexicalCandidate, ...]
    vector_candidates: tuple[VectorCandidate, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.lexical_candidates, tuple) or any(
            not isinstance(candidate, LexicalCandidate) for candidate in self.lexical_candidates
        ):
            raise RetrievalContractError(
                "lexical_candidates must be a tuple of LexicalCandidate values"
            )
        if not isinstance(self.vector_candidates, tuple) or any(
            not isinstance(candidate, VectorCandidate) for candidate in self.vector_candidates
        ):
            raise RetrievalContractError(
                "vector_candidates must be a tuple of VectorCandidate values"
            )


def validate_embedding_config(config: object) -> EmbeddingConfig:
    if not isinstance(config, EmbeddingConfig):
        raise RetrievalContractError("a declared embedding configuration is required")
    string_fields = (
        (config.provider, 64),
        (config.model, 128),
        (config.version, 64),
        (config.config_fingerprint, 128),
    )
    if any(
        not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum
        for value, maximum in string_fields
    ) or (
        not isinstance(config.dimension, int)
        or isinstance(config.dimension, bool)
        or not 1 <= config.dimension <= 4_096
    ):
        raise RetrievalContractError("the declared embedding configuration is invalid")
    return config


def _validate_query(query: object) -> str:
    if (
        not isinstance(query, str)
        or not query.strip()
        or len(query) > MAX_RETRIEVAL_QUERY_CHARACTERS
    ):
        raise RetrievalContractError("retrieval query must be non-blank and bounded")
    return query


def _validate_filters(filters: object) -> RetrievalFilters:
    if not isinstance(filters, (RetrievalScope, RetrievalScopeSet)):
        raise RetrievalContractError("filters must be a RetrievalScope or RetrievalScopeSet")
    return filters


def validate_query_vector(
    query_vector: Sequence[object],
    *,
    expected_dimension: int,
) -> tuple[float, ...]:
    """Snapshot and validate a vector before it reaches the pgvector operator."""

    if (
        not isinstance(expected_dimension, int)
        or isinstance(expected_dimension, bool)
        or not 1 <= expected_dimension <= 4_096
    ):
        raise RetrievalContractError("expected query vector dimension is invalid")
    if isinstance(query_vector, (str, bytes)) or not isinstance(query_vector, Sequence):
        raise RetrievalContractError("query vector must be a finite numeric sequence")
    snapshot = tuple(query_vector)
    if len(snapshot) != expected_dimension:
        raise RetrievalContractError(
            f"query vector dimension must be {expected_dimension}, found {len(snapshot)}"
        )
    if any(
        not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value)
        for value in snapshot
    ):
        raise RetrievalContractError("query vector values must be finite numbers")
    normalized = tuple(float(cast(int | float, value)) for value in snapshot)
    if any(abs(value) > MAX_QUERY_VECTOR_ABSOLUTE_VALUE for value in normalized):
        raise RetrievalContractError(
            f"query vector magnitude cannot exceed {MAX_QUERY_VECTOR_ABSOLUTE_VALUE:g}"
        )
    if not any(value != 0.0 for value in normalized):
        raise RetrievalContractError("cosine query vector must be non-zero")
    return normalized


class PostgresHybridRetrievalRepository:
    """Run both ranked channels in PostgreSQL over one hard-scoped record CTE."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        embedding_config: EmbeddingConfig,
        candidate_limit: int = 50,
    ) -> None:
        if (
            not isinstance(candidate_limit, int)
            or isinstance(candidate_limit, bool)
            or not 1 <= candidate_limit <= MAX_POSTGRES_CANDIDATES
        ):
            raise RetrievalContractError(
                f"candidate_limit must be between 1 and {MAX_POSTGRES_CANDIDATES}"
            )
        self._session = session
        self._embedding_config = validate_embedding_config(embedding_config)
        self._candidate_limit = candidate_limit

    @property
    def embedding_config(self) -> EmbeddingConfig:
        return self._embedding_config

    @property
    def candidate_limit(self) -> int:
        return self._candidate_limit

    def _chunk_scope_select(self, filters: RetrievalScope) -> Select[Any]:
        competency = aliased(TaxonomyNodeModel, name="chunk_competency")
        skill = aliased(TaxonomyNodeModel, name="chunk_skill")
        sub_skill = aliased(TaxonomyNodeModel, name="chunk_sub_skill")
        learning_concept = aliased(TaxonomyNodeModel, name="chunk_learning_concept")
        conditions: list[Any] = [
            KnowledgeChunkModel.review_state == ReviewState.REVIEWED,
            ExamConfigurationModel.active.is_(True),
            MediumModel.active.is_(True),
            SubjectModel.active.is_(True),
            CurriculumVersionModel.active.is_(True),
            SourceDocumentModel.active_for_ai.is_(True),
            or_(KnowledgeChunkModel.unit_id.is_(None), CurriculumUnitModel.active.is_(True)),
            or_(KnowledgeChunkModel.lesson_id.is_(None), CurriculumLessonModel.active.is_(True)),
            ExamConfigurationModel.grade == filters.grade,
            ExamConfigurationModel.id == filters.exam_id,
            MediumModel.id == filters.medium_id,
            SubjectModel.id == filters.subject_id,
            CurriculumVersionModel.id == filters.curriculum_version_id,
            competency.id == filters.taxonomy.competency_id,
            or_(
                KnowledgeChunkModel.skill_id.is_(None),
                and_(
                    skill.parent_id == competency.id,
                    skill.level == TaxonomyLevel.SKILL,
                    skill.active.is_(True),
                    skill.review_state == TaxonomyReviewState.REVIEWED,
                ),
            ),
            or_(
                KnowledgeChunkModel.sub_skill_id.is_(None),
                and_(
                    KnowledgeChunkModel.skill_id.is_not(None),
                    sub_skill.parent_id == skill.id,
                    sub_skill.level == TaxonomyLevel.SUB_SKILL,
                    sub_skill.active.is_(True),
                    sub_skill.review_state == TaxonomyReviewState.REVIEWED,
                ),
            ),
            or_(
                KnowledgeChunkModel.learning_concept_id.is_(None),
                and_(
                    KnowledgeChunkModel.sub_skill_id.is_not(None),
                    learning_concept.parent_id == sub_skill.id,
                    learning_concept.level == TaxonomyLevel.LEARNING_CONCEPT,
                    learning_concept.active.is_(True),
                    learning_concept.review_state == TaxonomyReviewState.REVIEWED,
                ),
            ),
        ]
        if filters.taxonomy.skill_id is not None:
            conditions.append(KnowledgeChunkModel.skill_id == filters.taxonomy.skill_id)
        if filters.taxonomy.sub_skill_id is not None:
            conditions.append(KnowledgeChunkModel.sub_skill_id == filters.taxonomy.sub_skill_id)
        if filters.taxonomy.learning_concept_id is not None:
            conditions.append(
                KnowledgeChunkModel.learning_concept_id == filters.taxonomy.learning_concept_id
            )
        if filters.unit_ids:
            conditions.append(KnowledgeChunkModel.unit_id.in_(filters.unit_ids))
        if filters.lesson_ids:
            conditions.append(KnowledgeChunkModel.lesson_id.in_(filters.lesson_ids))
        return (
            select(
                literal("knowledge_chunk").label("record_kind"),
                KnowledgeChunkModel.id.label("record_id"),
                KnowledgeChunkModel.text.label("text"),
                ExamConfigurationModel.grade.label("grade"),
                ExamConfigurationModel.id.label("exam_id"),
                MediumModel.id.label("medium_id"),
                SubjectModel.id.label("subject_id"),
                CurriculumVersionModel.id.label("curriculum_version_id"),
                KnowledgeChunkModel.unit_id.label("unit_id"),
                KnowledgeChunkModel.lesson_id.label("lesson_id"),
                KnowledgeChunkModel.competency_id.label("competency_id"),
                KnowledgeChunkModel.skill_id.label("skill_id"),
                KnowledgeChunkModel.sub_skill_id.label("sub_skill_id"),
                KnowledgeChunkModel.learning_concept_id.label("learning_concept_id"),
                KnowledgeChunkModel.source_document_id.label("source_document_id"),
                KnowledgeChunkModel.page_number.label("page_number"),
                KnowledgeChunkModel.source_block_id.label("source_block_id"),
            )
            .select_from(KnowledgeChunkModel)
            .join(
                CurriculumVersionModel,
                CurriculumVersionModel.id == KnowledgeChunkModel.curriculum_version_id,
            )
            .join(
                ExamConfigurationModel,
                ExamConfigurationModel.id == CurriculumVersionModel.exam_configuration_id,
            )
            .join(MediumModel, MediumModel.id == CurriculumVersionModel.medium_id)
            .join(SubjectModel, SubjectModel.id == CurriculumVersionModel.subject_id)
            .join(
                SourceDocumentModel,
                SourceDocumentModel.id == KnowledgeChunkModel.source_document_id,
            )
            .outerjoin(
                CurriculumUnitModel,
                and_(
                    CurriculumUnitModel.id == KnowledgeChunkModel.unit_id,
                    CurriculumUnitModel.curriculum_version_id
                    == KnowledgeChunkModel.curriculum_version_id,
                ),
            )
            .outerjoin(
                CurriculumLessonModel,
                and_(
                    CurriculumLessonModel.id == KnowledgeChunkModel.lesson_id,
                    CurriculumLessonModel.unit_id == KnowledgeChunkModel.unit_id,
                    CurriculumLessonModel.curriculum_version_id
                    == KnowledgeChunkModel.curriculum_version_id,
                ),
            )
            .join(
                competency,
                and_(
                    competency.id == KnowledgeChunkModel.competency_id,
                    competency.curriculum_version_id == KnowledgeChunkModel.curriculum_version_id,
                    competency.level == TaxonomyLevel.COMPETENCY,
                    competency.active.is_(True),
                    competency.review_state == TaxonomyReviewState.REVIEWED,
                ),
            )
            .outerjoin(
                skill,
                and_(
                    skill.id == KnowledgeChunkModel.skill_id,
                    skill.curriculum_version_id == KnowledgeChunkModel.curriculum_version_id,
                ),
            )
            .outerjoin(
                sub_skill,
                and_(
                    sub_skill.id == KnowledgeChunkModel.sub_skill_id,
                    sub_skill.curriculum_version_id == KnowledgeChunkModel.curriculum_version_id,
                ),
            )
            .outerjoin(
                learning_concept,
                and_(
                    learning_concept.id == KnowledgeChunkModel.learning_concept_id,
                    learning_concept.curriculum_version_id
                    == KnowledgeChunkModel.curriculum_version_id,
                ),
            )
            .where(*conditions)
        )

    def _question_scope_select(self, filters: RetrievalScope) -> Select[Any]:
        competency = aliased(TaxonomyNodeModel, name="question_competency")
        skill = aliased(TaxonomyNodeModel, name="question_skill")
        sub_skill = aliased(TaxonomyNodeModel, name="question_sub_skill")
        learning_concept = aliased(TaxonomyNodeModel, name="question_learning_concept")
        conditions: list[Any] = [
            HistoricalQuestionModel.review_state == ReviewState.REVIEWED,
            ExamConfigurationModel.active.is_(True),
            MediumModel.active.is_(True),
            SubjectModel.active.is_(True),
            CurriculumVersionModel.active.is_(True),
            SourceDocumentModel.active_for_ai.is_(True),
            or_(HistoricalQuestionModel.unit_id.is_(None), CurriculumUnitModel.active.is_(True)),
            or_(
                HistoricalQuestionModel.lesson_id.is_(None),
                CurriculumLessonModel.active.is_(True),
            ),
            ExamConfigurationModel.grade == filters.grade,
            ExamConfigurationModel.id == filters.exam_id,
            MediumModel.id == filters.medium_id,
            SubjectModel.id == filters.subject_id,
            CurriculumVersionModel.id == filters.curriculum_version_id,
            competency.id == filters.taxonomy.competency_id,
            or_(
                HistoricalQuestionModel.skill_id.is_(None),
                and_(
                    skill.parent_id == competency.id,
                    skill.level == TaxonomyLevel.SKILL,
                    skill.active.is_(True),
                    skill.review_state == TaxonomyReviewState.REVIEWED,
                ),
            ),
            or_(
                HistoricalQuestionModel.sub_skill_id.is_(None),
                and_(
                    HistoricalQuestionModel.skill_id.is_not(None),
                    sub_skill.parent_id == skill.id,
                    sub_skill.level == TaxonomyLevel.SUB_SKILL,
                    sub_skill.active.is_(True),
                    sub_skill.review_state == TaxonomyReviewState.REVIEWED,
                ),
            ),
            or_(
                HistoricalQuestionModel.learning_concept_id.is_(None),
                and_(
                    HistoricalQuestionModel.sub_skill_id.is_not(None),
                    learning_concept.parent_id == sub_skill.id,
                    learning_concept.level == TaxonomyLevel.LEARNING_CONCEPT,
                    learning_concept.active.is_(True),
                    learning_concept.review_state == TaxonomyReviewState.REVIEWED,
                ),
            ),
        ]
        if filters.taxonomy.skill_id is not None:
            conditions.append(HistoricalQuestionModel.skill_id == filters.taxonomy.skill_id)
        if filters.taxonomy.sub_skill_id is not None:
            conditions.append(HistoricalQuestionModel.sub_skill_id == filters.taxonomy.sub_skill_id)
        if filters.taxonomy.learning_concept_id is not None:
            conditions.append(
                HistoricalQuestionModel.learning_concept_id == filters.taxonomy.learning_concept_id
            )
        if filters.unit_ids:
            conditions.append(HistoricalQuestionModel.unit_id.in_(filters.unit_ids))
        if filters.lesson_ids:
            conditions.append(HistoricalQuestionModel.lesson_id.in_(filters.lesson_ids))
        return (
            select(
                literal("historical_question").label("record_kind"),
                HistoricalQuestionModel.id.label("record_id"),
                HistoricalQuestionModel.text.label("text"),
                ExamConfigurationModel.grade.label("grade"),
                ExamConfigurationModel.id.label("exam_id"),
                MediumModel.id.label("medium_id"),
                SubjectModel.id.label("subject_id"),
                CurriculumVersionModel.id.label("curriculum_version_id"),
                HistoricalQuestionModel.unit_id.label("unit_id"),
                HistoricalQuestionModel.lesson_id.label("lesson_id"),
                HistoricalQuestionModel.competency_id.label("competency_id"),
                HistoricalQuestionModel.skill_id.label("skill_id"),
                HistoricalQuestionModel.sub_skill_id.label("sub_skill_id"),
                HistoricalQuestionModel.learning_concept_id.label("learning_concept_id"),
                HistoricalQuestionModel.source_document_id.label("source_document_id"),
                HistoricalQuestionModel.page_number.label("page_number"),
                HistoricalQuestionModel.source_block_id.label("source_block_id"),
            )
            .select_from(HistoricalQuestionModel)
            .join(
                CurriculumVersionModel,
                CurriculumVersionModel.id == HistoricalQuestionModel.curriculum_version_id,
            )
            .join(
                ExamConfigurationModel,
                ExamConfigurationModel.id == CurriculumVersionModel.exam_configuration_id,
            )
            .join(MediumModel, MediumModel.id == CurriculumVersionModel.medium_id)
            .join(SubjectModel, SubjectModel.id == CurriculumVersionModel.subject_id)
            .join(
                SourceDocumentModel,
                SourceDocumentModel.id == HistoricalQuestionModel.source_document_id,
            )
            .outerjoin(
                CurriculumUnitModel,
                and_(
                    CurriculumUnitModel.id == HistoricalQuestionModel.unit_id,
                    CurriculumUnitModel.curriculum_version_id
                    == HistoricalQuestionModel.curriculum_version_id,
                ),
            )
            .outerjoin(
                CurriculumLessonModel,
                and_(
                    CurriculumLessonModel.id == HistoricalQuestionModel.lesson_id,
                    CurriculumLessonModel.unit_id == HistoricalQuestionModel.unit_id,
                    CurriculumLessonModel.curriculum_version_id
                    == HistoricalQuestionModel.curriculum_version_id,
                ),
            )
            .join(
                competency,
                and_(
                    competency.id == HistoricalQuestionModel.competency_id,
                    competency.curriculum_version_id
                    == HistoricalQuestionModel.curriculum_version_id,
                    competency.level == TaxonomyLevel.COMPETENCY,
                    competency.active.is_(True),
                    competency.review_state == TaxonomyReviewState.REVIEWED,
                ),
            )
            .outerjoin(
                skill,
                and_(
                    skill.id == HistoricalQuestionModel.skill_id,
                    skill.curriculum_version_id == HistoricalQuestionModel.curriculum_version_id,
                ),
            )
            .outerjoin(
                sub_skill,
                and_(
                    sub_skill.id == HistoricalQuestionModel.sub_skill_id,
                    sub_skill.curriculum_version_id
                    == HistoricalQuestionModel.curriculum_version_id,
                ),
            )
            .outerjoin(
                learning_concept,
                and_(
                    learning_concept.id == HistoricalQuestionModel.learning_concept_id,
                    learning_concept.curriculum_version_id
                    == HistoricalQuestionModel.curriculum_version_id,
                ),
            )
            .where(*conditions)
        )

    def _scoped_records(self, filters: RetrievalFilters) -> CTE:
        scopes = filters.scopes if isinstance(filters, RetrievalScopeSet) else (filters,)
        statements = tuple(
            statement
            for scope in scopes
            for statement in (
                self._chunk_scope_select(scope),
                self._question_scope_select(scope),
            )
        )
        return union(*statements).cte("reviewed_scoped_records")

    def build_lexical_statement(
        self,
        *,
        query: str,
        filters: RetrievalFilters,
    ) -> Select[Any]:
        """Build PostgreSQL full-text ranking over already-scoped reviewed rows."""

        valid_query = _validate_query(query)
        valid_filters = _validate_filters(filters)
        scoped = self._scoped_records(valid_filters)
        regconfig = literal("simple", type_=REGCONFIG)
        tsquery = func.websearch_to_tsquery(
            regconfig,
            bindparam("lexical_query", valid_query, type_=Text()),
        )
        search_document = func.to_tsvector(regconfig, scoped.c.text)
        score = func.cast(func.ts_rank_cd(search_document, tsquery), Float).label("score")
        return (
            select(*(scoped.c[name] for name in _RECORD_COLUMNS), score)
            .select_from(scoped)
            .where(search_document.bool_op("@@")(tsquery))
            .order_by(score.desc(), scoped.c.record_id, scoped.c.record_kind)
            .limit(self._candidate_limit)
        )

    def build_vector_statement(
        self,
        *,
        query_vector: Sequence[object],
        filters: RetrievalFilters,
    ) -> Select[Any]:
        """Build pgvector cosine ranking in exactly one declared embedding space."""

        vector = validate_query_vector(
            query_vector,
            expected_dimension=self._embedding_config.dimension,
        )
        valid_filters = _validate_filters(filters)
        scoped = self._scoped_records(valid_filters)
        target_join = or_(
            and_(
                scoped.c.record_kind == "knowledge_chunk",
                KnowledgeEmbeddingModel.knowledge_chunk_id == scoped.c.record_id,
                KnowledgeEmbeddingModel.historical_question_id.is_(None),
            ),
            and_(
                scoped.c.record_kind == "historical_question",
                KnowledgeEmbeddingModel.historical_question_id == scoped.c.record_id,
                KnowledgeEmbeddingModel.knowledge_chunk_id.is_(None),
            ),
        )
        configured = (
            select(
                *(scoped.c[name] for name in _RECORD_COLUMNS),
                KnowledgeEmbeddingModel.embedding.label("embedding"),
            )
            .select_from(scoped)
            .join(KnowledgeEmbeddingModel, target_join)
            .join(
                EmbeddingConfigurationModel,
                and_(
                    EmbeddingConfigurationModel.id
                    == KnowledgeEmbeddingModel.embedding_configuration_id,
                    EmbeddingConfigurationModel.dimension
                    == KnowledgeEmbeddingModel.embedding_dimension,
                ),
            )
            .where(
                EmbeddingConfigurationModel.provider == self._embedding_config.provider,
                EmbeddingConfigurationModel.model == self._embedding_config.model,
                EmbeddingConfigurationModel.version == self._embedding_config.version,
                EmbeddingConfigurationModel.config_fingerprint
                == self._embedding_config.config_fingerprint,
                EmbeddingConfigurationModel.dimension == self._embedding_config.dimension,
                KnowledgeEmbeddingModel.embedding_dimension == self._embedding_config.dimension,
                func.vector_dims(KnowledgeEmbeddingModel.embedding)
                == self._embedding_config.dimension,
            )
            .cte("configured_vector_records")
        )
        vector_parameter = bindparam(
            "query_vector",
            list(vector),
            type_=Vector(self._embedding_config.dimension),
        )
        score = (literal(1.0) - configured.c.embedding.cosine_distance(vector_parameter)).label(
            "score"
        )
        return (
            select(*(configured.c[name] for name in _RECORD_COLUMNS), score)
            .select_from(configured)
            .order_by(score.desc(), configured.c.record_id, configured.c.record_kind)
            .limit(self._candidate_limit)
        )

    async def retrieve_candidates(
        self,
        *,
        query: str,
        query_vector: Sequence[object],
        filters: RetrievalFilters,
    ) -> RetrievalCandidateSet:
        """Return bounded lexical/vector candidates without committing the session."""

        lexical_statement = self.build_lexical_statement(query=query, filters=filters)
        vector_statement = self.build_vector_statement(
            query_vector=query_vector,
            filters=filters,
        )
        lexical_result = await self._session.execute(lexical_statement)
        vector_result = await self._session.execute(vector_statement)
        lexical_candidates = tuple(
            LexicalCandidate(
                record=self._record_from_row(row),
                score=float(row["score"]),
            )
            for row in lexical_result.mappings().all()
        )
        vector_candidates = tuple(
            VectorCandidate(
                record=self._record_from_row(row),
                score=float(row["score"]),
                embedding_config_fingerprint=self._embedding_config.config_fingerprint,
            )
            for row in vector_result.mappings().all()
        )
        return RetrievalCandidateSet(
            lexical_candidates=lexical_candidates,
            vector_candidates=vector_candidates,
        )

    @staticmethod
    def _record_from_row(row: RowMapping) -> RetrievalRecord:
        taxonomy = TaxonomyScope(
            competency_id=cast(UUID, row["competency_id"]),
            skill_id=cast(UUID | None, row["skill_id"]),
            sub_skill_id=cast(UUID | None, row["sub_skill_id"]),
            learning_concept_id=cast(UUID | None, row["learning_concept_id"]),
        )
        return RetrievalRecord(
            chunk_id=cast(UUID, row["record_id"]),
            text=cast(str, row["text"]),
            scope=RetrievalScope(
                grade=cast(int, row["grade"]),
                exam_id=cast(UUID, row["exam_id"]),
                medium_id=cast(UUID, row["medium_id"]),
                subject_id=cast(
                    UUID,
                    row.get("subject_id", LEGACY_UNCLASSIFIED_SUBJECT_ID),
                ),
                curriculum_version_id=cast(UUID, row["curriculum_version_id"]),
                unit_ids=(() if row.get("unit_id") is None else (cast(UUID, row["unit_id"]),)),
                lesson_ids=(
                    () if row.get("lesson_id") is None else (cast(UUID, row["lesson_id"]),)
                ),
                taxonomy=taxonomy,
            ),
            provenance=SourceProvenance(
                source_document_id=cast(UUID, row["source_document_id"]),
                page_number=cast(int, row["page_number"]),
                source_block_id=cast(UUID | None, row["source_block_id"]),
            ),
        )

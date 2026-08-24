import math
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import cast
from uuid import UUID

import pytest

from exam_guru_api.retrieval.domain import RetrievalContractError, RetrievalScope
from exam_guru_api.retrieval.evaluation import (
    RelevanceJudgment,
    RetrievalEvalCase,
    RetrievalEvalObservation,
    evaluate_case,
    evaluate_suite,
    leakage_rate,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)
from exam_guru_api.retrieval.fusion import FusedCandidate
from tests.test_retrieval_fixtures import (
    IRRELEVANT_TEXT,
    OTHER_MEDIUM_ID,
    PROMPT_INJECTION_TEXT,
    grade_five_filter,
    grade_five_scope,
    retrieval_record,
)


def ranked(record: object, rank: int) -> FusedCandidate:
    return FusedCandidate(
        record=record,  # type: ignore[arg-type]
        score=1.0 / rank,
        lexical_rank=rank,
        vector_rank=None,
    )


def fixed_grade_five_eval() -> tuple[RetrievalEvalCase, tuple[FusedCandidate, ...]]:
    relevant_primary = retrieval_record(300, "Relevant primary evidence")
    injection = retrieval_record(301, PROMPT_INJECTION_TEXT, page_number=3)
    relevant_secondary = retrieval_record(302, "Relevant supporting evidence", page_number=4)
    leaked = retrieval_record(
        303,
        IRRELEVANT_TEXT,
        scope=replace(grade_five_scope(), medium_id=OTHER_MEDIUM_ID),
        page_number=5,
    )
    case = RetrievalEvalCase(
        name="grade5-skill-with-adversarial-source-data",
        query="Find reviewed evidence for the selected Grade 5 skill",
        filters=grade_five_filter(),
        judgments=(
            RelevanceJudgment(relevant_primary.chunk_id, relevance=3.0),
            RelevanceJudgment(relevant_secondary.chunk_id, relevance=1.0),
        ),
        forbidden_chunk_ids=frozenset({leaked.chunk_id}),
    )
    results = (
        ranked(relevant_primary, 1),
        ranked(injection, 2),
        ranked(relevant_secondary, 3),
        ranked(leaked, 4),
    )
    return case, results


def test_eval_reports_recall_precision_reciprocal_rank_ndcg_and_leakage() -> None:
    case, results = fixed_grade_five_eval()

    metrics = evaluate_case(case, results, k=4)

    ideal_dcg = 7.0 + (1.0 / math.log2(3))
    actual_dcg = 7.0 + (1.0 / math.log2(4))
    assert metrics.recall_at_k == 1.0
    assert metrics.precision_at_k == 0.5
    assert metrics.reciprocal_rank == 1.0
    assert metrics.ndcg_at_k == pytest.approx(actual_dcg / ideal_dcg)
    assert metrics.leakage_rate == 0.25
    assert metrics.retrieved_count == 4
    assert metrics.relevant_retrieved_count == 2


def test_prompt_injection_and_irrelevant_text_are_never_interpreted_as_relevance() -> None:
    case, results = fixed_grade_five_eval()

    metrics = evaluate_case(case, results, k=2)

    assert results[1].record.text == PROMPT_INJECTION_TEXT
    assert metrics.recall_at_k == 0.5
    assert metrics.precision_at_k == 0.5
    assert metrics.reciprocal_rank == 1.0
    assert metrics.leakage_rate == 0.0


def test_standalone_metrics_are_deterministic_and_use_standard_at_k_denominators() -> None:
    relevant = frozenset({UUID(int=1), UUID(int=3)})
    ranked_ids = (UUID(int=1), UUID(int=2), UUID(int=3))

    assert recall_at_k(ranked_ids, relevant, k=2) == 0.5
    assert precision_at_k(ranked_ids, relevant, k=4) == 0.5
    assert reciprocal_rank(ranked_ids, relevant) == 1.0
    assert ndcg_at_k(
        ranked_ids,
        {UUID(int=1): 3.0, UUID(int=3): 1.0},
        k=3,
    ) == pytest.approx((7.0 + (1.0 / math.log2(4))) / (7.0 + (1.0 / math.log2(3))))


def test_suite_report_contains_macro_mrr_and_other_macro_metrics() -> None:
    first_case, first_results = fixed_grade_five_eval()
    second_relevant = retrieval_record(310, "Second case relevant")
    second_irrelevant = retrieval_record(311, IRRELEVANT_TEXT, page_number=3)
    second_case = RetrievalEvalCase(
        name="second-case",
        query="A second deterministic query",
        filters=grade_five_filter(),
        judgments=(RelevanceJudgment(second_relevant.chunk_id),),
    )
    second_results = (ranked(second_irrelevant, 1), ranked(second_relevant, 2))

    report = evaluate_suite(
        (
            RetrievalEvalObservation(first_case, first_results),
            RetrievalEvalObservation(second_case, second_results),
        ),
        k=4,
    )

    assert [result.case_name for result in report.cases] == [first_case.name, second_case.name]
    assert report.mean_reciprocal_rank == 0.75
    assert report.mean_recall_at_k == 1.0
    assert report.mean_precision_at_k == pytest.approx((0.5 + 0.25) / 2)
    assert report.mean_leakage_rate == 0.125


@pytest.mark.parametrize(
    "build",
    [
        lambda: RelevanceJudgment(cast(UUID, "chunk")),
        lambda: RelevanceJudgment(UUID(int=1), relevance=0),
        lambda: RelevanceJudgment(UUID(int=1), relevance=cast(float, True)),
        lambda: RelevanceJudgment(UUID(int=1), relevance=math.inf),
        lambda: RelevanceJudgment(UUID(int=1), relevance=101),
        lambda: RetrievalEvalCase(
            name=cast(str, 123),
            query="query",
            filters=grade_five_filter(),
            judgments=(RelevanceJudgment(UUID(int=1)),),
        ),
        lambda: RetrievalEvalCase(
            name=" padded ",
            query="query",
            filters=grade_five_filter(),
            judgments=(RelevanceJudgment(UUID(int=1)),),
        ),
        lambda: RetrievalEvalCase(
            name="x" * 256,
            query="query",
            filters=grade_five_filter(),
            judgments=(RelevanceJudgment(UUID(int=1)),),
        ),
        lambda: RetrievalEvalCase(
            name=" ",
            query="query",
            filters=grade_five_filter(),
            judgments=(RelevanceJudgment(UUID(int=1)),),
        ),
        lambda: RetrievalEvalCase(
            name="case",
            query=" ",
            filters=grade_five_filter(),
            judgments=(RelevanceJudgment(UUID(int=1)),),
        ),
        lambda: RetrievalEvalCase(
            name="case",
            query=cast(str, 123),
            filters=grade_five_filter(),
            judgments=(RelevanceJudgment(UUID(int=1)),),
        ),
        lambda: RetrievalEvalCase(
            name="case",
            query="x" * 4_097,
            filters=grade_five_filter(),
            judgments=(RelevanceJudgment(UUID(int=1)),),
        ),
        lambda: RetrievalEvalCase(
            name="case",
            query="query",
            filters=cast(RetrievalScope, "filters"),
            judgments=(RelevanceJudgment(UUID(int=1)),),
        ),
        lambda: RetrievalEvalCase(
            name="case",
            query="query",
            filters=grade_five_filter(),
            judgments=cast(tuple[RelevanceJudgment, ...], [RelevanceJudgment(UUID(int=1))]),
        ),
        lambda: RetrievalEvalCase(
            name="case",
            query="query",
            filters=grade_five_filter(),
            judgments=(cast(RelevanceJudgment, "judgment"),),
        ),
        lambda: RetrievalEvalCase(
            name="duplicates",
            query="query",
            filters=grade_five_filter(),
            judgments=(
                RelevanceJudgment(UUID(int=1)),
                RelevanceJudgment(UUID(int=1)),
            ),
        ),
        lambda: RetrievalEvalCase(
            name="forbidden-type",
            query="query",
            filters=grade_five_filter(),
            judgments=(RelevanceJudgment(UUID(int=1)),),
            forbidden_chunk_ids=cast(frozenset[UUID], {UUID(int=2)}),
        ),
        lambda: RetrievalEvalCase(
            name="forbidden-value",
            query="query",
            filters=grade_five_filter(),
            judgments=(RelevanceJudgment(UUID(int=1)),),
            forbidden_chunk_ids=frozenset({cast(UUID, "chunk")}),
        ),
        lambda: RetrievalEvalCase(
            name="overlap",
            query="query",
            filters=grade_five_filter(),
            judgments=(RelevanceJudgment(UUID(int=1)),),
            forbidden_chunk_ids=frozenset({UUID(int=1)}),
        ),
    ],
)
def test_eval_contracts_reject_invalid_fixtures(build: Callable[[], object]) -> None:
    with pytest.raises(RetrievalContractError):
        build()


def test_eval_rejects_invalid_k_duplicate_rankings_and_empty_suites() -> None:
    case, results = fixed_grade_five_eval()

    with pytest.raises(RetrievalContractError, match="k"):
        evaluate_case(case, results, k=0)

    with pytest.raises(RetrievalContractError, match="duplicate"):
        evaluate_case(case, (results[0], results[0]), k=2)

    with pytest.raises(RetrievalContractError, match="non-empty"):
        evaluate_suite((), k=2)


@pytest.mark.parametrize(
    "evaluate",
    [
        lambda: recall_at_k((), (), k=cast(int, True)),
        lambda: recall_at_k((), (), k=101),
        lambda: recall_at_k(cast(tuple[UUID, ...], ("chunk",)), (), k=1),
        lambda: recall_at_k((UUID(int=1), UUID(int=1)), (), k=1),
        lambda: precision_at_k((), {cast(UUID, "relevant")}, k=1),
        lambda: ndcg_at_k((), cast(Mapping[UUID, float], []), k=1),
        lambda: ndcg_at_k((), {cast(UUID, "chunk"): 1.0}, k=1),
        lambda: ndcg_at_k((), {UUID(int=1): cast(float, True)}, k=1),
        lambda: ndcg_at_k((), {UUID(int=1): math.inf}, k=1),
        lambda: ndcg_at_k((), {UUID(int=1): 101.0}, k=1),
    ],
)
def test_standalone_metrics_reject_malformed_inputs(evaluate: Callable[[], float]) -> None:
    with pytest.raises(RetrievalContractError):
        evaluate()


def test_empty_relevance_metrics_have_explicit_zero_semantics() -> None:
    identifier = UUID(int=1)

    assert recall_at_k((identifier,), frozenset(), k=1) == 0.0
    assert precision_at_k((identifier,), frozenset(), k=1) == 0.0
    assert reciprocal_rank((identifier,), frozenset()) == 0.0
    assert ndcg_at_k((identifier,), {}, k=1) == 0.0


def test_leakage_metric_handles_empty_explicit_forbidden_and_invalid_scope() -> None:
    record = retrieval_record(320, IRRELEVANT_TEXT)
    result = ranked(record, 1)

    assert leakage_rate((), filters=grade_five_filter(), k=1) == 0.0
    assert (
        leakage_rate(
            (result,),
            filters=grade_five_filter(),
            forbidden_chunk_ids=frozenset({record.chunk_id}),
            k=1,
        )
        == 1.0
    )
    with pytest.raises(RetrievalContractError, match="filters"):
        leakage_rate(
            (result,),
            filters=cast(RetrievalScope, "filters"),
            k=1,
        )


def test_irrelevant_eval_case_and_observation_boundaries_are_explicit() -> None:
    record = retrieval_record(330, IRRELEVANT_TEXT)
    case = RetrievalEvalCase(
        name="irrelevant-query",
        query="A query with no relevant source in the fixture",
        filters=grade_five_filter(),
        judgments=(),
    )
    result = ranked(record, 1)

    metrics = evaluate_case(case, (result,), k=1)

    assert metrics.recall_at_k == 0.0
    assert metrics.precision_at_k == 0.0
    assert metrics.reciprocal_rank == 0.0
    assert metrics.ndcg_at_k == 0.0
    with pytest.raises(RetrievalContractError, match="case"):
        RetrievalEvalObservation(cast(RetrievalEvalCase, "case"), ())
    with pytest.raises(RetrievalContractError, match="FusedCandidate"):
        RetrievalEvalObservation(
            case,
            cast(tuple[FusedCandidate, ...], ("candidate",)),
        )
    with pytest.raises(RetrievalContractError, match="case"):
        evaluate_case(cast(RetrievalEvalCase, "case"), (), k=1)


def test_suite_rejects_duplicate_names_untyped_values_and_amplification() -> None:
    case = RetrievalEvalCase(
        name="same-name",
        query="query",
        filters=grade_five_filter(),
        judgments=(),
    )
    observation = RetrievalEvalObservation(case, ())

    with pytest.raises(RetrievalContractError, match="unique"):
        evaluate_suite((observation, observation), k=1)
    with pytest.raises(RetrievalContractError, match="non-empty"):
        evaluate_suite(
            cast(tuple[RetrievalEvalObservation, ...], ("observation",)),
            k=1,
        )
    with pytest.raises(RetrievalContractError, match="non-empty"):
        evaluate_suite(cast(tuple[RetrievalEvalObservation, ...], []), k=1)
    with pytest.raises(RetrievalContractError, match="non-empty"):
        evaluate_suite((observation,) * 10_001, k=1)

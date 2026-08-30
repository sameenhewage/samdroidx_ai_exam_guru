import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

import pytest
from pydantic import SecretStr

from exam_guru_api.validation.domain import GroundingSource
from exam_guru_api.validation.openai_semantic_verifier import (
    OPENAI_SEMANTIC_PROVIDER,
    OPENAI_SEMANTIC_PROVIDER_VERSION,
    OPENAI_SEMANTIC_SDK_MAX_RETRIES,
    OPENAI_SEMANTIC_TEMPERATURE,
    OpenAISemanticVerifier,
    OpenAISemanticVerifierConfig,
    SemanticVerifierBudget,
    SemanticVerifierPricing,
)
from exam_guru_api.validation.subject import (
    FACTUAL_CLAIM_DECOMPOSITION_VERSION,
    CurriculumSelection,
    SemanticVerificationRequest,
    SemanticVerificationStatus,
    decompose_factual_claims,
)

_REQUIRED_LIVE_ENV = (
    "OPENAI_API_KEY",
    "EXAM_GURU_SEMANTIC_LIVE_MODEL",
    "EXAM_GURU_SEMANTIC_LIVE_MODEL_VERSION",
    "EXAM_GURU_SEMANTIC_LIVE_PRICING_VERSION",
    "EXAM_GURU_SEMANTIC_LIVE_INPUT_MICROUSD_PER_MILLION_TOKENS",
    "EXAM_GURU_SEMANTIC_LIVE_OUTPUT_MICROUSD_PER_MILLION_TOKENS",
    "EXAM_GURU_SEMANTIC_LIVE_TIMEOUT_MS",
    "EXAM_GURU_SEMANTIC_LIVE_MAX_COST_MICROUSD",
)
_LIVE_OPT_IN = os.getenv("EXAM_GURU_RUN_SEMANTIC_LIVE") == "1"
_MISSING_LIVE_ENV = tuple(name for name in _REQUIRED_LIVE_ENV if not os.getenv(name))

pytestmark = [
    pytest.mark.live_openai,
    pytest.mark.skipif(
        not _LIVE_OPT_IN or bool(_MISSING_LIVE_ENV),
        reason=(
            "requires EXAM_GURU_RUN_SEMANTIC_LIVE=1 and every explicit semantic-verifier "
            "credential/model/pricing/timeout/cost setting"
        ),
    ),
]

_CURRICULUM_ID = UUID("20000000-0000-4000-8000-000000000701")
_SUBJECT_ID = UUID("10000000-0000-4000-8000-000000000701")
_UNIT_ID = UUID("30000000-0000-4000-8000-000000000701")
_LESSON_ID = UUID("40000000-0000-4000-8000-000000000701")


@dataclass(frozen=True, slots=True)
class LiveSemanticCase:
    case_id: str
    source_text: str
    question: str
    answer: str
    explanation: str
    expected: SemanticVerificationStatus


_CASES = (
    LiveSemanticCase(
        case_id="supported-water-freezing",
        source_text=(
            "Reviewed Grade 5 Science note: At standard atmospheric pressure, pure water "
            "freezes at 0 degrees Celsius."
        ),
        question="At standard atmospheric pressure, at what temperature does pure water freeze?",
        answer="0 degrees Celsius",
        explanation="The reviewed note states that pure water freezes at 0 degrees Celsius.",
        expected=SemanticVerificationStatus.SUPPORTED,
    ),
    LiveSemanticCase(
        case_id="contradicted-water-freezing",
        source_text=(
            "Reviewed Grade 5 Science note: At standard atmospheric pressure, pure water "
            "freezes at 0 degrees Celsius."
        ),
        question="At standard atmospheric pressure, at what temperature does pure water freeze?",
        answer="100 degrees Celsius",
        explanation="The proposed answer claims that pure water freezes at 100 degrees Celsius.",
        expected=SemanticVerificationStatus.CONTRADICTED,
    ),
    LiveSemanticCase(
        case_id="insufficient-planet-distance",
        source_text=(
            "Reviewed Grade 5 Science note: At standard atmospheric pressure, pure water "
            "freezes at 0 degrees Celsius."
        ),
        question="Which planet is farthest from the Sun?",
        answer="Neptune",
        explanation="The proposed answer identifies Neptune.",
        expected=SemanticVerificationStatus.INSUFFICIENT_EVIDENCE,
    ),
)


def _required_env(name: str) -> str:
    value = os.getenv(name)
    assert value is not None
    return value


def _request(case: LiveSemanticCase) -> SemanticVerificationRequest:
    context_id = f"knowledge_chunk:{case.case_id}"
    candidate: dict[str, object] = {
        "schema_version": "question.v1",
        "question_type": "short_answer",
        "stem": case.question,
        "options": [],
        "answer": {
            "correct_option_id": None,
            "accepted_responses": [case.answer],
            "explanation": case.explanation,
        },
        "marking": {
            "total_marks": 1,
            "criteria": [
                {
                    "criterion_id": "answer",
                    "description": f"Accept {case.answer}.",
                    "marks": 1,
                }
            ],
        },
        "context_references": [context_id],
    }
    return SemanticVerificationRequest(
        grade=5,
        medium="en",
        subject_id=_SUBJECT_ID,
        subject_code="SCIENCE",
        curriculum_version_id=_CURRICULUM_ID,
        selected_scope=CurriculumSelection((_UNIT_ID,), (_LESSON_ID,)),
        candidate=candidate,
        claims=decompose_factual_claims(candidate),
        grounding_sources=(
            GroundingSource(
                context_id=context_id,
                text=case.source_text,
                source_document_id="reviewed-grade5-science-fixture",
                source_version="fixture-v1",
                page_number=1,
                chunk_id=case.case_id,
            ),
        ),
    )


def test_live_openai_factual_semantic_verifier_records_quality_cost_and_latency_baseline(
    record_property: Callable[[str, object], None],
) -> None:
    model = _required_env("EXAM_GURU_SEMANTIC_LIVE_MODEL")
    model_version = _required_env("EXAM_GURU_SEMANTIC_LIVE_MODEL_VERSION")
    timeout_ms = int(_required_env("EXAM_GURU_SEMANTIC_LIVE_TIMEOUT_MS"))
    max_cost_microusd = int(_required_env("EXAM_GURU_SEMANTIC_LIVE_MAX_COST_MICROUSD"))
    pricing = SemanticVerifierPricing(
        pricing_version=_required_env("EXAM_GURU_SEMANTIC_LIVE_PRICING_VERSION"),
        model=model,
        model_version=model_version,
        input_microusd_per_million_tokens=int(
            _required_env("EXAM_GURU_SEMANTIC_LIVE_INPUT_MICROUSD_PER_MILLION_TOKENS")
        ),
        output_microusd_per_million_tokens=int(
            _required_env("EXAM_GURU_SEMANTIC_LIVE_OUTPUT_MICROUSD_PER_MILLION_TOKENS")
        ),
    )
    budget = SemanticVerifierBudget(
        max_grounding_sources=4,
        max_source_bytes=4_096,
        max_total_source_bytes=8_192,
        max_candidate_bytes=16_384,
        max_request_bytes=32_768,
        max_output_tokens=512,
        max_cost_microusd=max_cost_microusd,
    )
    adapter = OpenAISemanticVerifier(
        config=OpenAISemanticVerifierConfig(
            api_key=SecretStr(_required_env("OPENAI_API_KEY")),
            model=model,
            model_version=model_version,
            prompt_version="grounded-factual-verifier.live-v2",
            timeout_ms=timeout_ms,
        ),
        pricing=pricing,
        budget=budget,
    )

    results = tuple((case, adapter.verify(_request(case))) for case in _CASES)
    correct = sum(result.status is case.expected for case, result in results)
    input_tokens = sum(result.accounting.input_tokens for _, result in results)
    output_tokens = sum(result.accounting.output_tokens for _, result in results)
    total_tokens = sum(result.accounting.total_tokens for _, result in results)
    cost_microusd = sum(result.accounting.cost_microusd for _, result in results)
    latency_ms = sum(result.accounting.latency_ms for _, result in results)
    outcome_material = [
        {
            "case_id": case.case_id,
            "expected": case.expected.value,
            "actual": result.status.value,
            "evidence_count": len(result.evidence_refs),
            "claims": [
                {
                    "claim_id": claim.claim_id,
                    "status": claim.status.value,
                    "evidence_count": len(claim.evidence_refs),
                }
                for claim in result.claims
            ],
        }
        for case, result in results
    ]
    telemetry: dict[str, object] = {
        "provider": OPENAI_SEMANTIC_PROVIDER,
        "provider_version": OPENAI_SEMANTIC_PROVIDER_VERSION,
        "model": model,
        "model_version": model_version,
        "verifier_id": adapter.verifier_id,
        "verifier_version": adapter.verifier_version,
        "decomposition_version": FACTUAL_CLAIM_DECOMPOSITION_VERSION,
        "prompt_version": adapter.prompt_version,
        "pricing_version": pricing.pricing_version,
        "temperature": OPENAI_SEMANTIC_TEMPERATURE,
        "timeout_ms": timeout_ms,
        "sdk_max_retries": OPENAI_SEMANTIC_SDK_MAX_RETRIES,
        "max_output_tokens": budget.max_output_tokens,
        "max_cost_microusd": budget.max_cost_microusd,
        "case_count": len(results),
        "claim_count": sum(len(result.claims) for _, result in results),
        "correct_count": correct,
        "accuracy_numerator": correct,
        "accuracy_denominator": len(results),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cost_microusd": cost_microusd,
        "latency_ms": latency_ms,
        "outcomes": json.dumps(outcome_material, sort_keys=True),
        "result_fingerprint": hashlib.sha256(
            json.dumps(outcome_material, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "fixture_scope": "synthetic-reviewed-english-grade5-science-contract-only",
    }
    for name, value in telemetry.items():
        record_property(name, value)

    assert len(results) == len(_CASES)
    assert all(result.provider == OPENAI_SEMANTIC_PROVIDER for _, result in results)
    assert all(result.model_version == model_version for _, result in results)
    assert all(result.claims for _, result in results)
    assert all(result.accounting.total_tokens > 0 for _, result in results)
    assert total_tokens == input_tokens + output_tokens
    assert 0 <= correct <= len(results)

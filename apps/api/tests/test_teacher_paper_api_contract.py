from copy import deepcopy
from uuid import UUID

import pytest
from pydantic import ValidationError

from exam_guru_api.main import create_app
from exam_guru_api.teacher_papers.schemas import (
    ReviewPaperDetailResponse,
    ReviewQuestionEditRequest,
    ReviewQuestionRegenerateRequest,
    ReviewQuestionResponse,
    TeacherPaperJobCreateRequest,
)

BASE = "/api/v1/admin/paper-generation"
OPTIONS_PATH = f"{BASE}/options"
CURRICULA_PATH = f"{BASE}/curricula"
LESSONS_PATH = f"{BASE}/lessons"
JOBS_PATH = f"{BASE}/jobs"
JOB_PATH = f"{JOBS_PATH}/{{paper_job_id}}"
ADVANCE_PATH = f"{JOB_PATH}/advance"
RETRY_PATH = f"{JOB_PATH}/retry"
REVIEW_PATH = "/api/v1/admin/review-papers"
REVIEW_DETAIL_PATH = f"{REVIEW_PATH}/{{paper_job_id}}"
QUESTION_PATH = f"{REVIEW_DETAIL_PATH}/questions/{{question_id}}"
START_PATH = f"{QUESTION_PATH}/start"
APPROVE_PATH = f"{QUESTION_PATH}/approve"
REJECT_PATH = f"{QUESTION_PATH}/reject"
REGENERATE_PATH = f"{QUESTION_PATH}/regenerate"
CREATE_DRAFT_PATH = f"{REVIEW_DETAIL_PATH}/create-draft"


def valid_request() -> dict[str, object]:
    return {
        "target": {
            "grade": 7,
            "medium": "en",
            "subject": "mathematics",
            "assessment_programme": "school-g7",
        },
        "scope": {"kind": "lesson_range", "start_lesson": 1, "end_lesson": 3},
        "settings": {
            "question_count": 12,
            "duration_minutes": 50,
            "difficulty": "balanced",
        },
    }


def test_teacher_generation_request_accepts_only_teacher_codes_scope_and_simple_settings() -> None:
    request = TeacherPaperJobCreateRequest.model_validate(valid_request())

    assert request.target.grade == 7
    assert request.target.medium == "en"
    assert request.target.subject == "MATHEMATICS"
    assert request.target.assessment_programme == "SCHOOL-G7"
    assert request.scope.kind == "lesson_range"
    assert request.settings.question_count == 12

    for forbidden in (
        "curriculum_version_id",
        "taxonomy_ids",
        "context_ids",
        "blueprint_id",
        "blueprint_slot_ids",
        "provider",
        "model",
        "embedding_config",
        "query_vector",
        "temperature",
        "prompt_version",
    ):
        payload = deepcopy(valid_request())
        payload[forbidden] = "client-controlled"
        with pytest.raises(ValidationError):
            TeacherPaperJobCreateRequest.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {
            **valid_request(),
            "settings": {
                "question_count": 0,
                "duration_minutes": 50,
                "difficulty": "balanced",
            },
        },
        {
            **valid_request(),
            "settings": {
                "question_count": 51,
                "duration_minutes": 50,
                "difficulty": "balanced",
            },
        },
        {
            **valid_request(),
            "settings": {
                "question_count": 12,
                "duration_minutes": 0,
                "difficulty": "balanced",
            },
        },
        {
            **valid_request(),
            "settings": {
                "question_count": 12,
                "duration_minutes": 50,
                "difficulty": "provider-decides",
            },
        },
        {
            **valid_request(),
            "scope": {"kind": "lesson_range", "start_lesson": 3, "end_lesson": 1},
        },
        {
            **valid_request(),
            "scope": {"kind": "full_subject", "start_lesson": 1},
        },
    ],
)
def test_teacher_generation_request_rejects_malformed_ranges_and_unbounded_settings(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        TeacherPaperJobCreateRequest.model_validate(payload)


def test_openapi_exposes_async_teacher_generation_review_actions_and_security() -> None:
    schema = create_app().openapi()
    expected = {
        OPTIONS_PATH: ("get",),
        CURRICULA_PATH: ("get",),
        LESSONS_PATH: ("get",),
        JOBS_PATH: ("post",),
        JOB_PATH: ("get",),
        ADVANCE_PATH: ("post",),
        RETRY_PATH: ("post",),
        REVIEW_PATH: ("get",),
        REVIEW_DETAIL_PATH: ("get",),
        QUESTION_PATH: ("patch",),
        START_PATH: ("post",),
        APPROVE_PATH: ("post",),
        REJECT_PATH: ("post",),
        REGENERATE_PATH: ("post",),
        CREATE_DRAFT_PATH: ("post",),
    }
    for path, methods in expected.items():
        assert path in schema["paths"]
        for method in methods:
            assert schema["paths"][path][method]["security"] == [{"HTTPBearer": []}]

    create = schema["paths"][JOBS_PATH]["post"]
    assert create["responses"]["202"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/TeacherPaperJobResponse"
    }
    assert {parameter["name"] for parameter in create["parameters"]} == {"Idempotency-Key"}
    assert "429" in create["responses"]
    assert "503" in create["responses"]
    assert schema["paths"][REGENERATE_PATH]["post"]["responses"]["202"]

    request_schema = schema["components"]["schemas"]["TeacherPaperJobCreateRequest"]
    assert request_schema["additionalProperties"] is False
    assert set(request_schema["properties"]) == {"target", "scope", "settings"}
    settings_schema = schema["components"]["schemas"]["TeacherPaperSettingsRequest"]
    assert settings_schema["properties"]["question_count"]["minimum"] == 1
    assert settings_schema["properties"]["question_count"]["maximum"] == 50


def test_review_contract_keeps_one_screen_teacher_content_and_nested_technical_details() -> None:
    question_id = UUID(int=25_801)
    payload = {
        "id": question_id,
        "number": 1,
        "version": 2,
        "review_state": "awaiting_review",
        "stem": "What is three multiplied by four?",
        "options": [
            {"label": "A", "text": "7"},
            {"label": "B", "text": "12"},
        ],
        "answer": "B",
        "explanation": "Three groups of four make twelve.",
        "marking_scheme": {"total_marks": 1, "criteria": ["Multiplies 3 by 4."]},
        "scope": {
            "grade": 7,
            "subject": "Mathematics",
            "lessons": "Lessons 1\u20133",
            "unit": "Numbers",
            "lesson": "Lesson 1 — Whole numbers",
            "taxonomy": "Numbers / Multiplication",
        },
        "sources": [
            {
                "filename": "grade-7-maths-guide.pdf",
                "title": "grade-7-maths-guide.pdf",
                "page": 18,
            }
        ],
        "validation": {
            "status": "needs_attention",
            "summary": "One check needs human attention.",
            "findings": ["Language check needs attention."],
        },
        "technical_details": {
            "generation_run_id": question_id,
            "validation_run_id": UUID(int=25_802),
            "candidate_id": question_id,
            "blueprint_slot_id": "slot-001",
            "context_ids": ["knowledge_chunk:250"],
            "provider": "deterministic-fake",
            "model_version": "fixture",
            "validator_findings": [
                {
                    "code": "subject.factual.verifier_unavailable",
                    "status": "warn",
                    "message": "Human review is required.",
                    "evidence": [],
                }
            ],
        },
    }

    question = ReviewQuestionResponse.model_validate(payload)
    detail = ReviewPaperDetailResponse.model_validate(
        {
            "id": UUID(int=25_800),
            "paper_reference": "EGP-2500-0800",
            "title": "Grade 7 Mathematics practice paper",
            "grade": 7,
            "medium": "English",
            "subject": "Mathematics",
            "scope_summary": "Lessons 1\u20133",
            "status": "in_review",
            "version": 4,
            "created_at": "2026-08-25T10:00:00Z",
            "questions": [payload],
            "draft": None,
            "technical_details": {
                "curriculum_version_id": UUID(int=25_803),
                "paper_blueprint_id": UUID(int=25_804),
                "request_fingerprint": "sha256:" + "a" * 64,
                "cost_microusd": 0,
                "total_tokens": 64,
            },
        }
    )

    assert question.validation.status == "needs_attention"
    assert question.sources[0].filename == "grade-7-maths-guide.pdf"
    assert detail.questions[0].answer == "B"
    assert detail.questions[0].marking_scheme.total_marks == 1
    assert detail.technical_details.request_fingerprint.startswith("sha256:")


def test_review_edit_and_regeneration_reasons_must_be_trimmed() -> None:
    content = {
        "question_type": "multiple_choice",
        "stem": "Which answer is supported?",
        "options": [
            {"option_id": "A", "text": "First"},
            {"option_id": "B", "text": "Second"},
        ],
        "answer": "B",
        "explanation": "The source supports B.",
        "marks": 1,
        "marking_guide": ["Selects B."],
    }
    with pytest.raises(ValidationError):
        ReviewQuestionEditRequest.model_validate(
            {"content": content, "reason": " padded ", "expected_version": 3}
        )
    with pytest.raises(ValidationError):
        ReviewQuestionRegenerateRequest.model_validate(
            {"reason": " padded ", "expected_version": 4}
        )

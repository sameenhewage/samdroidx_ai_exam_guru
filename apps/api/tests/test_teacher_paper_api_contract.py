from copy import deepcopy
from uuid import UUID

import pytest
from pydantic import ValidationError

from exam_guru_api.main import create_app
from exam_guru_api.teacher_papers.schemas import (
    ProgrammePolicyCreateRequest,
    ProgrammePolicyScopeCreateRequest,
    ReviewPaperDetailResponse,
    ReviewQuestionApproveRequest,
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
POLICIES_PATH = f"{BASE}/programme-policies"
POLICY_PATH = f"{POLICIES_PATH}/{{policy_id}}"
POLICY_REVIEW_PATH = f"{POLICY_PATH}/review"
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
            "grade": 5,
            "medium": "si",
            "paper_type": "subject_practice",
            "subject": "mathematics",
        },
        "scope": {"kind": "lesson_range", "start_lesson": 1, "end_lesson": 3},
        "settings": {
            "paper_name": "Grade 5 Mathematics practice",
            "mcq_count": 12,
            "written_count": 0,
            "structured_count": 0,
            "duration_minutes": 50,
            "difficulty": "balanced",
        },
    }


def pilot_settings() -> dict[str, object]:
    return {
        "paper_name": "Grade 5 pilot paper",
        "duration_minutes": 50,
        "mcq_count": 5,
        "written_count": 10,
        "structured_count": 0,
        "difficulty": "balanced",
        "teacher_instruction": "Use clear Sinhala suitable for Grade 5.",
    }


def valid_programme_policy_scope(
    part: str,
    ordinal: int,
    identity: int,
    **overrides: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "part": part,
        "ordinal": ordinal,
        "anchor_unit_id": UUID(int=26_000 + identity),
        "anchor_lesson_id": UUID(int=27_000 + identity),
        "anchor_competency_id": UUID(int=28_000 + identity),
        "source_curriculum_version_id": UUID(int=29_000 + identity),
        "source_competency_id": UUID(int=30_000 + identity),
    }
    payload.update(overrides)
    return payload


def valid_programme_policy() -> dict[str, object]:
    return {
        "programme_exam_configuration_id": UUID(int=31_001),
        "medium_id": UUID(int=31_002),
        "anchor_curriculum_version_id": UUID(int=31_003),
        "code": "g5-scholarship",
        "version": "2026.v1",
        "title": "Grade 5 Scholarship",
        "paper_i_profile_version": "ability.v1",
        "paper_ii_profile_version": "curriculum-coverage.v1",
        "paper_i_weight": 1,
        "paper_ii_weight": 1,
        "scopes": [
            valid_programme_policy_scope("paper_i", 1, 1),
            valid_programme_policy_scope("paper_ii", 1, 2),
        ],
    }


@pytest.mark.parametrize(
    ("target", "scope"),
    [
        (
            {
                "grade": 5,
                "medium": "si",
                "paper_type": "subject_practice",
                "subject": "mathematics",
            },
            {"kind": "full_subject"},
        ),
        (
            {
                "grade": 5,
                "medium": "si",
                "paper_type": "term_test",
                "subject": "mathematics",
                "term": "term_1",
            },
            {"kind": "full_term"},
        ),
        (
            {
                "grade": 5,
                "medium": "si",
                "paper_type": "scholarship_practice",
                "scholarship_mode": "paper_i",
            },
            {"kind": "programme"},
        ),
        (
            {
                "grade": 5,
                "medium": "si",
                "paper_type": "scholarship_practice",
                "scholarship_mode": "paper_ii",
            },
            {"kind": "programme"},
        ),
        (
            {
                "grade": 5,
                "medium": "si",
                "paper_type": "scholarship_practice",
                "scholarship_mode": "full",
            },
            {"kind": "programme"},
        ),
    ],
)
def test_grade_five_pilot_request_accepts_subject_term_and_scholarship_targets(
    target: dict[str, object],
    scope: dict[str, object],
) -> None:
    request = TeacherPaperJobCreateRequest.model_validate(
        {"target": target, "scope": scope, "settings": pilot_settings()}
    )

    assert request.target.grade == 5
    assert request.target.medium == "si"
    assert request.target.paper_type == target["paper_type"]
    assert request.settings.total_questions == 15
    assert request.settings.paper_name == "Grade 5 pilot paper"
    assert request.settings.teacher_instruction == "Use clear Sinhala suitable for Grade 5."


@pytest.mark.parametrize(
    ("target", "scope", "settings"),
    [
        (
            {"grade": 5, "medium": "si", "paper_type": "subject_practice"},
            {"kind": "full_subject"},
            pilot_settings(),
        ),
        (
            {
                "grade": 5,
                "medium": "si",
                "paper_type": "term_test",
                "subject": "MATHEMATICS",
            },
            {"kind": "full_term"},
            pilot_settings(),
        ),
        (
            {
                "grade": 5,
                "medium": "si",
                "paper_type": "scholarship_practice",
                "scholarship_mode": "paper_i",
                "subject": "MATHEMATICS",
            },
            {"kind": "programme"},
            pilot_settings(),
        ),
        (
            {"grade": 5, "medium": "si", "paper_type": "scholarship_practice"},
            {"kind": "programme"},
            pilot_settings(),
        ),
        (
            {
                "grade": 4,
                "medium": "si",
                "paper_type": "scholarship_practice",
                "scholarship_mode": "paper_ii",
            },
            {"kind": "programme"},
            pilot_settings(),
        ),
        (
            {
                "grade": 5,
                "medium": "si",
                "paper_type": "subject_practice",
                "subject": "MATHEMATICS",
                "term": "term_1",
            },
            {"kind": "full_subject"},
            pilot_settings(),
        ),
        (
            {
                "grade": 5,
                "medium": "si",
                "paper_type": "term_test",
                "subject": "MATHEMATICS",
                "term": "term_1",
            },
            {"kind": "full_term"},
            {**pilot_settings(), "mcq_count": 0, "written_count": 0},
        ),
        (
            {
                "grade": 5,
                "medium": "si",
                "paper_type": "scholarship_practice",
                "scholarship_mode": "paper_i",
            },
            {"kind": "full_subject"},
            pilot_settings(),
        ),
        (
            {
                "grade": 5,
                "medium": "si",
                "paper_type": "term_test",
                "subject": "MATHEMATICS",
                "term": "term_1",
            },
            {"kind": "full_subject"},
            pilot_settings(),
        ),
        (
            {
                "grade": 5,
                "medium": "si",
                "paper_type": "subject_practice",
                "subject": "MATHEMATICS",
            },
            {"kind": "programme"},
            pilot_settings(),
        ),
    ],
)
def test_grade_five_pilot_request_rejects_inconsistent_target_combinations(
    target: dict[str, object],
    scope: dict[str, object],
    settings: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        TeacherPaperJobCreateRequest.model_validate(
            {"target": target, "scope": scope, "settings": settings}
        )


def test_teacher_generation_request_accepts_only_teacher_codes_scope_and_simple_settings() -> None:
    request = TeacherPaperJobCreateRequest.model_validate(valid_request())

    assert request.target.grade == 5
    assert request.target.medium == "si"
    assert request.target.paper_type == "subject_practice"
    assert request.target.subject == "MATHEMATICS"
    assert request.scope.kind == "lesson_range"
    assert request.settings.total_questions == 12

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


def test_teacher_generation_request_accepts_exact_selected_lessons() -> None:
    request = TeacherPaperJobCreateRequest.model_validate(
        {
            **valid_request(),
            "scope": {"kind": "selected_lessons", "lesson_numbers": [1, 3]},
        }
    )

    assert request.scope.kind == "selected_lessons"
    assert request.scope.lesson_numbers == (1, 3)


@pytest.mark.parametrize("lesson_numbers", [[], [0], [1, 1], [2, 1]])
def test_teacher_generation_request_rejects_invalid_selected_lessons(
    lesson_numbers: list[int],
) -> None:
    with pytest.raises(ValidationError):
        TeacherPaperJobCreateRequest.model_validate(
            {
                **valid_request(),
                "scope": {
                    "kind": "selected_lessons",
                    "lesson_numbers": lesson_numbers,
                },
            }
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"anchor_sub_skill_id": UUID(int=32_001)},
            "anchor sub-skill requires an anchor skill",
        ),
        (
            {"anchor_learning_concept_id": UUID(int=32_002)},
            "anchor learning concept requires an anchor sub-skill",
        ),
        (
            {"source_lesson_id": UUID(int=32_003)},
            "source lesson requires a source unit",
        ),
        (
            {"source_sub_skill_id": UUID(int=32_004)},
            "source sub-skill requires a source skill",
        ),
        (
            {"source_learning_concept_id": UUID(int=32_005)},
            "source learning concept requires a source sub-skill",
        ),
    ],
)
def test_programme_policy_scope_rejects_orphaned_hierarchy_nodes(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        ProgrammePolicyScopeCreateRequest.model_validate(
            valid_programme_policy_scope("paper_i", 1, 1, **overrides)
        )


def test_programme_policy_schema_accepts_complete_hierarchies_for_both_parts() -> None:
    payload = {
        **valid_programme_policy(),
        "scopes": [
            valid_programme_policy_scope(
                "paper_i",
                1,
                1,
                anchor_skill_id=UUID(int=33_001),
                anchor_sub_skill_id=UUID(int=33_002),
                anchor_learning_concept_id=UUID(int=33_003),
                source_unit_id=UUID(int=33_004),
                source_lesson_id=UUID(int=33_005),
                source_skill_id=UUID(int=33_006),
                source_sub_skill_id=UUID(int=33_007),
                source_learning_concept_id=UUID(int=33_008),
            ),
            valid_programme_policy_scope("paper_ii", 1, 2),
        ],
    }

    policy = ProgrammePolicyCreateRequest.model_validate(payload)

    assert policy.code == "G5-SCHOLARSHIP"
    assert [(scope.part, scope.ordinal) for scope in policy.scopes] == [
        ("paper_i", 1),
        ("paper_ii", 1),
    ]
    assert policy.scopes[0].anchor_learning_concept_id == UUID(int=33_003)
    assert policy.scopes[0].source_lesson_id == UUID(int=33_005)


@pytest.mark.parametrize(
    ("scopes", "message"),
    [
        (
            [
                valid_programme_policy_scope("paper_i", 1, 1),
                valid_programme_policy_scope("paper_i", 1, 2),
                valid_programme_policy_scope("paper_ii", 1, 3),
            ],
            "programme policy scope ordinals must be unique within each part",
        ),
        (
            [
                valid_programme_policy_scope("paper_i", 1, 1),
                valid_programme_policy_scope("paper_i", 2, 2),
            ],
            "programme policy requires Paper I and Paper II scopes",
        ),
    ],
)
def test_programme_policy_schema_rejects_duplicate_ordinals_or_a_missing_part(
    scopes: list[dict[str, object]],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        ProgrammePolicyCreateRequest.model_validate(
            {
                **valid_programme_policy(),
                "scopes": scopes,
            }
        )


@pytest.mark.parametrize(
    "payload",
    [
        {
            **valid_request(),
            "settings": {
                **pilot_settings(),
                "mcq_count": 0,
                "written_count": 0,
                "structured_count": 0,
            },
        },
        {
            **valid_request(),
            "settings": {**pilot_settings(), "mcq_count": 51},
        },
        {
            **valid_request(),
            "settings": {**pilot_settings(), "duration_minutes": 0},
        },
        {
            **valid_request(),
            "settings": {**pilot_settings(), "difficulty": "provider-decides"},
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
        POLICIES_PATH: ("post",),
        POLICY_PATH: ("get",),
        POLICY_REVIEW_PATH: ("post",),
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
    target_schema = schema["components"]["schemas"]["TeacherPaperTargetRequest"]
    assert set(target_schema["properties"]) == {
        "grade",
        "medium",
        "paper_type",
        "scholarship_mode",
        "subject",
        "term",
    }
    settings_schema = schema["components"]["schemas"]["TeacherPaperSettingsRequest"]
    assert set(settings_schema["properties"]) == {
        "difficulty",
        "duration_minutes",
        "mcq_count",
        "paper_name",
        "structured_count",
        "teacher_instruction",
        "written_count",
    }
    assert settings_schema["properties"]["mcq_count"]["minimum"] == 0
    assert settings_schema["properties"]["mcq_count"]["maximum"] == 50


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
        "marking_scheme": {
            "total_marks": 1,
            "criteria": ["Multiplies 3 by 4."],
            "point_marks": [1],
        },
        "marking_confirmation": {
            "confirmed": False,
            "status": "teacher_confirmation_required",
            "confirmed_at": None,
        },
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
    assert detail.questions[0].marking_confirmation.confirmed is False
    assert detail.technical_details.request_fingerprint.startswith("sha256:")


def test_review_approval_requires_explicit_teacher_marking_confirmation() -> None:
    with pytest.raises(ValidationError):
        ReviewQuestionApproveRequest.model_validate({"expected_version": 3, "note": None})
    with pytest.raises(ValidationError):
        ReviewQuestionApproveRequest(
            expected_version=3,
            note=None,
            marking_confirmed=False,
        )
    with pytest.raises(ValidationError, match="surrounding whitespace"):
        ReviewQuestionApproveRequest(
            expected_version=3,
            note=" padded ",
            marking_confirmed=True,
        )

    request = ReviewQuestionApproveRequest(
        expected_version=3,
        note=None,
        marking_confirmed=True,
    )
    assert request.marking_confirmed is True

    schema = create_app().openapi()["components"]["schemas"]["ReviewQuestionApproveRequest"]
    assert "marking_confirmed" in schema["required"]
    assert schema["properties"]["marking_confirmed"]["const"] is True


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
        "marking_point_marks": [1],
    }
    without_allocations = deepcopy(content)
    without_allocations.pop("marking_point_marks")
    with pytest.raises(ValidationError):
        ReviewQuestionEditRequest.model_validate(
            {
                "content": without_allocations,
                "reason_code": "marking_inconsistent",
                "expected_version": 3,
            }
        )
    with pytest.raises(ValidationError):
        ReviewQuestionEditRequest.model_validate(
            {
                "content": {**content, "marks": 2},
                "reason_code": "marking_inconsistent",
                "expected_version": 3,
            }
        )
    with pytest.raises(ValidationError, match="surrounding whitespace"):
        ReviewQuestionEditRequest.model_validate(
            {
                "content": content,
                "reason_code": "marking_inconsistent",
                "note": " padded ",
                "expected_version": 3,
            }
        )
    with pytest.raises(ValidationError, match="surrounding whitespace"):
        ReviewQuestionRegenerateRequest.model_validate(
            {
                "reason_code": "marking_inconsistent",
                "note": " padded ",
                "expected_version": 4,
            }
        )

    edit = ReviewQuestionEditRequest.model_validate(
        {
            "content": content,
            "reason_code": "marking_inconsistent",
            "note": "Clarify the marking allocation.",
            "expected_version": 3,
        }
    )
    regeneration = ReviewQuestionRegenerateRequest.model_validate(
        {
            "reason_code": "marking_inconsistent",
            "note": "Use an unambiguous prompt.",
            "expected_version": 4,
        }
    )

    assert edit.note == "Clarify the marking allocation."
    assert edit.content.marking_point_marks == (1,)
    assert regeneration.note == "Use an unambiguous prompt."

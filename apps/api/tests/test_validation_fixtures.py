from collections.abc import Mapping

from exam_guru_api.validation import (
    BlueprintRequirements,
    DuplicateReference,
    GroundingSource,
    ValidationInput,
)

LEXICAL_SIMILARITY_FIXTURES = (
    (
        "english-near-copy",
        "Nimal has twenty four red marbles and gives four red marbles to Kamal. How many remain?",
        "Nimal has 24 red marbles and gives four of the red marbles to Kamal. How many remain?",
    ),
    (
        "sinhala-near-copy",
        "නිමල් සතුව රතු බෝල විසි හතරක් ඇත. ඔහු බෝල හතරක් කමල්ට දුන්නේය. ඉතිරි ගණන කීයද?",
        "නිමල් ළඟ රතු බෝල විසි හතරක් ඇත. ඔහු එයින් බෝල හතරක් කමල්ට දුන්නේය. ඉතිරි ගණන කීයද?",
    ),
    (
        "clause-reordering",
        "First add the two tens. Then add the seven ones. What total do you obtain?",
        "What total do you obtain? Add the seven ones, then first add the two tens.",
    ),
    (
        "conservative-false-positive",
        "A bus has 48 empty seats and 12 occupied seats. How many seats are empty?",
        "A bus has 12 empty seats and 48 occupied seats. How many seats are empty?",
    ),
)


def valid_candidate() -> dict[str, object]:
    return {
        "schema_version": "question.v1",
        "question_type": "multiple_choice",
        "stem": "What is 27 + 15?",
        "options": [
            {"option_id": "A", "text": "32"},
            {"option_id": "B", "text": "42"},
            {"option_id": "C", "text": "52"},
        ],
        "answer": {
            "correct_option_id": "B",
            "accepted_responses": [],
            "explanation": "Add the ones, regroup one ten, and then add the tens.",
        },
        "marking": {
            "total_marks": 2,
            "criteria": [
                {
                    "criterion_id": "correct-answer",
                    "description": "Selects the correct sum.",
                    "marks": 2,
                }
            ],
        },
        "context_references": ["context-01"],
    }


def blueprint(**changes: object) -> BlueprintRequirements:
    values: dict[str, object] = {
        "slot_id": "paper-1-section-a-slot-01",
        "schema_version": "question.v1",
        "question_type": "multiple_choice",
        "marks": 2,
        "language": "en-LK",
        "minimum_age": 9,
        "maximum_age": 11,
        "minimum_options": 2,
        "maximum_options": 4,
    }
    values.update(changes)
    return BlueprintRequirements(**values)  # type: ignore[arg-type]


def source(
    context_id: str = "context-01",
    *,
    text: str = "Adding ones before tens preserves place value.",
    source_document_id: str | None = "curriculum-grade-5-maths",
    source_version: str | None = "reviewed-v3",
    page_number: int | None = 7,
    chunk_id: str | None = "chunk-01",
) -> GroundingSource:
    return GroundingSource(
        context_id=context_id,
        text=text,
        source_document_id=source_document_id,
        source_version=source_version,
        page_number=page_number,
        chunk_id=chunk_id,
    )


def validation_input(
    *,
    candidate: Mapping[str, object] | None = None,
    sources: tuple[GroundingSource, ...] | None = None,
    duplicates: tuple[DuplicateReference, ...] = (),
) -> ValidationInput:
    return ValidationInput(
        candidate_id="candidate-01",
        candidate=valid_candidate() if candidate is None else candidate,
        blueprint=blueprint(),
        grounding_sources=(source(),) if sources is None else sources,
        duplicate_references=duplicates,
    )

from uuid import UUID

import pytest

from exam_guru_api.curriculum.domain import (
    TaxonomyLevel,
    TaxonomyNode,
    TaxonomyReviewState,
    TaxonomyValidationError,
    TaxonomyViolation,
    transition_review_state,
    update_taxonomy_node,
    validate_taxonomy,
)

CURRICULUM_A = UUID(int=100)
CURRICULUM_B = UUID(int=200)


def make_node(
    identifier: int,
    level: TaxonomyLevel,
    code: str,
    *,
    parent_id: UUID | None = None,
    curriculum_version_id: UUID = CURRICULUM_A,
    title: str | None = None,
    active: bool = True,
    review_state: TaxonomyReviewState = TaxonomyReviewState.DRAFT,
) -> TaxonomyNode:
    return TaxonomyNode(
        id=UUID(int=identifier),
        curriculum_version_id=curriculum_version_id,
        level=level,
        code=code,
        title=title or code,
        parent_id=parent_id,
        active=active,
        review_state=review_state,
    )


def valid_hierarchy() -> tuple[TaxonomyNode, ...]:
    competency = make_node(1, TaxonomyLevel.COMPETENCY, "C1")
    skill = make_node(2, TaxonomyLevel.SKILL, "S1", parent_id=competency.id)
    sub_skill = make_node(3, TaxonomyLevel.SUB_SKILL, "SS1", parent_id=skill.id)
    concept = make_node(4, TaxonomyLevel.LEARNING_CONCEPT, "LC1", parent_id=sub_skill.id)
    return competency, skill, sub_skill, concept


@pytest.mark.parametrize(
    ("level", "code", "title", "parent_id", "violation"),
    [
        (TaxonomyLevel.COMPETENCY, "lower", "Title", None, TaxonomyViolation.INVALID_CODE),
        (TaxonomyLevel.COMPETENCY, "C1", " Title ", None, TaxonomyViolation.INVALID_TITLE),
        (
            TaxonomyLevel.COMPETENCY,
            "C1",
            "Title",
            UUID(int=9),
            TaxonomyViolation.PARENT_NOT_ALLOWED,
        ),
        (TaxonomyLevel.SKILL, "S1", "Title", None, TaxonomyViolation.PARENT_REQUIRED),
    ],
)
def test_node_shape_rejects_invalid_values(
    level: TaxonomyLevel,
    code: str,
    title: str,
    parent_id: UUID | None,
    violation: TaxonomyViolation,
) -> None:
    with pytest.raises(TaxonomyValidationError) as raised:
        make_node(1, level, code, title=title, parent_id=parent_id)

    assert raised.value.violation is violation
    assert raised.value.node_id == UUID(int=1)


def test_valid_taxonomy_hierarchy_is_preserved() -> None:
    nodes = valid_hierarchy()

    assert validate_taxonomy(nodes) == nodes


def test_duplicate_identifier_is_rejected() -> None:
    competency, skill, _, _ = valid_hierarchy()
    duplicate = make_node(
        skill.id.int,
        TaxonomyLevel.SKILL,
        "S2",
        parent_id=competency.id,
    )

    with pytest.raises(TaxonomyValidationError) as raised:
        validate_taxonomy((competency, skill, duplicate))

    assert raised.value.violation is TaxonomyViolation.DUPLICATE_ID


def test_missing_parent_is_rejected() -> None:
    orphan = make_node(2, TaxonomyLevel.SKILL, "S1", parent_id=UUID(int=999))

    with pytest.raises(TaxonomyValidationError) as raised:
        validate_taxonomy((orphan,))

    assert raised.value.violation is TaxonomyViolation.PARENT_NOT_FOUND


def test_parent_must_be_at_the_immediately_higher_level() -> None:
    competency = make_node(1, TaxonomyLevel.COMPETENCY, "C1")
    sub_skill = make_node(3, TaxonomyLevel.SUB_SKILL, "SS1", parent_id=competency.id)

    with pytest.raises(TaxonomyValidationError) as raised:
        validate_taxonomy((competency, sub_skill))

    assert raised.value.violation is TaxonomyViolation.INVALID_PARENT_LEVEL


def test_parent_must_belong_to_the_same_curriculum_version() -> None:
    competency = make_node(1, TaxonomyLevel.COMPETENCY, "C1")
    skill = make_node(
        2,
        TaxonomyLevel.SKILL,
        "S1",
        parent_id=competency.id,
        curriculum_version_id=CURRICULUM_B,
    )

    with pytest.raises(TaxonomyValidationError) as raised:
        validate_taxonomy((competency, skill))

    assert raised.value.violation is TaxonomyViolation.CROSS_CURRICULUM_PARENT


def test_active_node_cannot_have_an_inactive_parent() -> None:
    competency = make_node(
        1,
        TaxonomyLevel.COMPETENCY,
        "C1",
        active=False,
        review_state=TaxonomyReviewState.DEPRECATED,
    )
    skill = make_node(2, TaxonomyLevel.SKILL, "S1", parent_id=competency.id)

    with pytest.raises(TaxonomyValidationError) as raised:
        validate_taxonomy((competency, skill))

    assert raised.value.violation is TaxonomyViolation.INACTIVE_PARENT


def test_sibling_codes_must_be_unique() -> None:
    competency = make_node(1, TaxonomyLevel.COMPETENCY, "C1")
    first = make_node(2, TaxonomyLevel.SKILL, "S1", parent_id=competency.id)
    duplicate = make_node(3, TaxonomyLevel.SKILL, "S1", parent_id=competency.id)

    with pytest.raises(TaxonomyValidationError) as raised:
        validate_taxonomy((competency, first, duplicate))

    assert raised.value.violation is TaxonomyViolation.DUPLICATE_SIBLING_CODE


def test_same_code_is_allowed_under_different_parents() -> None:
    first_competency = make_node(1, TaxonomyLevel.COMPETENCY, "C1")
    second_competency = make_node(2, TaxonomyLevel.COMPETENCY, "C2")
    first_skill = make_node(3, TaxonomyLevel.SKILL, "S1", parent_id=first_competency.id)
    second_skill = make_node(4, TaxonomyLevel.SKILL, "S1", parent_id=second_competency.id)
    nodes = first_competency, second_competency, first_skill, second_skill

    assert validate_taxonomy(nodes) == nodes


def test_reviewed_child_requires_reviewed_parent() -> None:
    parent = make_node(11, TaxonomyLevel.COMPETENCY, "C11")
    child = make_node(
        12,
        TaxonomyLevel.SKILL,
        "S11",
        parent_id=parent.id,
        review_state=TaxonomyReviewState.REVIEWED,
    )

    with pytest.raises(TaxonomyValidationError) as raised:
        validate_taxonomy((parent, child))

    assert raised.value.violation is TaxonomyViolation.REVIEWED_PARENT_REQUIRED


def test_review_lifecycle_is_forward_only_and_deprecation_sets_inactive() -> None:
    draft = make_node(21, TaxonomyLevel.COMPETENCY, "C21")

    reviewed = transition_review_state(draft, TaxonomyReviewState.REVIEWED)
    deprecated = transition_review_state(reviewed, TaxonomyReviewState.DEPRECATED)

    assert reviewed.review_state is TaxonomyReviewState.REVIEWED
    assert reviewed.active
    assert deprecated.review_state is TaxonomyReviewState.DEPRECATED
    assert not deprecated.active

    with pytest.raises(TaxonomyValidationError) as raised:
        transition_review_state(reviewed, TaxonomyReviewState.DRAFT)

    assert raised.value.violation is TaxonomyViolation.INVALID_REVIEW_TRANSITION


def test_reviewed_taxonomy_node_is_immutable() -> None:
    reviewed = make_node(
        31,
        TaxonomyLevel.COMPETENCY,
        "C31",
        review_state=TaxonomyReviewState.REVIEWED,
    )

    with pytest.raises(TaxonomyValidationError) as raised:
        update_taxonomy_node(reviewed, code="C32", title="Changed")

    assert raised.value.violation is TaxonomyViolation.REVIEWED_NODE_IMMUTABLE


def test_review_state_and_active_flag_must_match() -> None:
    with pytest.raises(TaxonomyValidationError) as raised:
        make_node(
            40,
            TaxonomyLevel.COMPETENCY,
            "C40",
            active=False,
            review_state=TaxonomyReviewState.DRAFT,
        )

    assert raised.value.violation is TaxonomyViolation.ACTIVE_STATE_MISMATCH


def test_transition_to_current_review_state_is_idempotent() -> None:
    draft = make_node(42, TaxonomyLevel.COMPETENCY, "C42")

    assert transition_review_state(draft, TaxonomyReviewState.DRAFT) is draft


def test_draft_taxonomy_node_can_be_updated() -> None:
    draft = make_node(41, TaxonomyLevel.COMPETENCY, "C41")

    updated = update_taxonomy_node(draft, code="C42", title="Updated competency")

    assert updated.code == "C42"
    assert updated.title == "Updated competency"

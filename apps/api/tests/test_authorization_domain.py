from uuid import UUID

import pytest

from exam_guru_api.auth.domain import (
    AdminRole,
    AuthorizationError,
    Permission,
    Principal,
    authorize,
)

ADMIN_ID = UUID(int=1)
REVIEWER_ID = UUID(int=2)


@pytest.mark.parametrize(
    "permission",
    [
        Permission.TAXONOMY_READ,
        Permission.TAXONOMY_WRITE,
        Permission.CONTENT_REVIEW,
        Permission.PAPER_PUBLISH,
    ],
)
def test_admin_has_every_priority_one_permission(permission: Permission) -> None:
    principal = Principal(subject_id=ADMIN_ID, roles=frozenset({AdminRole.ADMIN}))

    assert authorize(principal, permission) is principal


@pytest.mark.parametrize(
    "permission",
    [Permission.TAXONOMY_READ, Permission.CONTENT_REVIEW],
)
def test_reviewer_has_read_and_review_permissions(permission: Permission) -> None:
    principal = Principal(subject_id=REVIEWER_ID, roles=frozenset({AdminRole.REVIEWER}))

    assert authorize(principal, permission) is principal


@pytest.mark.parametrize(
    "permission",
    [Permission.TAXONOMY_WRITE, Permission.PAPER_PUBLISH],
)
def test_reviewer_cannot_manage_taxonomy_or_publish(permission: Permission) -> None:
    principal = Principal(subject_id=REVIEWER_ID, roles=frozenset({AdminRole.REVIEWER}))

    with pytest.raises(AuthorizationError) as raised:
        authorize(principal, permission)

    assert raised.value.subject_id == REVIEWER_ID
    assert raised.value.permission is permission


def test_principal_without_roles_has_no_permissions() -> None:
    principal = Principal(subject_id=UUID(int=3), roles=frozenset())

    with pytest.raises(AuthorizationError):
        authorize(principal, Permission.TAXONOMY_READ)

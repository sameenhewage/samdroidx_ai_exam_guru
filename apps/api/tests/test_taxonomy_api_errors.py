import asyncio
from typing import cast
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.api.routes.taxonomy import (
    _execute_taxonomy_write,
    create_taxonomy_node,
    deactivate_taxonomy_node_route,
    list_taxonomy_nodes,
    review_taxonomy_node_route,
    update_taxonomy_node_route,
)
from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.curriculum.domain import (
    TaxonomyLevel,
    TaxonomyNode,
    TaxonomyValidationError,
    TaxonomyViolation,
)
from exam_guru_api.curriculum.schemas import (
    TaxonomyNodeCreate,
    TaxonomyNodeResponse,
    TaxonomyNodeUpdate,
)
from exam_guru_api.curriculum.service import (
    CurriculumVersionInactiveError,
    CurriculumVersionNotFoundError,
    TaxonomyNodeNotFoundError,
    TaxonomyService,
)


class RollbackSession:
    def __init__(self) -> None:
        self.rolled_back = False

    async def rollback(self) -> None:
        self.rolled_back = True


def test_taxonomy_write_conflict_rolls_back_and_returns_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def raise_integrity_error(
        _service: TaxonomyService,
        _node: TaxonomyNode,
        *,
        actor_id: UUID,
    ) -> TaxonomyNode:
        del actor_id
        raise IntegrityError("INSERT", {}, RuntimeError("concurrent conflict"))

    monkeypatch.setattr(TaxonomyService, "add_node", raise_integrity_error)
    session = RollbackSession()
    principal = Principal(subject_id=UUID(int=1), roles=frozenset({AdminRole.ADMIN}))

    async def exercise() -> HTTPException:
        with pytest.raises(HTTPException) as raised:
            await create_taxonomy_node(
                curriculum_version_id=UUID(int=2),
                request=TaxonomyNodeCreate(
                    level=TaxonomyLevel.COMPETENCY,
                    code="C1",
                    title="Competency 1",
                ),
                principal=principal,
                session=cast(AsyncSession, session),
            )
        return raised.value

    error = asyncio.run(exercise())

    assert error.status_code == 409
    assert cast(dict[str, str], error.detail) == {"code": "taxonomy_conflict"}
    assert session.rolled_back


def test_taxonomy_routes_return_success_and_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    curriculum_version_id = UUID(int=2)
    node = TaxonomyNode(
        id=UUID(int=3),
        curriculum_version_id=curriculum_version_id,
        level=TaxonomyLevel.COMPETENCY,
        code="C1",
        title="Competency 1",
    )
    principal = Principal(subject_id=UUID(int=1), roles=frozenset({AdminRole.ADMIN}))
    session = cast(AsyncSession, RollbackSession())

    async def list_existing(
        _service: TaxonomyService,
        _curriculum_version_id: UUID,
    ) -> tuple[TaxonomyNode, ...]:
        return (node,)

    async def add_existing(
        _service: TaxonomyService,
        created: TaxonomyNode,
        *,
        actor_id: UUID,
    ) -> TaxonomyNode:
        del actor_id
        return created

    monkeypatch.setattr(TaxonomyService, "list_nodes", list_existing)
    monkeypatch.setattr(TaxonomyService, "add_node", add_existing)

    listed = asyncio.run(list_taxonomy_nodes(curriculum_version_id, principal, session))
    created = asyncio.run(
        create_taxonomy_node(
            curriculum_version_id=curriculum_version_id,
            request=TaxonomyNodeCreate(
                level=TaxonomyLevel.COMPETENCY,
                code="C2",
                title="Competency 2",
            ),
            principal=principal,
            session=session,
        )
    )

    assert [item.id for item in listed] == [node.id]
    assert created.code == "C2"

    async def raise_not_found(
        _service: TaxonomyService,
        missing_curriculum_version_id: UUID,
    ) -> tuple[TaxonomyNode, ...]:
        raise CurriculumVersionNotFoundError(missing_curriculum_version_id)

    async def raise_not_found_on_add(
        _service: TaxonomyService,
        missing_node: TaxonomyNode,
        *,
        actor_id: UUID,
    ) -> TaxonomyNode:
        del actor_id
        raise CurriculumVersionNotFoundError(missing_node.curriculum_version_id)

    monkeypatch.setattr(TaxonomyService, "list_nodes", raise_not_found)
    monkeypatch.setattr(TaxonomyService, "add_node", raise_not_found_on_add)

    async def exercise_not_found() -> tuple[HTTPException, HTTPException]:
        with pytest.raises(HTTPException) as list_error:
            await list_taxonomy_nodes(curriculum_version_id, principal, session)
        with pytest.raises(HTTPException) as create_error:
            await create_taxonomy_node(
                curriculum_version_id=curriculum_version_id,
                request=TaxonomyNodeCreate(
                    level=TaxonomyLevel.COMPETENCY,
                    code="C3",
                    title="Competency 3",
                ),
                principal=principal,
                session=session,
            )
        return list_error.value, create_error.value

    list_error, create_error = asyncio.run(exercise_not_found())

    assert list_error.status_code == 404
    assert create_error.status_code == 404


def test_taxonomy_lifecycle_route_wrappers_return_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    curriculum_version_id = UUID(int=50)
    node = TaxonomyNode(
        id=UUID(int=51),
        curriculum_version_id=curriculum_version_id,
        level=TaxonomyLevel.COMPETENCY,
        code="C1",
        title="Competency",
    )
    principal = Principal(subject_id=UUID(int=52), roles=frozenset({AdminRole.ADMIN}))
    session = cast(AsyncSession, RollbackSession())

    async def return_node(
        _service: TaxonomyService,
        *_args: object,
        **_kwargs: object,
    ) -> TaxonomyNode:
        return node

    monkeypatch.setattr(TaxonomyService, "update_node", return_node)
    monkeypatch.setattr(TaxonomyService, "review_node", return_node)
    monkeypatch.setattr(TaxonomyService, "deactivate_node", return_node)

    async def exercise() -> tuple[
        TaxonomyNodeResponse,
        TaxonomyNodeResponse,
        TaxonomyNodeResponse,
    ]:
        return (
            await update_taxonomy_node_route(
                curriculum_version_id,
                node.id,
                TaxonomyNodeUpdate(code="C2", title="Updated"),
                principal,
                session,
            ),
            await review_taxonomy_node_route(
                curriculum_version_id,
                node.id,
                principal,
                session,
            ),
            await deactivate_taxonomy_node_route(
                curriculum_version_id,
                node.id,
                principal,
                session,
            ),
        )

    updated, reviewed, deprecated = asyncio.run(exercise())

    assert updated.id == node.id
    assert reviewed.id == node.id
    assert deprecated.id == node.id


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (
            CurriculumVersionNotFoundError(UUID(int=60)),
            404,
            "curriculum_version_not_found",
        ),
        (
            CurriculumVersionInactiveError(UUID(int=60)),
            409,
            "curriculum_version_inactive",
        ),
        (TaxonomyNodeNotFoundError(UUID(int=61)), 404, "taxonomy_node_not_found"),
        (
            TaxonomyValidationError(TaxonomyViolation.REVIEWED_NODE_IMMUTABLE, UUID(int=61)),
            409,
            "reviewed_node_immutable",
        ),
        (
            TaxonomyValidationError(TaxonomyViolation.INVALID_CODE, UUID(int=61)),
            422,
            "invalid_code",
        ),
    ],
)
def test_taxonomy_write_helper_maps_domain_errors(
    error: Exception,
    status_code: int,
    code: str,
) -> None:
    async def fail() -> TaxonomyNode:
        raise error

    with pytest.raises(HTTPException) as raised:
        asyncio.run(_execute_taxonomy_write(cast(AsyncSession, RollbackSession()), fail))

    assert raised.value.status_code == status_code
    assert cast(dict[str, str], raised.value.detail)["code"] == code


def test_taxonomy_write_helper_rolls_back_integrity_error() -> None:
    session = RollbackSession()

    async def fail() -> TaxonomyNode:
        raise IntegrityError("UPDATE", {}, RuntimeError("conflict"))

    with pytest.raises(HTTPException) as raised:
        asyncio.run(_execute_taxonomy_write(cast(AsyncSession, session), fail))

    assert raised.value.status_code == 409
    assert session.rolled_back

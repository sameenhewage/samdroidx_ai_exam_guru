import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

import exam_guru_api.knowledge.preparation_jobs as jobs
from exam_guru_api.auth.domain import (
    AdminRole,
    AuthorizationError,
    Permission,
    Principal,
    authorize,
)
from tests.test_document_understanding_worker import Resources


@pytest.mark.parametrize("failure", [False, True])
def test_recovery_owns_a_bounded_broker_transport_and_closes_it(
    monkeypatch: pytest.MonkeyPatch, failure: bool
) -> None:
    resources = Resources()
    configured: list[dict[str, Any]] = []
    closed: list[bool] = []
    declared: list[object] = []
    broker = SimpleNamespace(close=lambda: closed.append(True), declare_actor=declared.append)

    def create_broker(**options: Any) -> Any:
        configured.append(options)
        return broker

    recovery = AsyncMock(side_effect=RuntimeError("controlled") if failure else None)
    monkeypatch.setattr(jobs, "RedisBroker", create_broker, raising=False)
    monkeypatch.setattr(jobs, "create_resources", lambda _: resources)
    monkeypatch.setattr(jobs, "recover_knowledge_preparation_jobs", recovery)
    if failure:
        with pytest.raises(RuntimeError, match="controlled"):
            asyncio.run(jobs._recover_knowledge_preparation_jobs())
    else:
        asyncio.run(jobs._recover_knowledge_preparation_jobs())
    assert resources.closed
    assert closed == [True]
    assert declared == [jobs.prepare_knowledge_page]
    assert len(configured) == 1
    assert configured[0]["socket_connect_timeout"] == 5
    assert configured[0]["socket_timeout"] == 5
    assert configured[0]["retry"].get_retries() == 0
    assert recovery.await_args is not None
    assert recovery.await_args.args[1].broker is broker


@pytest.mark.parametrize("phase", ["construction", "shutdown"])
def test_broker_lifecycle_errors_still_close_application_resources(
    monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    resources = Resources()

    def fail() -> None:
        raise RuntimeError("controlled broker lifecycle failure")

    def create_broker(**_options: Any) -> Any:
        if phase == "construction":
            fail()
        return SimpleNamespace(close=fail, declare_actor=lambda _actor: None)

    monkeypatch.setattr(jobs, "RedisBroker", create_broker)
    monkeypatch.setattr(jobs, "create_resources", lambda _: resources)
    monkeypatch.setattr(jobs, "recover_knowledge_preparation_jobs", AsyncMock())
    with pytest.raises(RuntimeError, match="controlled broker lifecycle"):
        asyncio.run(jobs._recover_knowledge_preparation_jobs())
    assert resources.closed


def test_bounded_dispatcher_enqueues_only_the_existing_actor_and_job_identity() -> None:
    messages: list[Any] = []

    def enqueue(message: Any) -> Any:
        messages.append(message)
        return SimpleNamespace(message_id="bounded-message")

    identifier = uuid4()
    dispatcher = jobs.DramatiqKnowledgePreparationDispatcher(
        broker=SimpleNamespace(enqueue=enqueue)
    )
    assert dispatcher.dispatch(identifier) == "bounded-message"
    assert len(messages) == 1
    assert messages[0].queue_name == jobs.PREPARATION_QUEUE_NAME
    assert messages[0].actor_name == jobs.prepare_knowledge_page.actor_name
    assert messages[0].args == (str(identifier),)
    assert messages[0].kwargs == {}


@pytest.mark.parametrize("stage", ["discovery", "recovery"])
def test_candidate_selection_has_a_database_statement_deadline(
    monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    session = AsyncMock()
    session.execute.return_value = SimpleNamespace(mappings=lambda: SimpleNamespace(all=list))
    session.scalars.return_value = SimpleNamespace(all=list)
    if stage == "discovery":
        asyncio.run(jobs.discover_knowledge_preparation(session))
    else:
        monkeypatch.setattr(jobs, "discover_knowledge_preparation", AsyncMock(return_value=()))
        asyncio.run(jobs.recover_knowledge_preparation_jobs(session, SimpleNamespace()))
    assert session.execute.await_args_list
    assert str(session.execute.await_args_list[0].args[0]) == "SET LOCAL statement_timeout = '90s'"


def test_current_source_trust_roles_also_authorize_knowledge_preparation() -> None:
    authorized: set[AdminRole] = set()
    for role in AdminRole:
        principal = Principal(uuid4(), frozenset({role}))
        try:
            authorize(principal, Permission.SOURCE_TRUST)
        except AuthorizationError:
            continue
        authorize(principal, Permission.KNOWLEDGE_WRITE)
        authorized.add(role)
    assert authorized == {AdminRole.ADMIN}

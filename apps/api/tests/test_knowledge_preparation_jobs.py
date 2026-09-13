import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import exam_guru_api.knowledge.preparation_jobs as jobs
from exam_guru_api.documents.understanding_models import TrustedPageKnowledgeModel
from exam_guru_api.knowledge.preparation_models import KnowledgePreparationJobModel
from exam_guru_api.knowledge.unit_service import KnowledgePreparationError, KnowledgeUnitService
from tests.test_document_understanding_verification import approve, candidate
from tests.test_document_understanding_worker import Resources
from tests.test_knowledge_units import scope


@pytest.mark.parametrize("size", [True, 0, 9, 100])
def test_discovery_never_accepts_more_than_eight_pages(size: Any) -> None:
    session = AsyncMock(spec=AsyncSession)
    with pytest.raises(ValueError, match="limit"):
        asyncio.run(jobs.discover_knowledge_preparation(session, batch_size=size))
    assert not session.mock_calls


@pytest.mark.parametrize("seconds", [True, 0, 120, 3601])
def test_worker_lease_is_finite_and_exceeds_actor_deadline(seconds: Any) -> None:
    session = AsyncMock(spec=AsyncSession)
    with pytest.raises(ValueError, match="lease"):
        asyncio.run(jobs.run_knowledge_preparation_job(session, uuid4(), lease_seconds=seconds))
    assert not session.mock_calls


@pytest.mark.parametrize(
    ("size", "age"), [(9, 5), (0, 5), (True, 5), (8, -1), (8, 3601), (8, True)]
)
def test_recovery_bounds_are_checked_before_any_database_work(size: Any, age: Any) -> None:
    session = AsyncMock(spec=AsyncSession)
    with pytest.raises(ValueError, match="limit"):
        asyncio.run(
            jobs.recover_knowledge_preparation_jobs(
                session, SimpleNamespace(), batch_size=size, min_age_seconds=age
            )
        )
    assert not session.mock_calls


@pytest.mark.parametrize("failure", [False, True])
def test_worker_and_recovery_close_resources_without_opening_provider_or_original_storage(
    monkeypatch: pytest.MonkeyPatch,
    failure: bool,
) -> None:
    resources = Resources()
    process = AsyncMock(side_effect=RuntimeError("controlled failure") if failure else None)
    monkeypatch.setattr(jobs, "create_resources", lambda _: resources)
    monkeypatch.setattr(jobs, "run_knowledge_preparation_job", process)
    identifier = uuid4()
    if failure:
        with pytest.raises(RuntimeError, match="controlled"):
            asyncio.run(jobs._execute_knowledge_preparation_job(identifier))
    else:
        asyncio.run(jobs._execute_knowledge_preparation_job(identifier))
    assert resources.closed
    process.assert_awaited_once_with(resources.session, identifier)
    resources.closed = False
    recovery = AsyncMock(side_effect=RuntimeError("controlled failure") if failure else None)
    monkeypatch.setattr(jobs, "recover_knowledge_preparation_jobs", recovery)
    if failure:
        with pytest.raises(RuntimeError, match="controlled"):
            asyncio.run(jobs._recover_knowledge_preparation_jobs())
    else:
        asyncio.run(jobs._recover_knowledge_preparation_jobs())
    assert resources.closed


def test_job_actors_only_accept_opaque_job_identity_and_have_zero_broker_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execute, recover = AsyncMock(), AsyncMock()
    monkeypatch.setattr(jobs, "_execute_knowledge_preparation_job", execute)
    monkeypatch.setattr(jobs, "_recover_knowledge_preparation_jobs", recover)
    identifier = uuid4()
    jobs.prepare_knowledge_page(str(identifier))
    jobs.recover_material_knowledge()
    execute.assert_awaited_once_with(identifier)
    recover.assert_awaited_once_with()
    for actor in (jobs.prepare_knowledge_page, jobs.recover_material_knowledge):
        assert actor.options["max_retries"] == 0
        assert actor.options["time_limit"] == 120000


def test_preparation_is_registered_with_existing_worker_and_maintenance_brokers() -> None:
    from exam_guru_api import maintenance, worker

    assert id(jobs.recover_material_knowledge) in {
        id(actor) for actor in maintenance._RECOVERY_ACTORS
    }
    assert (
        worker.broker.actors[jobs.prepare_knowledge_page.actor_name] is jobs.prepare_knowledge_page
    )
    assert (
        worker.broker.actors[jobs.recover_material_knowledge.actor_name]
        is jobs.recover_material_knowledge
    )


def test_dispatcher_forwards_only_the_job_id() -> None:
    identifiers: list[str] = []

    class Actor:
        def send(self, identifier: str) -> Any:
            identifiers.append(identifier)
            return SimpleNamespace(message_id="preparation-message")

    identifier = uuid4()
    assert (
        jobs.DramatiqKnowledgePreparationDispatcher(Actor()).dispatch(identifier)
        == "preparation-message"
    )
    assert identifiers == [str(identifier)]


def job_fixture() -> KnowledgePreparationJobModel:
    scope_payload = scope(approve(candidate())).model_dump(mode="json")
    payload: dict[str, object] = {"scope": scope_payload}
    return KnowledgePreparationJobModel(
        id=uuid4(),
        document_id=uuid4(),
        page_number=1,
        status="running",
        version=1,
        attempts=0,
        lease_token=uuid4(),
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=300),
        failure_code=None,
        unit_count=None,
        projection_count=None,
        input_snapshot=payload,
        input_fingerprint=jobs._fingerprint(payload),
        scope_fingerprint=jobs._fingerprint(scope_payload),
    )


@pytest.mark.parametrize("missing", ["reference", "job"])
def test_missing_job_dependencies_fail_closed_and_release_the_transaction(missing: str) -> None:
    session = AsyncMock(spec=AsyncSession)
    session.scalar.side_effect = [None] if missing == "reference" else [uuid4(), None]
    with pytest.raises(KnowledgePreparationError, match="not_found"):
        asyncio.run(jobs.run_knowledge_preparation_job(session, uuid4()))
    session.rollback.assert_awaited_once()


def test_invalid_trusted_snapshot_is_not_copied_into_a_job() -> None:
    trusted = approve(candidate())
    record = TrustedPageKnowledgeModel(
        id=trusted.id, fingerprint="0" * 64, payload=trusted.model_dump(mode="json")
    )
    with pytest.raises(KnowledgePreparationError, match="fingerprint_invalid"):
        jobs._input(record, scope(trusted))


@pytest.mark.parametrize("field", ["input_fingerprint", "scope_fingerprint"])
def test_corrupt_job_hashes_fail_before_checking_currentness(field: str) -> None:
    session = AsyncMock(spec=AsyncSession)
    job = job_fixture()
    setattr(job, field, "0" * 64)
    with pytest.raises(KnowledgePreparationError, match="input_invalid"):
        asyncio.run(jobs._eligibility(session, job))
    assert not session.mock_calls


@pytest.mark.parametrize("failure", ["replaced", "expired", "lease_missing"])
def test_late_failure_cannot_overwrite_a_reclaimed_or_expired_lease(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    session = AsyncMock(spec=AsyncSession)
    job = job_fixture()
    claim = jobs._snapshot(job)
    if failure == "replaced":
        job.lease_token = uuid4()
    elif failure == "expired":
        job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    else:
        job.lease_expires_at = None
    monkeypatch.setattr(jobs, "_locked", AsyncMock(return_value=job))
    result = asyncio.run(jobs._failed_claim(session, claim, "knowledge_preparation_failed"))
    assert result == jobs._snapshot(job)
    session.flush.assert_not_awaited()


def test_invalid_derivation_is_sanitized_and_transactionally_rolled_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock(spec=AsyncSession)
    job = job_fixture()
    claim = jobs._snapshot(job)
    monkeypatch.setattr(jobs, "_locked", AsyncMock(return_value=job))
    monkeypatch.setattr(jobs, "_eligibility", AsyncMock(return_value="eligible"))
    job.requested_by, job.trusted_page_id = uuid4(), uuid4()
    monkeypatch.setattr(
        KnowledgeUnitService,
        "prepare_page",
        AsyncMock(side_effect=ValueError("private source detail")),
    )
    failure = AsyncMock(return_value=claim)
    monkeypatch.setattr(jobs, "_failed_claim", failure)
    asyncio.run(jobs._execute_claim(session, claim))
    failure.assert_awaited_once_with(session, claim, "knowledge_preparation_invalid")
    session.rollback.assert_awaited_once()


@pytest.mark.parametrize("changed", ["scope", "eligibility", "request"])
def test_discovery_rechecks_after_lock_and_rejects_disappearing_inputs(
    monkeypatch: pytest.MonkeyPatch, changed: str
) -> None:
    session = AsyncMock(spec=AsyncSession)
    selected = {"request_id": uuid4(), "document_id": uuid4(), "trusted_id": uuid4()}
    session.execute.return_value = SimpleNamespace(
        mappings=lambda: SimpleNamespace(all=lambda: [selected])
    )
    value = scope(approve(candidate())).model_dump(mode="json")
    session.scalar.side_effect = (
        [None]
        if changed == "scope"
        else [value, "deferred"]
        if changed == "eligibility"
        else [value, "eligible", None]
    )
    session.get.return_value = None
    monkeypatch.setattr(jobs, "_source_lock", AsyncMock())
    if changed == "request":
        with pytest.raises(KnowledgePreparationError, match="input_unavailable"):
            asyncio.run(jobs.discover_knowledge_preparation(session))
        session.rollback.assert_awaited_once()
    else:
        assert asyncio.run(jobs.discover_knowledge_preparation(session)) == ()
    session.add.assert_not_called()


@pytest.mark.parametrize("state", ["deferred", "running", "unacknowledged", "invalid"])
def test_recovery_handles_racing_currentness_and_unacknowledged_delivery(
    monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    session = AsyncMock(spec=AsyncSession)
    job = job_fixture()
    job.status = "running" if state == "running" else "queued"
    session.scalars.return_value = SimpleNamespace(all=lambda: [job.id])
    monkeypatch.setattr(jobs, "discover_knowledge_preparation", AsyncMock(return_value=()))
    monkeypatch.setattr(jobs, "_locked", AsyncMock(return_value=job))
    monkeypatch.setattr(
        jobs,
        "_eligibility",
        AsyncMock(return_value="deferred" if state == "deferred" else "eligible"),
    )
    deferred = AsyncMock()
    monkeypatch.setattr(jobs, "_defer", deferred)
    monkeypatch.setattr(jobs, "_transition", AsyncMock())
    dispatcher = SimpleNamespace(
        dispatch=lambda _: "" if state == "unacknowledged" else cast(str, None)
    )
    result = asyncio.run(jobs.recover_knowledge_preparation_jobs(session, dispatcher))
    assert result.failures == int(state in {"invalid", "unacknowledged"})
    assert result.enqueued == 0
    assert deferred.await_count == int(state == "deferred")

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any, cast
from uuid import UUID

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import exam_guru_api.papers.publication_repository as publication_repository_module
from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.auth.models import AdminAuditEventModel
from exam_guru_api.blueprints.models import PaperBlueprintModel
from exam_guru_api.generation.jobs import (
    DeterministicGenerationDispatcher,
    GenerationDispatcher,
)
from exam_guru_api.generation.runtime import GenerationRuntimeRegistry
from exam_guru_api.papers.publication_models import (
    PaperArchiveEventModel,
    PaperDraftCandidateModel,
    PaperDraftVersionModel,
    PracticePaperModel,
    PublishedPaperVersionModel,
)
from exam_guru_api.papers.publication_service import PaperPublicationService
from tests.integration.test_generation_runs_api import (
    ADMIN_HEADERS,
    OTHER_CURRICULUM_ID,
    REVIEWER_HEADERS,
    REVIEWER_ID,
    GenerationSeed,
    api_client,
    generation_seed,  # noqa: F401 - imported fixture is discovered by pytest
)
from tests.integration.test_review_candidates_api import (
    create_passing_validation,
    review_candidate_path,
)
from tests.test_blueprint_domain import CURRICULUM_VERSION_ID


def paper_draft_path() -> str:
    return f"/api/v1/admin/curricula/{CURRICULUM_VERSION_ID}/paper-drafts"


def papers_path() -> str:
    return f"/api/v1/admin/curricula/{CURRICULUM_VERSION_ID}/papers"


def create_approved_candidate(
    seed: GenerationSeed,
    *,
    key: str,
    stem: str,
) -> tuple[UUID, GenerationDispatcher, GenerationRuntimeRegistry]:
    validation_id, candidate_id, dispatcher, runtime = create_passing_validation(
        seed,
        key=key,
        stem=stem,
    )
    with api_client(seed, dispatcher, runtime=runtime) as client:
        created = client.post(
            review_candidate_path(seed),
            json={"validation_run_id": str(validation_id)},
            headers=REVIEWER_HEADERS,
        )
        assert created.status_code == 201
        started = client.post(
            f"{review_candidate_path(seed)}/{candidate_id}/start-review",
            json={"expected_version": 2},
            headers=REVIEWER_HEADERS,
        )
        assert started.status_code == 200
        approved = client.post(
            f"{review_candidate_path(seed)}/{candidate_id}/approve",
            json={"expected_version": 3, "note": "Source and validation reviewed."},
            headers=REVIEWER_HEADERS,
        )
        assert approved.status_code == 200
        assert approved.json()["state"] == "approved"
    return candidate_id, dispatcher, runtime


@pytest.mark.integration
def test_paper_api_assembles_publishes_revises_and_archives_authoritatively(
    request: pytest.FixtureRequest,
) -> None:
    seed = cast(GenerationSeed, request.getfixturevalue("generation_seed"))
    candidate_id, dispatcher, runtime = create_approved_candidate(
        seed,
        key="paper-lifecycle-approved",
        stem="Thirteen mangoes are grouped with nine limes; which option gives the total?",
    )
    provider_dispatch_count = len(cast(DeterministicGenerationDispatcher, dispatcher).dispatched)
    create_body = {
        "paper_blueprint_id": str(seed.paper_blueprint_id),
        "title": "Grade 5 Scholarship Practice Paper",
        "candidate_ids": [str(candidate_id)],
    }
    create_headers = {**REVIEWER_HEADERS, "Idempotency-Key": "paper-lifecycle-1"}
    with api_client(seed, dispatcher, runtime=runtime) as client:
        assert client.post(paper_draft_path(), json=create_body).status_code == 401
        assert (
            client.post(
                paper_draft_path(),
                json=create_body,
                headers={"Authorization": "Bearer no-role-token", "Idempotency-Key": "x"},
            ).status_code
            == 403
        )
        forged = client.post(
            paper_draft_path(),
            json={
                **create_body,
                "state": "published",
                "content_hash": "0" * 64,
                "published_by": str(UUID(int=1)),
                "snapshot": {"forged": True},
            },
            headers=create_headers,
        )
        assert forged.status_code == 422
        approved_bank = client.get(
            review_candidate_path(seed),
            params={"state": "approved"},
            headers=REVIEWER_HEADERS,
        )
        assert approved_bank.status_code == 200
        assert str(candidate_id) in {item["id"] for item in approved_bank.json()}

        duplicate_candidate = client.post(
            paper_draft_path(),
            json={**create_body, "candidate_ids": [str(candidate_id), str(candidate_id)]},
            headers={**REVIEWER_HEADERS, "Idempotency-Key": "paper-duplicate-candidate"},
        )
        assert duplicate_candidate.status_code == 422

        created = client.post(paper_draft_path(), json=create_body, headers=create_headers)
        duplicate = client.post(paper_draft_path(), json=create_body, headers=create_headers)
        reordered_duplicate = client.post(
            paper_draft_path(),
            json={**create_body, "candidate_ids": list(reversed(create_body["candidate_ids"]))},
            headers=create_headers,
        )
        assert (
            created.status_code == duplicate.status_code == reordered_duplicate.status_code == 201
        ), (created.json(), duplicate.json(), reordered_duplicate.json())
        assert created.json()["deduplicated"] is False
        assert duplicate.json()["deduplicated"] is True
        assert reordered_duplicate.json()["deduplicated"] is True
        paper_id = UUID(created.json()["paper_id"])
        assert created.json()["version"] == 1
        assert created.json()["candidates"] == [
            {
                "ordinal": 1,
                "blueprint_slot_id": seed.slot_id,
                "candidate_id": str(candidate_id),
                "candidate_version": 4,
                "candidate_revision": 1,
            }
        ]
        changed_request = client.post(
            paper_draft_path(),
            json={**create_body, "title": "Changed title"},
            headers=create_headers,
        )
        assert changed_request.status_code == 409
        assert changed_request.json()["detail"]["code"] == "paper_idempotency_conflict"

        paper_path = f"{papers_path()}/{paper_id}"
        assert client.get(paper_path, headers=REVIEWER_HEADERS).json()["state"] == "draft"
        listed = client.get(
            papers_path(),
            params={"state": "draft", "paper_blueprint_id": str(seed.paper_blueprint_id)},
            headers=REVIEWER_HEADERS,
        )
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()] == [str(paper_id)]
        assert (
            client.get(
                f"/api/v1/admin/curricula/{OTHER_CURRICULUM_ID}/papers/{paper_id}",
                headers=REVIEWER_HEADERS,
            ).status_code
            == 404
        )
        for params in ({"limit": 0}, {"limit": 101}, {"offset": 100_001}):
            bounded = client.get(papers_path(), params=params, headers=REVIEWER_HEADERS)
            assert bounded.status_code == 422

        above_version_bound = client.post(
            f"{paper_path}/publish",
            json={"expected_version": 33},
            headers=ADMIN_HEADERS,
        )
        assert above_version_bound.status_code == 422
        assert client.get(paper_path, headers=REVIEWER_HEADERS).json()["state"] == "draft"

        revise_draft = client.post(
            f"{paper_path}/revisions",
            json={"expected_version": 1, "candidate_ids": [str(candidate_id)]},
            headers=REVIEWER_HEADERS,
        )
        assert revise_draft.status_code == 409
        assert revise_draft.json()["detail"]["code"] == "paper_state_conflict"
        assert (
            client.post(
                f"{paper_path}/publish",
                json={"expected_version": 1},
                headers=REVIEWER_HEADERS,
            ).status_code
            == 403
        )

        published_v1 = client.post(
            f"{paper_path}/publish",
            json={"expected_version": 1},
            headers=ADMIN_HEADERS,
        )
        duplicate_publish = client.post(
            f"{paper_path}/publish",
            json={"expected_version": 1},
            headers=ADMIN_HEADERS,
        )
        assert published_v1.status_code == duplicate_publish.status_code == 200
        assert published_v1.json()["deduplicated"] is False
        assert duplicate_publish.json()["deduplicated"] is True
        publication = published_v1.json()
        first_hash = publication["content_hash"]
        assert len(first_hash) == 64
        assert publication["snapshot"]["paper_id"] == str(paper_id)
        assert publication["snapshot"]["paper_version"] == 1
        assert publication["snapshot"]["questions"][0]["content"]["stem"]
        assert publication["snapshot"]["questions"][0]["lineage"]["provenance"]
        assert publication["snapshot"]["questions"][0]["review_history"][-1]["action"] == "approved"
        assert publication["snapshot"]["questions"][0]["validation"]["passed"] is True
        assert (
            client.get(
                f"{paper_path}/publication-versions/1",
                headers=REVIEWER_HEADERS,
            ).json()["content_hash"]
            == first_hash
        )
        summaries = client.get(
            f"{paper_path}/publication-versions",
            headers=REVIEWER_HEADERS,
        )
        assert summaries.status_code == 200
        assert "snapshot" not in summaries.json()[0]
        stale_publish = client.post(
            f"{paper_path}/publish",
            json={"expected_version": 2},
            headers=ADMIN_HEADERS,
        )
        assert stale_publish.status_code == 409
        assert stale_publish.json()["detail"]["code"] == "paper_version_conflict"

        revised = client.post(
            f"{paper_path}/revisions",
            json={
                "expected_version": 1,
                "title": "Revised Grade 5 Scholarship Practice Paper",
                "candidate_ids": [str(candidate_id)],
            },
            headers=REVIEWER_HEADERS,
        )
        assert revised.status_code == 201
        assert revised.json()["version"] == 2
        assert revised.json()["supersedes_content_hash"] == first_hash
        assert client.get(paper_path, headers=REVIEWER_HEADERS).json()["state"] == "draft"

        barrier = Barrier(2)

        def publish_v2() -> tuple[int, dict[str, Any]]:
            barrier.wait()
            response = client.post(
                f"{paper_path}/publish",
                json={"expected_version": 2},
                headers=ADMIN_HEADERS,
            )
            return response.status_code, response.json()

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = tuple(executor.map(lambda _index: publish_v2(), range(2)))
        assert [status_code for status_code, _body in outcomes] == [200, 200]
        assert sorted(body["deduplicated"] for _status, body in outcomes) == [False, True]
        published_v2 = next(body for _status, body in outcomes if not body["deduplicated"])
        assert published_v2["version"] == 2
        assert published_v2["previous_version"] == 1
        assert published_v2["supersedes_content_hash"] == first_hash
        assert published_v2["content_hash"] != first_hash

        assert (
            client.post(
                f"{paper_path}/archive",
                json={"expected_version": 2, "reason": "Reviewer cannot archive."},
                headers=REVIEWER_HEADERS,
            ).status_code
            == 403
        )
        archive_body = {"expected_version": 2, "reason": "Retired after publication review."}
        archived = client.post(
            f"{paper_path}/archive",
            json=archive_body,
            headers=ADMIN_HEADERS,
        )
        duplicate_archive = client.post(
            f"{paper_path}/archive",
            json=archive_body,
            headers=ADMIN_HEADERS,
        )
        changed_archive = client.post(
            f"{paper_path}/archive",
            json={**archive_body, "reason": "Changed terminal reason."},
            headers=ADMIN_HEADERS,
        )
        assert archived.status_code == duplicate_archive.status_code == 200
        assert archived.json()["deduplicated"] is False
        assert duplicate_archive.json()["deduplicated"] is True
        assert changed_archive.status_code == 409
        assert changed_archive.json()["detail"]["code"] == "paper_idempotency_conflict"
        stored_archive = client.get(
            f"{paper_path}/archive",
            headers=REVIEWER_HEADERS,
        )
        assert stored_archive.json()["reason"] == archive_body["reason"]
        terminal_revision = client.post(
            f"{paper_path}/revisions",
            json={"expected_version": 2, "candidate_ids": [str(candidate_id)]},
            headers=REVIEWER_HEADERS,
        )
        terminal_publish = client.post(
            f"{paper_path}/publish",
            json={"expected_version": 2},
            headers=ADMIN_HEADERS,
        )
        assert terminal_revision.status_code == terminal_publish.status_code == 409
        assert client.get(paper_path, headers=REVIEWER_HEADERS).json()["state"] == "archived"
        assert len(cast(DeterministicGenerationDispatcher, dispatcher).dispatched) == (
            provider_dispatch_count
        )

    async def verify_database() -> None:
        engine = create_async_engine(seed.database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as session:
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(PracticePaperModel)
                        .where(PracticePaperModel.id == paper_id)
                    )
                    == 1
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(PaperDraftVersionModel)
                        .where(PaperDraftVersionModel.paper_id == paper_id)
                    )
                    == 2
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(PublishedPaperVersionModel)
                        .where(PublishedPaperVersionModel.paper_id == paper_id)
                    )
                    == 2
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(PaperArchiveEventModel)
                        .where(PaperArchiveEventModel.paper_id == paper_id)
                    )
                    == 1
                )
                actions = tuple(
                    await session.scalars(
                        select(AdminAuditEventModel.action)
                        .where(
                            AdminAuditEventModel.resource_type == "practice_paper",
                            AdminAuditEventModel.resource_id == paper_id,
                        )
                        .order_by(AdminAuditEventModel.created_at, AdminAuditEventModel.id)
                    )
                )
                assert actions.count("practice_paper.created") == 1
                assert actions.count("practice_paper.revised") == 1
                assert actions.count("practice_paper.published") == 2
                assert actions.count("practice_paper.archived") == 1

                publication_model = await session.get(PublishedPaperVersionModel, (paper_id, 1))
                assert publication_model is not None
                with pytest.raises(IntegrityError):
                    await session.execute(
                        update(PublishedPaperVersionModel)
                        .where(
                            PublishedPaperVersionModel.paper_id == paper_id,
                            PublishedPaperVersionModel.version == 1,
                        )
                        .values(content_hash="0" * 64)
                    )
                await session.rollback()
                with pytest.raises(IntegrityError):
                    await session.execute(
                        text(
                            "DELETE FROM published_paper_versions "
                            "WHERE paper_id = :paper_id AND version = 1"
                        ),
                        {"paper_id": paper_id},
                    )
                await session.rollback()
        finally:
            await engine.dispose()

    asyncio.run(verify_database())


@pytest.mark.integration
def test_paper_selection_and_database_triggers_reject_unapproved_stale_foreign_and_forged_data(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed = cast(GenerationSeed, request.getfixturevalue("generation_seed"))
    approved_id, dispatcher, runtime = create_approved_candidate(
        seed,
        key="paper-guard-approved",
        stem="Seven drums stand beside eight flags; which answer records the combined count?",
    )
    validation_id, validated_id, validated_dispatcher, validated_runtime = (
        create_passing_validation(
            seed,
            key="paper-guard-validated",
            stem="Five birds rest near six nests; which option gives the combined count?",
        )
    )
    with api_client(seed, validated_dispatcher, runtime=validated_runtime) as client:
        validated = client.post(
            review_candidate_path(seed),
            json={"validation_run_id": str(validation_id)},
            headers=REVIEWER_HEADERS,
        )
        assert validated.status_code == 201
        assert validated.json()["state"] == "validated"

    rejected_validation_id, rejected_id, rejected_dispatcher, rejected_runtime = (
        create_passing_validation(
            seed,
            key="paper-guard-rejected",
            stem="Twelve shells are shown near two stones; which total is displayed?",
        )
    )
    with api_client(seed, rejected_dispatcher, runtime=rejected_runtime) as client:
        rejected = client.post(
            review_candidate_path(seed),
            json={"validation_run_id": str(rejected_validation_id)},
            headers=REVIEWER_HEADERS,
        )
        assert rejected.status_code == 201
        assert (
            client.post(
                f"{review_candidate_path(seed)}/{rejected_id}/start-review",
                json={"expected_version": 2},
                headers=REVIEWER_HEADERS,
            ).status_code
            == 200
        )
        terminal = client.post(
            f"{review_candidate_path(seed)}/{rejected_id}/reject",
            json={"expected_version": 3, "reason": "Answer is not adequately supported."},
            headers=REVIEWER_HEADERS,
        )
        assert terminal.status_code == 200
        assert terminal.json()["state"] == "rejected"

    with api_client(seed, dispatcher, runtime=runtime) as client:
        for index, candidate_id in enumerate((validated_id, rejected_id)):
            blocked = client.post(
                paper_draft_path(),
                json={
                    "paper_blueprint_id": str(seed.paper_blueprint_id),
                    "title": "Blocked candidate paper",
                    "candidate_ids": [str(candidate_id)],
                },
                headers={
                    **REVIEWER_HEADERS,
                    "Idempotency-Key": f"paper-guard-blocked-{index}",
                },
            )
            assert blocked.status_code == 422
            assert blocked.json()["detail"]["code"] == "paper_candidate_selection_invalid"

        missing_candidate = client.post(
            paper_draft_path(),
            json={
                "paper_blueprint_id": str(seed.paper_blueprint_id),
                "title": "Foreign candidate paper",
                "candidate_ids": [str(UUID(int=998_991))],
            },
            headers={**REVIEWER_HEADERS, "Idempotency-Key": "paper-guard-missing"},
        )
        assert missing_candidate.status_code == 422
        wrong_blueprint = client.post(
            paper_draft_path(),
            json={
                "paper_blueprint_id": str(UUID(int=998_992)),
                "title": "Wrong blueprint paper",
                "candidate_ids": [str(approved_id)],
            },
            headers={**REVIEWER_HEADERS, "Idempotency-Key": "paper-guard-blueprint"},
        )
        assert wrong_blueprint.status_code == 404

        source_limit = publication_repository_module.MAX_PAPER_SELECTION_SOURCE_BYTES
        monkeypatch.setattr(
            publication_repository_module,
            "MAX_PAPER_SELECTION_SOURCE_BYTES",
            1,
        )
        oversized = client.post(
            paper_draft_path(),
            json={
                "paper_blueprint_id": str(seed.paper_blueprint_id),
                "title": "Resource bounded paper",
                "candidate_ids": [str(approved_id)],
            },
            headers={**REVIEWER_HEADERS, "Idempotency-Key": "paper-guard-resource-limit"},
        )
        monkeypatch.setattr(
            publication_repository_module,
            "MAX_PAPER_SELECTION_SOURCE_BYTES",
            source_limit,
        )
        assert oversized.status_code == 422
        assert oversized.json()["detail"]["code"] == "paper_candidate_selection_too_large"

        valid = client.post(
            paper_draft_path(),
            json={
                "paper_blueprint_id": str(seed.paper_blueprint_id),
                "title": "Database guard paper",
                "candidate_ids": [str(approved_id)],
            },
            headers={**REVIEWER_HEADERS, "Idempotency-Key": "paper-guard-valid"},
        )
        assert valid.status_code == 201
        valid_paper_id = UUID(valid.json()["paper_id"])

    async def verify_bypass_guards() -> None:
        engine = create_async_engine(seed.database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as session:
                blueprint = await session.get(PaperBlueprintModel, seed.paper_blueprint_id)
                assert blueprint is not None
                blueprint_identity = blueprint.blueprint_id

                async def reject(operation: Any) -> None:
                    with pytest.raises(IntegrityError):
                        await operation()
                    await session.rollback()

                await reject(
                    lambda: session.execute(
                        update(PaperDraftCandidateModel)
                        .where(
                            PaperDraftCandidateModel.paper_id == valid_paper_id,
                            PaperDraftCandidateModel.paper_version == 1,
                        )
                        .values(candidate_version=3)
                    )
                )
                await reject(
                    lambda: session.execute(
                        update(PaperDraftCandidateModel)
                        .where(
                            PaperDraftCandidateModel.paper_id == valid_paper_id,
                            PaperDraftCandidateModel.paper_version == 1,
                        )
                        .values(blueprint_slot_id="forged-slot")
                    )
                )
                await reject(
                    lambda: session.execute(
                        update(PracticePaperModel)
                        .where(PracticePaperModel.id == valid_paper_id)
                        .values(state="archived")
                    )
                )

                async def prepare_manual(paper_id: UUID, suffix: str) -> None:
                    session.add(
                        PracticePaperModel(
                            id=paper_id,
                            curriculum_version_id=CURRICULUM_VERSION_ID,
                            paper_blueprint_id=seed.paper_blueprint_id,
                            blueprint_id=blueprint_identity,
                            blueprint_version=blueprint_identity,
                            state="draft",
                            current_version=1,
                            idempotency_key_hash="sha256:" + suffix * 64,
                            create_request_fingerprint="sha256:" + suffix * 64,
                            created_by=UUID(int=998_001),
                            updated_by=UUID(int=998_001),
                        )
                    )
                    await session.flush()
                    session.add(
                        PaperDraftVersionModel(
                            paper_id=paper_id,
                            curriculum_version_id=CURRICULUM_VERSION_ID,
                            version=1,
                            title="Direct SQL bypass attempt",
                            supersedes_content_hash=None,
                            created_by=UUID(int=998_001),
                        )
                    )
                    await session.flush()

                invalid_cases = (
                    (validated_id, 2, 1, seed.slot_id, "a"),
                    (rejected_id, 4, 1, seed.slot_id, "b"),
                    (approved_id, 3, 1, seed.slot_id, "c"),
                    (approved_id, 4, 1, "forged-slot", "d"),
                )
                for index, (candidate_id, version, revision, slot_id, suffix) in enumerate(
                    invalid_cases
                ):
                    manual_id = UUID(int=998_100 + index)
                    await prepare_manual(manual_id, suffix)
                    session.add(
                        PaperDraftCandidateModel(
                            paper_id=manual_id,
                            curriculum_version_id=CURRICULUM_VERSION_ID,
                            paper_version=1,
                            ordinal=1,
                            blueprint_slot_id=slot_id,
                            candidate_id=candidate_id,
                            candidate_version=version,
                            candidate_revision=revision,
                        )
                    )
                    with pytest.raises(IntegrityError):
                        await session.flush()
                    await session.rollback()

                duplicate_id = UUID(int=998_200)
                await prepare_manual(duplicate_id, "e")
                session.add(
                    PaperDraftCandidateModel(
                        paper_id=duplicate_id,
                        curriculum_version_id=CURRICULUM_VERSION_ID,
                        paper_version=1,
                        ordinal=1,
                        blueprint_slot_id=seed.slot_id,
                        candidate_id=approved_id,
                        candidate_version=4,
                        candidate_revision=1,
                    )
                )
                await session.flush()
                session.add(
                    PaperDraftCandidateModel(
                        paper_id=duplicate_id,
                        curriculum_version_id=CURRICULUM_VERSION_ID,
                        paper_version=1,
                        ordinal=2,
                        blueprint_slot_id=seed.slot_id,
                        candidate_id=approved_id,
                        candidate_version=4,
                        candidate_revision=1,
                    )
                )
                with pytest.raises(IntegrityError):
                    await session.flush()
                await session.rollback()

                forged_id = UUID(int=998_300)
                await prepare_manual(forged_id, "f")
                session.add(
                    PaperDraftCandidateModel(
                        paper_id=forged_id,
                        curriculum_version_id=CURRICULUM_VERSION_ID,
                        paper_version=1,
                        ordinal=1,
                        blueprint_slot_id=seed.slot_id,
                        candidate_id=approved_id,
                        candidate_version=4,
                        candidate_revision=1,
                    )
                )
                await session.flush()
                session.add(
                    PublishedPaperVersionModel(
                        paper_id=forged_id,
                        curriculum_version_id=CURRICULUM_VERSION_ID,
                        version=1,
                        previous_version=None,
                        supersedes_content_hash=None,
                        snapshot={"client": "forged"},
                        content_hash="0" * 64,
                        published_by=UUID(int=998_001),
                    )
                )
                with pytest.raises(IntegrityError):
                    await session.flush()
                await session.rollback()

                foreign_id = UUID(int=998_400)
                await prepare_manual(foreign_id, "9")
                session.add(
                    PaperDraftCandidateModel(
                        paper_id=foreign_id,
                        curriculum_version_id=OTHER_CURRICULUM_ID,
                        paper_version=1,
                        ordinal=1,
                        blueprint_slot_id=seed.slot_id,
                        candidate_id=approved_id,
                        candidate_version=4,
                        candidate_revision=1,
                    )
                )
                with pytest.raises(IntegrityError):
                    await session.flush()
                await session.rollback()
        finally:
            await engine.dispose()

    asyncio.run(verify_bypass_guards())


@pytest.mark.integration
def test_paper_create_idempotency_races_converge_and_changed_requests_conflict(
    request: pytest.FixtureRequest,
) -> None:
    seed = cast(GenerationSeed, request.getfixturevalue("generation_seed"))
    candidate_id, dispatcher, runtime = create_approved_candidate(
        seed,
        key="paper-create-race-approved",
        stem="Nine pencils are beside four books; which answer gives the combined count?",
    )
    body = {
        "paper_blueprint_id": str(seed.paper_blueprint_id),
        "title": "Concurrent paper",
        "candidate_ids": [str(candidate_id)],
    }
    with api_client(seed, dispatcher, runtime=runtime) as client:
        barrier = Barrier(2)

        def create_same() -> tuple[int, dict[str, Any]]:
            barrier.wait()
            response = client.post(
                paper_draft_path(),
                json=body,
                headers={**REVIEWER_HEADERS, "Idempotency-Key": "paper-create-race-same"},
            )
            return response.status_code, response.json()

        with ThreadPoolExecutor(max_workers=2) as executor:
            same_results = tuple(executor.map(lambda _index: create_same(), range(2)))
        assert [status_code for status_code, _payload in same_results] == [201, 201]
        assert len({payload["paper_id"] for _status, payload in same_results}) == 1
        assert sorted(payload["deduplicated"] for _status, payload in same_results) == [False, True]

        conflict_barrier = Barrier(2)

        def create_changed(title: str) -> tuple[int, dict[str, Any]]:
            conflict_barrier.wait()
            response = client.post(
                paper_draft_path(),
                json={**body, "title": title},
                headers={**REVIEWER_HEADERS, "Idempotency-Key": "paper-create-race-conflict"},
            )
            return response.status_code, response.json()

        with ThreadPoolExecutor(max_workers=2) as executor:
            conflict_results = tuple(
                executor.map(create_changed, ("Concurrent paper A", "Concurrent paper B"))
            )
        assert sorted(status_code for status_code, _payload in conflict_results) == [201, 409]
        conflict = next(payload for status_code, payload in conflict_results if status_code == 409)
        assert conflict["detail"]["code"] == "paper_idempotency_conflict"


@pytest.mark.integration
def test_paper_audit_failure_rolls_back_the_aggregate_and_draft(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed = cast(GenerationSeed, request.getfixturevalue("generation_seed"))
    candidate_id, _dispatcher, _runtime = create_approved_candidate(
        seed,
        key="paper-audit-rollback-approved",
        stem="Six flowers are arranged beside five leaves; which answer gives their total?",
    )

    async def exercise() -> None:
        engine = create_async_engine(seed.database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as session:
                papers_before = int(
                    await session.scalar(select(func.count()).select_from(PracticePaperModel)) or 0
                )
                audits_before = int(
                    await session.scalar(
                        select(func.count())
                        .select_from(AdminAuditEventModel)
                        .where(AdminAuditEventModel.resource_type == "practice_paper")
                    )
                    or 0
                )
                service = PaperPublicationService(session)

                def fail_audit(**_values: object) -> None:
                    raise RuntimeError("audit persistence unavailable")

                monkeypatch.setattr(service, "_add_audit", fail_audit)
                with pytest.raises(RuntimeError, match="audit persistence unavailable"):
                    await service.create_draft(
                        CURRICULUM_VERSION_ID,
                        paper_blueprint_id=seed.paper_blueprint_id,
                        title="Rolled back paper",
                        candidate_ids=(candidate_id,),
                        idempotency_key="paper-audit-rollback",
                        principal=Principal(REVIEWER_ID, frozenset({AdminRole.REVIEWER})),
                    )
                assert (
                    int(
                        await session.scalar(select(func.count()).select_from(PracticePaperModel))
                        or 0
                    )
                    == papers_before
                )
                assert (
                    int(
                        await session.scalar(
                            select(func.count())
                            .select_from(AdminAuditEventModel)
                            .where(AdminAuditEventModel.resource_type == "practice_paper")
                        )
                        or 0
                    )
                    == audits_before
                )
        finally:
            await engine.dispose()

    asyncio.run(exercise())

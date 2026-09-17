import asyncio
from typing import Literal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from exam_guru_api.documents.source_consensus import IndependentReading, SourceRegionInput
from exam_guru_api.documents.source_geometry import SourcePageGeometry
from exam_guru_api.documents.source_machine import Purpose, SourceConsensusEngine
from exam_guru_api.documents.source_machine_models import (
    MachineSourceCandidateModel,
    SourceWitnessEventModel,
)
from exam_guru_api.documents.source_reading import SourceLayout, SourceLayoutRegion
from exam_guru_api.documents.source_reading_openai import SourcePassRecorder
from exam_guru_api.documents.source_reading_qwen import QwenSourceReadConfig
from exam_guru_api.documents.source_verification_models import VerifiedSourceContentModel
from exam_guru_api.documents.understanding_contracts import RegionBounds
from exam_guru_api.documents.understanding_jobs import (
    UnderstandingJobService,
    run_understanding_job,
)
from exam_guru_api.documents.understanding_models import TrustedPageKnowledgeModel
from exam_guru_api.documents.understanding_provider import (
    UnderstandingProviderProfile,
    UnderstandingProviderResult,
    UnderstandingRequest,
)
from exam_guru_api.documents.understanding_runtime import (
    PreparedUnderstandingInput,
    UnderstandingRuntime,
)
from exam_guru_api.generation.domain import GenerationAccounting
from tests.integration.test_document_understanding_postgres import page_input
from tests.integration.test_fidelity_workspace_postgres import (
    ADMIN,
    REVIEWER_HEADERS,
    database_session,
)
from tests.integration.test_fidelity_workspace_postgres import (
    workspace_database_url as workspace_database_url,
)
from tests.integration.test_understanding_api import client as client
from tests.test_source_consensus import reading
from tests.test_source_reading_openai import request as reading_request

pytestmark = pytest.mark.integration


class RecordedFixture:
    def __init__(self, recorder: SourcePassRecorder | None = None) -> None:
        self.recorder = recorder

    def with_recorder(self, recorder: SourcePassRecorder) -> "RecordedFixture":
        return RecordedFixture(recorder)

    def understand(self, request: UnderstandingRequest) -> UnderstandingProviderResult:
        recorder = self.recorder
        qwen = request.profile.qwen
        assert recorder is not None
        assert qwen is not None
        region = SourceLayoutRegion(
            key="text",
            kind="paragraph",
            reading_order=0,
            parent_key=None,
            bounds=RegionBounds(left=0.0, top=0.0, right=1.0, bottom=1.0),
        )

        def image(region: SourceLayoutRegion, purpose: Purpose, dpi: int) -> SourceRegionInput:
            return SourceRegionInput(
                source=request.source,
                region=region,
                purpose=purpose,
                language_hint="si",
                image_png=request.image_png,
                image_sha256=request.source.image_sha256,
                render_dpi=dpi,
            )

        def witness(
            name: Literal["qwen", "openai"], source: SourceRegionInput
        ) -> IndependentReading:
            value = reading(source, name, "මව්බස")
            identity = value.reader.model_copy(
                update={
                    "configuration_fingerprint": qwen.fingerprint
                    if name == "qwen"
                    else request.profile.fingerprint
                }
            )
            value = value.model_copy(update={"reader": identity})
            number = 0 if name == "qwen" else 1
            base = {
                "schema_version": "source-witness-event.v1",
                "pass_number": number,
                "reader": name,
                "input": source.model_dump(mode="json"),
                "input_fingerprint": source.fingerprint,
                "reader_configuration_fingerprint": identity.configuration_fingerprint,
            }
            for event in ("requested", "provider_completed", "parsed"):
                recorder(
                    {
                        **base,
                        "event": event,
                        **(
                            {
                                "reading": value.model_dump(mode="json"),
                                "reading_fingerprint": value.fingerprint,
                            }
                            if event == "parsed"
                            else {}
                        ),
                    }
                )
            return value

        geometry = SourcePageGeometry(
            image_sha256=request.source.image_sha256,
            dpi=300,
            width=request.image_dimensions[0],
            height=request.image_dimensions[1],
            tables=(),
            regions=(region,),
            unassigned_ink_pixels=0,
            findings=(),
        )
        machine = SourceConsensusEngine(max_rereads=0).read_page(
            request.source,
            SourceLayout(
                schema_version="source-layout.v1",
                language="si",
                regions=(region,),
                relationships=(),
            ),
            geometry,
            image,
            lambda source: witness("qwen", source),
            lambda source: witness("openai", source),
        )
        return UnderstandingProviderResult(
            source=request.source,
            profile=request.profile,
            content=machine.content.as_legacy_envelope(),
            machine=machine,
            accounting=GenerationAccounting(
                input_tokens=10,
                output_tokens=10,
                total_tokens=20,
                cost_microusd=request.profile.cost_microusd(10, 10),
                latency_ms=1,
            ),
        )


def test_independent_witnesses_and_machine_candidate_persist_without_creating_trust(
    workspace_database_url: str, client: TestClient
) -> None:
    async def check() -> tuple[UUID, UUID]:
        async with database_session(workspace_database_url) as session:
            base, _, metadata = await page_input(session)
            template = reading_request()
            profile = UnderstandingProviderProfile.model_validate(
                {
                    **template.profile.model_dump(),
                    "prompt_version": "qwen-openai-source-consensus.v1",
                    "qwen": QwenSourceReadConfig(model_digest="a" * 64),
                }
            )
            request = UnderstandingRequest(
                source=base.source,
                image_png=base.image_png,
                profile=profile,
                budget=template.budget,
            )
            runtime = UnderstandingRuntime(profile, request.budget, RecordedFixture())
            job = await UnderstandingJobService(session).create(
                principal=ADMIN,
                request_id=uuid4(),
                document_id=request.source.document_id,
                page_number=1,
                expected_version=0,
                runtime=runtime,
                reason="Synthetic independent-witness persistence regression",
            )
            finished = await run_understanding_job(
                session,
                job.id,
                runtime=runtime,
                input_factory=lambda _: PreparedUnderstandingInput(request, metadata),
            )
            assert finished.status == "succeeded"
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(SourceWitnessEventModel)
                    .where(SourceWitnessEventModel.job_id == job.id)
                )
                == 6
            )
            machine = await session.scalar(
                select(MachineSourceCandidateModel).where(
                    MachineSourceCandidateModel.job_id == job.id
                )
            )
            assert machine is not None
            assert machine.payload["state"] == "machine_ready"
            assert machine.payload["human_verified"] is False
            assert (
                await session.scalar(select(func.count()).select_from(VerifiedSourceContentModel))
                == 0
            )
            assert (
                await session.scalar(select(func.count()).select_from(TrustedPageKnowledgeModel))
                == 0
            )
            await session.rollback()
            with pytest.raises(DBAPIError):
                await session.execute(
                    text("UPDATE source_machine_candidates SET payload=payload WHERE job_id=:job"),
                    {"job": job.id},
                )
            await session.rollback()
            return request.source.document_id, job.id

    document_id, job_id = asyncio.run(check())
    url = f"/api/v1/admin/materials/{document_id}/pages/1/understanding"
    first = client.get(url, headers=REVIEWER_HEADERS)
    assert first.status_code == 200
    assert first.json()["machine"]["state"] == "machine_ready"
    assert first.json()["verified_source"] is None
    assert first.json()["source_status"] == "source_fidelity_needs_review"
    assert client.get(url, headers=REVIEWER_HEADERS).json()["machine"] == first.json()["machine"]
    evidence = client.get(
        f"/api/v1/admin/materials/understanding/jobs/{job_id}/witnesses", headers=REVIEWER_HEADERS
    )
    assert evidence.status_code == 200
    assert len(evidence.json()["items"]) == 6
    assert evidence.headers["Cache-Control"] == "private, no-store"

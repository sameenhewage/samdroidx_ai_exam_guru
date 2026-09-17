import asyncio
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from exam_guru_api.documents.source_reading_openai import OpenAISourceReadingProvider
from exam_guru_api.documents.source_verification_models import (
    SourceReadingEventModel,
    VerifiedSourceContentModel,
)
from exam_guru_api.documents.understanding_jobs import (
    UnderstandingJobService,
    run_understanding_job,
)
from exam_guru_api.documents.understanding_models import TrustedPageKnowledgeModel
from exam_guru_api.documents.understanding_provider import UnderstandingRequest
from exam_guru_api.documents.understanding_runtime import (
    PreparedUnderstandingInput,
    UnderstandingRuntime,
)
from tests.integration.test_document_understanding_postgres import page_input
from tests.integration.test_fidelity_workspace_postgres import ADMIN, database_session
from tests.integration.test_fidelity_workspace_postgres import (
    workspace_database_url as workspace_database_url,
)
from tests.test_document_understanding_openai import configuration
from tests.test_source_reading_openai import client_for, layout, region
from tests.test_source_reading_openai import request as reading_request

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("malformed_region", [False, True])
def test_each_source_pass_is_durable_and_no_provider_result_creates_verification(
    workspace_database_url: str,
    malformed_region: bool,
) -> None:
    async def check() -> None:
        async with database_session(workspace_database_url) as session:
            base, _result, metadata = await page_input(session)
            template = reading_request()
            request = UnderstandingRequest(
                source=base.source,
                image_png=base.image_png,
                profile=template.profile,
                budget=template.budget,
            )
            replies = (
                [layout(), {"education": {"claims": []}}]
                if malformed_region
                else [layout(), region("මව්බස"), region("info@nie.lk")]
            )
            sent: list[dict[str, Any]] = []
            with client_for(replies, sent, request) as client:
                provider = OpenAISourceReadingProvider(configuration(request), client=client)
                runtime = UnderstandingRuntime(request.profile, request.budget, provider)
                job = await UnderstandingJobService(session).create(
                    principal=ADMIN,
                    request_id=uuid4(),
                    document_id=request.source.document_id,
                    page_number=1,
                    expected_version=0,
                    runtime=runtime,
                    reason="Synthetic source-reading journal regression",
                )
                finished = await run_understanding_job(
                    session,
                    job.id,
                    runtime=runtime,
                    input_factory=lambda _: PreparedUnderstandingInput(request, metadata),
                )
            assert finished.status == ("failed" if malformed_region else "succeeded")
            assert finished.accounting is not None
            assert finished.accounting.input_tokens == (200 if malformed_region else 300)
            events = list(
                (
                    await session.scalars(
                        select(SourceReadingEventModel)
                        .where(
                            SourceReadingEventModel.job_id == job.id,
                        )
                        .order_by(
                            SourceReadingEventModel.pass_number, SourceReadingEventModel.event
                        )
                    )
                ).all()
            )
            assert len(events) == (4 if malformed_region else 6)
            assert all(
                cast(list[dict[str, object]], e.payload["images"])[0]["parent_sha256"]
                == request.source.image_sha256
                for e in events
            )
            completed = [e for e in events if e.event == "provider_completed"]
            assert all(e.payload["response_text"] for e in completed)
            assert completed[-1].payload["valid_structure"] is (not malformed_region)
            assert (
                await session.scalar(select(func.count()).select_from(VerifiedSourceContentModel))
                == 0
            )
            assert (
                await session.scalar(select(func.count()).select_from(TrustedPageKnowledgeModel))
                == 0
            )

    asyncio.run(check())

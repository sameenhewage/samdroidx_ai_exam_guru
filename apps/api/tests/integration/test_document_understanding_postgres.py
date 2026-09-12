import asyncio

import pytest
from sqlalchemy import text

from exam_guru_api.documents.understanding_contracts import _canonical_bytes
from tests.integration.test_fidelity_workspace_postgres import database_session
from tests.integration.test_fidelity_workspace_postgres import (
    workspace_database_url as workspace_database_url,
)
from tests.test_document_understanding_contracts import counting_candidate, parse

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("coordinate", [0.1, 0.0000001, -0.0])
def test_structured_source_fingerprints_have_the_same_canonical_bytes_in_postgres(
    workspace_database_url: str, coordinate: float
) -> None:
    async def compare() -> None:
        payload = counting_candidate()
        payload["observation"]["regions"][0]["bounds"]["left"] = coordinate
        candidate = parse(payload)
        async with database_session(workspace_database_url) as session:
            canonical = await session.scalar(
                text("SELECT public.paper_canonical_jsonb(CAST(:payload AS jsonb))"),
                {"payload": candidate.model_dump_json()},
            )
        assert _canonical_bytes(candidate).decode("utf-8") == canonical

    asyncio.run(compare())

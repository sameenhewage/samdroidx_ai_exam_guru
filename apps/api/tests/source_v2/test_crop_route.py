"""The crop endpoint over HTTP: same integrity contract as the page render.

Driven through the real router with the repository's disk and database access
faked, so what is proved here is the HTTP contract — the media type, the
checksum header, the privacy headers, and above all that a checksum mismatch
is a refusal rather than a body nobody will look at twice.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from exam_guru_api.api.dependencies import get_database_session
from exam_guru_api.api.routes.source_v2 import router
from exam_guru_api.auth.api import get_current_principal
from exam_guru_api.auth.domain import AdminRole, Principal
from exam_guru_api.core.config import Settings
from exam_guru_api.source_v2 import repository
from exam_guru_api.source_v2.repository import CropNotFoundError, PageHeader, PageNotFoundError

PAGE_ID = UUID("00000000-0000-0000-0000-0000000001b6")
PNG = b"\x89PNG\r\n\x1a\ncanonical-crop-bytes"
CROP = hashlib.sha256(PNG).hexdigest()
ADMIN = Principal(subject_id=uuid4(), roles=frozenset({AdminRole.ADMIN}))


def page_header() -> PageHeader:
    return PageHeader(
        page_id=PAGE_ID,
        document_id=uuid4(),
        page_number=186,
        image_sha256="a" * 64,
        width=2480,
        height=3509,
        dpi=300.0,
        language="sinhala",
        detector_version="test",
    )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Callable[..., TestClient]:
    def build(
        *,
        crop_sha256: str | None = CROP,
        on_disk: bytes | None = PNG,
    ) -> TestClient:
        async def get_page(_session: AsyncSession, page_id: UUID) -> PageHeader:
            if page_id != PAGE_ID:
                raise PageNotFoundError(f"unknown Source V2 page {page_id}")
            return page_header()

        async def region_crop_sha256(_session: AsyncSession, _page: UUID, region: str) -> str:
            if crop_sha256 is None:
                raise CropNotFoundError(f"region {region} has no canonical crop")
            return crop_sha256

        def crop_bytes(_page: PageHeader, region: str, expected: str, **_: object) -> bytes:
            # Exactly the repository's rule, without a filesystem: the bytes
            # are served only when they hash to what the region recorded.
            if on_disk is None or hashlib.sha256(on_disk).hexdigest() != expected:
                raise CropNotFoundError(f"no crop matching {expected[:12]}… for region {region}")
            return on_disk

        monkeypatch.setattr(repository, "get_page", get_page)
        monkeypatch.setattr(repository, "region_crop_sha256", region_crop_sha256)
        monkeypatch.setattr(repository, "crop_bytes", crop_bytes)

        app = FastAPI()
        app.include_router(router, prefix="/api/v1/admin")
        app.state.settings = Settings(_env_file=None, environment="test")

        async def session() -> AsyncSession:
            return cast(AsyncSession, object())

        async def identity() -> Principal:
            return ADMIN

        app.dependency_overrides[get_database_session] = session
        app.dependency_overrides[get_current_principal] = identity
        return TestClient(app)

    return build


def url(region: str = "p186-r002") -> str:
    return f"/api/v1/admin/source-v2/pages/{PAGE_ID}/regions/{region}/crop"


def test_the_crop_is_served_as_png_naming_the_checksum_it_was_verified_against(
    client: Callable[..., TestClient],
) -> None:
    response = client().get(url())
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["X-Crop-Sha256"] == CROP
    assert response.content == PNG


def test_the_crop_response_carries_the_same_private_headers_as_the_render(
    client: Callable[..., TestClient],
) -> None:
    response = client().get(url())
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Cross-Origin-Resource-Policy"] == "same-origin"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_a_checksum_mismatch_is_refused_rather_than_served(
    client: Callable[..., TestClient],
) -> None:
    """Different pixels under the same name is the failure this prevents."""

    response = client(on_disk=b"different pixels entirely").get(url())
    assert response.status_code == 404
    assert "no crop matching" in response.json()["detail"]


def test_a_region_with_no_recorded_crop_is_refused(
    client: Callable[..., TestClient],
) -> None:
    response = client(crop_sha256=None).get(url())
    assert response.status_code == 404
    assert "no canonical crop" in response.json()["detail"]


def test_an_unknown_page_is_refused(client: Callable[..., TestClient]) -> None:
    response = client().get(f"/api/v1/admin/source-v2/pages/{uuid4()}/regions/p186-r002/crop")
    assert response.status_code == 404

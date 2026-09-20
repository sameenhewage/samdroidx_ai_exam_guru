"""Serving the canonical crop, under the same integrity contract as the render.

D17 makes `<document>/crops/crop-NNN-rNNN.png` the only image a region may be
read from — by the agent transcribing it and by the reviewer confirming it.
Serving anything else under that name would let someone confirm a reading
against pixels it never came from, which looks identical to agreement.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

import pytest

from exam_guru_api.source_v2.repository import (
    CropNotFoundError,
    PageHeader,
    crop_bytes,
    crop_filename,
)

PNG = b"\x89PNG\r\n\x1a\nnot-a-real-encoder-just-bytes"
OTHER = b"\x89PNG\r\n\x1a\ndifferent-pixels-entirely"


def header(page_number: int = 186) -> PageHeader:
    return PageHeader(
        page_id=uuid4(),
        document_id=uuid4(),
        page_number=page_number,
        image_sha256="a" * 64,
        width=2480,
        height=3509,
        dpi=300.0,
        language="sinhala",
        detector_version="test",
    )


def write_crop(root: Path, name: str, payload: bytes) -> Path:
    crops = root / "grade-05" / "sinhala" / "mawbasa-teacher-guide" / "crops"
    crops.mkdir(parents=True, exist_ok=True)
    path = crops / name
    path.write_bytes(payload)
    return path


def test_the_crop_filename_follows_the_page_and_the_region() -> None:
    assert crop_filename(186, "p186-r002") == "crop-186-r002.png"
    assert crop_filename(7, "p007-r000") == "crop-007-r000.png"


def test_the_matching_crop_is_returned(tmp_path: Path) -> None:
    write_crop(tmp_path, "crop-186-r002.png", PNG)
    digest = hashlib.sha256(PNG).hexdigest()
    assert crop_bytes(header(), "p186-r002", digest, root=tmp_path) == PNG


def test_a_checksum_mismatch_is_refused_rather_than_served(tmp_path: Path) -> None:
    """The file is there and readable. It is simply not this region's crop."""

    write_crop(tmp_path, "crop-186-r002.png", OTHER)
    digest = hashlib.sha256(PNG).hexdigest()
    with pytest.raises(CropNotFoundError) as error:
        crop_bytes(header(), "p186-r002", digest, root=tmp_path)
    assert digest[:12] in str(error.value)


def test_a_missing_crop_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CropNotFoundError):
        crop_bytes(header(), "p186-r002", hashlib.sha256(PNG).hexdigest(), root=tmp_path)


def test_a_region_with_no_recorded_crop_is_refused(tmp_path: Path) -> None:
    write_crop(tmp_path, "crop-186-r002.png", PNG)
    with pytest.raises(CropNotFoundError, match="no canonical crop"):
        crop_bytes(header(), "p186-r002", "", root=tmp_path)


def test_the_crop_of_another_page_is_never_served(tmp_path: Path) -> None:
    """The page row owns the page number, not the region id."""

    write_crop(tmp_path, "crop-156-r002.png", PNG)
    with pytest.raises(CropNotFoundError):
        crop_bytes(header(186), "p186-r002", hashlib.sha256(PNG).hexdigest(), root=tmp_path)


def test_the_crop_survives_being_read_twice(tmp_path: Path) -> None:
    """Extraction and confirmation never consume the original image."""

    path = write_crop(tmp_path, "crop-186-r002.png", PNG)
    digest = hashlib.sha256(PNG).hexdigest()
    assert crop_bytes(header(), "p186-r002", digest, root=tmp_path) == PNG
    assert crop_bytes(header(), "p186-r002", digest, root=tmp_path) == PNG
    assert path.read_bytes() == PNG

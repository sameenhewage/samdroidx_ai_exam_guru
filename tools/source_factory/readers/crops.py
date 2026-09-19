"""Cut real region crops out of the fixed benchmark pages.

Readers are benchmarked on the *same* crops, produced deterministically from the
committed layout, so a difference between two readers is a difference in reading
and not in framing.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from tools.source_factory.layout.corpus import Document
from tools.source_factory.layout.detect import detect_layout
from tools.source_factory.layout.model import Box

READABLE_TYPES = ("text", "heading", "table")
CROP_PAD = 8  # a reader needs a little paper around the ink


@dataclass(frozen=True)
class Crop:
    crop_id: str
    document_id: str
    page_number: int
    region_id: str
    region_type: str
    bbox: list[int]
    path: Path
    sha256: str

    def to_json(self) -> dict:
        return {
            "crop_id": self.crop_id,
            "document_id": self.document_id,
            "page_number": self.page_number,
            "region_id": self.region_id,
            "region_type": self.region_type,
            "bbox": self.bbox,
            "file": self.path.name,
            "sha256": self.sha256,
        }


def read_page_image(path: Path) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise SystemExit(f"cannot decode page image: {path}")
    return image


def write_image(path: Path, image: np.ndarray) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, buffer = cv2.imencode(".png", image)
    if not ok:
        raise SystemExit(f"cannot encode crop: {path}")
    payload = buffer.tobytes()
    path.write_bytes(payload)
    return payload


def extract(
    document: Document,
    page_numbers: list[int],
    *,
    types: tuple[str, ...] = READABLE_TYPES,
    min_lines: int = 1,
) -> list[Crop]:
    root = document.folder / "readers" / "crops"
    crops: list[Crop] = []
    for page_number in page_numbers:
        page = document.page(page_number)
        image = read_page_image(page.path)
        layout = detect_layout(
            image,
            document_id=document.document_id,
            page_number=page_number,
            dpi=document.dpi,
            image_sha256=page.sha256,
        )
        bounds = Box(0, 0, image.shape[1], image.shape[0])
        for region in layout.regions:
            if region.type not in types or region.line_count < min_lines:
                continue
            box = region.bbox.pad(CROP_PAD, bounds)
            patch = image[box.y0 : box.y1, box.x0 : box.x1]
            if patch.size == 0:
                continue
            crop_id = f"{page_number:03d}-{region.id.split('-')[-1]}"
            target = root / f"crop-{crop_id}.png"
            payload = write_image(target, patch)
            crops.append(
                Crop(
                    crop_id=crop_id,
                    document_id=document.document_id,
                    page_number=page_number,
                    region_id=region.id,
                    region_type=region.type,
                    bbox=box.as_list(),
                    path=target,
                    sha256=hashlib.sha256(payload).hexdigest(),
                )
            )
    manifest = root / "crops.json"
    manifest.write_text(
        json.dumps([crop.to_json() for crop in crops], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return crops


def load(document: Document) -> list[Crop]:
    root = document.folder / "readers" / "crops"
    manifest = root / "crops.json"
    if not manifest.exists():
        raise SystemExit(f"no crops yet; run the crops command first ({manifest})")
    entries = json.loads(manifest.read_text(encoding="utf-8"))
    return [
        Crop(
            crop_id=entry["crop_id"],
            document_id=entry["document_id"],
            page_number=entry["page_number"],
            region_id=entry["region_id"],
            region_type=entry["region_type"],
            bbox=entry["bbox"],
            path=root / entry["file"],
            sha256=entry["sha256"],
        )
        for entry in entries
    ]

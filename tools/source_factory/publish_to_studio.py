# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx==0.28.1"]
# ///
"""Hand a rendered, read document to the Studio over the API.

    uv run tools/source_factory/publish_to_studio.py --document <folder> [--refresh]

Uploads the original PDF if the Studio does not have it yet, then posts each
page's layout, reader evidence and Machine Candidates to
`POST /admin/source-v2/pages`. Nothing published here is verified: every region
still has to be decided by a person against the original page.

This replaces writing to the database directly, so the Studio API is the only
way source content enters the system.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

DEFAULT_API = "http://127.0.0.1:8000/api/v1/admin"
DEFAULT_TOKEN = "exam-guru-admin-local-token"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def reader_rows(folder: Path, page_number: int) -> list[dict]:
    """Per-reader measured rows for this page, flattened for the API."""

    prefix = f"{page_number:03d}-"
    rows: list[dict] = []
    for path in sorted((folder / "readers" / "results").glob("*.json")):
        report = load(path)
        for row in report.get("crops", []):
            if not row["crop_id"].startswith(prefix):
                continue
            rows.append(
                {
                    # crop ids are "002-r001"; region ids are "p002-r001".
                    "region_id": f"p{page_number:03d}-{row['crop_id'].split('-')[-1]}",
                    "reader": report["reader"],
                    "text": row.get("text", ""),
                    "abstained": bool(row.get("abstained")),
                    "failure": row.get("failure"),
                    "seconds": float(row.get("seconds", 0.0)),
                    "signals": {
                        key: row[key]
                        for key in (
                            "repetition",
                            "structural_repetition",
                            "foreign_script",
                            "expected_script",
                        )
                        if row.get(key) is not None
                    },
                }
            )
    return rows


def upload_original(client, folder: Path, manifest: dict) -> str:
    original = folder / "source" / "original.pdf"
    response = client.post(
        "/source-documents",
        files={"file": (f"{manifest['document_id']}.pdf", original.read_bytes(), "application/pdf")},
        data={"document_type": manifest.get("document_type", "teacher_guide")},
    )
    if response.status_code == 409:
        # Already uploaded: the checksum identifies it.
        existing = response.json().get("detail", {})
        raise SystemExit(f"already uploaded; pass --document-id. detail={existing}")
    response.raise_for_status()
    return response.json()["id"]


def main() -> int:
    import httpx

    parser = argparse.ArgumentParser(description="Publish a read document to the Studio")
    parser.add_argument("--document", type=Path, required=True)
    parser.add_argument("--document-id", default=None, help="skip the upload and reuse this id")
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--token", default=DEFAULT_TOKEN)
    parser.add_argument("--language", default="sinhala")
    parser.add_argument("--refresh", action="store_true")
    arguments = parser.parse_args()

    folder = arguments.document.resolve()
    manifest = load(folder / "manifest.json")
    candidate_files = sorted((folder / "candidates" / "pages").glob("page-*.json"))
    if not candidate_files:
        raise SystemExit(f"no candidates under {folder}/candidates/pages")

    client = httpx.Client(
        base_url=arguments.api,
        headers={"Authorization": f"Bearer {arguments.token}"},
        timeout=120.0,
    )
    document_id = arguments.document_id or upload_original(client, folder, manifest)

    published = []
    for path in candidate_files:
        page_number = int(path.stem.split("-")[-1])
        layout = load(folder / "layout" / "regions" / f"page-{page_number:03d}.json")
        response = client.post(
            "/source-v2/pages",
            params={"refresh": "true"} if arguments.refresh else None,
            json={
                "document_id": document_id,
                "page_number": page_number,
                "language": arguments.language,
                "image_sha256": layout["image_sha256"],
                "width": layout["width"],
                "height": layout["height"],
                "dpi": layout["dpi"],
                "detector_version": layout["detector_version"],
                "layout": layout,
                "candidates": [
                    {
                        "region_id": region["region_id"],
                        "region_type": region["region_type"],
                        "text": region.get("text", ""),
                        "abstained": bool(region.get("abstained")),
                        "chosen_reader": region.get("chosen_reader"),
                        "reason": (region.get("reason") or "")[:400],
                        "critical_conflict": bool(region.get("critical_conflict")),
                        "agreement_ratio": float(region.get("agreement_ratio", 1.0)),
                        "disagreement": region.get("disagreement", {}),
                    }
                    for region in load(path)["regions"]
                ],
                "reader_results": reader_rows(folder, page_number),
            },
        )
        if response.status_code >= 400:
            raise SystemExit(f"page {page_number}: {response.status_code} {response.text[:400]}")
        published.append(response.json())

    print(json.dumps({"document_id": document_id, "pages": published}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

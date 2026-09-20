# /// script
# requires-python = ">=3.12"
# dependencies = ["psycopg[binary]==3.2.10"]
# ///
"""Load offline Source V2 output into the Studio database.

    uv run tools/source_factory/import_studio.py --pages 156,186 --document-id <uuid>

Moves what the offline pipeline produced — the layout and the Machine
Candidates, one per region, each carrying the single primary reading — into
`source_v2_*`. It never marks anything verified: that is a human act,
performed in the Studio.

Superseded by `publish_to_studio.py`, which goes through the Studio API.
Kept as the direct-database fallback for a Studio that is not running.

Idempotent on (document, page, rendered image sha256). A different render of
the same page number is refused, because a re-render is a new page and
verification must not follow it.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import unicodedata
import uuid
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[2]
STUDIO = (
    REPO
    / ".exam-guru-data"
    / "source-content"
    / "grade-05"
    / "sinhala"
    / "mawbasa-teacher-guide"
)
DSN = (
    "host=127.0.0.1 port=55432 dbname=exam_guru user=exam_guru "
    "password=exam-guru-local-db"
)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def refresh_candidates(cursor, page_id, page_number: int, candidates: list[dict]) -> dict:
    """Supersede the current readings of an already-stored page.

    A re-run of the pipeline is a re-read, not a replacement. The previous
    revision stays, linked as the parent, and any verification of a superseded
    region is withdrawn because the proposed text is no longer the one the
    reviewer looked at. Review history is untouched: it is evidence.
    """

    superseded = 0
    withdrawn = 0
    for region in candidates:
        current = cursor.execute(
            "select id, revision, text from source_v2_machine_candidates"
            " where page_id = %s and region_id = %s and is_current",
            (page_id, region["region_id"]),
        ).fetchone()
        proposed = unicodedata.normalize("NFC", region.get("text", ""))
        if current is None:
            continue
        if current[2] == proposed:
            continue
        cursor.execute(
            "update source_v2_machine_candidates"
            " set is_current = false, state = 'unverified' where id = %s",
            (current[0],),
        )
        removed = cursor.execute(
            "delete from source_v2_verified_regions"
            " where page_id = %s and region_id = %s returning id",
            (page_id, region["region_id"]),
        ).fetchall()
        withdrawn += len(removed)
        cursor.execute(
            """
            insert into source_v2_machine_candidates
              (id, page_id, region_id, region_type, revision, parent_id, origin, text,
               abstained, reason, state, is_current)
            values (%s, %s, %s, %s, %s, %s, 'machine', %s, %s, %s, 'unverified', true)
            """,
            (
                uuid.uuid4(),
                page_id,
                region["region_id"],
                region["region_type"],
                current[1] + 1,
                current[0],
                proposed,
                bool(region.get("abstained")),
                (region.get("reason") or "")[:400],
            ),
        )
        superseded += 1
    return {
        "page": page_number,
        "page_id": str(page_id),
        "refreshed": True,
        "superseded": superseded,
        "verifications_withdrawn": withdrawn,
    }


def import_page(
    cursor, document_id: uuid.UUID, page_number: int, language: str, refresh: bool = False
) -> dict:
    layout = load_json(STUDIO / "layout" / "regions" / f"page-{page_number:03d}.json")
    candidates = load_json(
        STUDIO / "candidates" / "pages" / f"page-{page_number:03d}.json"
    )["regions"]
    sha = layout["image_sha256"]

    existing = cursor.execute(
        "select id, image_sha256 from source_v2_pages"
        " where document_id = %s and page_number = %s",
        (document_id, page_number),
    ).fetchone()
    if existing:
        if existing[1] != sha:
            raise SystemExit(
                f"page {page_number} already stored against render {existing[1][:12]}…; "
                "a re-render is a new page and must not silently replace evidence"
            )
        if not refresh:
            return {"page": page_number, "page_id": str(existing[0]), "reused": True}
        return refresh_candidates(cursor, existing[0], page_number, candidates)

    page_id = uuid.uuid4()
    cursor.execute(
        """
        insert into source_v2_pages
          (id, document_id, page_number, image_sha256, dpi, width, height,
           language, detector_version, layout)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            page_id,
            document_id,
            page_number,
            sha,
            float(layout["dpi"]),
            int(layout["width"]),
            int(layout["height"]),
            language,
            layout["detector_version"],
            json.dumps(layout, ensure_ascii=False),
        ),
    )

    for region in candidates:
        cursor.execute(
            """
            insert into source_v2_machine_candidates
              (id, page_id, region_id, region_type, revision, origin, text, abstained,
               reason, state, is_current)
            values (%s, %s, %s, %s, 1, 'machine', %s, %s, %s, 'unverified', true)
            """,
            (
                uuid.uuid4(),
                page_id,
                region["region_id"],
                region["region_type"],
                unicodedata.normalize("NFC", region.get("text", "")),
                bool(region.get("abstained")),
                (region.get("reason") or "")[:400],
            ),
        )

    return {
        "page": page_number,
        "page_id": str(page_id),
        "regions": len(candidates),
        "reused": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Import Source V2 output into the Studio")
    parser.add_argument("--pages", default="156,186")
    parser.add_argument("--document-id", default=None)
    parser.add_argument("--language", default="sinhala")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="supersede existing readings with new revisions instead of skipping",
    )
    parser.add_argument("--dsn", default=DSN)
    arguments = parser.parse_args()

    import psycopg

    pages = [int(value) for value in arguments.pages.split(",") if value.strip()]
    with psycopg.connect(arguments.dsn, connect_timeout=10) as connection:
        if arguments.document_id:
            document_id = uuid.UUID(arguments.document_id)
        else:
            row = connection.execute("select id from source_documents limit 1").fetchone()
            if row is None:
                raise SystemExit("no source_documents row to attach Source V2 pages to")
            document_id = row[0]
        results = [
            import_page(
                connection,
                document_id,
                page_number,
                arguments.language,
                refresh=arguments.refresh,
            )
            for page_number in pages
        ]
        connection.commit()
    print(json.dumps({"document_id": str(document_id), "pages": results}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

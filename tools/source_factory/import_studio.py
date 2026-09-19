# /// script
# requires-python = ">=3.12"
# dependencies = ["psycopg[binary]==3.2.10"]
# ///
"""Load offline Source V2 output into the Studio database.

    uv run tools/source_factory/import_studio.py --pages 156,186 --document-id <uuid>

Moves what the offline pipeline produced — the layout, the per-reader evidence
and the Machine Candidates — into `source_v2_*`. It never marks anything
verified: that is a human act, performed in the Studio.

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


def reader_rows(page_number: int) -> dict[str, list[dict]]:
    """Per-reader measured rows for this page, keyed by reader name."""

    folder = STUDIO / "readers" / "results"
    prefix = f"{page_number:03d}-"
    evidence: dict[str, list[dict]] = {}
    for path in sorted(folder.glob("*.json")):
        report = load_json(path)
        rows = [row for row in report.get("crops", []) if row["crop_id"].startswith(prefix)]
        if rows:
            evidence[report["reader"]] = [
                {
                    # crop ids are "156-r001"; region ids are "p156-r001".
                    "region_id": f"p{page_number:03d}-{row['crop_id'].split('-')[-1]}",
                    "text": row.get("text", ""),
                    "abstained": bool(row.get("abstained")),
                    "failure": row.get("failure"),
                    "seconds": float(row.get("seconds", 0.0)),
                    "repetition": row.get("repetition"),
                    "structural_repetition": row.get("structural_repetition"),
                    "foreign_script": row.get("foreign_script"),
                }
                for row in rows
            ]
    return evidence


def import_page(cursor, document_id: uuid.UUID, page_number: int, language: str) -> dict:
    layout = load_json(STUDIO / "layout" / "regions" / f"page-{page_number:03d}.json")
    candidates = load_json(STUDIO / "candidates" / f"page-{page_number:03d}.json")["regions"]
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
        return {"page": page_number, "page_id": str(existing[0]), "reused": True}

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

    readers = 0
    for reader, rows in reader_rows(page_number).items():
        for row in rows:
            cursor.execute(
                """
                insert into source_v2_reader_candidates
                  (id, page_id, region_id, reader, text, abstained, failure, seconds, signals)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (page_id, region_id, reader) do nothing
                """,
                (
                    uuid.uuid4(),
                    page_id,
                    row["region_id"],
                    reader,
                    row["text"],
                    row["abstained"],
                    row["failure"],
                    row["seconds"],
                    json.dumps(
                        {
                            key: row[key]
                            for key in ("repetition", "structural_repetition", "foreign_script")
                            if row.get(key) is not None
                        }
                    ),
                ),
            )
            readers += 1

    for region in candidates:
        cursor.execute(
            """
            insert into source_v2_machine_candidates
              (id, page_id, region_id, region_type, revision, origin, text, abstained,
               chosen_reader, reason, critical_conflict, agreement_ratio, disagreement,
               state, is_current)
            values (%s, %s, %s, %s, 1, 'machine', %s, %s, %s, %s, %s, %s, %s,
                    'unverified', true)
            """,
            (
                uuid.uuid4(),
                page_id,
                region["region_id"],
                region["region_type"],
                unicodedata.normalize("NFC", region.get("text", "")),
                bool(region.get("abstained")),
                region.get("chosen_reader"),
                (region.get("reason") or "")[:400],
                bool(region.get("critical_conflict")),
                float(region.get("agreement_ratio", 1.0)),
                json.dumps(region.get("disagreement", {}), ensure_ascii=False),
            ),
        )

    return {
        "page": page_number,
        "page_id": str(page_id),
        "regions": len(candidates),
        "reader_rows": readers,
        "reused": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Import Source V2 output into the Studio")
    parser.add_argument("--pages", default="156,186")
    parser.add_argument("--document-id", default=None)
    parser.add_argument("--language", default="sinhala")
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
            import_page(connection, document_id, page_number, arguments.language)
            for page_number in pages
        ]
        connection.commit()
    print(json.dumps({"document_id": str(document_id), "pages": results}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

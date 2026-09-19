# /// script
# requires-python = ">=3.12"
# dependencies = ["psycopg[binary]==3.2.10"]
# ///
"""Measure every reading against what a human actually confirmed.

    uv run tools/source_factory/benchmark_primary.py --document <folder>

Compares, per region, against the human-confirmed Verified Source Content:

    primary-agent-reading   the executing agent's own visual reading
    sinhala-deepseek        strong secondary witness
    sinhala-lightonocr      weak corroborating witness
    machine-candidate       what the Studio actually proposed

The headline number is the last one: **how often a human had to change the
Machine Candidate**. Everything else is diagnosis. Without this, "the agent
reads better than OCR" is an opinion.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from tools.source_factory.readers.metrics import character_error_rate, edit_counts  # noqa: E402

DSN = (
    "host=127.0.0.1 port=55432 dbname=exam_guru user=exam_guru "
    "password=exam-guru-local-db"
)
PRIMARY = "primary-agent-reading"


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


def verified_by_region(connection, document_id: str) -> dict[str, str]:
    rows = connection.execute(
        """
        select v.region_id, v.text
        from source_v2_verified_regions v
        join source_v2_pages p on p.id = v.page_id
        where p.document_id = %s
        """,
        (document_id,),
    ).fetchall()
    return {region_id: nfc(text) for region_id, text in rows}


def score(reference: str, hypothesis: str) -> dict:
    substitutions, deletions, insertions = edit_counts(reference, hypothesis)
    return {
        "cer": round(character_error_rate(reference, hypothesis), 4),
        "substitutions": substitutions,
        "deletions": deletions,
        "insertions": insertions,
        "exact": reference == hypothesis,
    }


def main() -> int:
    import psycopg

    parser = argparse.ArgumentParser(description="Primary vs local readers vs candidate")
    parser.add_argument("--document", type=Path, required=True)
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--dsn", default=DSN)
    parser.add_argument("--out", type=Path, default=None)
    arguments = parser.parse_args()

    folder = arguments.document.resolve()
    primary: dict[str, str] = {}
    for path in sorted((folder / "primary" / "pages").glob("page-*.json")):
        for region in json.loads(path.read_text(encoding="utf-8"))["regions"]:
            primary[region["region_id"]] = nfc(region["exact_text"])

    readers: dict[str, dict[str, str]] = {}
    for path in sorted((folder / "readers" / "results").glob("*.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        table = {}
        for row in report.get("crops", []):
            page, suffix = row["crop_id"].split("-", 1)
            table[f"p{int(page):03d}-{suffix}"] = nfc(row.get("text", ""))
        readers[report["reader"]] = table

    candidate: dict[str, str] = {}
    for path in sorted((folder / "candidates" / "pages").glob("page-*.json")):
        for region in json.loads(path.read_text(encoding="utf-8"))["regions"]:
            candidate[region["region_id"]] = nfc(region.get("selected_text", ""))

    with psycopg.connect(arguments.dsn, connect_timeout=10) as connection:
        verified = verified_by_region(connection, arguments.document_id)
    if not verified:
        raise SystemExit(
            "no human-confirmed regions for this document; "
            "accuracy cannot be claimed without a reference a person signed off"
        )

    sources = {PRIMARY: primary, "machine-candidate": candidate} | readers
    summary: dict[str, dict] = {}
    per_region: list[dict] = []

    for name, table in sources.items():
        scored = [
            score(reference, table[region_id])
            for region_id, reference in verified.items()
            if region_id in table
        ]
        if not scored:
            continue
        summary[name] = {
            "regions_compared": len(scored),
            "mean_cer": round(sum(item["cer"] for item in scored) / len(scored), 4),
            "exact_regions": sum(1 for item in scored if item["exact"]),
            "substitutions": sum(item["substitutions"] for item in scored),
            "deletions": sum(item["deletions"] for item in scored),
            "insertions": sum(item["insertions"] for item in scored),
        }

    for region_id, reference in sorted(verified.items()):
        row = {"region_id": region_id, "verified_chars": len(reference)}
        for name, table in sources.items():
            if region_id in table:
                row[name] = score(reference, table[region_id])
        per_region.append(row)

    # The headline: how often the human had to change what was proposed.
    proposed = summary.get("machine-candidate", {})
    corrected = proposed.get("regions_compared", 0) - proposed.get("exact_regions", 0)
    same_hand = sorted(
        region_id
        for region_id, reference in verified.items()
        if primary.get(region_id) == reference
    )
    report = {
        "document_id": arguments.document_id,
        "reference": "human-confirmed Verified Source Content",
        "regions_with_reference": len(verified),
        "caveat": (
            "Where the same party produced the primary reading and confirmed it, "
            "the primary and machine-candidate scores are CIRCULAR and say nothing "
            "about accuracy. They show only that the candidate carried the primary "
            "reading into the Studio unmutated. The local-reader scores are the "
            "meaningful comparison, because those readings were produced "
            "independently of the reference. An independent reviewer is required "
            "before any accuracy claim is made for the primary reading."
        ),
        "regions_where_reference_equals_primary": len(same_hand),
        "headline": {
            "machine_candidate_regions": proposed.get("regions_compared", 0),
            "regions_a_human_changed": corrected,
            "human_correction_rate": (
                round(corrected / proposed["regions_compared"], 4)
                if proposed.get("regions_compared")
                else None
            ),
        },
        "summary": summary,
        "regions": per_region,
    }
    target = arguments.out or (folder / "benchmark" / "primary-vs-readers.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(target), **report["headline"], "summary": summary}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

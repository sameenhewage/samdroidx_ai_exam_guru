"""Validate detector output against the committed page-layout contract.

The detector is deterministic geometry, so its output is checked against the
schema every time it is written. A contract that is only checked in tests is a
contract that drifts.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from tools.source_factory.layout.corpus import REPO_ROOT

SCHEMA_PATH = REPO_ROOT / "schemas" / "source-content" / "page-layout.schema.json"


@lru_cache(maxsize=1)
def schema() -> dict:
    if not SCHEMA_PATH.exists():
        raise SystemExit(f"missing layout contract: {SCHEMA_PATH}")
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate(payload: dict) -> None:
    """Raise if the payload does not satisfy the page-layout contract."""

    import jsonschema

    jsonschema.validate(payload, schema())


def check_reading_order(payload: dict) -> None:
    """Reading order must be a dense zero-based sequence over every region."""

    orders = [region["reading_order"] for region in payload.get("regions", [])]
    if orders != list(range(len(orders))):
        raise ValueError(f"reading order is not a dense sequence: {orders}")


def check_parents(payload: dict) -> None:
    """A parent link must point at a region that is emitted earlier and encloses it."""

    seen: dict[str, list[int]] = {}
    for region in payload.get("regions", []):
        parent = region.get("parent")
        if parent is not None:
            if parent not in seen:
                raise ValueError(f"region {region['id']} names unknown parent {parent}")
            px0, py0, px1, py1 = seen[parent]
            x0, y0, x1, y1 = region["bbox"]
            if x0 < px0 or y0 < py0 or x1 > px1 or y1 > py1:
                raise ValueError(f"region {region['id']} escapes its parent {parent}")
        seen[region["id"]] = region["bbox"]


def check_page(payload: dict) -> None:
    validate(payload)
    check_reading_order(payload)
    check_parents(payload)


def _cli() -> int:
    import sys

    failures = 0
    for argument in sys.argv[1:]:
        path = Path(argument)
        try:
            check_page(json.loads(path.read_text(encoding="utf-8")))
            print(f"ok   {path}")
        except Exception as error:  # noqa: BLE001 - reported, not swallowed
            failures += 1
            print(f"FAIL {path}: {error}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_cli())

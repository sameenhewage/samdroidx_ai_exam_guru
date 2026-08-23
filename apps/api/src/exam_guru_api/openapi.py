import argparse
import json
from pathlib import Path

from exam_guru_api.main import create_app


def write_openapi(output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    schema = create_app().openapi()
    output_path.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="exam-guru-openapi")
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    write_openapi(arguments.output)

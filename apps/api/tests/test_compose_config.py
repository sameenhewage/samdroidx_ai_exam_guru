import json
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.integration
def test_compose_defines_healthy_maintenance_scheduler_with_api_runtime_contract() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    docker = shutil.which("docker")
    assert docker is not None
    completed = subprocess.run(  # noqa: S603
        [
            docker,
            "compose",
            "--file",
            str(repository_root / "compose.yaml"),
            "config",
            "--format",
            "json",
        ],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    )
    services = json.loads(completed.stdout)["services"]
    maintenance = services["maintenance"]
    worker = services["worker"]

    assert maintenance["command"] == ["exam-guru-maintenance"]
    assert maintenance["build"] == worker["build"]
    assert maintenance["environment"] == worker["environment"]
    assert maintenance["environment"]["EXAM_GURU_STORAGE_RECONCILIATION_INTERVAL_SECONDS"] == "3600"
    assert maintenance["environment"]["EXAM_GURU_STORAGE_RECONCILIATION_GRACE_SECONDS"] == "86400"
    assert (
        maintenance["environment"]["EXAM_GURU_STORAGE_RECONCILIATION_MAX_OBJECTS_PER_RUN"] == "1000"
    )
    assert maintenance["environment"]["EXAM_GURU_STORAGE_RECONCILIATION_APPLY_TAGS"] == "false"
    assert maintenance["environment"]["EXAM_GURU_IDENTITY_PROVIDER"] == "deterministic"
    assert worker["depends_on"]["minio-init"] == {
        "condition": "service_completed_successfully",
        "required": True,
    }
    assert maintenance["depends_on"] == {
        "migrate": {
            "condition": "service_completed_successfully",
            "required": True,
        },
        "valkey": {
            "condition": "service_healthy",
            "required": True,
        },
    }
    assert maintenance["healthcheck"]["test"] == [
        "CMD",
        "python",
        "-c",
        "import os; os.kill(1, 0)",
    ]

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
    api = services["api"]
    maintenance = services["maintenance"]
    worker = services["worker"]

    assert maintenance["command"] == ["exam-guru-maintenance"]
    assert maintenance["build"] == worker["build"]
    assert api["environment"] == worker["environment"]
    assert api["environment"]["EXAM_GURU_SEMANTIC_VERIFIER_PROVIDER"] == ""
    assert api["environment"]["EXAM_GURU_SEMANTIC_VERIFIER_MAX_REQUEST_BYTES"] == "65536"
    assert api["environment"]["EXAM_GURU_RETRIEVAL_EMBEDDING_PROVIDER"] == ""
    assert api["environment"]["EXAM_GURU_RETRIEVAL_EMBEDDING_MODEL"] == (
        "grade5-deterministic-shake256"
    )
    assert api["environment"]["EXAM_GURU_RETRIEVAL_EMBEDDING_DIMENSION"] == "32"
    assert api["environment"]["EXAM_GURU_RETRIEVAL_EMBEDDING_PRICING_VERSION"] == ""
    assert (
        api["environment"]["EXAM_GURU_RETRIEVAL_EMBEDDING_INPUT_MICROUSD_PER_MILLION_TOKENS"] == ""
    )
    for secret_name in (
        "EXAM_GURU_SEMANTIC_VERIFIER_OPENAI_API_KEY",
        "EXAM_GURU_RETRIEVAL_EMBEDDING_OPENAI_API_KEY",
    ):
        assert secret_name in api["environment"]
        assert secret_name not in maintenance["environment"]
        assert secret_name not in services["migrate"]["environment"]
    assert maintenance["environment"]["EXAM_GURU_STORAGE_BACKEND"] == "local"
    assert maintenance["environment"]["EXAM_GURU_STORAGE_ROOT"] == "/data"
    assert maintenance["environment"]["EXAM_GURU_STORAGE_RECONCILIATION_INTERVAL_SECONDS"] == "3600"
    assert maintenance["environment"]["EXAM_GURU_STORAGE_RECONCILIATION_GRACE_SECONDS"] == "86400"
    assert (
        maintenance["environment"]["EXAM_GURU_STORAGE_RECONCILIATION_MAX_OBJECTS_PER_RUN"] == "1000"
    )
    assert maintenance["environment"]["EXAM_GURU_STORAGE_RECONCILIATION_APPLY_TAGS"] == "false"
    assert maintenance["environment"]["EXAM_GURU_IDENTITY_PROVIDER"] == "deterministic"
    for service in (api, worker, maintenance):
        data_mounts = [volume for volume in service["volumes"] if volume["target"] == "/data"]
        assert len(data_mounts) == 1
        assert data_mounts[0]["type"] == "bind"
        assert data_mounts[0]["source"] == api["volumes"][0]["source"]
        assert "minio" not in service.get("depends_on", {})
        assert "minio-init" not in service.get("depends_on", {})
    assert {"minio", "minio-init"}.isdisjoint(services)
    assert {"api", "worker", "maintenance", "web", "postgres", "valkey"} <= services.keys()

    profile_completed = subprocess.run(  # noqa: S603
        [
            docker,
            "compose",
            "--file",
            str(repository_root / "compose.yaml"),
            "--profile",
            "s3",
            "config",
            "--format",
            "json",
        ],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    )
    profile_services = json.loads(profile_completed.stdout)["services"]
    assert profile_services["minio"]["profiles"] == ["s3"]
    assert profile_services["minio-init"]["profiles"] == ["s3"]
    assert profile_services["minio-init"]["depends_on"]["minio"] == {
        "condition": "service_started",
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

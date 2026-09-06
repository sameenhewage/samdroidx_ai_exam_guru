import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


def test_docker_context_excludes_private_corpus_storage_and_evidence() -> None:
    root = Path(__file__).resolve().parents[3]
    ignored = set((root / ".dockerignore").read_text().splitlines())
    assert {
        "RAG DATA",
        ".exam-guru-data",
        ".exam-guru-pilot-data",
        ".exam-guru-evidence",
        "graphify-out",
    } <= ignored


def test_worker_image_includes_tamil_for_mixed_real_sources() -> None:
    root = Path(__file__).resolve().parents[3]
    assert "tesseract-ocr-tam" in (root / "apps/api/Dockerfile").read_text()


@pytest.mark.integration
def test_compose_defines_healthy_maintenance_scheduler_with_api_runtime_contract() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    docker = shutil.which("docker")
    assert docker is not None
    completed = subprocess.run(  # noqa: S603
        [
            docker,
            "compose",
            "--env-file",
            str(repository_root / ".env.example"),
            "--file",
            str(repository_root / "compose.yaml"),
            "config",
            "--format",
            "json",
        ],
        cwd=repository_root,
        env={"PATH": os.defpath, "HOME": str(Path.home())},
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
    api_shared = {
        key: value
        for key, value in api["environment"].items()
        if key != "EXAM_GURU_TEST_RUNTIME_ID"
    }
    assert api_shared.items() <= worker["environment"].items()
    for name, service in services.items():
        if name != "api":
            assert "EXAM_GURU_TEST_RUNTIME_ID" not in service.get("environment", {})
        assert all(port.get("host_ip") == "127.0.0.1" for port in service.get("ports", []))
    for name in (
        "EXAM_GURU_SOURCE_UPLOAD_MAX_OWNER_STAGED_BYTES",
        "EXAM_GURU_SOURCE_UPLOAD_MAX_STAGED_BYTES",
        "EXAM_GURU_SOURCE_UPLOAD_MAX_ACTIVE_SESSIONS_PER_OWNER",
    ):
        assert name in api_shared
        assert api_shared[name] == maintenance["environment"][name]
    assert worker["environment"]["EXAM_GURU_OCR_PROVIDER"] == "tesseract"
    assert worker["environment"]["EXAM_GURU_OCR_TESSERACT_LANGUAGE"] == "sin+eng"
    assert worker["environment"]["EXAM_GURU_OCR_TESSERACT_MAX_PAGES"] == "40"
    assert worker["environment"]["EXAM_GURU_OCR_TESSERACT_TIMEOUT_SECONDS"] == "5"
    assert worker["environment"]["EXAM_GURU_OCR_TESSERACT_MAX_SOURCE_BYTES"] == "268435456"
    assert api["environment"]["EXAM_GURU_MAX_UPLOAD_BYTES"] == "268435456"
    assert api["environment"]["EXAM_GURU_RATE_LIMIT_SOURCE_UPLOAD"] == "30"
    for service in (api, maintenance, services["migrate"]):
        assert "EXAM_GURU_OCR_PROVIDER" not in service["environment"]
    assert api["environment"]["EXAM_GURU_SEMANTIC_VERIFIER_PROVIDER"] == ""
    assert api["environment"]["EXAM_GURU_SEMANTIC_VERIFIER_MAX_REQUEST_BYTES"] == "65536"
    assert api["environment"]["EXAM_GURU_GENERATION_PROVIDER"] == ""
    assert api["environment"]["EXAM_GURU_GENERATION_TEMPERATURE"] == ""
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
        "EXAM_GURU_GENERATION_OPENAI_API_KEY",
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
    dockerfile = (repository_root / "apps" / "api" / "Dockerfile").read_text(encoding="utf-8")
    assert "--no-install-recommends" in dockerfile
    for package in ("tesseract-ocr", "tesseract-ocr-eng", "tesseract-ocr-sin", "tesseract-ocr-tam"):
        assert package in dockerfile

    profile_completed = subprocess.run(  # noqa: S603
        [
            docker,
            "compose",
            "--env-file",
            str(repository_root / ".env.example"),
            "--file",
            str(repository_root / "compose.yaml"),
            "--profile",
            "s3",
            "config",
            "--format",
            "json",
        ],
        cwd=repository_root,
        env={"PATH": os.defpath, "HOME": str(Path.home())},
        check=True,
        capture_output=True,
        text=True,
    )
    profile_services = json.loads(profile_completed.stdout)["services"]
    assert all(
        port.get("host_ip") == "127.0.0.1"
        for service in profile_services.values()
        for port in service.get("ports", [])
    )
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

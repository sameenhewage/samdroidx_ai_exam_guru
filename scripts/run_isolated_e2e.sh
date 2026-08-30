#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

run_suffix="${E2E_RUN_SUFFIX:-$$}"
project_name="${E2E_COMPOSE_PROJECT_NAME:-ai-exam-guru-e2e-$run_suffix}"
if [[ ! "$project_name" =~ ^ai-exam-guru-e2e-[a-z0-9][a-z0-9-]{0,47}$ ]]; then
  printf 'E2E_COMPOSE_PROJECT_NAME must identify a throwaway ai-exam-guru-e2e-* project.\n' >&2
  exit 2
fi

port_seed=$(($$ % 1000))
export WEB_PORT="${E2E_WEB_PORT:-$((41000 + port_seed))}"
export API_PORT="${E2E_API_PORT:-$((42000 + port_seed))}"
export POSTGRES_PORT="${E2E_POSTGRES_PORT:-$((43000 + port_seed))}"
export VALKEY_PORT="${E2E_VALKEY_PORT:-$((44000 + port_seed))}"
for port in "$WEB_PORT" "$API_PORT" "$POSTGRES_PORT" "$VALKEY_PORT"; do
  if [[ ! "$port" =~ ^[0-9]+$ ]] || ((port < 1024 || port > 65535)); then
    printf 'Isolated E2E ports must be integers from 1024 through 65535.\n' >&2
    exit 2
  fi
done
if [[ "$WEB_PORT" == "3000" || "$API_PORT" == "8000" || "$POSTGRES_PORT" == "55432" || "$VALKEY_PORT" == "56379" ]]; then
  printf 'Isolated E2E ports must not reuse normal Studio ports.\n' >&2
  exit 2
fi

export APP_BASE_URL="http://127.0.0.1:$WEB_PORT"
export APP_ENVIRONMENT="test"
export EXAM_GURU_ENVIRONMENT="test"
export EXAM_GURU_STORAGE_BACKEND="local"
export EXAM_GURU_OCR_PROVIDER=""
export EXAM_GURU_SEMANTIC_VERIFIER_PROVIDER=""
export EXAM_GURU_SEMANTIC_VERIFIER_OPENAI_API_KEY=""
export EXAM_GURU_SEMANTIC_VERIFIER_MODEL=""
export EXAM_GURU_SEMANTIC_VERIFIER_MODEL_VERSION=""
export EXAM_GURU_SEMANTIC_VERIFIER_PROMPT_VERSION=""
export EXAM_GURU_SEMANTIC_VERIFIER_PRICING_VERSION=""
export EXAM_GURU_SEMANTIC_VERIFIER_INPUT_MICROUSD_PER_MILLION_TOKENS=""
export EXAM_GURU_SEMANTIC_VERIFIER_OUTPUT_MICROUSD_PER_MILLION_TOKENS=""
export EXAM_GURU_SEMANTIC_VERIFIER_TIMEOUT_MS=""
data_path="$(mktemp -d "${TMPDIR:-/tmp}/ai-exam-guru-e2e.XXXXXX")"
export EXAM_GURU_DATA_PATH="$data_path"

cleanup() {
  status=$?
  trap - EXIT INT TERM
  docker compose --project-name "$project_name" down --volumes --remove-orphans >/dev/null 2>&1 || true
  rm -rf -- "$data_path"
  exit "$status"
}
trap cleanup EXIT INT TERM

docker compose --project-name "$project_name" up --build --detach --wait --wait-timeout 240
ocr_languages="$(docker compose --project-name "$project_name" exec -T worker tesseract --list-langs 2>/dev/null)"
for language in eng sin; do
  if ! grep --fixed-strings --line-regexp --quiet "$language" <<<"$ocr_languages"; then
    printf 'Worker image is missing required Tesseract language: %s\n' "$language" >&2
    exit 1
  fi
done
curl --fail --silent "http://127.0.0.1:$API_PORT/api/v1/health/ready" >/dev/null
curl --fail --silent "$APP_BASE_URL/" >/dev/null
E2E_RUNTIME_ISOLATED=true E2E_COMPOSE_PROJECT_NAME="$project_name" E2E_BASE_URL="$APP_BASE_URL" npm run test:e2e --prefix apps/web -- "$@"
docker compose --project-name "$project_name" ps

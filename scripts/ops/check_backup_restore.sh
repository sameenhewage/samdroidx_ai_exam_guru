#!/usr/bin/env bash
set -Eeuo pipefail

script_directory=$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
readonly script_directory
repository_root=$(cd -P -- "$script_directory/../.." && pwd)
readonly repository_root
readonly runbook="$repository_root/docs/ops/BACKUP_RESTORE.md"
readonly scripts=(
    "$script_directory/backup_postgres.sh"
    "$script_directory/restore_postgres.sh"
    "$script_directory/check_backup_restore.sh"
)

[[ -f "$runbook" ]] || {
    printf 'Missing runbook: %s\n' "$runbook" >&2
    exit 1
}

bash -n "${scripts[@]}"

if command -v shellcheck >/dev/null 2>&1; then
    shellcheck --severity=warning "${scripts[@]}"
elif [[ "${REQUIRE_SHELLCHECK:-0}" == "1" ]]; then
    printf 'shellcheck is required but unavailable\n' >&2
    exit 1
else
    printf 'shellcheck unavailable; completed bash syntax checks only\n' >&2
fi

if grep -nE -- '(^|[[:space:]])set[[:space:]]+-x|--password([=[:space:]]|$)' \
    "$script_directory/backup_postgres.sh" \
    "$script_directory/restore_postgres.sh"; then
    printf 'Unsafe tracing or password argument found in an operations script\n' >&2
    exit 1
fi

printf 'Backup/restore static checks passed.\n'

#!/usr/bin/env bash
set +x
set -Eeuo pipefail
umask 077

readonly PROGRAM_NAME="${0##*/}"
readonly DEFAULT_CONNECT_TIMEOUT_SECONDS="10"
readonly DEFAULT_LOCK_WAIT_TIMEOUT_MS="30000"

destination=""
temporary_directory=""

usage() {
    cat <<'EOF'
Usage: backup_postgres.sh --destination DIRECTORY

Create a PostgreSQL custom-format archive in a new destination directory.
Connection identity comes only from libpq environment/service configuration.
Use PGPASSFILE, a protected ~/.pgpass, a client certificate, or peer auth;
PGPASSWORD is rejected and database URLs/password flags are not accepted.
EOF
}

fail() {
    printf '%s: %s\n' "$PROGRAM_NAME" "$1" >&2
    exit "${2:-2}"
}

cleanup() {
    local status=$?
    trap - EXIT
    if [[ -n "$temporary_directory" && -d "$temporary_directory" ]]; then
        rm -rf -- "$temporary_directory"
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' HUP INT TERM

while (($# > 0)); do
    case "$1" in
        --destination)
            (($# >= 2)) || fail "--destination requires a value"
            [[ -z "$destination" ]] || fail "--destination may be supplied only once"
            destination=$2
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            fail "unknown argument; see --help"
            ;;
    esac
done

[[ -n "$destination" ]] || fail "an explicit --destination is required"
[[ "$destination" != "/" ]] || fail "the filesystem root cannot be a backup destination"
[[ -z "${PGPASSWORD+x}" ]] || fail "PGPASSWORD is not accepted; use a protected PGPASSFILE or non-password libpq authentication"
[[ -n "${PGDATABASE:-}" || -n "${PGSERVICE:-}" ]] || fail "set PGDATABASE or PGSERVICE without placing credentials in arguments"

connect_timeout_seconds="${PGCONNECT_TIMEOUT:-$DEFAULT_CONNECT_TIMEOUT_SECONDS}"
[[ "$connect_timeout_seconds" =~ ^[0-9]+$ ]] \
    || fail "PGCONNECT_TIMEOUT must be a bounded integer from 1 to 60 seconds"
((10#$connect_timeout_seconds >= 1 && 10#$connect_timeout_seconds <= 60)) \
    || fail "PGCONNECT_TIMEOUT must be a bounded integer from 1 to 60 seconds"
export PGCONNECT_TIMEOUT="$connect_timeout_seconds"

lock_wait_timeout_ms="${PG_DUMP_LOCK_WAIT_TIMEOUT_MS:-$DEFAULT_LOCK_WAIT_TIMEOUT_MS}"
[[ "$lock_wait_timeout_ms" =~ ^[0-9]+$ ]] \
    || fail "PG_DUMP_LOCK_WAIT_TIMEOUT_MS must be a bounded integer from 1 to 300000"
((10#$lock_wait_timeout_ms >= 1 && 10#$lock_wait_timeout_ms <= 300000)) \
    || fail "PG_DUMP_LOCK_WAIT_TIMEOUT_MS must be a bounded integer from 1 to 300000"

readonly PG_DUMP_COMMAND="${PG_DUMP_BIN:-pg_dump}"
readonly PG_RESTORE_COMMAND="${PG_RESTORE_BIN:-pg_restore}"
command -v -- "$PG_DUMP_COMMAND" >/dev/null 2>&1 || fail "pg_dump is unavailable"
command -v -- "$PG_RESTORE_COMMAND" >/dev/null 2>&1 || fail "pg_restore is unavailable"
command -v sha256sum >/dev/null 2>&1 || fail "sha256sum is unavailable"
command -v mktemp >/dev/null 2>&1 || fail "mktemp is unavailable"

if [[ -n "${PGPASSFILE:-}" ]]; then
    [[ -f "$PGPASSFILE" && ! -L "$PGPASSFILE" && -r "$PGPASSFILE" ]] \
        || fail "PGPASSFILE must be a readable regular file, not a symlink"
    pgpass_mode=$(stat -c '%a' -- "$PGPASSFILE")
    [[ "$pgpass_mode" =~ ^[0-7]*00$ ]] || fail "PGPASSFILE must not grant group or other permissions"
fi

parent_directory=$(dirname -- "$destination")
destination_name=$(basename -- "$destination")
[[ "$destination_name" != "." && "$destination_name" != ".." && -n "$destination_name" ]] \
    || fail "destination must name a new directory"
[[ -d "$parent_directory" && ! -L "$parent_directory" ]] \
    || fail "destination parent must be an existing non-symlink directory"
parent_directory=$(cd -P -- "$parent_directory" && pwd)
destination="$parent_directory/$destination_name"
[[ ! -e "$destination" && ! -L "$destination" ]] || fail "destination already exists"

temporary_directory=$(mktemp -d -- "$parent_directory/.${destination_name}.tmp.XXXXXX")
readonly archive_path="$temporary_directory/database.dump"
readonly archive_list_path="$temporary_directory/database.dump.list"
readonly metadata_path="$temporary_directory/backup-metadata.txt"

dump_version=$("$PG_DUMP_COMMAND" --version)
restore_version=$("$PG_RESTORE_COMMAND" --version)
dump_version=${dump_version//$'\n'/ }
restore_version=${restore_version//$'\n'/ }

"$PG_DUMP_COMMAND" \
    --format=custom \
    --no-owner \
    --no-acl \
    "--lock-wait-timeout=$lock_wait_timeout_ms" \
    "--file=$archive_path"

[[ -s "$archive_path" ]] || fail "pg_dump did not create a non-empty archive"
[[ "$(head -c 5 -- "$archive_path")" == "PGDMP" ]] || fail "pg_dump output is not a custom-format archive"
"$PG_RESTORE_COMMAND" --list "$archive_path" > "$archive_list_path"
[[ -s "$archive_list_path" ]] || fail "pg_restore could not produce an archive inventory"

{
    printf 'artifact_format=postgresql-custom\n'
    printf 'created_utc=%s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
    printf 'pg_dump_version=%s\n' "$dump_version"
    printf 'pg_restore_version=%s\n' "$restore_version"
    printf 'pg_dump_no_owner_option=true\n'
    printf 'pg_dump_no_acl_option=true\n'
    printf 'required_restore_no_owner=true\n'
    printf 'required_restore_no_acl=true\n'
} > "$metadata_path"

(
    cd -- "$temporary_directory"
    LC_ALL=C sha256sum \
        backup-metadata.txt \
        database.dump \
        database.dump.list > SHA256SUMS
    LC_ALL=C sha256sum --check --strict SHA256SUMS >/dev/null
)

# GNU mv -T prevents treating a raced destination directory as a parent. The
# no-clobber postcondition makes an existing file/directory fail closed.
mv -T --no-clobber -- "$temporary_directory" "$destination"
if [[ -e "$temporary_directory" || ! -d "$destination" ]]; then
    fail "destination appeared during backup finalization; no artifact was replaced"
fi
temporary_directory=""
trap - EXIT

printf 'Backup completed atomically: %s\n' "$destination"
printf 'This local artifact is not an off-host backup until encrypted, copied, and independently verified.\n'

#!/usr/bin/env bash
set +x
set -Eeuo pipefail
umask 077

readonly PROGRAM_NAME="${0##*/}"
readonly CONFIRMATION_PREFIX="RESTORE:"
readonly DEFAULT_CONNECT_TIMEOUT_SECONDS="10"

backup_directory=""
execute_restore=false
confirmation=""

usage() {
    cat <<'EOF'
Usage:
  restore_postgres.sh --backup-dir DIRECTORY
  restore_postgres.sh --backup-dir DIRECTORY --execute \
      --confirm-empty-target RESTORE:<exact-PGDATABASE>

The default is an offline dry-run: verify the closed checksum manifest and ask
pg_restore to parse/list the archive without connecting to a database.

Execution is intentionally destructive and fail-closed. It requires all of:
  * --execute
  * the exact target-bound confirmation shown above
  * a safe explicit PGDATABASE (not postgres/template0/template1)
  * no user relations/schema objects, no non-plpgsql extensions, and no other sessions

Connection identity comes only from libpq environment/service configuration.
PGPASSWORD and credential-bearing command-line URLs are not accepted.
EOF
}

fail() {
    printf '%s: %s\n' "$PROGRAM_NAME" "$1" >&2
    exit "${2:-2}"
}

while (($# > 0)); do
    case "$1" in
        --backup-dir)
            (($# >= 2)) || fail "--backup-dir requires a value"
            [[ -z "$backup_directory" ]] || fail "--backup-dir may be supplied only once"
            backup_directory=$2
            shift 2
            ;;
        --execute)
            [[ "$execute_restore" == false ]] || fail "--execute may be supplied only once"
            execute_restore=true
            shift
            ;;
        --confirm-empty-target)
            (($# >= 2)) || fail "--confirm-empty-target requires a value"
            [[ -z "$confirmation" ]] || fail "--confirm-empty-target may be supplied only once"
            confirmation=$2
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

[[ -n "$backup_directory" ]] || fail "an explicit --backup-dir is required"
[[ -z "${PGPASSWORD+x}" ]] || fail "PGPASSWORD is not accepted; use a protected PGPASSFILE or non-password libpq authentication"
connect_timeout_seconds="${PGCONNECT_TIMEOUT:-$DEFAULT_CONNECT_TIMEOUT_SECONDS}"
[[ "$connect_timeout_seconds" =~ ^[0-9]+$ ]] \
    || fail "PGCONNECT_TIMEOUT must be a bounded integer from 1 to 60 seconds"
((10#$connect_timeout_seconds >= 1 && 10#$connect_timeout_seconds <= 60)) \
    || fail "PGCONNECT_TIMEOUT must be a bounded integer from 1 to 60 seconds"
export PGCONNECT_TIMEOUT="$connect_timeout_seconds"

[[ -d "$backup_directory" && ! -L "$backup_directory" ]] \
    || fail "backup directory must be a non-symlink directory"
backup_directory=$(cd -P -- "$backup_directory" && pwd)

readonly PG_RESTORE_COMMAND="${PG_RESTORE_BIN:-pg_restore}"
readonly PSQL_COMMAND="${PSQL_BIN:-psql}"
command -v -- "$PG_RESTORE_COMMAND" >/dev/null 2>&1 || fail "pg_restore is unavailable"
command -v sha256sum >/dev/null 2>&1 || fail "sha256sum is unavailable"

if [[ -n "${PGPASSFILE:-}" ]]; then
    [[ -f "$PGPASSFILE" && ! -L "$PGPASSFILE" && -r "$PGPASSFILE" ]] \
        || fail "PGPASSFILE must be a readable regular file, not a symlink"
    pgpass_mode=$(stat -c '%a' -- "$PGPASSFILE")
    [[ "$pgpass_mode" =~ ^[0-7]*00$ ]] || fail "PGPASSFILE must not grant group or other permissions"
fi

readonly archive_path="$backup_directory/database.dump"
readonly archive_list_path="$backup_directory/database.dump.list"
readonly metadata_path="$backup_directory/backup-metadata.txt"
readonly manifest_path="$backup_directory/SHA256SUMS"
for required_path in "$archive_path" "$archive_list_path" "$metadata_path" "$manifest_path"; do
    [[ -f "$required_path" && ! -L "$required_path" ]] \
        || fail "backup contains a missing, non-regular, or symlinked required file"
    required_mode=$(stat -c '%a' -- "$required_path")
    [[ "$required_mode" =~ ^[0-7]*00$ ]] \
        || fail "backup files must not grant group or other permissions"
done

# Accept exactly the three files emitted by backup_postgres.sh. This prevents a
# crafted checksum manifest from traversing outside the reviewed backup bundle.
declare -A manifest_entries=()
manifest_count=0
while IFS= read -r manifest_line || [[ -n "$manifest_line" ]]; do
    if [[ "$manifest_line" =~ ^([0-9a-f]{64})\ \ (backup-metadata\.txt|database\.dump|database\.dump\.list)$ ]]; then
        manifest_name=${BASH_REMATCH[2]}
        [[ -z "${manifest_entries[$manifest_name]+x}" ]] \
            || fail "checksum manifest contains a duplicate entry"
        manifest_entries[$manifest_name]=1
        ((manifest_count += 1))
    else
        fail "checksum manifest is not closed over the expected backup files"
    fi
done < "$manifest_path"
[[ "$manifest_count" -eq 3 ]] || fail "checksum manifest must contain exactly three entries"
for manifest_name in backup-metadata.txt database.dump database.dump.list; do
    [[ -n "${manifest_entries[$manifest_name]+x}" ]] \
        || fail "checksum manifest is missing $manifest_name"
done

(
    cd -- "$backup_directory"
    LC_ALL=C sha256sum --check --strict SHA256SUMS >/dev/null
) || fail "backup checksum verification failed"
[[ "$(head -c 5 -- "$archive_path")" == "PGDMP" ]] \
    || fail "database.dump is not a PostgreSQL custom-format archive"
"$PG_RESTORE_COMMAND" --list "$archive_path" >/dev/null \
    || fail "pg_restore could not parse the archive"

if [[ "$execute_restore" == false ]]; then
    [[ -z "$confirmation" ]] \
        || fail "--confirm-empty-target is invalid without --execute"
    printf 'Dry-run verification passed; no target database connection or write was attempted.\n'
    exit 0
fi

[[ -n "${PGDATABASE:-}" ]] || fail "execution requires an explicit PGDATABASE"
[[ "$PGDATABASE" =~ ^[A-Za-z0-9_.-]{1,63}$ ]] \
    || fail "PGDATABASE must use a bounded operator-safe name"
case "$PGDATABASE" in
    postgres|template0|template1)
        fail "refusing to restore into a PostgreSQL maintenance/template database"
        ;;
esac
readonly expected_confirmation="${CONFIRMATION_PREFIX}${PGDATABASE}"
[[ "$confirmation" == "$expected_confirmation" ]] \
    || fail "confirmation mismatch; require --confirm-empty-target $expected_confirmation"
command -v -- "$PSQL_COMMAND" >/dev/null 2>&1 || fail "psql is unavailable"

readonly guard_sql="SELECT current_database(),
    (SELECT count(*) FROM pg_class AS relation
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname NOT IN ('pg_catalog', 'information_schema')
          AND namespace.nspname !~ '^pg_toast'
          AND relation.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')),
    (SELECT count(*) FROM pg_extension WHERE extname <> 'plpgsql'),
    (SELECT count(*) FROM pg_stat_activity
        WHERE datname = current_database() AND pid <> pg_backend_pid()),
    (SELECT count(*) FROM (
        SELECT namespace.oid
        FROM pg_namespace AS namespace
        WHERE namespace.nspname NOT IN ('pg_catalog', 'information_schema', 'public')
          AND namespace.nspname !~ '^pg_toast'
        UNION ALL
        SELECT routine.oid
        FROM pg_proc AS routine
        JOIN pg_namespace AS namespace ON namespace.oid = routine.pronamespace
        WHERE namespace.nspname = 'public'
        UNION ALL
        SELECT data_type.oid
        FROM pg_type AS data_type
        JOIN pg_namespace AS namespace ON namespace.oid = data_type.typnamespace
        WHERE namespace.nspname = 'public'
          AND data_type.typtype IN ('c', 'd', 'e', 'r', 'm')
    ) AS user_object);"

guard_state=$("$PSQL_COMMAND" \
    --no-psqlrc \
    --set=ON_ERROR_STOP=1 \
    --tuples-only \
    --no-align \
    "--field-separator=|" \
    "--command=$guard_sql") || fail "target guard query failed"
[[ "$guard_state" != *$'\n'* && "$guard_state" != *$'\r'* ]] \
    || fail "target guard returned an unexpected result"
IFS='|' read -r actual_database relation_count extension_count session_count user_object_count extra \
    <<< "$guard_state"
[[ -z "${extra:-}" && "$relation_count" =~ ^[0-9]+$ && "$extension_count" =~ ^[0-9]+$ \
    && "$session_count" =~ ^[0-9]+$ && "$user_object_count" =~ ^[0-9]+$ ]] \
    || fail "target guard returned an invalid result"
[[ "$actual_database" == "$PGDATABASE" ]] || fail "target identity does not match PGDATABASE"
[[ "$relation_count" -eq 0 ]] || fail "target is not empty; user relations already exist"
[[ "$extension_count" -eq 0 ]] || fail "target has a non-default extension and is not empty"
[[ "$session_count" -eq 0 ]] || fail "target has another active session; isolate it before restore"
[[ "$user_object_count" -eq 0 ]] \
    || fail "target has a user-defined schema object and is not empty"

"$PG_RESTORE_COMMAND" \
    --exit-on-error \
    --single-transaction \
    --no-owner \
    --no-acl \
    "--dbname=$PGDATABASE" \
    "$archive_path"

printf 'Restore completed into the confirmed empty target. Keep it isolated until all runbook checks pass.\n'

# Backup and Restore Runbook

## Purpose and safety boundary

This runbook covers the durable Priority 1 evidence owned by AI Exam Guru:

- PostgreSQL 18 schema and data, including pgvector data;
- immutable source objects in S3-compatible storage (S3 or MinIO);
- cluster-level role definitions needed to recreate access, without role passwords;
- restore validation for provenance, append-only history, and published papers.

It does **not** authorize an in-place restore. Restore only into a newly created,
isolated, empty database and empty target bucket/prefix. Keep the application,
workers, maintenance scheduler, and all writers stopped until every validation
step passes. `scripts/ops/restore_postgres.sh` is verification-only by default
and will not execute unless its empty-target guard and exact target-bound
confirmation both pass.

A local dump is not a backup by itself. A releasable backup is encrypted in
transit and at rest, held off-host under a separate administrative boundary,
covered by retention/immutability controls, inventoried, and proven by an
isolated restore.

## Recovery objectives are deployment decisions

No production RPO, RTO, schedule, or retention target is established by this
repository. Those values depend on deployment topology, data volume, source
availability, legal retention, and business approval. They are **deployment
decisions** and must be approved before launch; do not infer them from CI test
runtime.

Record these fields for each environment and each recovery exercise:

| Field | Approved target | Measured result | Evidence timestamp / exercise ID | Owner |
|---|---|---|---|---|
| RPO | deployment decision | last durable DB transaction and object represented by the recovery point | | |
| RTO | deployment decision | incident declaration to validated service cutover | | |
| Backup interval | deployment decision | observed interval and last-success age | | |
| Retention / legal hold | deployment decision | oldest and newest independently recoverable points | | |
| DB dump duration / bytes / throughput | n/a | measure | | |
| Object inventory and final mirror duration / bytes / object count | n/a | measure | | |
| Provisioning duration | n/a | measure | | |
| Object restore and checksum duration | n/a | measure | | |
| Database restore duration | n/a | measure | | |
| Migration, invariant, readiness, and cutover duration | n/a | measure | | |

For RPO measurement, record the coordinated write-freeze timestamp, database
snapshot start/end, latest represented audit event, object inventory timestamp,
and any object/database mismatch. For RTO measurement, record incident decision,
provisioning, artifact retrieval/decryption, object copy, DB restore, validation,
and cutover separately. A failed exercise is evidence, not a result to average
away.

## Repository-verified data map

This inventory is derived from the current repository, not from an assumed
production schema.

### Platform and schema sources

- `compose.yaml:15-29` pins PostgreSQL 18 with pgvector 0.8.6 and a persistent
  PostgreSQL volume. `compose.yaml:44-67` defines MinIO and the
  `exam-guru-sources` bucket.
- `apps/api/migrations/versions/0001_enable_pgvector.py:11-12` creates the
  `vector` extension.
- `apps/api/migrations/versions/0003_admin_audit_events.py:14-65` creates
  `admin_audit_events` and its append-only mutation trigger.
- `apps/api/migrations/versions/0005_source_documents.py:37-81` binds each
  source row to a unique `object_key`, checksum value, size, source
  type, curriculum metadata, and lifecycle.
- `apps/api/migrations/versions/0006_extraction_persistence.py` and
  `0017_ocr_worker_pipeline.py` preserve page/block extraction, reviewed text,
  native/OCR implementation/configuration, confidence, and immutable
  provenance.
- `apps/api/migrations/versions/0007_knowledge_foundation.py` creates reviewed
  historical questions, knowledge chunks, embedding configurations, and
  pgvector embeddings with source-text hash checks.
- `apps/api/migrations/versions/0011_analytics_runs.py` through
  `0015_review_candidates.py` persist immutable analytics/blueprints,
  generation attempts, validation findings, candidate revisions, and review
  decisions.
- `apps/api/migrations/versions/0016_published_papers.py` creates immutable
  draft/publication/archive history, canonical JSON functions, authoritative
  snapshot reconstruction, and hash-enforcing triggers.
- `apps/api/migrations/versions/0018_embedding_jobs.py` and
  `0019_extraction_outbox.py` persist queue/recovery identity and lifecycle.
- `apps/api/migrations/versions/0020_restore_safe_canonical_json.py` schema-qualifies
  canonical JSON recursion so PostgreSQL's intentionally empty dump/restore
  `search_path` cannot break publication checks during `COPY`.
- `apps/api/migrations/versions/0021_storage_reconciliation.py` persists bounded
  source-object reconciliation runs, the singleton scan lease/bounded opaque
  pagination cursor, and reversible orphan-candidate findings/tag outcomes.
  The current Alembic head is `0021_storage_reconciliation`; always resolve the
  head from the release being restored rather than assuming this literal remains
  current.
- `apps/api/src/exam_guru_api/documents/domain.py:76-82` derives the source key
  as `sources/<sha256-prefix>/<sha256>.pdf`, where `<sha256-prefix>` is the first
  two lowercase checksum characters.
- `apps/api/src/exam_guru_api/infrastructure/object_storage.py:166-200` performs
  conditional immutable writes and stores SHA-256 in object user metadata.
- `apps/api/src/exam_guru_api/papers/domain.py:831-838` and
  `apps/api/src/exam_guru_api/papers/serialization.py:38-120` define canonical
  UTF-8 JSON hashing and domain reconstruction for a published snapshot.

### Critical PostgreSQL relations

Back up the database as a whole. The following list is for inventory and
post-restore evidence; it is not a suggestion to perform partial table dumps.

| Capability | Critical relations |
|---|---|
| Grade 5 configuration/taxonomy | `exam_configurations`, `media`, `curriculum_versions`, `taxonomy_nodes` |
| Source and extraction provenance | `source_documents`, `source_pages`, `extracted_blocks`, `storage_reconciliation_state`, `storage_reconciliation_runs`, `storage_orphan_findings` |
| Reviewed knowledge and RAG | `historical_questions`, `knowledge_chunks`, `embedding_configurations`, `knowledge_embeddings`, `embedding_jobs` |
| Analytics and deterministic blueprint | `analytics_runs`, `paper_blueprints` |
| Generation | `generation_runs`, `generation_attempts`, `generation_jobs` |
| Validation | `validation_runs`, `validation_findings` |
| Human review | `question_candidates`, `question_candidate_revisions`, `candidate_review_events` |
| Paper publication | `practice_papers`, `paper_draft_versions`, `paper_draft_candidates`, `published_paper_versions`, `paper_archive_events` |
| Audit | `admin_audit_events` |
| Schema version | `alembic_version` |

Valkey is not the durable source of truth for these records. Do not restore a
stale queue/cache snapshot as authoritative state. Provision a clean Valkey and
let the PostgreSQL-backed recovery actors reconcile eligible extraction,
generation, and embedding work only after the restored database passes checks.

### Critical objects

The current concrete object contract is every `source_documents.object_key` in
the configured source bucket. Each object must match both
`source_documents.size_bytes` and `source_documents.checksum_sha256`. Preserve
all object versions retained by policy, legal holds, tags/metadata needed by
operations, and the application `sha256` user metadata. ETag is not a portable
SHA-256 guarantee, especially for multipart or encrypted objects. Reconciliation
may persist findings and, only when explicitly configured, merge/remove the
application-owned `exam-guru-orphan-candidate` and
`exam-guru-orphan-detected-at` tags. It never deletes an object or overwrites
operator-owned tags. Any external lifecycle deletion remains a separate,
explicitly approved storage-policy action outside application reconciliation.

The V1 architecture permits generated artifacts in S3-compatible storage. If a
deployment adds another bucket or DB-to-object reference, add it to the signed
inventory and this runbook before relying on the procedure.

## What the database archive does and does not contain

`scripts/ops/backup_postgres.sh` invokes `pg_dump --format=custom --no-owner
--no-acl`, verifies that `pg_restore` can list the archive, writes metadata and
a closed `SHA256SUMS` inventory, and atomically renames a same-filesystem
staging directory. It requires a new explicit destination, private
credential/file permissions, and bounded connection/lock-wait configuration.

The archive contains database-local extension declarations, schema, triggers,
functions, constraints, `alembic_version`, tables, and data. It does not contain
cluster roles, role passwords, tablespaces, server settings, service secrets, or
object data. A custom archive can retain owner identity metadata even when
`pg_dump --no-owner` is requested; `pg_restore --no-owner --no-acl` is therefore
a mandatory enforcement control, not an optional convenience. Never restore
this archive by bypassing the reviewed script/flags. Deployment automation
reapplies approved owner/grant state separately.

Capture role shape separately from a trusted administration host when required:

```bash
pg_dumpall --roles-only --no-role-passwords > roles-without-passwords.sql
```

Review that file for unexpected superuser, replication, bypass-RLS, or role
membership grants before encrypting it. Restore role definitions and grants
from approved infrastructure-as-code or a reviewed file; inject new credentials
from the secret manager. Because the scripted restore suppresses owner/ACL
application, the target restore role owns restored objects and deployment
automation must reapply the least-privilege application/reviewer grants.

Use a PostgreSQL client that is the same major as, or newer than, the source
server. This deployment is PostgreSQL 18, so the operational default is the
PostgreSQL 18 `pg_dump`, `pg_restore`, and `psql` toolchain. The target must have
the compatible pgvector extension binaries/control files installed before
restore; the archive creates the database extension itself.

## Backup procedure

### 1. Authorize and prepare

1. Open a change/recovery record. Name the operator, source environment,
   recovery-point ID, approved off-host destination, encryption key alias,
   retention class, and expected write-freeze window.
2. Confirm recent backup monitoring is healthy, available capacity exceeds the
   measured database plus object size with margin, and the off-host destination
   is in a different failure/administration domain.
3. Use an encrypted staging filesystem with mode `0700`. Do not stage on a
   developer laptop, application container layer, or unencrypted shared disk.
4. Obtain short-lived least-privilege backup credentials through the deployment
   secret mechanism. Do not put a password or credential-bearing URL in shell
   arguments, command history, CI output, or the recovery record.
5. Configure libpq through a protected `PGPASSFILE`, client certificate, peer
   authentication, or approved service configuration. The scripts reject
   `PGPASSWORD` so it cannot be inherited accidentally.

Example shape only; placeholders are not credentials:

```bash
install -m 0600 /dev/null "<private-runtime-dir>/pgpass"
# Populate the file from the secret manager without echoing it to a terminal.
export PGPASSFILE="<private-runtime-dir>/pgpass"
export PGHOST="<source-db-host>"
export PGPORT="<source-db-port>"
export PGUSER="<backup-role>"
export PGDATABASE="<source-db-name>"
unset PGPASSWORD
export PG_DUMP_BIN="<postgresql-18-bin-dir>/pg_dump"
export PG_RESTORE_BIN="<postgresql-18-bin-dir>/pg_restore"
```

Preflight without revealing connection secrets:

```bash
"$PG_DUMP_BIN" --version
"$PG_RESTORE_BIN" --version
psql --no-psqlrc --set=ON_ERROR_STOP=1 --command="SELECT current_database(), current_setting('server_version_num')::int;"
psql --no-psqlrc --set=ON_ERROR_STOP=1 --command="SELECT extname, extversion FROM pg_extension ORDER BY extname;"
```

### 2. Establish a coordinated recovery point

A PostgreSQL dump is transactionally consistent for the database snapshot, but
it is not automatically atomic with object storage. The application writes an
immutable object before its `source_documents` row, so a pre-copy can contain a
harmless unreferenced object; a database row whose object was not copied is not
acceptable. Coordinate as follows:

1. Pre-copy objects while the service is available to reduce outage duration.
2. Enter an application outage/write freeze. Disable admin write ingress and
   uploads, pause scheduled maintenance, and drain then stop API workers that
   can mutate extraction, embedding, generation, validation, review, publish,
   or audit state. Do not rely on UI maintenance text alone; verify at the
   database and process/runtime levels.
3. Record UTC freeze time and latest durable audit/job identifiers. Ensure no
   active application write transactions remain. Keep operator/read-only
   inspection connections identifiable by `application_name`.
4. Generate the authoritative DB object inventory under the freeze:

   ```bash
   psql --no-psqlrc --set=ON_ERROR_STOP=1 --csv \
     --command="SELECT object_key, checksum_sha256, size_bytes FROM source_documents ORDER BY object_key" \
     > "<encrypted-staging>/<recovery-id>-source-objects.csv"
   ```

5. Run a final object replication/mirror pass and capture its logs and inventory
   timestamp. Never use a delete/remove mirror option in this procedure.
6. Run the database backup into a destination that does not exist:

   ```bash
   scripts/ops/backup_postgres.sh \
     --destination "<encrypted-staging>/<recovery-id>-postgres"
   ```

7. Capture role shape (without passwords), PostgreSQL/pgvector versions, release
   identifier, Alembic heads/current revision, table row counts, object bucket
   configuration, and checksums in the recovery evidence envelope.
8. Independently run the script's checksum verification and archive listing:

   ```bash
   (cd "<encrypted-staging>/<recovery-id>-postgres" && sha256sum --check --strict SHA256SUMS)
   pg_restore --list "<encrypted-staging>/<recovery-id>-postgres/database.dump" > /dev/null
   ```

9. If any command fails, keep writes frozen until the recovery owner decides
   whether to repeat the coordinated point or safely resume. Never label a
   partial DB/object pair as recoverable.

### 3. Protect objects off-host

Preferred S3-compatible controls are:

- object versioning before first production write;
- SSE-KMS (or the approved MinIO KMS/KES equivalent) with TLS in transit;
- cross-account and, where required, cross-region replication;
- retention/Object Lock so the application and ordinary backup principal cannot
  shorten or delete protected history;
- separate credentials and alerting for replication/retention administration;
- provider inventory reports including bucket, key, size, version ID, last
  modified time, encryption status, and a strong checksum where supported.

For MinIO without configured replication, a one-way fallback copy may use
`mc mirror` into a versioned, encrypted, separately administered destination:

```bash
mc mirror --preserve \
  "<source-alias>/<source-bucket>" \
  "<backup-alias>/<recovery-prefix>/objects"
```

Do **not** add `--remove`. A simple mirror represents current objects, not all
historical versions; use replication or an explicit version-aware export when
retained versions are part of the recovery contract. Never mirror back into the
source during backup.

### 4. Encrypt and transfer the database bundle

The script output is plaintext database material even when its staging disk is
encrypted. Package it with the object inventory and role/config evidence, then
apply approved authenticated client-side encryption or upload over TLS with
mandatory KMS encryption and immutability. Keep encryption private keys outside
the backup account. Do not put key material in command arguments or logs.

Record both:

- the internal plaintext `SHA256SUMS` (inside the encrypted envelope), and
- the SHA-256/checksum, size, KMS key alias/version, object version ID, and
  retention status of the encrypted off-host artifact.

A checksum detects corruption; it does not authenticate a maliciously replaced
artifact. Sign the evidence manifest or rely on an approved authenticated
encryption/signing mechanism plus immutable audit logs.

### 5. Close the backup window

1. Verify the remote artifact/object inventory, encryption, retention, and
   replication status from the destination side.
2. Compare every DB `object_key`, size, and SHA-256 with the remote inventory.
   Download and hash all objects when practical; otherwise document the
   provider checksum mechanism and sampling limitation. Do not equate ETag with
   SHA-256.
3. Remove plaintext staging only under the approved retention/sanitization
   process after remote verification. Remove the temporary `PGPASSFILE`.
4. Resume workers and writes in a controlled order; verify readiness and job
   recovery health.
5. A backup is not marked usable until a scheduled isolated restore proves it.

## Restore procedure

### 1. Declare the restore and preserve rollback

1. Identify the exact recovery-point DB bundle, object inventory/version set,
   release image, PostgreSQL/pgvector versions, role/grant definition, and
   encryption key version.
2. Keep the original environment unchanged. If it is still reachable, maintain
   the outage/write freeze and take a final forensic snapshot where authorized.
3. Provision a separate network/security group, new empty PostgreSQL 18 target,
   fresh Valkey, and an empty target bucket or unique empty prefix. No production
   DNS, load balancer, worker, scheduler, or application role may point to it.
4. Restrict database `CONNECT` and bucket access to recovery operators. The
   restore script's session guard reduces mistakes but does not close the race
   between its guard query and `pg_restore`; network/role isolation is required.
5. Confirm the target PostgreSQL image has the compatible pgvector package.
   Do not run application migrations into the empty target before the archive
   restore. The archive contains extensions, schema, migration state, and data.

### 2. Restore objects first

Restore objects before database references become available to an application:

1. Verify the encrypted artifact checksum/signature and decrypt only onto an
   encrypted restricted staging volume.
2. Copy the selected object versions into the empty isolated target bucket or
   prefix. With `mc mirror`, omit all deletion options. Do not delete or mutate
   source/backup objects.
3. Produce a target inventory. For every `source-objects.csv` row, verify key,
   byte size, and SHA-256 of the retrieved bytes. Preserve content type and
   `sha256` user metadata. Quarantine any extra objects rather than deleting
   them during the recovery exercise.
4. Keep target object credentials inaccessible to application processes until
   DB checks pass.

### 3. Verify, then restore the database second

Configure target libpq variables and a protected `PGPASSFILE` as in the backup
procedure. `PGDATABASE` must be the new isolated target's exact name. First run
the default non-writing verification:

```bash
scripts/ops/restore_postgres.sh \
  --backup-dir "<encrypted-staging>/<recovery-id>-postgres"
```

That validates the closed manifest, custom archive signature, and
`pg_restore --list` without connecting to the target. Review the target identity
and prove it is empty. Then execute only with change/recovery-owner approval:

```bash
scripts/ops/restore_postgres.sh \
  --backup-dir "<encrypted-staging>/<recovery-id>-postgres" \
  --execute \
  --confirm-empty-target "RESTORE:${PGDATABASE}"
```

Execution refuses `postgres`, `template0`, and `template1`; checks the exact
current database; rejects user relations, user-defined schema objects,
non-default extensions, and other active sessions; and runs
`pg_restore --exit-on-error --single-transaction
--no-owner --no-acl`. It does not run `--clean`, drop a database, or overwrite a
nonempty target.

If role definitions are required, create only approved login/group roles and
memberships through infrastructure automation, issue new secrets, then apply
least-privilege grants after the archive is restored. Never restore role
password hashes from the backup.

## Mandatory post-restore validation

Run every check while the target remains isolated. Save command versions,
stdout/stderr (with secrets redacted), query results, counts, durations, and the
recovery-point ID.

### 1. Versions, extension, and Alembic head

```sql
SELECT current_database(), current_setting('server_version_num')::int;
SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';
SELECT version_num FROM alembic_version;
```

Use the restored release's Alembic configuration and a secret-injected
`EXAM_GURU_DATABASE_URL` (never a credential in shell history):

```bash
uv run --project apps/api alembic -c apps/api/alembic.ini current --check-heads
uv run --project apps/api alembic -c apps/api/alembic.ini check
```

The restored revision must be a head for the release under test. If restoring
an older supported backup to a newer release, first capture all pre-migration
checks, then run `alembic upgrade head` under a separate approved step and
repeat the entire invariant/hash suite. Never use downgrade as a recovery
shortcut.

### 2. Counts and critical lineage

Compare pre-backup and restored row counts for every critical relation listed
above. Explain every difference; under a coordinated write freeze they should
match. At minimum, require nonzero counts where the source inventory was
nonzero and inspect these relationships:

```sql
SELECT count(*) AS invalid_source_hashes
FROM source_documents
WHERE checksum_sha256 !~ '^[0-9a-f]{64}$' OR size_bytes <= 0;

SELECT count(*) AS invalid_embedding_hashes
FROM knowledge_embeddings AS embedding
LEFT JOIN knowledge_chunks AS chunk ON chunk.id = embedding.knowledge_chunk_id
LEFT JOIN historical_questions AS question ON question.id = embedding.historical_question_id
WHERE vector_dims(embedding.embedding) <> embedding.embedding_dimension
   OR embedding.source_text_sha256 <> encode(
       sha256(convert_to(coalesce(chunk.text, question.text), 'UTF8')), 'hex'
   );

SELECT count(*) AS orphan_publication_drafts
FROM published_paper_versions AS publication
LEFT JOIN paper_draft_versions AS draft
  ON draft.paper_id = publication.paper_id AND draft.version = publication.version
WHERE draft.paper_id IS NULL;
```

Reconcile all source objects against the restored target bytes, not just object
metadata:

```text
for each source_documents row:
  target object exists at object_key
  downloaded byte count == size_bytes
  SHA-256(downloaded bytes) == checksum_sha256
```

A missing object, checksum mismatch, wrong curriculum provenance, or invalid
embedding is release-blocking.

### 3. Append-only trigger inventory and negative probes

Confirm expected non-internal triggers exist on audit, blueprints, generation
attempts, validation, candidate revision/review, paper version, archive, and
embedding relations:

```sql
SELECT tgrelid::regclass AS relation, tgname
FROM pg_trigger
WHERE NOT tgisinternal
ORDER BY tgrelid::regclass::text, tgname;
```

Run negative probes in an isolated transaction. The following blocks are safe:
if a trigger is missing, the deliberate exception rolls back the attempted
mutation; if it is present, SQLSTATE `23514` is caught.

```sql
DO $probe$
BEGIN
  BEGIN
    UPDATE admin_audit_events
    SET action = 'restore_probe_must_not_persist'
    WHERE id = (SELECT id FROM admin_audit_events LIMIT 1);
    RAISE EXCEPTION 'admin audit append-only trigger did not reject mutation';
  EXCEPTION WHEN check_violation THEN
    NULL;
  END;
END
$probe$;

DO $probe$
BEGIN
  BEGIN
    DELETE FROM published_paper_versions
    WHERE (paper_id, version) = (
      SELECT paper_id, version FROM published_paper_versions LIMIT 1
    );
    RAISE EXCEPTION 'published version append-only trigger did not reject mutation';
  EXCEPTION WHEN check_violation THEN
    NULL;
  END;
END
$probe$;
```

Repeat equivalent approved probes for immutable blueprints, generation
attempts, validation findings, candidate revisions/review events, archive
events, and embeddings. Require a fixture row for each probe; a zero-row update
is not evidence.

### 4. Published snapshot and content hash reconstruction

The SQL canonical hash and authoritative relational reconstruction must both
match every immutable publication:

```sql
SELECT count(*) AS publication_hash_mismatches
FROM published_paper_versions
WHERE content_hash <> encode(
  sha256(convert_to(paper_canonical_jsonb(snapshot), 'UTF8')), 'hex'
);

SELECT count(*) AS publication_snapshot_mismatches
FROM published_paper_versions
WHERE snapshot IS DISTINCT FROM paper_expected_publication_snapshot(
  paper_id, curriculum_version_id, version
);
```

Both counts must be zero. Then exercise application-level reconstruction for
every publication through the isolated admin publication-version read path.
That path calls `reconstruct_published_snapshot` and rejects a noncanonical,
hash-mismatched, or domain-invalid snapshot. Compare the returned
`content_hash`, ordered blueprint slots, candidate revision, validation
references, review history, and source provenance with the pre-backup evidence.
The bounded executable proof is:

```bash
uv run --project apps/api pytest \
  apps/api/tests/integration/test_backup_restore_postgres.py \
  -m backup_restore
```

The test uses disposable PostgreSQL 18/pgvector containers, never a persistent
project database.

### 5. Roles, grants, secrets, and object access

- Compare restored/recreated role attributes and memberships with reviewed
  infrastructure definitions; no unexpected superuser, replication, or
  bypass-RLS role may exist.
- Reapply grants intentionally because owner/ACL application was suppressed.
- Confirm application roles cannot write append-only relations directly or
  access backup buckets/keys.
- Confirm restored object access is limited to the isolated application role
  and every object checksum is valid.
- Ensure backup/decryption credentials are removed from target hosts and logs.

### 6. Readiness and controlled job recovery

1. Provision a fresh Valkey; do not import stale queue messages.
2. Start the API only, configured for the isolated DB and restored object
   target. Keep ingress private and workers/maintenance stopped.
3. Require `/api/v1/health/ready` to pass and inspect startup/migration,
   database, object, and cache telemetry.
4. Run read-only admin checks across source/extraction review, reviewed
   knowledge/provenance, RAG retrieval, analytics/blueprints, generation and
   validation history, review events, and published paper versions.
5. Start one worker/recovery actor class at a time. Run source-object
   reconciliation in its default tag-disabled mode first; review aggregate
   counts/findings before explicitly authorizing reversible app-owned tags.
   Verify PostgreSQL-backed queued/claimed/stale jobs reconcile idempotently
   without duplicate provider calls, publications, or audit events. Keep
   paid/external model calls disabled unless separately authorized.
6. Re-run counts, hashes, source checks, append-only probes, readiness, and
   security scans after reconciliation.
7. Obtain recovery owner, security, data owner, and application owner sign-off
   before any route change.

## Cutover and key rotation

At cutover, prevent split brain: exactly one environment may accept writes.
Revoke old application connections, update target grants/secrets, switch object
and DB endpoints atomically through deployment configuration, enable API write
traffic, then workers and maintenance in a controlled order. Observe ingestion,
job recovery, retrieval, generation cost/error telemetry, validation, review,
publishing, and object checksum errors.

Perform key rotation when recovery follows suspected compromise, backup access
exposure, or policy requires it:

- database application, worker, maintenance, backup, and restore credentials;
- S3/MinIO access keys and temporary sessions;
- KMS/KES grants and data-encryption key versions;
- production identity/session signing keys and active sessions where in scope;
- external provider credentials available to the affected environment.

Issue credentials from the secret manager and never copy old values from logs,
role dumps, or database archives. Re-encrypt future backups with the new key.
Do not retire an old decryption key until required retained backups have an
approved re-encryption/retirement plan and a restore has been proven.

## Failure rollback

The failure rollback rule is fail closed: any failed checksum, object reconciliation, `pg_restore`, Alembic, trigger,
canonical hash, provenance, authorization, readiness, or job-idempotency check
blocks cutover.

Before cutover:

1. Keep the original environment and protected backup untouched.
2. Stop all target processes and preserve restore logs/evidence.
3. Quarantine the isolated DB and object target. Destroy only disposable target
   resources with explicit incident/change approval; never run cleanup against
   source or backup buckets and never use a destructive mirror.
4. Correct the cause by creating another new empty target and restarting from
   verified artifacts. Do not use `--clean` to patch over a partial restore.
5. If the original environment is healthy and the recovery owner approves,
   route back to it and end the write freeze in a controlled order. Record the
   resulting recovery-point change.

After cutover, immediately stop writes if a release-blocking integrity issue is
found. If the old environment has accepted no later writes and remains valid,
use the approved routing rollback. If either side accepted writes, do not copy
back or run both: declare split-brain/data-reconciliation response, preserve
both data sets, and obtain data-owner approval for a deterministic merge or a
new recovery point.

## Evidence checklist

A recovery exercise is complete only when its evidence package contains:

- recovery-point ID, release/image digests, PostgreSQL/pgvector/client versions;
- approved RPO/RTO fields and actual phase timings, without unsupported claims;
- write-freeze start/end and active-writer evidence;
- encrypted off-host DB/object artifact identifiers, versions, checksums,
  retention/immutability, KMS key alias/version, and destination-side inventory;
- verified `SHA256SUMS`, archive list, role-shape review, Alembic head;
- source and target row counts for all critical relations;
- object key/size/SHA-256 reconciliation results;
- extension, trigger inventory, negative mutation probes, constraints/invariants;
- zero canonical publication hash and authoritative snapshot mismatches plus
  application reconstruction evidence;
- readiness and controlled worker recovery results;
- grant/authz review, secret cleanup, key rotation decision;
- failures, accepted limitations, approvers, and final cutover/rollback result.

## Upstream references

Repository behavior is authoritative for application-specific invariants. Tool
semantics and storage controls should be checked against the deployed versions:

- PostgreSQL 18 `pg_dump`: https://www.postgresql.org/docs/18/app-pgdump.html
- PostgreSQL 18 `pg_restore`: https://www.postgresql.org/docs/18/app-pgrestore.html
- PostgreSQL 18 `pg_dumpall`: https://www.postgresql.org/docs/18/app-pg-dumpall.html
- PostgreSQL SQL-dump recovery: https://www.postgresql.org/docs/18/backup-dump.html
- PostgreSQL password file: https://www.postgresql.org/docs/18/libpq-pgpass.html
- Amazon S3 Versioning: https://docs.aws.amazon.com/AmazonS3/latest/userguide/Versioning.html
- Amazon S3 replication: https://docs.aws.amazon.com/AmazonS3/latest/userguide/replication.html
- Amazon S3 Object Lock: https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock.html
- Amazon S3 Inventory: https://docs.aws.amazon.com/AmazonS3/latest/userguide/storage-inventory.html
- MinIO `mc mirror`: https://min.io/docs/minio/linux/reference/minio-mc/mc-mirror.html
- MinIO object versioning: https://min.io/docs/minio/linux/administration/object-management/object-versioning.html
- MinIO bucket replication: https://min.io/docs/minio/linux/administration/bucket-replication.html

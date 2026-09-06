# Backup and Restore Runbook

## Purpose and safety boundary

This runbook follows the [private/local Studio architecture](../SYSTEM_ARCHITECTURE.md#16-backup-and-disaster-recovery). The default source backend is the operator-owned **local POSIX filesystem**, not MinIO. A coordinated recovery point covers:

- PostgreSQL 18 schema/data, including pgvector, page candidates/review/ground truth, catalogue admission, upload receipts and durable jobs;
- the matching private `STORAGE_ROOT`: immutable `sources/`, `.metadata/` sidecars, retained `.source-uploads/` chunks and `fidelity-page-images/` artifacts;
- immutable benchmark/baseline evidence and release/configuration manifests held outside the active data root;
- cluster-level role definitions needed to recreate access, without role passwords;
- restore validation for provenance, append-only history and published papers.

The PostgreSQL scripts do **not** copy or verify those filesystem artifacts. S3/MinIO guidance below applies only when that optional provider is explicitly deployed; it is not a Studio prerequisite or a substitute for local artifact recovery.

This runbook does **not** authorize an in-place restore. Restore only into a newly created, isolated, empty database and a **new empty target filesystem** with the correct application owner and private modes (or an empty bucket/prefix for the optional S3 objects). Never overwrite/delete the source or backup. Keep the application, workers, maintenance scheduler and all writers stopped until every validation step passes. `scripts/ops/restore_postgres.sh` is verification-only by default and will not execute unless its empty-target guard and exact target-bound confirmation both pass; filesystem isolation must be verified separately.

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

| Field                                                           | Approved target     | Measured result                                                          | Evidence timestamp / exercise ID | Owner |
| --------------------------------------------------------------- | ------------------- | ------------------------------------------------------------------------ | -------------------------------- | ----- |
| RPO                                                             | deployment decision | last durable DB transaction and object represented by the recovery point |                                  |       |
| RTO                                                             | deployment decision | incident declaration to validated service cutover                        |                                  |       |
| Backup interval                                                 | deployment decision | observed interval and last-success age                                   |                                  |       |
| Retention / legal hold                                          | deployment decision | oldest and newest independently recoverable points                       |                                  |       |
| DB dump duration / bytes / throughput                           | n/a                 | measure                                                                  |                                  |       |
| Filesystem/artifact inventory and copy duration / bytes / count | n/a                 | measure                                                                  |                                  |       |
| Provisioning duration                                           | n/a                 | measure                                                                  |                                  |       |
| Filesystem/artifact restore and checksum duration               | n/a                 | measure                                                                  |                                  |       |
| Database restore duration                                       | n/a                 | measure                                                                  |                                  |       |
| Migration, invariant, readiness, and cutover duration           | n/a                 | measure                                                                  |                                  |       |

For RPO measurement, record the coordinated write-freeze timestamp, database snapshot start/end, latest represented audit event, filesystem/artifact inventory timestamps and any database/artifact mismatch. For RTO, record incident decision, provisioning, retrieval/decryption, artifact copy, DB restore, validation and cutover separately. A failed exercise is evidence, not a result to average away.

## Repository-verified data map

This inventory is derived from the current repository, not from an assumed
production schema.

### Platform and schema sources

- `compose.yaml` selects local storage, binds `EXAM_GURU_DATA_PATH` (default `./.exam-guru-data`) to `/data` in the API, worker and maintenance containers, and pins PostgreSQL 18/pgvector 0.8.6 with a persistent database volume. Studio ports bind to loopback. MinIO and its bucket initializer exist only in the optional `s3` profile.
- The release checkpoint is **`0038_upload_request_identity`**, declared in `apps/api/migrations/versions/0038_source_upload_request_identity.py`. Always resolve the actual selected release's Alembic head and compare the restored revision; do not treat this literal as a permanent target.
- Migrations under `apps/api/migrations/versions/`: `0001_enable_pgvector.py` enables vectors; `0003_admin_audit_events.py` protects auditing; `0005_source_documents.py`, `0006_extraction_persistence.py` and `0017_ocr_worker_pipeline.py` retain immutable source/extraction identity and legacy review provenance.
- `0007_knowledge_foundation.py`, `0011_analytics_runs.py` through `0016_published_papers.py`, and `0018_embedding_jobs.py` / `0019_extraction_outbox.py` preserve knowledge/vectors, analytics/blueprints, generation attempts, validation, human review, immutable publication and job recovery. `0020_restore_safe_canonical_json.py` preserves publication hashing under PostgreSQL's empty restore `search_path`; `0021_storage_reconciliation.py` adds non-destructive reconciliation history, not the current head.
- `0033_source_page_fidelity.py` adds original page counts, immutable raw UTF-8/separate NFC candidates, versioned review events/current state, fixed benchmarks, human ground-truth references and `SourceReadJob` progress/leases. Image bytes remain outside PostgreSQL; versioned image references live in candidate provenance.
- `0034_catalogue_admission.py` adds append-only admission decisions/current pointers bound to the exact active curriculum scope fingerprint. Metadata admission is independent of page ground truth.
- `0035_verified_knowledge_lineage.py` adds `source_candidate_id` links on chunks/historical questions and current verified-candidate, exact NFC-span, admitted-scope guards through embedding, generation, validation, review and new publication. It preserves legacy history without manufacturing eligible bindings.
- `0036_resumable_source_uploads.py` adds retained upload sessions/chunk receipts and widens source size storage; `0038_source_upload_request_identity.py` adds immutable owner-scoped `request_id` identity and creation-audit checks. Finalization state lives in `source_upload_sessions`, not a separate upload-job table.
- `0037_studio_fixture_quarantine.py` adds `source_documents.quarantined_for_teacher_use` plus audited, versioned quarantine/restore guards. There is no separate quarantine table and no filename-based automatic deletion.
- `apps/api/src/exam_guru_api/infrastructure/object_storage.py` implements immutable content-addressed local originals and private reconciliation sidecars; `infrastructure/private_artifacts.py` implements retained private chunks. `documents/page_images.py` binds images to source/page/rasterizer/hash/size and fails closed on missing declared artifacts. `documents/page_reading_jobs.py`, `documents/upload_jobs.py`, `maintenance.py` and `worker.py` define reading/finalization and recovery dispatch.
- `apps/api/src/exam_guru_api/papers/domain.py` and `papers/serialization.py` define canonical UTF-8 publication hashing/reconstruction. Later curriculum, teacher-paper and subject-quality relations are included below; a whole-database backup is mandatory.

Publication invariants are implemented in `apps/api/migrations/versions/0016_published_papers.py`; the storage provider contract and implementations are in `apps/api/src/exam_guru_api/infrastructure/object_storage.py`. Restore checks must use the matching release versions of both.

### Critical PostgreSQL relations

Back up the database as a whole. The following list is for inventory and
post-restore evidence; it is not a suggestion to perform partial table dumps.

| Capability                                | Critical relations                                                                                                                                                                                              |
| ----------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Multi-grade curriculum/taxonomy           | `exam_configurations`, `media`, `subjects`, `curriculum_versions`, `curriculum_units`, `curriculum_lessons`, `taxonomy_nodes`, `curriculum_lesson_taxonomy_mappings`                                            |
| Catalogue admission                       | `catalogue_admission_decisions`, `catalogue_admission_current`                                                                                                                                                  |
| Sources and legacy extraction             | `source_documents`, `source_pages`, `extracted_blocks`                                                                                                                                                          |
| Page fidelity and reading                 | `source_page_text_candidates`, `source_page_review_events`, `source_page_review_states`, `source_read_jobs`                                                                                                     |
| Fixed benchmark and ground truth          | `source_fidelity_benchmarks`, `source_fidelity_benchmark_pages`, `source_page_ground_truth`                                                                                                                     |
| Resumable uploads/finalization            | `source_upload_sessions`, `source_upload_chunks`                                                                                                                                                                |
| Fixture quarantine                        | `source_documents` quarantine/use/scope fields and matching `admin_audit_events` (not a separate table)                                                                                                         |
| Storage reconciliation                    | `storage_reconciliation_state`, `storage_reconciliation_runs`, `storage_orphan_findings`                                                                                                                        |
| Knowledge and RAG                         | `historical_questions`, `knowledge_chunks` (including `source_candidate_id`), `embedding_configurations`, `knowledge_embeddings`, `embedding_jobs`                                                              |
| Analytics and deterministic blueprint     | `analytics_runs`, `paper_blueprints`                                                                                                                                                                            |
| Generation and validation                 | `generation_runs`, `generation_attempts`, `generation_jobs`, `validation_runs`, `validation_findings`                                                                                                           |
| Human review and subject-quality evidence | `question_candidates`, `question_candidate_revisions`, `candidate_review_events`, `subject_quality_feedback`, `subject_quality_eval_case_versions`, `subject_quality_eval_runs`, `subject_quality_eval_results` |
| Teacher paper/programme orchestration     | `assessment_programme_policy_versions`, `assessment_programme_policy_scopes`, `teacher_paper_jobs`, `teacher_paper_slots`, `teacher_paper_slot_runs`, `teacher_paper_marking_confirmations`                     |
| Paper publication                         | `practice_papers`, `paper_draft_versions`, `paper_draft_candidates`, `published_paper_versions`, `paper_archive_events`                                                                                         |
| Audit/schema version                      | `admin_audit_events`, `alembic_version`                                                                                                                                                                         |

Valkey is not the durable source of truth. Provision it clean; do not replay stale messages. After **both database and filesystem checks** pass, recover only eligible PostgreSQL-backed work: source reading, upload finalization, legacy extraction, generation, embeddings, teacher-paper orchestration and non-destructive storage reconciliation. Preserve terminal failures and completed history; recovery is not bulk retry, automatic trust or permission to enable paid providers.

### Critical local filesystem artifacts

`STORAGE_ROOT` means `EXAM_GURU_STORAGE_ROOT` (Compose: `/data`, backed by `EXAM_GURU_DATA_PATH`). Inventory and copy all four namespaces with the database, including hidden directories and historical references:

| Namespace                                 | Recovery identity and checks                                                                                                                                                                                                                       |
| ----------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `sources/<sha256-prefix>/<sha256>.pdf`    | Every `source_documents.object_key`, byte size and SHA-256, including removed/quarantined sources and published-history dependencies. The prefix is the first two lowercase hash characters.                                                       |
| `.metadata/sources/`                      | Private bounded reconciliation sidecars, including application and reserved operator tags. These are not arbitrary S3 provider tags.                                                                                                               |
| `.source-uploads/<session-id-hex>/`       | Retained chunks keyed by hexadecimal byte offset; reconcile `source_upload_sessions` owner/request ID, size/status/version/offset and every `source_upload_chunks` receipt/hash. Completed/failed staging remains retained and charged to quota.   |
| `fidelity-page-images/<artifact-id-hex>/` | Immutable chunked PNG artifacts referenced by **all** candidate versions' `provenance.page_image`, not only current candidates. Verify schema, source/page identity, rasterizer/version, dimensions/DPI, byte size, whole-image and chunk SHA-256. |

The private artifact adapter requires application-owned `0700` directories and `0600` chunk files, safe non-symlink paths, and a POSIX filesystem supporting hard links, atomic replacement and directory `fsync`. Originals and sidecars must retain their private application ownership/permissions too. Verify the restored copy as the actual application UID/GID (currently `10001:10001` in the standard image), not a default root `docker compose exec` user. Never loosen source permissions to bypass a failed check.

A PostgreSQL dump contains page-image **references**, not their bytes. A declared missing/corrupt image is a failure, not a cache miss; do not silently regenerate it or rewrite its reference. Keep original raw candidate bytes, NFC views, engine/configuration versions, benchmark membership and human evidence unchanged. Include private baseline/evidence files outside `STORAGE_ROOT` in the coordinated inventory; never commit them to Git.

Reconciliation detects candidates but never deletes originals, sidecars, upload staging or page-image history. There is **no automatic purge** or restore-time quota reset. Record already-missing artifacts and unreferenced extras explicitly; missing evidence is not made recoverable by excluding it from the manifest, marking it trusted or recreating a fixture. Any separately approved retention/deletion process is outside this runbook.

### Optional S3/MinIO object inventory

For a deployment explicitly using the S3 byte-storage provider, also preserve every DB-referenced object version, content type, application `sha256` metadata, legal hold and operator tag. Verify bytes/size/SHA-256, not ETag (which is not portable SHA-256 for multipart/encrypted objects). App-owned `exam-guru-orphan-candidate` / `exam-guru-orphan-detected-at` tags remain optional and disabled by default. Current resumable upload, seekable source reading and durable page-image paths do **not** gain S3 support from this runbook; switching providers needs a separately verified migration, never a hidden local store or whole-file fallback.

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

In particular, the database archive does not contain original PDFs, reconciliation sidecars, retained upload chunks, page-image PNG chunks or private benchmark output files. Capture them at the same coordinated recovery point. Keep their inventories/checksums in the **outer encrypted evidence envelope**: the database bundle's `SHA256SUMS` is deliberately closed over `backup-metadata.txt`, `database.dump` and `database.dump.list`; do not add filesystem inventory entries to it or bypass that guard.

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
2. Confirm recent backup monitoring is healthy, capacity exceeds the measured database plus originals, sidecars, **all retained upload/image artifacts** and external evidence with margin, and the off-host destination is in a different failure/administration domain. Upload reservation limits are operational quotas, not backup-space estimates.
3. Use an encrypted staging filesystem with mode `0700`. Do not stage on a
   developer laptop, application container layer, or unencrypted shared disk.
4. Obtain short-lived least-privilege backup credentials through the deployment
   secret mechanism. Do not put a password or credential-bearing URL in shell
   arguments, command history, CI output, or the recovery record.
5. Configure libpq through a protected `PGPASSFILE`, client certificate, peer
   authentication, or approved service configuration. The scripts reject
   `PGPASSWORD` so it cannot be inherited accidentally. For existing local configuration, use the approved secure helper with the known database identity and a temporary mode-`0600` password file; never discover, print or log credentials or enable shell tracing.

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

A PostgreSQL dump is transactionally consistent only for its database snapshot, not for filesystem writes. Originals, image chunks and upload chunks may be durable before their DB references/receipts; a pre-copy can contain unreferenced extras, but a DB reference without matching bytes is not recoverable. Coordinate all namespaces, not only `sources/`:

1. If pre-copying while the service is available, use a separate private destination and record it as provisional, not a recovery point. Do not alter or delete the source.
2. Enter a verified application write freeze: disable uploads/admin writes, pause maintenance, and drain then stop API/worker processes that can append upload chunks, finalize uploads, read/render pages, review/admit metadata, embed, generate, validate, publish or audit. Verify writers are stopped at process/filesystem and database levels, not just in the UI.
3. Record UTC freeze time, latest audit/review/job versions and upload offsets. Ensure no active application write transactions remain; identify read-only inspection connections with `application_name`.
4. Inventory every DB source reference under the freeze:

   ```bash
   psql --no-psqlrc --set=ON_ERROR_STOP=1 --csv \
     --command="SELECT object_key, checksum_sha256, size_bytes FROM source_documents ORDER BY object_key" \
     > "<encrypted-staging>/<recovery-id>-source-objects.csv"
   ```

   Also inventory all candidate image references and retained upload session/chunk receipts, including historical, completed, failed and quarantined records. Record relative keys, byte sizes, hashes, owner/mode and source-to-artifact identity; keep private content and credentials out of logs.

5. While frozen, take the final coordinated copy/snapshot of `sources/`, `.metadata/`, `.source-uploads/`, `fidelity-page-images/` and separately held private benchmark/evidence files. Include hidden directories and historical images; verify the copied bytes against the frozen inventories. Preserve approved owner/mode/mtime metadata. Never use a delete/remove mirror option or purge retained staging. A provisional copy is not complete until this final pass and comparison succeed.
6. Run the database backup into a destination that does not exist:

   ```bash
   scripts/ops/backup_postgres.sh \
     --destination "<encrypted-staging>/<recovery-id>-postgres"
   ```

7. Capture role shape (without passwords), PostgreSQL/pgvector/client versions, release/image digests, Alembic heads/current revision, all critical row counts, storage backend/root/bind identity, application UID/GID and artifact manifests in the outer recovery envelope. The dump and filesystem inventory must describe the same frozen state.
8. Independently verify the script's closed checksum manifest and archive listing:

   ```bash
   (cd "<encrypted-staging>/<recovery-id>-postgres" && sha256sum --check --strict SHA256SUMS)
   pg_restore --list "<encrypted-staging>/<recovery-id>-postgres/database.dump" > /dev/null
   ```

9. If any check fails, keep writes frozen until the recovery owner decides whether to repeat the coordinated point or safely resume. Record missing pre-existing evidence honestly; never label a partial DB/filesystem pair recoverable. Source inode/device/ctime evidence proves the source was untouched; those identities need not equal a new cross-filesystem copy's identities.

### 3. Protect filesystem/artifact copies off-host

The local default requires an independently administered encrypted copy on another disk/machine/NAS or another approved off-host destination. Protect originals, sidecars, retained upload chunks, page images and private benchmark evidence together with the matched database bundle. Use authenticated encryption and approved transport, retention/immutability and access controls; a second directory or volume on the Studio host is not off-host recovery. Verify the destination inventory and sample/read back according to the approved policy, explicitly recording any sampling limitation. Do not treat the existing on-host pre-remediation dumps or a verification-only restore as completion of this step.

#### Optional S3/MinIO provider controls

Only when an S3-compatible provider is explicitly deployed, preserve these controls:

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

The script output is plaintext database material even when its staging disk is encrypted. Package it with the matched filesystem/artifact inventories and role/config evidence, then apply approved authenticated client-side encryption or, for an explicitly selected object destination, upload over TLS with mandatory KMS encryption and immutability. The associated filesystem/evidence copies require the same protection. Keep encryption private keys outside the backup administration boundary; never put key material in command arguments or logs.

Record both:

- the internal plaintext `SHA256SUMS` (inside the encrypted envelope), and
- the SHA-256/checksum, size, approved encryption-key identifier/version (KMS alias where used), backup/artifact version identity and retention status of every encrypted off-host component; include provider object version IDs when applicable.

A checksum detects corruption; it does not authenticate a maliciously replaced
artifact. Sign the evidence manifest or rely on an approved authenticated
encryption/signing mechanism plus immutable audit logs.

### 5. Close the backup window

1. Verify the off-host DB/filesystem/evidence inventory, encryption, retention/immutability and any replication status from the destination side.
2. Compare source keys, image references, retained upload receipts, sizes and SHA-256 values with that inventory. Read back/hash all artifacts when practical; otherwise record the approved checksum mechanism and sampling limitation. Do not equate ETag with SHA-256 or a successful database archive listing with artifact verification.
3. Remove **temporary plaintext backup staging only** under the approved retention/sanitization process after off-host verification, and remove the temporary `PGPASSFILE`. This is not authorization to delete `.source-uploads`, source/page-image history or the protected original corpus; no automatic purge is implemented.
4. Resume writers/workers in a controlled order after integrity checks; verify readiness and idempotent job recovery.
5. A backup is not marked usable until a scheduled isolated restore of the coordinated database **and artifacts** proves it.

## Restore procedure

### 1. Declare the restore and preserve rollback

1. Identify the exact matched DB bundle, local filesystem snapshot/artifact manifests, external benchmark evidence, release image, PostgreSQL/pgvector versions, role/grant definition and encryption-key version.
2. Keep the original environment and backup unchanged. If the source is reachable, maintain the write freeze and take a final forensic snapshot where authorized.
3. Provision an isolated private network/Compose project, **new empty PostgreSQL 18 database, fresh Valkey and a new empty local target filesystem**. Verify the target is neither the source nor a backup path/volume, including resolved paths and mount identity. Use separate ports/configuration; no existing API, worker, scheduler, production route or default Studio bind mount may point at it. For optional S3 objects, also allocate a new empty bucket/prefix.
4. Restrict database `CONNECT` and filesystem/bucket access to recovery operators. The script's session guard does not close the race between its query and `pg_restore`; network, process and role isolation remain mandatory. Preparing ownership does not authorize starting the application.
5. Confirm the target has compatible pgvector binaries. Do not run application migrations into the empty database before archive restore: the archive contains schema, extension declarations and migration state.

### 2. Restore local filesystem artifacts first

Restore the matching artifacts before database references become available to an application:

1. Verify authenticated encryption/signature and artifact checksums; decrypt only onto an encrypted restricted staging volume.
2. Prove the new target root is empty, non-symlinked and on a suitable POSIX filesystem. Copy the exact recovery-point `sources/`, `.metadata/`, `.source-uploads/`, `fidelity-page-images/` and separately held evidence into their isolated destinations, preserving relative keys and all retained versions. Do not overwrite/delete source or backup paths, merge with an existing Studio root, omit hidden directories or purge staged uploads. Unexpected pre-existing target content requires another new target, not cleanup against the source.
3. Establish the approved application UID/GID and private modes on **the new copy only**: artifact directories `0700`, chunk files `0600`, with private original/sidecar permissions. The standard image uses `10001:10001`, but verify the selected release's actual process owner. Record any deliberate target-owner mapping; do not relax modes or run the adapter as root to mask mismatches.
4. Verify target bytes against every source row, upload receipt and declared image artifact, including historical candidate images. Validate full/chunk hashes, sizes, source/page/rasterizer identity and safe paths. Restore sidecar maps without overwriting reserved operator tags. Record extra/unreferenced artifacts; do not delete them. A missing declared image must remain a blocking failure, not be silently re-rendered.
5. Keep all application writers/workers/maintenance stopped until the DB and artifact checks below pass. For optional S3 copies preserve content type, `sha256` metadata, legal holds and retained versions; use only non-deleting copy/mirror operations and keep target credentials isolated.

Filesystem recovery is an operator-controlled part of the coordinated restore, not an operation performed by `restore_postgres.sh`.

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
uv run --project apps/api alembic -c apps/api/alembic.ini heads
uv run --project apps/api alembic -c apps/api/alembic.ini current --check-heads
uv run --project apps/api alembic -c apps/api/alembic.ini check
```

The restored revision must be a head for the selected release (`0038_upload_request_identity` at this checkpoint). If restoring an older supported backup to a newer release, first capture all pre-migration checks, then run `alembic upgrade head` under a separate approved step and repeat the entire database/artifact/lineage suite. Preserve exact IDs, hashes, raw/NFC candidate text, review/ground-truth and published history; migration is not permission to backfill trust. Never use downgrade, constraint/trigger removal or `alembic stamp` as a recovery shortcut.

### 2. Counts and critical lineage

Compare pre-backup and restored row counts for every critical relation listed above, including page states/candidate/event versions, benchmark membership/ground truth, catalogue decisions, quarantine audits, upload receipts and jobs. Under a coordinated freeze they must match; explain every difference and preserve zero counts where appropriate. A zero-ground-truth source must not gain references, verified pages or catalogue admission during restore. Compare latest whole-document job status separately from historical attempts; completed reading and inventory/footer totals are not educational approval.

Inspect at least these existing relationships:

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

Reconcile database references against restored **bytes**, not just filesystem/provider metadata:

```text
for each source_documents row:
  target original exists at object_key
  byte count == size_bytes
  SHA-256(bytes) == checksum_sha256
for every declared image artifact in every source_page_text_candidates version:
  source/page/schema/rasterizer identity matches the retained provenance
  artifact size, whole-image/chunk hashes and PNG structure are valid
for each retained source_upload_chunks receipt:
  chunk at the session/offset key matches its size and SHA-256
  receipt sequence agrees with the session's committed offset
```

Also compare raw candidate UTF-8 bytes, separate NFC text/hash and provenance with the frozen evidence; validate page-state versions/current pointers against their immutable events, and ground truth against benchmark membership, candidate/source hashes and human confirmation events. Verify catalogue decision/current-pointer versions, exact scope fingerprints and quarantine upload-audit evidence. Do not normalize with NFKC or manufacture missing review events.

`knowledge_chunks` and `historical_questions` retain exact document/page/block/`source_candidate_id` links; generation snapshots bind candidate hashes and record versions. Check these against the restored evidence. Preserve null legacy bindings and legitimately stale historical records as history, while `knowledge_source_lineage_is_current`, `knowledge_record_is_eligible` and `generation_context_lineage_is_current` must still reject their **new** use. Do not require all preserved history to become eligible or rewrite published versions to pass current source gates.

A missing declared artifact, checksum mismatch, broken immutable reference, wrong curriculum provenance or invalid embedding blocks complete recovery/cutover. A declared missing image is not a cache miss. Keep already-known missing-fixture evidence explicit; neither deletion nor silent recreation repairs the backup.

### 3. Append-only trigger inventory and negative probes

Compare the full non-internal trigger inventory with the selected release: audit, blueprints, generation attempts, validation, candidate revisions/review, paper/archive versions and embeddings, **plus page candidates/events/ground truth/benchmarks, catalogue admission, upload identity/receipts, quarantine evidence and verified downstream lineage**. Check the version/evidence/lease guards on mutable current-state and job/session rows; they are not permission to alter protected history:

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

Repeat equivalent approved probes for immutable blueprints, generation attempts, validation findings, candidate/review history, archive events, embeddings, page candidates/events/ground truth, benchmark membership, catalogue decisions and upload receipts/request identity. Include rejection of forged verification, unaudited quarantine and stale-candidate downstream writes. Require an actual row for each probe; a zero-row mutation is not evidence. Where the restored table is legitimately empty (such as ground truth), use a separate disposable fixture database for regression probes and record that scope—never manufacture human references or fixtures in the restored corpus. Keep every negative probe transactionally rolled back.

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

The test uses disposable PostgreSQL 18/pgvector containers and synthetic data, never a persistent project database. It proves the database archive/reconstruction path, **not a full local-artifact restore**. Additional contract coverage lives in `apps/api/tests/integration/test_source_fidelity_postgres.py`, `test_resumable_uploads_postgres.py`, `test_verified_knowledge_lineage_postgres.py` and `apps/api/tests/test_page_images.py`; these regressions do not replace this recovery point's byte/lineage verification or human adjudication.

### 5. Roles, grants, secrets, and filesystem/object access

- Compare restored/recreated role attributes and memberships with reviewed
  infrastructure definitions; no unexpected superuser, replication, or
  bypass-RLS role may exist.
- Reapply grants intentionally because owner/ACL application was suppressed.
- Confirm application roles cannot bypass append-only/lineage guards to mutate protected history or access backup destinations/keys. Restore owner/ACL suppression is not a substitute for least-privilege grants.
- Confirm only the approved isolated application owner can access the target data root; validate every private directory/file mode, no-follow path and source/image/chunk checksum as that owner. The raw directory must never be served statically. `SOURCE_READ` authorization and private/no-store controls remain required for PDF/image endpoints.
- When S3 is explicitly used, restrict object access to its isolated application role and verify every retained object version/checksum.
- Ensure temporary backup/decryption credentials are removed from target hosts and logs.

### 6. Readiness and controlled job recovery

1. Provision a fresh Valkey; do not import stale queue messages. Complete database/artifact/hash/permission/lineage checks **before** allowing any job recovery.
2. Start the API only against the isolated database and restored local root (`EXAM_GURU_STORAGE_BACKEND=local`, the intended `EXAM_GURU_STORAGE_ROOT`/host bind). Keep ingress private and all write clients, workers and maintenance stopped. Do not accidentally reuse the default Studio bind/ports or start Compose's all-services workflow against the restore.
3. Require `/api/v1/health/ready` to pass and inspect startup/migration, database, filesystem/storage and cache telemetry. Readiness alone does not prove artifact completeness.
4. Run read-only admin checks across original PDF/image access, candidate/review/benchmark history, metadata admission/quarantine, reviewed knowledge/provenance, RAG, generation/validation and published versions. Verify stale/legacy lineage remains ineligible for new content use, with no automatic ground truth or changed published history.
5. Start only the deliberately selected worker queue/recovery class at a time; keep the maintenance scheduler stopped until this controlled pass is complete. Run source-object reconciliation tag-disabled first. Then authorize eligible PostgreSQL-backed recovery below, verifying each class's version/lease checks and idempotent progress. Do not bulk retry terminal failures, relax resource budgets or enable paid/external calls without separate authorization. Source-semantic diagnostics remain disabled/unwired by default.
6. Re-run counts, hashes, artifact/source checks, append-only probes, readiness and security scans after recovery. Explain every newly appended attempt/event; original protected history must be unchanged.
7. Obtain recovery owner, security, data owner and application owner sign-off before cutover or routine maintenance resumes.

The registered actors in `apps/api/src/exam_guru_api/maintenance.py` and `worker.py` include:

| Durable work                  | Recovery actor / requirement                                                                                                                                                                                  |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Source-page reading           | `documents.page_reading_jobs.recover_source_read_jobs`: queued/expired claims resume from persisted page progress/configuration; preserve human/verified/excluded work and prior failures.                    |
| Resumable upload finalization | `documents.upload_jobs.recover_source_upload_jobs`: pending/expired finalization resumes only with matching retained chunks/receipts, owner/request identity and immutable original checks; no staging purge. |
| Legacy extraction             | `documents.jobs.recover_extraction_jobs`: recover its durable outbox/lease lifecycle without replacing page-fidelity history.                                                                                 |
| Embedding                     | `knowledge.embedding_jobs.recover_embedding_jobs`: current verified-source eligibility and versioned provider configuration still apply.                                                                      |
| Generation                    | `generation.jobs.recover_generation_jobs`: recheck current source/context lineage; no replay of stale provider requests as approved output.                                                                   |
| Teacher papers                | `teacher_papers.jobs.recover_teacher_papers`: preserve programme/slot/marking/review versions and publishing gates.                                                                                           |
| Source-object reconciliation  | `storage_reconciliation.jobs.reconcile_source_objects`: inspect findings with tags disabled by default; never delete artifacts.                                                                               |

## Cutover and key rotation

At cutover, prevent split brain: exactly one environment may accept writes. Revoke old application connections, update target grants/secrets, and switch the matched **database and local data-root bind** (plus optional provider endpoints) together through approved deployment configuration. Enable API writes, workers and maintenance in a controlled order. Observe upload finalization, source-page reading/artifact errors, legacy ingestion, job recovery, retrieval, generation cost/error telemetry, validation, review and publishing. Keep the former environment and protected backup unchanged for the approved rollback window.

Perform key rotation when recovery follows suspected compromise, backup access
exposure, or policy requires it:

- database application, worker, maintenance, backup, and restore credentials;
- off-host filesystem/backup access credentials and, where deployed, S3/MinIO keys and temporary sessions;
- approved backup/data-encryption key versions and any KMS/KES grants;
- production identity/session signing keys and active sessions where in scope;
- external provider credentials available to the affected environment.

Issue credentials from the secret manager and never copy old values from logs,
role dumps, or database archives. Re-encrypt future backups with the new key.
Do not retire an old decryption key until required retained backups have an
approved re-encryption/retirement plan and a restore has been proven.

## Failure rollback

Fail closed: any failed source/image/chunk checksum, filesystem ownership/path check, reconciliation, `pg_restore`, Alembic, trigger, canonical publication hash, provenance, authorization, readiness or job-idempotency check blocks cutover. Current source-page and generation guards require the exact restored candidate/versioned artifacts and admitted lineage. Never rewrite hashes, candidate pointers, ground truth or catalogue decisions, disable guards, regenerate an image under its old reference, or downgrade the schema to make recovery pass.

For failure rollback before cutover:

1. Keep the original environment and protected backup untouched.
2. Stop all target processes and preserve restore logs/evidence.
3. Quarantine the isolated database and filesystem target (and optional bucket). Destroy only disposable target resources with explicit incident/change approval; never clean up source/backup paths, retained upload chunks or image history, and never use a destructive mirror.
4. Correct the cause by creating another **new empty** database/filesystem target and restarting from the same validated release/DB/artifact recovery point, or an explicitly selected complete alternative. Do not use `--clean`, overwrite a partial restore or substitute newly inferred source evidence for missing originals.
5. If the original environment is healthy and the recovery owner approves, route back to its matched database/filesystem and end the write freeze in a controlled order. Record the recovery-point change and recheck lineage before any job resumes.

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
- encrypted off-host database/filesystem/evidence artifact identifiers, versions, checksums, retention/immutability, encryption-key identity/version and destination-side inventory (provider version IDs/KMS aliases where applicable);
- verified closed `SHA256SUMS`, archive list, role-shape review and the selected release's actual Alembic head;
- source/target row counts for all critical relations, including retained failures, candidate/review/ground-truth/benchmark versions, catalogue/quarantine and upload receipts;
- original, sidecar, retained upload and all declared page-image key/size/hash reconciliation, plus local root/mount identity, owner/mode and safe-path checks;
- exact candidate/NFC-span/current-lineage checks with unchanged raw source/benchmark and published history, no fabricated trust and explicit known missing-artifact evidence;
- extension/trigger inventory, rolled-back negative mutation probes and constraints/invariants, identifying any separate synthetic fixture scope;
- zero canonical publication hash and authoritative snapshot mismatches plus application reconstruction evidence;
- readiness and controlled source-reading/upload-finalization plus legacy recovery results, with no stale queue replay or unapproved provider calls;
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

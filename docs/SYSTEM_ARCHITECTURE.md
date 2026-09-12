# AI Exam Guru — System Architecture

## Status

This is the **authoritative system architecture document** for AI Exam Guru.

It records the target deployment, data-boundary, storage, publishing, security and runtime architecture for the product as a whole. V1 phase documents may add implementation detail, but they must not silently contradict this document. If the architecture changes, update this file in the same change.

## 1. Architectural intent

AI Exam Guru is split into two deliberately different environments:

1. **Exam Guru Studio — private/local content and AI factory**
2. **Exam Guru Student — hosted/public student platform**

The core rule is:

> Raw educational source material and the AI/RAG content-production system stay local. Only human-approved, validated, versioned student-ready content is published to the hosted student platform.

This design is intentional for:

- low infrastructure cost while the product is bootstrapped and seeking investment;
- strong isolation of syllabus, teacher-guide, past-paper and RAG source material;
- use of existing 1–2 TB local machines instead of paid bulk cloud storage;
- lower production attack surface;
- predictable student-serving cost and latency;
- human-controlled publishing rather than live LLM generation for students.

## 2. Top-level architecture

```text
                         OUTBOUND ONLY WHERE REQUIRED
                    ┌──────────────────────────────────┐
                    │ OpenAI / approved AI providers  │
                    └────────────────▲─────────────────┘
                                     │
                                     │ HTTPS outbound
                                     │
┌────────────────────────────────────┴────────────────────────────────────┐
│                 EXAM GURU STUDIO — PRIVATE / LOCAL                      │
│                                                                          │
│  Docker Compose on an operator-owned PC/workstation                      │
│                                                                          │
│  ┌──────────────┐   ┌──────────────┐   ┌─────────────────────────────┐  │
│  │ Next.js      │   │ FastAPI      │   │ Workers / Valkey           │  │
│  │ teacher UI   │──▶│ domain/API   │──▶│ OCR, ingest, generation    │  │
│  └──────────────┘   └──────┬───────┘   └─────────────────────────────┘  │
│                            │                                             │
│            ┌───────────────┼────────────────┐                            │
│            │               │                │                            │
│            ▼               ▼                ▼                            │
│   Local filesystem   PostgreSQL+pgvector   Local audit/config            │
│   raw materials      metadata/chunks/RAG   generation/review data        │
│                                                                          │
│  NO PUBLIC INBOUND ACCESS TO RAW MATERIAL OR RAG DATA                     │
└──────────────────────────────┬───────────────────────────────────────────┘
                               │
                               │ explicit human-approved publish action
                               │ signed/versioned publish package
                               ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                    EXAM GURU STUDENT — HOSTED                            │
│                                                                          │
│  Student authentication / subscriptions                                  │
│  Published paper catalog                                                 │
│  Exam runner                                                              │
│  Server-side marking                                                      │
│  Student attempts / progress / analytics                                 │
│                                                                          │
│  Stores only student-runtime data and approved published content.         │
│  Does NOT require raw teacher guides, source PDFs, RAG corpus or vectors. │
└──────────────────────────────────────────────────────────────────────────┘
```

## 3. Exam Guru Studio — local/private content factory

### 3.1 Runtime model

The Studio is intended to run locally using versioned Docker images and Docker Compose.

Local services may include:

- Next.js teacher/content-operator UI;
- FastAPI backend;
- PostgreSQL + pgvector;
- Valkey;
- background workers;
- OCR/extraction tooling;
- provider-independent LLM and embedding adapters;
- OpenAI as the initial external model provider.

The local Studio is not intended to be a public internet service.

The actual local Studio with the real Grade 3/4/5 source corpus is the primary product-acceptance environment. GitHub provides source control, code versioning/collaboration and secondary regression/security checks; it is not a Studio runtime dependency or a backup of the private database/source library. Failures caused by a current change and genuine security/data-integrity failures must be fixed. Unrelated external infrastructure or pre-existing CI failures must be classified and recorded without blocking otherwise safe local product work. Audit, secret, test and integrity controls must not be disabled or weakened.

Local browser acceptance may exercise owner-authorized real-source workflows through normal application APIs, with read-only inspection first and no fabricated human approval. Synthetic fixtures remain confined to the attested disposable test runtime. A unit-test or cached-browser pass is not equivalent to real-source acceptance or fresh-environment installer proof.

### 3.2 Network boundary

Default network posture:

```text
Internet -> Local Studio                  DENY
Local Studio -> approved external APIs    ALLOW outbound as required
Local browser -> localhost Studio         ALLOW
Local containers -> local data services   ALLOW
```

Do not expose PostgreSQL, Valkey, raw-material storage, OCR services or internal APIs through router port-forwarding or public ingress.

If LAN access is later required, it must be an explicit security decision rather than a default.

## 4. Local storage architecture

### 4.1 Raw material storage

Raw educational files are stored on the host machine's filesystem, outside ephemeral container filesystems.

Examples include:

- syllabuses;
- teacher guides;
- past papers;
- marking schemes;
- evaluation reports;
- scanned source pages;
- approved source images or student-facing source assets where needed.

The application owns keys beneath a configurable private storage root; teachers do not manage the directories. Current durable namespaces are:

```text
STORAGE_ROOT/
├── sources/<sha256-prefix>/<sha256>.pdf
├── .metadata/sources/       # bounded local reconciliation sidecars
├── .source-uploads/         # retained resumable-session chunks
└── fidelity-page-images/   # immutable comparison/OCR-image artifacts
```

These are the implemented storage contracts, not a requirement to create speculative working/export directories. Backup copies require a separate protected destination, not merely a directory beside the active originals.

### 4.2 Docker persistence

Raw files must **not** live only inside a Docker container layer. Compose bind-mounts:

```text
HOST:      EXAM_GURU_DATA_PATH (default ./.exam-guru-data)
CONTAINER: /data (API, worker and maintenance)
```

Rebuilding, replacing or upgrading Docker images must not delete originals, retained upload progress or page-image evidence. The filesystem must support private POSIX ownership/permissions, hard links, atomic replacement and directory `fsync`.

### 4.3 Storage abstraction

The domain depends on the `ObjectStorage` port. `LocalFileObjectStorage` is the default, with byte and seekable streaming operations. `S3ObjectStorage` supports the optional S3/MinIO legacy byte path; streaming/resumable/image-artifact support is not implemented for it.

Current backend settings (Compose explicitly uses `/data`):

```env
EXAM_GURU_STORAGE_BACKEND=local
EXAM_GURU_STORAGE_ROOT=/data
```

`STORAGE_ROOT` elsewhere in this document abbreviates `EXAM_GURU_STORAGE_ROOT`; Compose operators configure its host bind directory through `EXAM_GURU_DATA_PATH`. MinIO/S3 must not be required merely because the application runs in Docker.

### 4.4 What belongs in PostgreSQL

PostgreSQL stores structured application state, not bulk PDF blobs.

Examples:

- document identity and metadata;
- grade, subject, medium and curriculum relationships;
- document type/year/version;
- local storage key/path reference;
- SHA-256 checksum;
- extraction/OCR status;
- extracted and reviewed text blocks;
- provenance and review history;
- semantic chunks;
- embedding metadata and vectors;
- taxonomy and curriculum mappings;
- historical-question normalization;
- blueprint definitions;
- generation/validation/review records;
- approved/published paper source-of-truth;
- audit history.

Imported sources may retain bounded, immutable intake candidates before an authoritative curriculum assignment exists. Candidate grade/subject/medium labels are display-only and must be visibly marked as needing review. They never become retrieval scope authority. Explicit audited metadata confirmation requires a current admitted curriculum scope, including active exam/grade, medium, subject and curriculum records. Catalogue admission binds an explicit educational approval and evidence to the current scope fingerprint; changed, unapproved or quarantined scope is not admitted. **Metadata/catalogue admission is independent of page-text verification and benchmark ground truth.** A metadata reviewer need not invent page ground truth to admit a scope, and confirmed page text cannot approve its metadata.

Unassigned sources that still require metadata review support separate, append-only description corrections through `source_metadata_candidates`. The original `intake_metadata`, upload session and source bytes remain immutable. Each correction binds document checksum, current metadata-scope version, candidate version, proposed labels/material category, actor, timestamp, reason and a matching audit event. `SOURCE_WRITE` and optimistic version checks are required; assigned, quarantined, trusted or downstream-linked sources cannot use this candidate-only path. Current proposals may change unverified Materials display/filter hints, never catalogue authority or page trust. Scope changes make earlier proposals non-current without deleting them.

Explicit curriculum confirmation must bind the exact current candidate revision when one exists. It still requires current catalogue admission; it can then adopt the reviewed year/material category with old/new audit evidence, without changing extraction/page trust. Saving descriptions alone never clears metadata review or makes a document Ready for AI. The teacher editor defaults to read-only comparison, reveals editable descriptions on request, captures version identity with the draft and retains unsaved values on conflict.

PostgreSQL also stores immutable per-page text candidates, versioned review events/current pointers, fixed benchmark membership and ground-truth references, resumable upload sessions/chunk receipts and `SourceReadJob` progress/leases. Bulk originals and page-image bytes remain on durable host storage.

### 4.5 Separate legacy and resumable intake contracts

The existing byte API is retained, not silently widened. `POST /api/v1/admin/source-documents` defaults to 25 MiB in backend settings (256 MiB maximum); Compose sets 256 MiB. Its legacy browser multipart proxy limit is 26 MiB including form overhead. This is not the large-document Materials/corpus intake path. Legacy Compose OCR permits 40 routed pages and five seconds per command; the `EXAM_GURU_OCR_*` settings belong to that pipeline.

The current Materials upload uses `/api/v1/admin/source-uploads`: durable, owner-scoped sessions with **4 MiB raw octet-stream parts** and a shorter final part. Browser recovery stores identifiers, not source bytes or credentials. A client `request_id` persisted before creation permits owner-scoped lookup/replay after an ambiguous response; different metadata cannot reuse that request. Reselected files must match the complete committed prefix's ordered, paginated SHA-256 receipts before more bytes are appended. Finalization rehashes the staged bytes, checks receipt continuity, declared size and any expected source hash, then publishes/deduplicates one immutable original and queues reading through a durable outbox.

For an acknowledged upload session, the Materials browser explicitly enables bounded HTTP 429 recovery: honor a valid delta-seconds `Retry-After` (at least one second), then retry the identical request, with at most three retries and 180 seconds of waiting per request. The wait budget resets only after a successful response, not by recreating a session. Waiting is visible and immediately cancellable; acknowledged bytes, request identity and prefix/receipt verification remain intact. Missing/invalid/excessive hints, authentication/validation failures and unacknowledged creation do not enter this automatic loop. Low-level callers retain manual retry by default. Server rate limits and staging quotas are unchanged; these waits are not document-size limits.

The operator corpus importer uses the same resumable API, not legacy multipart/extraction. It verifies immutable source descriptors and sends at most 4 MiB at once; a locked, atomically saved v2 ledger persists request identity before creation and binds owner, local origin/runtime report, corpus root and manifest/source identity. Resume verifies every committed receipt against the original bytes. Ambiguous mutations reconcile through saved-session lookup rather than blind recreation. Explicit legacy-ledger migration preserves the old ledger as history without trusting its document IDs in another runtime. Finalization queues the existing reader/outbox; upload completion does not imply completed reading, approved metadata or source trust. Interruptions pause safely; server cancellation and a unique installation UUID remain separate limitations.

Default capacity reservations are **8 GiB staged per owner, 32 GiB staged globally, eight active sessions per owner**, configured by `EXAM_GURU_SOURCE_UPLOAD_MAX_OWNER_STAGED_BYTES`, `EXAM_GURU_SOURCE_UPLOAD_MAX_STAGED_BYTES` and `EXAM_GURU_SOURCE_UPLOAD_MAX_ACTIVE_SESSIONS_PER_OWNER`. These are **operational quotas, not hard-coded PDF product limits**. Reserved declared sizes include retained completed and failed sessions; clearing a browser checkpoint does not release server quota. Staging under `STORAGE_ROOT/.source-uploads` is retained; no automatic purge is implemented. Filesystem capacity, integer bounds and bounded finalization work still apply. A quota change requires capacity planning, not a claim of unlimited uploads.

Resumable upload, seekable source streaming and durable page-image artifacts currently support the **local POSIX backend only**. S3/MinIO remains an optional legacy byte-storage provider; these new paths fail explicitly when streaming is unsupported. Do not introduce hidden whole-file buffering or a second local store to make an unsupported provider appear functional.

### 4.6 Restart-safe page reading and durable comparison images

`POST /api/v1/admin/source-documents/{id}/read` creates a `SourceReadJob`. Workers read a verified seekable original rather than loading PDF bytes into memory, commit candidates/progress per page, process at most **eight pages per batch**, and requeue continuations. Leases, compare-and-swap versions and recovery of queued/expired claims make restarts safe. Batch bounds are not document bounds: this path has **no 1,000-page document limit and no legacy 40-page OCR limit**. Whole-document rereads skip verified, excluded, processing and human-edited pages; explicit page rereads require the current review version and retain all prior evidence.

The persisted `PageReadingConfiguration` currently defaults to `TesseractOCRConfig` with **30 seconds per command, 300 dpi, 40 million pixels per page and 8 MiB command output**, using one page per OCR call. Source-read execution is bounded to 300 seconds per batch, with a 360-second actor limit and 600-second lease. These defaults are separate from the legacy `EXAM_GURU_OCR_*` profile and from the controlled benchmark's 60-second command setting. Multiple operations can occur for one page; no per-page completion-time or quality guarantee follows. Native/font/script/image evidence routes each page independently; missing required languages, unsafe text and render/OCR failures remain explicit, unverified outcomes.

Source-fidelity v2/rules-2 treats known legacy-font native text, suspicious encoded output and missing original-language script as failed readings, not ordinary confirmable text. Short suspicious Latin carriers are checked without a minimum prose length or known-font prerequisite; tested legitimate mixed-language terms, acronyms, units and mathematical notation are preserved. These are conservative routing/confirmation heuristics, not a font conversion or proof of arbitrary language correctness. Migration 0040 makes the canonical SQL helper require this rules revision for every candidate method, so historical earlier-policy results cannot remain eligible merely because `can_confirm` was once true. Their raw evidence, diagnostics, review history and ground truth are not rewritten; downgrade cannot discard the policy while fidelity evidence exists. Sinhala pages compare native, bilingual `sin+eng` and Sinhala-only `sin` candidates within the existing budgets; successful execution is not a quality pass. Every candidate remains immutable and unverified. An explicit ranked-selection event can select an earlier, better candidate without discarding later failed attempts. This legacy reader applies no unproven font mapping, language-model transcription or grammar correction. The separate candidate-only visual-understanding boundary in §4.8 does not change this reader's contract.

An optional deterministic native-equation recovery step may append up to two derived OCR candidates, for at most five candidates per page. It copies only complete, unambiguous non-legacy standalone equations from the original PDF, preserving literal operators, values, interior whitespace and untouched OCR text. Black, opaque, visible text and safe word/order/region evidence are required; legacy/mixed fonts, hidden/covered text, tables, prose and ambiguous layouts cannot supply recovered equations. It never solves arithmetic or invents answers. Canonically hashed source/word/edit evidence and validated input candidate IDs bind each derivation to the same document, page and job. The entire original mathematical reference remains in force. A readable derived candidate can gain selection priority only through new native-anchor preservation and lower parent-relative risk without new risks or omitted evidence; unmatched raster numbers are not treated as proof of invention or used to reward omission. Recovery never grants confirmation or changes the current document-wide trust gate.

Mathematical fidelity is checked separately from Unicode readability: retain literal numbers/operators, native non-legacy anchors, word positions and table-cell/row/column evidence. Legacy glyphs and unresolved raster/table content cannot silently supply mathematical authority; this checks source preservation, not whether the source's arithmetic is correct. Larger reference/word JSON is stored losslessly with a versioned, hash-checked zlib/Base64 envelope, bounded to 24 KiB/16 KiB encoded and 1 MiB decoded with depth/node limits. The existing 65,536-byte candidate metadata ceiling is unchanged. Unrepresentable evidence fails closed with digest/count identity rather than losing the raw candidate or treating a truncated reference as complete.

Workers retain page images for native comparison and the exact OCR input in **`STORAGE_ROOT/fidelity-page-images`**, backed by immutable private artifacts and schema-versioned candidate provenance. References bind source identity, page, rasterizer/version, DPI, dimensions, byte size, whole-image SHA-256 and chunk hashes. A read verifies these values and PNG structure. **A declared artifact that is missing or damaged fails closed; it is not a cache miss.** Bounded rendering of the immutable original is allowed only when no durable artifact was declared. Images are provenance, not disposable cache or ground truth.

Materials PDF/image endpoints require `SOURCE_READ`, retain private/no-store and same-origin controls, and never serve the source directory statically. Original PDFs stream in bounded chunks with GET/HEAD and a single validated byte range (206/416), rather than a whole-file browser/API buffer. Native-comparison/fallback subprocess rendering retains CPU, memory, pixel, output and time limits. Native reader threads wait within the existing render deadline for one of two render slots; renderer diagnostics are separated from its bounded JSON result, not ignored by relaxing integrity checks.

Normal **View PDF / PDF බලන්න** stays inside the application. Material Details mounts one `OriginalPageViewer` using the canonical `/api/v1/admin/materials/{id}/pages/{page}/image` route, not a PDF iframe, plugin, new tab or whole-file browser buffer. It provides bounded first/previous/jump/next/last navigation, current/total pages, CSS zoom, fit-page and reset controls; no adjacent-page prefetch is performed. Unknown page counts remain explicit rather than invented, and page 1 can still be inspected while the operator retries metadata. Page-image failures show Retry, never an automatic raw-PDF fallback. The same read-only image/zoom/retry pane is reused by source-text review, evaluation-reference comparison and the legacy Materials comparison view; their surrounding review/version gates still own navigation and approval decisions.

**Download original PDF / මුල් PDF ගොනුව බාගන්න** is a separate explicit same-origin download link to the existing integrity-checked streaming original endpoint. It does not fetch the file into a JavaScript Blob. Changing a view, page or zoom cannot invoke that download. Display uses immutable source-derived page images and never modifies/recompresses the authoritative PDF. A future PDF.js/range viewer is optional, not a dependency of normal inspection or comparison.

The original-PDF web proxy has separate **30-second header/integrity-check**, **30-second no-progress**, and **15-minute total transfer** deadlines. The ordinary HTTP/OIDC deadline must not cancel a healthy large response body. Backpressure-preserving streams forward bytes without whole-file buffering; only nonempty chunks reset the idle deadline. Completion, disconnect, downstream cancellation and upstream failure release timers/readers and cancel transport where needed. These are bounded transfer policies, not PDF-size promises; authentication, validated range/disposition headers, source checksums and private/same-origin controls are unchanged.

Implementation anchors: `documents/resumable_uploads.py`, `documents/page_reading_jobs.py`, `documents/page_reading.py`, `documents/page_images.py` and `api/routes/page_images.py` under `apps/api/src/exam_guru_api/`; browser recovery is in `apps/web/src/lib/source-upload.ts` and `source-upload-checkpoint.ts`.

### 4.7 Independent human evaluation references

A fixed benchmark selection may collect a human transcription even while its operational page reading is failed, unread or otherwise unconfirmable. Migration `0042_evaluation_references` adds private, append-only `source_evaluation_previews` and `source_evaluation_references`; neither table replaces `source_page_ground_truth` or changes page-review state. Preparing a preview requires content-review permission, uses the existing bounded extraction-trigger rate limit, verifies the immutable original and binds an integrity-checked durable image to the exact benchmark/document/page/checksum and reviewer. A declared missing or corrupt image fails closed rather than being regenerated.

Saving requires that reviewer's prepared preview, the current reference version, an explicit original-image comparison and human-review attestation, and a reason. Keep the exact UTF-8 transcription (at most 100,000 characters / 400,000 bytes), a separate NFC view and both hashes. An empty reference requires an explicit blank-page declaration. PostgreSQL serializes source identity/quarantine and reference revisions, requires matching audit evidence, and refuses update, deletion, truncation or a downgrade that would discard this evidence. All reads remain permission-checked, private/no-store and same-origin; references and image artifacts remain private Studio recovery data.

The teacher editor starts blank or from an earlier human reference, never from machine output. It keeps the original and reference in bounded comparison panes, requires the original image to load before saving, and preserves drafts through version conflicts with explicit keep/use-latest choices and renewed comparison. English/Sinhala presentation choices do not rewrite reference text or source language. Evaluation-reference counts are separate from content-use confirmation counts. Saving cannot approve metadata, confirm/exclude a page, clear Maths/table/layout failures, create legacy ground truth, authorize knowledge/RAG or establish OCR accuracy. Existing source-confirmed benchmark gates remain unchanged.

### 4.8 Visual document understanding and trusted educational knowledge

**Architecture correction: IN PROGRESS.** This is the authoritative target for source-to-RAG work. It supersedes the assumption that reviewed extracted text can be chunked directly into the new RAG pipeline. The local/private Studio, immutable originals, existing evidence, metadata admission, deterministic blueprints, validation and human publication boundary remain unchanged. Existing text/OCR and mathematical checks are retained as evidence-producing and verification mechanisms, not replaced with a model's assertions.

```text
PDF / image -> immutable original -> rendered page / regions
            -> native / OCR / visual observation candidates
            -> source-faithful observation + separate educational understanding
            -> independent checks + teacher verification where required
            -> immutable, versioned TrustedPageKnowledge
            -> educational KnowledgeUnits + source links
            -> deterministic, versioned retrieval projections
            -> hard-scoped lexical + vector retrieval
            -> grounded generation -> validation -> human review -> publication
```

Keep four distinct layers:

1. **Original evidence:** immutable original bytes and integrity-checked rendered images. Neither a model nor a correction rewrites them.
2. **Source observation:** candidate exact Unicode text, equations, literal values, blank/unreadable cells, visible groups, labels, relationships and spatial/reading-order evidence. Native/OCR/visual candidates retain method and run lineage. A job's success or a readable candidate is not verification.
3. **Educational understanding:** separately attributed candidate topics, concepts, skills, objectives, activities, prerequisites and educational relationships, each linked to observed regions. Interpretations cannot be inserted into literal source fields or treated as approved curriculum metadata.
4. **Verified page knowledge:** the source-bound observation and explicitly accepted understanding, with versioned verification findings, original-comparison/uncertainty decisions and audit lineage. `TrustedPageKnowledge` is the only authority allowed to produce new RAG knowledge after cutover.

A page region is bound through its candidate to the original document checksum, page number and rendered-image checksum. Preserve stable region keys, kind, parent relationship, reading order and normalized bounds/polygon when available. Headings, prose, instructions, questions, examples, equations, vertical arithmetic, tables, grids, diagrams, illustrations, repeated-object groups, labels and blank answer areas must not collapse into an undifferentiated text blob. Missing geometry or source detail remains explicit uncertainty, never invented evidence.

Raw Unicode remains byte-preserved; NFC is a separate derived view, never NFKC or grammatical rewriting. Preserve the printed equation even if its arithmetic is wrong. Every grid position is represented explicitly, including blank and unreadable cells; blanks cannot acquire inferred answers. A visual count and a printed total are different observations. Keep legacy glyph/font/script diagnostics, original numerical anchors and image/layout comparisons available to independent verification. Model agreement does not clear them.

`DocumentUnderstandingProvider` is a first-party rendered-image port. Its input requires a bounded, integrity-checked image rather than a PDF text layer alone; its output is candidate observation/interpretation only. Provider/model/prompt/schema/configuration/pricing versions, source/image identity, attempts, tokens, integer cost, latency and ambiguous failures belong to durable run lineage. SDK types and credentials stay in adapters. Reuse completed unchanged inputs, bound page/region work and retry/cost budgets, and preserve partial progress; viewing a page cannot trigger paid analysis. Provider unavailability must be visible, not silently replaced with unsafe native text or a synthetic success.

The initial `OpenAIUnderstandingProvider` adapter uses SDK 3.1.0 and prompt `document-understanding.v1`, explicit image/structured-output capability attestations, a pinned server-owned profile and nonzero versioned pricing. It sends only the bounded rendered image and source identity as untrusted user data under fixed developer instructions, with strict schema output, provider storage disabled and zero SDK retries. First-party parsing occurs after receipt of the completion so malformed candidate output can retain known token/cost/latency accounting; incomplete usage or clock failure is not invented as zero. Response/profile/budget failures remain typed and have no source-trust authority. This adapter has deterministic transport tests, not a live Sinhala-quality claim or automatic intake activation.

Verification must reject malformed structure, invalid Unicode/provenance, source-anchor mismatches and unresolved high-risk facts. Decisions bind the exact candidate/report/source revisions and an authorized actor. Independent evaluation references remain separate benchmark evidence under §4.7; they are never prefilled from model output or implicitly converted into operational trust. Representative human adjudication measures quality; an entire corpus must not be manually transcribed merely to populate an ingestion pipeline. Any automatic verification policy requires independently sufficient source evidence and explicit versioned eligibility rules, never a provider self-pass.

A `KnowledgeUnit` retains its exact observed evidence, accepted educational meaning, source-region links and approved curriculum scope. A `KnowledgeProjection` is a deterministic retrieval representation, not a new source of truth or a free-form model summary. Bind projection, embedding and context identities to the trusted-page revision and transformation versions. A changed/revoked trusted revision, re-analysis requiring renewed review, removed source or changed admitted scope invalidates active downstream use. Recheck freshness during indexing, retrieval, generation, validation replay, approval and new publication; retain historical vectors, source history and immutable published snapshots without automatically rebinding them.

Reuse the existing storage, audit, authorization, scope, hybrid ranking, embedding-provider and generation infrastructure. Forward migrations may add normalized observation/understanding/verification and knowledge-version relationships; they must not reset data or promote legacy text/chunks merely because they were previously marked reviewed. The existing whole-document resolution and scope guards remain in force during the foundation stage; changing their implementation requires explicit migration and cross-scope/freshness regressions, not an undocumented bypass.

Teacher review reuses the app-owned original-page viewer and presents **what is visible**, **what it means**, and **what is uncertain** in readable, distinct sections. Original-image failure blocks comparison decisions. Corrections and conflicts preserve drafts and create new candidate revisions; UUIDs, hashes, raw JSON and provider accounting stay under technical details.

Migration **0043** adds a separate persistence foundation: `source_understanding_runs`, `source_understanding_candidates`, `source_understanding_regions`, `source_understanding_reports`, `source_understanding_decisions`, `trusted_page_knowledge` and `source_understanding_pages`. Candidate observations and educational interpretations have separate structured columns; immutable region identities refer back to the exact candidate geometry and source. Hash-bound reports and decisions use the existing append-only admin audit, while locked page heads enforce independent candidate/trusted revisions and optimistic command versions. Repeated deliveries converge without rewriting history; a new candidate clears the active trusted pointer. Reads verify stored fingerprints. The current-page predicate is a freshness check, not catalogue admission or permission to bypass unresolved sibling pages.

The migration creates no trusted rows from legacy data. Evidence tables reject update/delete/truncation, and downgrade refuses to discard retained understanding records. Forward/empty-downgrade/reapply tests preserve existing legacy source/candidate/review evidence. The initial service records bounded provider results and explicit source-comparison decisions; it does not expose a client-controlled trust endpoint or automatically queue paid analysis.

Migration **0044** adds `source_understanding_jobs` and an exact active-job link on the understanding page head. Explicit one-page commands bind source checksum, expected page version, immutable provider/budget configuration, actor, reason and owner-scoped request identity. Locked duplicate requests converge; normal commands do not replace verified/excluded pages. Processing clears active trust, prevents approval of the previous observation and fences result delivery by the current page/job/lease. The new private APIs create a job, read its status and read one page's observation/understanding plus the latest recorded job, so failure/accounting context survives a browser reload without initiating another analysis.

Workers reuse verified original streaming and declared durable page-image capture; they do not create evaluation-reference rows or rerender missing declared evidence. Source/image metadata, chunk hashes, dimensions, profile and budgets are checked before provider dispatch. The worker records the provider-request key and dispatch marker durably before the call, then retains candidate-only success or failed/unknown accounting in immutable run history. Known usage is retained on rejected output or removal of the source. Terminal jobs are immutable. Recovery requeues only expired claims that never reached the dispatch marker, for at most three claims; a possibly consumed call remains `unknown` rather than being automatically billed again. An already-committed result is reconciled without a new call. The existing Valkey/Dramatiq worker and maintenance scheduler own dispatch/recovery, with zero broker-level retries, a 300-second actor bound and a default 600-second lease. Upgrade and empty downgrade/reapply preserve prior trusted page history; downgrade refuses to discard retained job history.

The understanding runtime is **disabled by default**. `EXAM_GURU_DOCUMENT_UNDERSTANDING_*` settings require explicit model/version/pricing/key/temperature and verified input/output capabilities for OpenAI; defaults validate a 30-second provider deadline, 8,192 output tokens and 1,000,000 micro-USD of reported per-result cost. This validation is not a guarantee of a remote provider's eventual charge. Enqueue has its own per-principal rate scope (default five requests per configured window), separate from legacy OCR. Provider credentials go only to API/workers, not migration/maintenance containers. Deterministic visual fixtures require the test environment and an explicit `ai-exam-guru-e2e-*` identity; the worker's dedicated fixture identity must agree with the API identity when both are supplied. The isolated runner disables private dotenv loading, clears live visual-provider configuration and uses that fixture identity. It never activates this mode in the real Studio.

**Current implementation boundary:** first-party structured candidate, verification/snapshot, rendered-input and PostgreSQL persistence contracts, candidate-only OpenAI transport and bounded job/API wiring have been added. Teacher understanding review, KnowledgeUnit/projection indexes and generation cutover are still pending. The real Studio remains on migration `0042_evaluation_references`, with no automatic source trust or backfill. Its inspected running configuration has no live generation, semantic-verification or embedding provider enabled. Deterministic contract tests are not a real Sinhala/visual benchmark, and these contracts alone do not close the architecture milestone. The fixed counting, multiplication and legacy-guide cases, trusted retrieval/generation comparisons and local browser acceptance must be evidenced before broad Grade 3/4/5 backfill.

## 5. Duplicate and source-integrity model

Every raw upload must be content-addressed/deduplicated using a cryptographic hash such as SHA-256.

```text
upload
  ↓
SHA-256
  ↓
already known?
  ├─ yes -> point user to existing material / reuse safely
  └─ no  -> persist one original file + metadata
```

Uploading the same PDF repeatedly reuses one content-addressed original and source identity. Retained resumable-session chunks are separate staging data and remain charged to quota even after deduplication; they must not be mistaken for additional admitted originals.

The original source is immutable evidence. Metadata may be corrected through audited/versioned workflows.

## 6. Material lifecycle

A material may move through operator-friendly states such as:

```text
Processing -> Needs review -> Ready for AI -> Removed from use
```

`Removed from use` means the material is excluded from future retrieval/generation while preserving audit/provenance unless a separate safe-purge policy allows physical deletion.

Example: if a Grade 11 paper was mistakenly classified under Grade 5, the teacher must be able to:

- correct its grade/subject metadata where legitimate; or
- remove it from AI use.

A wrong classification must not silently contaminate RAG results.

Per-page source fidelity uses immutable `PageTextCandidate` records with raw UTF-8 bytes and a separate NFC view, versioned engine/configuration/diagnostic provenance, and append-only review events. Unsafe raw bytes remain evidence even when no safe normalized view can be stored. Do not destructively normalize with NFKC, repair educational wording or apply an unevidenced legacy-font conversion. Human edits create unverified child candidates; confirmation requires the exact current candidate/version and explicit comparison with the original. Fixed benchmark membership/baselines and human ground-truth records are protected history, not replaceable OCR outputs.

`Ready for AI` requires an active, non-quarantined original, confirmed metadata and current catalogue admission, with every original page explicitly verified or excluded and at least one verified page. Exclusion is not ground truth. A legacy document-level `trusted` flag is insufficient. Audited fixture quarantine requires exact source/checksum/upload-audit evidence, hides that fixture from normal Materials and future AI use, and preserves its originals and downstream history; filenames alone do not authorize quarantine or deletion.

## 7. Multi-grade educational model

The reusable architecture supports Sri Lankan **Grades 1–13** while V1 quality acceptance remains Grade 5 first.

Curriculum hierarchy:

```text
Grade
  -> Medium
      -> Subject
          -> Curriculum Version
              -> Unit / Module
                  -> Lesson / Topic
```

Educational taxonomy remains separately modeled:

```text
Competency -> Skill -> Sub-skill -> Learning Concept
```

Lessons/topics may map to one or more competency/skill nodes. Do not force both hierarchies to be identical.

National-exam programmes are assessment programmes associated with grade/curriculum scope, including:

- Grade 5 Scholarship;
- GCE O/L, normally Grade 11;
- GCE A/L, normally Grade 13;
- ordinary school-grade papers for Grades 1–13.

The paper-generation system must support both full-grade/full-subject generation and narrower scope such as:

```text
Grade 7 -> Mathematics -> Lessons 1–3 only
```

Hard grade/subject/curriculum/lesson filters must apply before semantic retrieval so content cannot leak across incompatible scopes.

## 8. RAG and AI data boundary

The private Studio owns the complete content-intelligence system:

- raw source documents;
- reviewed extraction/OCR data;
- curriculum knowledge base;
- historical question bank;
- chunks;
- embeddings;
- pgvector indexes;
- retrieval diagnostics;
- forecasting/backtesting data;
- blueprint logic;
- model prompts/configurations;
- generation attempts;
- validation evidence;
- reviewer decisions.

The hosted Student platform does **not** need this private corpus to serve an exam.

Students must never trigger live RAG + LLM generation as part of starting an exam in the normal product flow.

### 8.1 Verified source lineage for new content use

New knowledge/history records must bind the **current verified page candidate** and a nonblank exact contiguous span of its NFC text, not merely a legacy block or document-level trust flag. The source must be active, its metadata confirmed, and its catalogue scope currently admitted. The entire original document must also be resolved: every page currently verified or explicitly excluded with immutable exclusion evidence, with at least one verified page. A verified page cannot authorize chunking, embedding or RAG use while a sibling remains unresolved. Embedding/retrieval additionally require reviewed knowledge and active, reviewed taxonomy with matching curriculum/unit/lesson scope.

Generation context binds exact record text/version, source document checksum/page/block, candidate ID and candidate text hash. Services and PostgreSQL guards recheck current lineage for new knowledge/embedding writes, generation creation/completion, validation, review-candidate creation/approval and publication. RAG filters enforce eligibility before ranking. Page edits/rereads/exclusions, source removal or changed/revoked catalogue admission invalidate future use of stale bindings; a saved context or previous validation cannot bypass this boundary. Safe rejection is not promotion.

Forward migrations preserve legacy chunks, embeddings, generation/review evidence and **existing immutable published versions** without manufacturing candidate bindings or human approval. Historical published readback remains available; new content-use writes require fresh verified lineage. Migration `0039_source_fidelity_v2` requires current v2 validation for every method, including human candidates, and document-wide resolution; historical v1 rows/events are not rewritten or exempted. Sibling review-state locks prevent a concurrent unresolved page from slipping through a downstream write. Its downgrade refuses to discard protections while fidelity evidence exists. These rules extend migrations `0033_source_page_fidelity`, `0034_catalogue_admission`, `0035_verified_knowledge_lineage` and their application services, not a new external RAG subsystem.

The optional source-image/text semantic-diagnostic factory (`documents/semantic_diagnostics.py`) is disabled by default and not wired into general corpus reading. It produces bounded `PASS_CANDIDATE` / `FLAG_FOR_REVIEW` diagnostics only, never source transcription/replacement text, confirmation, trust or publication. Structural failures cannot be cleared by an LLM. It is separate from generated-question semantic validation; no live source-accuracy claim follows from its implementation or tests.

## 9. Teacher-first operator workflow

The primary Studio users are teachers, reviewers and education/content staff, not engineers.

Normal navigation should focus on goals:

```text
Home
Materials
Generate Papers
Review & Approve
Published Papers
```

Engineering internals such as vector IDs, request fingerprints, prompt versions, retry lineage, context IDs and raw JSON belong behind Advanced/Technical areas.

The page-review workspace's overall `language` is a presentation default, distinct from the candidate/page's observed text language. Known page language wins. For an unread or language-undetermined page, the UI may fall back to the assigned medium or a recognized current candidate/intake medium label; that hint never changes `page.language`, raw text, OCR configuration, diagnostics, confirmation eligibility or RAG scope. Unknown labels stay unknown. An explicit browser-local teacher language choice remains higher priority than automatic presentation defaults.

Typical content flow:

```text
Upload material
  ↓
Native/OCR candidates + durable page images
  ↓
Teacher confirms/corrects or explicitly excludes each page
  ↓
Page readiness + independently confirmed/admitted metadata
  ↓
Ready for AI
  ↓
RAG / historical intelligence
  ↓
Generate paper + answer/marking scheme
  ↓
Automated validation
  ↓
Teacher/reviewer validation
  ↓
APPROVED
  ↓
Publish
```

## 10. Paper generation and validation

A generated paper is never treated as trusted merely because an LLM produced it.

Required chain:

```text
Scoped teacher request
  ↓
Deterministic/versioned blueprint
  ↓
Hard metadata filters + RAG
  ↓
Structured LLM generation
  ↓
Question + answer + marking scheme
  ↓
Automated validators/evals
  ↓
Human review/edit/reject/regenerate/approve
  ↓
Approved immutable paper version
```

The reviewer experience should show readable questions, options, correct answers, explanations, marks, source scope and validation findings. Engineering diagnostics are secondary.

## 11. Publishing boundary

### 11.1 Copy/publish, never move the source-of-truth

Approved content is **published/copied** from the local Studio to the hosted Student platform.

Do not move the only copy out of the Studio.

```text
LOCAL Studio source-of-truth
        │
        ├── keeps immutable approved version
        │
        └── exports/publishes copy
                    ↓
             Hosted Student platform
```

### 11.2 Publish package

The publication boundary should use an explicit, versioned package/API contract.

A package may include:

- stable paper ID;
- paper version;
- grade;
- subject;
- medium;
- curriculum/version reference;
- unit/module/lesson scope;
- exam programme where applicable;
- duration/instructions;
- questions and options;
- marks;
- server-side answers and marking rules;
- approved explanations shown only at the appropriate stage;
- student-facing assets required by the questions;
- publication schema version;
- checksum/content digest;
- approval/audit reference;
- created/published timestamps.

It must not include raw teacher guides, bulk source PDFs, the private RAG corpus or embedding database merely to serve the exam.

### 11.3 Hosted ingestion

The hosted side must validate before accepting a publication:

- schema/version compatibility;
- checksum/integrity;
- paper ID/version uniqueness;
- required metadata;
- asset references;
- publication authorization.

Publication should eventually be a controlled product action, e.g. `Publish to Student Platform`, rather than a teacher manually copying database dumps or SSH files.

## 12. Published-paper immutability and versioning

A published paper is immutable.

Do not edit production version `v3` in place. A correction creates `v4`.

```text
v3 -> remains historical/archived as required
v4 -> corrected -> validated -> approved -> published
```

Important versioned artifacts include:

- application/Docker image version;
- database schema/migration version;
- curriculum version;
- extraction/OCR implementation version;
- embedding provider/model/config version;
- retrieval version;
- forecasting/blueprint version;
- prompt version;
- LLM provider/model configuration;
- validation-rule version;
- paper version;
- publication-package schema version.

Reproducibility metadata belongs in the Studio even when the student does not see it.

## 13. Hosted Student platform

The hosted product is intentionally smaller and easier to scale than the Studio.

Responsibilities:

- student identity/login;
- subscription/entitlements;
- published-paper catalog;
- exam runner;
- timer/navigation/autosave/resume;
- answer submission;
- deterministic/server-side marking where possible;
- skill/progress analytics;
- recommendations based on approved application logic;
- student-facing reporting.

It should not require OpenAI/RAG availability to start or complete a normal published exam.

## 14. Answer security on the hosted platform

Do not send the full answer key or marking scheme to the browser when the exam starts.

```text
Browser during exam:
  questions/options        YES
  correct answers          NO
  private marking rules    NO

Hosted backend:
  correct answers          YES
  marking rules            YES
```

After submission, the product may reveal explanations/correct answers according to the configured exam policy.

## 15. Security model

### 15.1 Local Studio

Protect the local source-of-truth with:

- full-disk encryption where available;
- OS account permissions;
- no raw-data directory served by Next.js/static web hosting;
- no public inbound exposure by default;
- local firewall;
- secret management outside Git;
- prompt-injection handling for uploaded/retrieved text;
- audited admin/reviewer actions;
- least-privilege access to local files/database;
- checksums and provenance.

### 15.2 Security benefit of the split

A compromise of the public Student platform must not automatically provide access to:

- raw syllabus/teacher-guide library;
- past-paper source corpus;
- private OCR corpus;
- embedding/vector database;
- retrieval diagnostics;
- private generation prompts/configurations.

The public environment only holds data genuinely required to operate the student service.

## 16. Backup and disaster recovery

Local-only source ownership makes backup mandatory.

At minimum maintain:

- the active local data store on the primary Studio machine;
- a second physical encrypted copy on another disk/machine;
- repeatable PostgreSQL backups;
- configuration/version manifests needed to restore the Studio.

A sensible bootstrap pattern:

```text
Primary 1–2 TB workstation
        ↓ scheduled backup
External disk / secondary laptop / NAS
```

Do not scatter the active source library independently across several laptops without a single authoritative database/storage index. Prefer one primary Studio source-of-truth plus backups.

Temporary OCR/render/worker files should have cleanup/lifecycle rules and should not be treated as backups. Persisted page images and retained upload staging are different: their references/progress survive restarts and must not be discarded as temporary render cache.

A coordinated local recovery inventory must include PostgreSQL (including candidates, review/ground-truth evidence, catalogue admission, upload receipts and job progress), immutable originals, storage sidecars, retained `STORAGE_ROOT/.source-uploads`, and `STORAGE_ROOT/fidelity-page-images`, plus the release/configuration identity. Preserve private ownership/permissions and verify source and image hashes/sizes. Freeze or otherwise coordinate writers when taking the database/filesystem recovery point; a PostgreSQL snapshot alone is not atomic with artifact writes.

The guarded `scripts/ops/backup_postgres.sh` / `restore_postgres.sh` cover PostgreSQL, not filesystem copying, retention or off-host replication. Follow the [backup/restore runbook](ops/BACKUP_RESTORE.md) with a new empty isolated database and new empty local filesystem, correct application ownership/private modes, and matching original versioned artifacts. Never overwrite/delete the source, substitute regenerated images for lost historical artifacts, manufacture ground truth or reset retained upload quota to make a restore appear complete.

Use a protected temporary `PGPASSFILE` from known authorized configuration, never password arguments or logged secrets. Only after database, filesystem, lineage and permission checks pass may PostgreSQL-backed source-reading/upload-finalization recovery actors resume alongside the legacy extraction, embedding, generation and teacher-paper queues. Do not replay old Valkey messages or enable paid providers automatically. Verification-only restore and a disposable synthetic restore test are distinct from a full isolated Studio restore drill. A private on-host dump is not an off-host disaster-recovery copy and never authorizes an in-place restore of the persistent Studio.

## 17. Cost model

The architecture deliberately avoids paying for bulk cloud storage before it adds business value.

Local resources provide:

- bulk PDF/source storage;
- PostgreSQL + pgvector;
- OCR and background processing;
- private RAG/AI production workflows.

Hosted resources are concentrated on the customer-facing workload:

- API/app hosting;
- authentication;
- subscriptions;
- published papers;
- student attempts/results/analytics;
- required student-facing assets.

This keeps early-stage infrastructure spend lower while preserving a clean future migration path.

## 18. Future evolution

The local-first choice must not trap the system permanently.

Because storage, LLM and embedding providers are behind interfaces, the Studio may later move to:

- a dedicated local server;
- a NAS-backed deployment;
- a private network/VPN environment;
- S3-compatible object storage;
- managed PostgreSQL;
- a secured cloud/VPC environment;

without changing curriculum/RAG/publishing domain semantics.

Do not introduce these costs/complexities until scale, availability, collaboration or investor-backed operations justify them.

## 19. Non-negotiable architecture invariants

1. Raw educational source material is private Studio data by default.
2. The Studio's active source-of-truth runs locally/private in the bootstrap architecture.
3. Raw PDFs are stored on durable host storage, not ephemeral container layers.
4. PostgreSQL stores structured metadata/content/vector state, not bulk source PDFs as blobs.
5. Same-file uploads are deduplicated by content identity.
6. Grade/subject/curriculum/lesson boundaries are enforced before RAG retrieval.
7. LLM output is untrusted until automated validation and human approval complete.
8. Only approved/versioned student-ready content crosses the Studio -> Student boundary.
9. Publishing copies content; it does not remove the Studio's source-of-truth.
10. Published paper versions are immutable.
11. The hosted Student platform does not need the private RAG corpus for normal exam serving.
12. Correct answers/marking rules remain server-side during active exams.
13. Production student service compromise must not directly expose the local raw material library.
14. Local data must have a tested backup/restore path.
15. V1 validates Grade 5 first, but reusable domain/storage/publishing architecture supports Grades 1–13.
16. New content use requires current verified page candidates, exact NFC source spans and independently admitted metadata/scope; legacy trust does not bypass these checks.
17. Originals, raw candidates, benchmark baselines and human review/ground-truth history are preserved; neither OCR nor LLM output grants automatic trust or authors source truth.
18. Durable page-image artifacts and retained upload staging are private, integrity-checked recovery data, distinct from disposable render files and from legacy byte-API limits.

## 20. Architecture change discipline

Any future change that alters one of the following must update this document in the same engineering change:

- local-vs-hosted data boundary;
- source storage provider/default, resumable intake or durable page-image contract;
- Studio public exposure/network model;
- RAG data location or verified-source/catalogue admission boundary;
- publication contract;
- hosted answer-security model;
- published-paper immutability;
- backup/source-of-truth model;
- multi-grade core hierarchy.

Architecture decisions should not exist only in chat messages, prompts or implementation code.

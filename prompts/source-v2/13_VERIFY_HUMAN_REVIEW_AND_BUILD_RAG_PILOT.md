# Source V2 — Validate Human Review and Build the First Verified Knowledge + Vector + RAG Pilot

## Operator attestation

The operator has explicitly stated that the real Source V2 pilot has now been human reviewed in the Studio.

Treat that statement as the human-review attestation for this run.

Do not perform source Confirm / Correct / Exclude actions on the operator's behalf.

Your first job is to validate that the persisted Source V2 state is internally consistent with the operator's review, then continue downstream.

## Mission

Start from latest master.

Current known engineering baseline includes:

- Source V2 single-primary-reader architecture.
- Legacy OCR/Qwen/DeepSeek/LightOnOCR reader ensemble removed from active architecture.
- Two real pilot documents only:
  - mawbasa-teacher-guide
  - sankhya-rata
- 5 pilot Source V2 pages:
  - teacher guide pages 156 and 186
  - sankhya-rata pages 1–3
- 33 fresh canonical regions/crops before human review.
- Current-agent primary readings sealed against canonical crops.
- Migration HEAD at least 0061.
- Source review keyboard bug fixed in latest master.

This task moves the product across the next boundary:

AI visual reading
→ human-verified Source V2
→ immutable verified-source snapshot
→ source-faithful Knowledge Units
→ retrieval projections
→ embeddings / pgvector
→ hard-scoped hybrid RAG
→ provenance-verified retrieval pilot

This task does not build final paper generation yet. Paper generation comes only after the verified Source V2 → RAG path is proven.

## 1. NON-DESTRUCTIVE RULE — DO NOT RESET THE DATABASE

The operator has just completed real human review.

Therefore:

- DO NOT drop the DB/schema.
- DO NOT delete Source V2 pages/candidates/review events.
- DO NOT re-import the pilot.
- DO NOT rerun the source-reading agent.
- DO NOT replace human corrections with old machine text.
- DO NOT recreate review state.
- DO NOT regenerate crops unless integrity verification proves an artifact is missing/corrupt.

Before changing code or data:

1. identify the active local Studio DB and storage;
2. capture a read-only database inventory;
3. record both pilot document IDs/checksums;
4. record Source V2 page IDs/image hashes;
5. record region/candidate/review state;
6. record current review-event counts;
7. record verified/excluded/unverified counts;
8. record current downstream knowledge/embedding counts;
9. record managed source/crop artifact hashes where practical.

This baseline protects the operator's real human review.

## 2. Validate the REAL human-review state first

For each pilot document, inspect Source V2 directly.

Required invariants:

- unverified regions = 0;
- verified + excluded = total current regions;
- at least one verified source region exists;
- every verified region points to the current candidate revision;
- every verification binds the required page image/checksum;
- human corrections, if any, are current revisions;
- excluded regions are not treated as verified text;
- document gate = usable:true.

Validate D18 behavior:

TEXT_ONLY
- final verified text must be non-empty;
- exact reviewer-approved text is the downstream source text.

VISUAL_WITH_TEXT
- final verified printed labels/text must be non-empty;
- canonical crop provenance must remain attached.

VISUAL_ONLY
- zero source characters are valid;
- crop/source-kind provenance must remain attached;
- a generated visual description is derived knowledge, never Verified Source Content.

DECORATIVE
- must never enter educational retrieval/vector content.

UNDECIDED
- must not remain if the document gate is considered usable.

Do not assume old expected counts. Use actual current DB state after the user's review.

If any region is still unverified, any review lineage is inconsistent, or either document gate is false, STOP DOWNSTREAM MUTATION.

Return VERIFIED RAG PILOT: HUMAN_REVIEW_INCOMPLETE with exact document/page/region IDs still requiring attention.

Do not fix source content yourself.

## 3. Audit current downstream architecture before using it

The repository still contains an older knowledge-preparation architecture built around concepts such as:

- TrustedPageKnowledgeModel
- source_understanding_pages
- source_understanding_regions
- PageUnderstandingService
- SQL helpers such as source_understanding_document_is_resolved

That is the pre-Source-V2 knowledge lineage.

Do not simply push newly human-verified Source V2 data through that legacy understanding path.

Do not create fake TrustedPageKnowledge rows solely to satisfy old foreign keys.

Inspect all active paths from:

Source V2 verified region
→ knowledge preparation
→ knowledge unit
→ projection
→ embedding
→ retrieval
→ generation

Identify every active dependency on the superseded source-understanding model.

Historical migrations/tables may remain for history.

The active knowledge/RAG path must be rebased on Source V2 Verified Source Content.

## 4. Define an immutable Source V2 downstream trust snapshot

Introduce a clean first-party immutable source boundary for downstream use.

The implementation may choose exact naming, but conceptually create a SourceV2VerifiedPageSnapshot or equivalent immutable verified-source aggregate.

Each snapshot must bind at minimum:

- document ID;
- document/source SHA-256;
- page ID;
- page number;
- page image SHA-256;
- render DPI;
- detector/layout identity;
- ordered resolved regions;
- region ID;
- source kind;
- final verified source text where applicable;
- candidate ID + revision;
- human review event/decision identity where applicable;
- bbox;
- crop SHA-256 where applicable;
- excluded/decorative disposition;
- snapshot schema version;
- deterministic snapshot fingerprint.

The snapshot is created only when the Source V2 page/document gate is currently resolved.

The snapshot must keep VERIFIED SOURCE CONTENT separate from DERIVED KNOWLEDGE / VISUAL DESCRIPTION.

A later human correction/reclassification/exclusion to a Source V2 region must make older downstream snapshots stale.

Never silently mutate the old snapshot.

## 5. Rebase Knowledge Units on Source V2 lineage

The active Knowledge Unit model/preparation flow must no longer require old source-understanding tables for new work.

Use a forward migration. Expected next migration is 0062 or later depending on latest master.

Never edit/delete historical migrations.

Required direction:

- KnowledgeUnit/projection lineage must point to the immutable Source V2 verified snapshot or equivalent Source V2 evidence identity;
- remove active new-write foreign-key dependence on trusted_page_knowledge / old understanding candidates;
- preserve historical readback only if needed;
- current-source checks must use Source V2 current review/snapshot lineage;
- changing a verified Source V2 region must invalidate/supersede affected active knowledge/projections/embeddings;
- no unverified region can participate.

Because this clean-slate pilot currently has no legitimate downstream knowledge rows, prefer a clean active model rather than carrying obsolete compatibility complexity forward.

## 6. Build SOURCE-FAITHFUL Knowledge Units first

For this pilot, do not ask an LLM to rewrite verified source into a summary and then embed the summary as truth.

Start with deterministic source-faithful Knowledge Units.

A unit may group adjacent regions only when deterministic ordering and provenance are preserved.

Every unit must retain:

- exact human-verified source text;
- document/page/region provenance;
- source kinds;
- crop SHA/bbox references where applicable;
- grade/medium/subject/curriculum scope from trusted metadata;
- unit/lesson/topic metadata only when genuinely known;
- immutable fingerprint;
- Source V2 verified snapshot identity.

Rules:

TEXT_ONLY
- include exact final verified text.

VISUAL_WITH_TEXT
- include exact verified printed labels/text as source content and retain crop reference.

VISUAL_ONLY
- do not invent source text.
- If a machine-generated visual description exists, keep it as a derived-knowledge proposal.
- Do not merge it into source transcript.
- It may enter a separate derived-knowledge field/unit only if an explicit human-review/approval path exists.
- If that approval path does not exist yet, keep semantic visual enrichment pending.

DECORATIVE / excluded
- do not create educational retrieval text.

Preserve audit lineage.

## 7. Curriculum/metadata gate

RAG must be scope-safe.

Before creating indexable units, verify each pilot document has authoritative current metadata for:

- grade;
- medium;
- subject;
- curriculum version;
- material type;
- year where applicable.

Do not infer authoritative grade/subject/curriculum from filename, folder name, machine transcript or model guess.

If required metadata is not human-confirmed/admitted, do not fake it.

Return VERIFIED RAG PILOT: METADATA_REVIEW_REQUIRED with exact missing fields/document IDs.

If document-level scope is confirmed but unit/lesson/topic is not yet known, it is acceptable for this first pilot to index at admitted document-level curriculum scope, provided retrieval cannot falsely claim lesson mapping.

Later lesson-level mapping will refine the RAG corpus.

## 8. Retrieval projections

Create deterministic retrieval projections from source-faithful units.

Projection text must:

- remain traceable to exact verified source;
- preserve Sinhala/Tamil/English Unicode correctly;
- not translate;
- not silently normalize meaning;
- not insert facts absent from verified source;
- retain source fingerprint/version.

Store enough provenance so a retrieval result identifies document, page, region(s), source kind, verified snapshot fingerprint and source/crop evidence.

A projection is a retrieval representation, not a new source of truth.

## 9. Embeddings / pgvector

Use the repository's configured embedding-provider abstraction.

For the real pilot:

1. inspect current provider configuration;
2. use the configured real/pilot embedding provider only if intentionally configured;
3. preserve provider/model/version/dimension/config fingerprint;
4. create embeddings only for eligible current source-faithful projections;
5. make embedding creation idempotent;
6. do not embed excluded/decorative/unverified content;
7. do not embed stale projections.

If no real embedding provider is configured:

- deterministic/fake embeddings may be used for unit/integration tests;
- do not claim real semantic-RAG quality from them;
- leave the real pilot ready for provider configuration and report the exact blocker.

Do not expose API secrets.

## 10. Hard-scoped hybrid RAG

Use PostgreSQL lexical + pgvector semantic retrieval.

The first real pilot must enforce hard metadata filtering before ranking.

At minimum filter by:

- grade;
- medium;
- subject;
- curriculum version;
- active document;
- current Source V2 lineage;
- current projection.

Unit/lesson filters apply only when genuinely mapped.

No semantic similarity score may cross an incompatible grade/subject/curriculum boundary.

Every returned context item must carry source provenance sufficient to trace it back to human-verified material.

## 11. Pilot RAG evaluation

Create a private/local pilot evaluation set from the two reviewed documents.

Do not commit private source text if repository policy says it must remain local.

Use representative queries derived from verified material.

Evaluate at minimum:

- exact lexical anchor retrieval;
- Sinhala semantic retrieval where a real embedding provider is configured;
- page/region provenance correctness;
- cross-document retrieval where appropriate;
- hard-scope exclusion;
- excluded/decorative content absence;
- no unverified/stale content;
- visual-with-text labels retrievable;
- visual-only source handled without invented text;
- top-K relevance inspection.

For this small pilot, record metrics such as Recall@K, MRR where useful, lexical/vector/fused candidate counts, latency, scope leakage count and provenance completeness.

Do not claim production-quality RAG from five pages. This is a correctness/integration pilot.

## 12. Staleness/invalidation tests — mandatory

Use disposable synthetic data, not the real human-reviewed pilot, to prove:

verified source
→ snapshot
→ unit
→ projection
→ embedding
→ retrievable

then:

human correction/reclassification/exclusion
→ prior Source V2 snapshot becomes stale
→ old unit/projection/embedding becomes ineligible
→ retrieval no longer returns it
→ new review required before replacement lineage can become active

Never mutate or invalidate the real human review merely to test this.

## 13. Remove active old-understanding dependencies

After the Source V2 downstream bridge works, perform a repo-wide active-code audit for:

- TrustedPageKnowledgeModel
- TrustedPageKnowledge
- PageUnderstandingService
- source_understanding_pages
- source_understanding_regions
- source_understanding_document_is_resolved
- trusted_page_knowledge
- knowledge-preparation SQL/functions tied only to removed understanding runtime

Classify every remaining use.

Allowed:
1. historical migration;
2. historical archive/docs;
3. explicit legacy readback isolated from new Source V2 work.

Not allowed:
- new Source V2 knowledge preparation;
- embedding eligibility;
- current retrieval eligibility;
- new generation lineage.

Do not delete historical migration files.

## 14. Update authoritative architecture docs

Update active docs so repository truth becomes:

immutable PDF
→ deterministic page render
→ canonical regions/crops
→ current AI agent exact visual source reading
→ human Source V2 verification
→ immutable verified-source snapshot
→ source-faithful Knowledge Units
→ deterministic retrieval projections
→ embeddings
→ hybrid RAG
→ blueprint/generation later

Update as applicable:

- README.md
- docs/SYSTEM_ARCHITECTURE.md
- docs/v1/00_V1_MASTER_PLAN.md
- docs/v1/02_PRIORITY_1_ADMIN_RAG_LLM_SPEC.md
- docs/v1/05_TEACHER_FIRST_MULTI_GRADE_CONTENT_STUDIO.md
- docs/v1/PHASE_TRACKER.md
- Source V2 decisions/state docs
- relevant agent skills

Remove active wording that says current knowledge truth originates from old visual-understanding runtime.

## 15. Automated validation

Use TDD/eval-driven development.

Required focused tests:

- human-reviewed Source V2 document can cross downstream gate;
- unresolved Source V2 document cannot;
- excluded regions never become retrieval text;
- decorative regions never become retrieval text;
- VISUAL_ONLY zero-text remains valid and does not invent text;
- VISUAL_WITH_TEXT exact labels remain attributable;
- Source V2 snapshot fingerprint is deterministic;
- snapshot becomes stale after source revision;
- knowledge units depend on current Source V2 lineage;
- projections are source-faithful;
- embeddings require current eligible projections;
- hard scope filtering works;
- retrieval provenance resolves to Source V2 page/regions;
- stale source cannot be retrieved;
- idempotent rerun creates no duplicates.

Then run broad gates:

- Source V2 test suite;
- knowledge-preparation/indexing tests;
- embedding tests;
- retrieval tests;
- relevant generation-lineage tests;
- real PostgreSQL/pgvector integration tests;
- migration BASE → HEAD on fresh DB;
- Ruff check;
- Ruff format check;
- strict mypy;
- frontend tests;
- frontend lint;
- TypeScript;
- production builds;
- OpenAPI/client reproducibility.

Fix every new failure introduced by this work.

## 16. Real Studio execution

After code gates are green, run the real pilot against the operator's existing human-reviewed DB.

Do not reset it.

Sequence:

1. validate the two document gates;
2. create current Source V2 verified snapshots;
3. derive source-faithful units;
4. build projections;
5. create embeddings if a real provider is configured;
6. run hybrid retrieval queries;
7. inspect DB lineage;
8. prove no excluded/unverified data crossed the gate.

Capture actual counts.

## 17. Chrome DevTools MCP validation

Use the real Studio.

Validate:

Materials / Source review
- both pilot documents remain present;
- human review state remains unchanged;
- no source review action is performed by the agent.

Knowledge/RAG operator surface
- use existing RAG Explorer / knowledge UI if present;
- if no safe inspection path exists, add only the smallest useful view; do not redesign the whole Studio;
- document reports Ready for AI only when Source V2 gate is current;
- knowledge/indexing state is understandable;
- a pilot retrieval query returns source-backed results;
- result provenance identifies the correct document/page;
- no excluded/decorative/unverified region appears;
- Network clean;
- Console clean except already-classified non-blocking advisory.

## 18. Cold restart

After first real-RAG acceptance:

- rebuild/restart migrate;
- api;
- worker;
- web;
- maintenance.

Use no-cache where stale images could retain removed code.

Then verify:

- migration HEAD;
- Source V2 human state unchanged;
- knowledge/projection/embedding counts unchanged/idempotent;
- retrieval produces the same current-source results;
- no stale worker actor/path appears.

## 19. Acceptance status

Return one of:

VERIFIED RAG PILOT: PASS

Use when:
- both pilot documents are genuinely human-resolved;
- both Source V2 gates are usable;
- active downstream lineage is Source V2-native, not old source-understanding;
- source-faithful units/projections are created;
- real embeddings are created using an intentionally configured provider;
- hybrid retrieval returns correct scoped source-backed results;
- provenance is complete;
- staleness tests pass;
- Chrome and cold restart pass.

VERIFIED RAG PILOT: READY_FOR_EMBEDDING_PROVIDER

Use only when all Source V2 → Knowledge → projection engineering is correct but no real embedding provider is intentionally configured.

Do not silently promote deterministic test vectors as production semantic RAG.

VERIFIED RAG PILOT: HUMAN_REVIEW_INCOMPLETE

Use if real Source V2 review is not actually fully resolved.

VERIFIED RAG PILOT: METADATA_REVIEW_REQUIRED

Use if authoritative grade/medium/subject/curriculum metadata is missing.

VERIFIED RAG PILOT: BLOCKED

Use only for a genuine technical/external blocker.

## 20. Commit

After the highest truthful acceptance state is reached, commit and push master.

Suggested commit when fully implemented:

refactor(rag): build Source V2 verified knowledge pipeline

Do not commit real private source text/eval payloads to Git.

## 21. Final response format

Return only:

VERIFIED RAG PILOT:
<PASS / READY_FOR_EMBEDDING_PROVIDER / HUMAN_REVIEW_INCOMPLETE / METADATA_REVIEW_REQUIRED / BLOCKED>

Final commit:
<hash>

Human Source V2 gate:
<per-document verified/excluded/unverified + usable>

Source V2 snapshot:
<count + fingerprint/provenance result>

Old understanding dependency:
<removed / isolated historical only / blocker>

Knowledge Units:
<count>

Retrieval projections:
<count>

Embeddings:
<count + provider/model/dimension or not configured>

Pilot RAG evaluation:
<key metrics + scope leakage>

Provenance:
<result>

Staleness/invalidation tests:
<result>

Backend tests:
<result>

Frontend tests:
<result>

Ruff/mypy/TypeScript/build:
<result>

Chrome DevTools MCP:
<result>

Cold restart:
<result>

Human review state changed by agent:
NO

Next step:
<one concise line>

Blocker:
<only if applicable>

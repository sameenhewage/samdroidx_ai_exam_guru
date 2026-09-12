# Grade 5 Teacher Pilot Readiness Contract

## Purpose

This document defines when Exam Guru is ready to be handed to real teachers for a Grade 5 pilot.

The teacher pilot is **not** an architecture test. Teachers should receive a coherent product they can actually use to prepare, review and approve Grade 5 papers.

## Current verdict — NOT READY (10 September 2026 local inspection)

The actual Windows Docker Desktop Studio was inspected directly, rather than inferred from the historical rollout below. At the initial checkpoint its database contained **one checksum-matched real corpus source / four page-review states**, 24 candidate versions and 33 page events, with **zero current verified pages, ground truth, benchmarks, chunks, embeddings or admitted curricula**. Legacy `source_pages` and `extracted_blocks` are both empty; those tables are distinct from the newer page-review records. The sole subject is an exactly identified migration bootstrap placeholder, not a proven E2E fixture. No reset, deletion, quarantine or fabricated admission was performed.

The fresh read-only Grade 3/4/5 inventory found **711 files / 696 PDF paths / 587 distinct PDFs / 5,234 unique pages** (7,112 pages across duplicate paths). Grade 3 has 307 PDF paths, Grade 4 has 197, and Grade 5 has 192. All 711 files retained their original hashes, sizes and recorded filesystem identity. All unique pages were inspected; five distinct encrypted PDFs are readable without passwords, and 99 PDFs emitted parser warnings. These are inventory results, not OCR accuracy or trusted content. The uploaded four-page multiplication source matches two Grade-5 paths exactly.

Actual browser inspection exposed a 32-pixel reading area at 1280×720 and an empty-catalogue metadata-correction dead end. Noto Sans Sinhala rendered correctly; current mathematical/grid/table failures remained blocked. The deployed web security patch was subsequently updated from Next 16.3.1 to the verified 16.3.4 release without changing source/database state. Local source-review, safe corpus intake, representative human ground truth and real accuracy measurements remain unfinished; no teacher-pilot invitation or Ready-for-AI claim follows.

Subsequently, committed release `e4cf19d` and migration 0040 were deployed locally. All 17 checked existing data tables and six managed files matched the pre-rollout snapshot. The real multiplication review now provides 270 pixels of text area at 1280×720 and 318 at 1366×768, with visible actions and no outer-page overflow. A full-corpus read-only upload preflight passed; one Grade-3 English source was then imported normally, while the existing Grade-5 multiplication PDF deduplicated to its existing identity. Replay created no additional documents. The bounded checkpoint is **two real documents / five page states / 30 candidates / 43 review events**, with **zero verified pages, ground truth, chunks or embeddings**. The English page remains blocked for incomplete/high-resolution raster evidence; an explicit multiplication page-2 reread selected native-equation recovery but retained unresolved raster/position risks. This is not full-corpus or accuracy acceptance.

The following 11 September checkpoint has **three real documents / 376 original pages** after the genuine 169,816,530-byte guide completed its retained upload session. One unverified metadata candidate corrected the multiplication material's displayed category without replacing original intake or approving anything. The guide resumed across a graceful reader restart with earlier candidate/state evidence intact, but its job ended `source_fidelity_failed` after processing all 371 pages: **366 failed pages / five needing review** in that guide alone. Vector-outline/native-font and mathematical-reference blockers remain; all three documents still have zero human ground truth, trusted pages, chunks and embeddings. A tested presentation fallback for unread/undetermined pages does not repair these source-fidelity failures. No full-corpus or teacher-pilot readiness claim follows.

The owner has made actual local runtime/source fidelity the primary acceptance gate. GitHub remains secondary: current-change regressions and genuine security/data-integrity failures require correction, while unrelated external failures are classified and recorded without stopping safe local product work. The reported runtime-CI failure was a Google Chrome APT package-index hash mismatch after a successful zero-vulnerability npm install; no integrity check was bypassed.

## Historical separate-runtime checkpoint — 6 September 2026

The following counts and benchmark links describe the earlier rollout, not the current Desktop database:

All 711 raw original/evidence files remain unchanged: 696 Grade 3/4/5 PDF paths represent 587 unique originals and 5,234 unique-PDF pages. The final integrity proof verifies SHA-256, size, nanosecond mtime/ctime, inode, device and mode. Forward migration from `0032` to `0038_upload_request_identity` preserved 659 source rows and extraction, knowledge, review and published history. Audited quarantine of 72 exactly proven E2E sources leaves the 587 real originals in normal Materials, active but metadata-required, untrusted and unindexed. Inventory/footer counts do not establish educational coverage. See [known limitations](06_KNOWN_LIMITATIONS.md) for the exact checkpoint and private evidence reference.

**All 587 latest whole-document reading jobs are completed; all 5,234 current page-review states are `needs_review`.** There are zero failed, verified or excluded current pages and zero ground truth. The 116 image-failure whole-document jobs were retried through the application API after the MuPDF diagnostic/renderer-slot fixes, without larger budgets; old failed attempts remain historical evidence. The 10,362 candidate versions and 10,546 page events are immutable review evidence, not approvals. One real legacy reviewed page remains an unconfirmed human candidate. Completed reading, durable images and review access do not make this a completed teacher-pilot or release gate.

Normal Generate Papers now exposes only current admitted scope, with bound grade/medium/subject/template choices and a required source-scope fingerprint. The live Studio correctly shows no approved curriculum rather than fixture labels; no real catalogue was approved to make the selector nonempty. Exact final verification and CI outcomes remain in the phase tracker, not inferred from earlier checkpoints.

Teachers must not be invited for the full paper-generation pilot yet. Gates C, E, I, K and L remain blocked:

- source text, including the previously corrupt Grade 3 Sinhala Maths material, still needs explicit original-page adjudication;
- catalogue/metadata admission and page-text verification are separate outstanding decisions;
- reviewed Scholarship Paper I ability/reasoning authority and sufficient past-paper evidence are unavailable;
- Paper II lacks verified, admitted Grade 3–5 programme coverage; and
- fresh clean-system real-source Paper I/Paper II/full-package validation and sample-paper adjudication have not run.

The fixed 25-original/40-page coverage benchmark (nine Grade 3, nine Grade 4, 22 Grade 5 pages) has zero human references. Fast `sin+eng` and best Sinhala plus the same fast English each completed 40/40 attempts, with 31 differing outputs and nine identical outputs; that is not CER/WER, an accuracy result or a model winner. Its 60-second command budget is not the page-reader's 30-second default. The current worker has `eng`, `sin` and `tam`, but language availability does not establish reading quality.

Earlier live `text-embedding-3-small`, `gpt-5.6-luna` generation and `gpt-5.6-terra` generated-question verification baselines prove bounded provider integration, structured output and accounting only. They cannot replace reviewed Scholarship sources or human sample-paper assessment. The optional source-image/text semantic-diagnostic factory is separate, disabled by default and not connected to general corpus reading; no live source-semantic accuracy is claimed. Unsupported Scholarship generation still fails closed with `paper_generation_programme_policy_unavailable`. No P2/P3/P4/P5/P10 acceptance gate or Priority 2 lock is promoted by this remediation.

### Bounded source-adjudication handoff, not a paper-generation pilot

The [historical fixed benchmark review queue](http://localhost:3000/admin/materials/benchmark-review?benchmark_id=6d050ee1-141c-4424-88a9-a6dbff2af9ec) belongs to that earlier runtime and is not present in the newly inspected Desktop database. Establish a checksum-bound representative queue in the actual Studio before presenting it as a current reviewer handoff. Compare every selected page with its original image/PDF; retain raw candidate and failure history, save corrections as new candidates, and confirm only the exact current version after comparison. Exclude unsuitable pages explicitly with a reason. Do not substitute an OCR/LLM rewrite or a legacy reviewed flag for human ground truth. Catalogue metadata may be reviewed independently; confirming either gate must not manufacture the other.

For pages whose reading cannot yet be confirmed, the independent **Add evaluation reference** workflow collects a person's transcription against the original image without changing operational source trust. Its editor starts blank or from the previous human reference and retains drafts through revision conflicts. Report its evaluation-only counts separately: they do not populate legacy source-confirmed ground truth, clear visual/Maths failures, admit curriculum or make content Ready for AI. A fixed real-source selection and genuinely human-written references remain prerequisites for accuracy measurement; synthetic workflow tests do not satisfy them.

## V1 scope

Teacher-facing V1 scope:

- Grade 5 only
- Sinhala Medium first
- Grade 5 Scholarship as the primary national-exam workflow
- Grade 5 Subject Practice and Term Test only where already useful and stable

The underlying platform remains extensible to Grades 1-13, but future grades, O/L and A/L are not part of this teacher-pilot acceptance.

## Grade 5 Scholarship model

### Paper I — Ability & Reasoning

Must use a versioned Scholarship-specific ability/reasoning framework.

It must not be represented as one ordinary subject syllabus.

### Paper II — Curriculum Knowledge

Must use a versioned Scholarship programme coverage policy capable of combining the supporting reviewed curriculum scopes required for the exam, including where applicable:

- Grade 3 reviewed curriculum/material
- Grade 4 reviewed curriculum/material
- Grade 5 reviewed curriculum/material limited to eligible Terms 1-2

These are supporting source scopes for the Grade 5 Scholarship exam; they are not separate Grade 3/4 teacher products.

### Full Scholarship Practice

Must compose Paper I and Paper II using their own rules and preserve that structure through generation, review and publication.

## DO NOT INVITE TEACHERS UNTIL THIS GATE PASSES

A build is not Teacher-Pilot Ready merely because:

- the database supports the model;
- unit tests pass;
- the UI renders;
- a fake fixture paper generates;
- the LLM returns questions.

All required product gates below must pass.

## Gate A — Materials

A teacher can:

1. open Materials and immediately see uploaded real documents;
2. search and filter;
3. paginate without endless scrolling;
4. identify title, filename, medium, material type, year, pages and status;
5. open **View PDF** successfully;
6. open **Review extracted text** successfully;
7. correct extraction/OCR text;
8. promote only reviewed content to **Ready for AI**.

No synthetic/test labels or internal IDs appear in normal teacher screens.

`Ready for AI` requires an active, non-quarantined original, confirmed metadata/current catalogue admission, every original page explicitly verified or excluded, and at least one verified page. Excluding every page is neither readiness nor ground truth. Source-page confirmation is distinct from metadata review and does not create or approve curriculum scope.

Upload recovery must retain progress and reject a reselected PDF with a different size or committed-prefix checksum before appending. PDF/image review must preserve source identity across restarts; a missing or corrupt declared page image must show an unavailable/error state, not silently substitute another artifact. Detailed resource/legacy limits remain in [the architecture contract](../SYSTEM_ARCHITECTURE.md#45-separate-legacy-and-resumable-intake-contracts), not teacher-facing setup choices.

## Gate B — Grade 5 Scholarship setup

A teacher can select:

- Grade 5
- Sinhala Medium
- Grade 5 Scholarship Practice

The ordinary Subject selector disappears.

The teacher can then choose:

- Paper I — Ability & Reasoning
- Paper II — Curriculum Knowledge
- Full Scholarship Practice

No O/L/A/L or future-grade choices clutter the V1 workflow.

## Gate C — Real source readiness

The local RAG source corpus must be inventoried and the pilot must know whether it has enough reviewed source material for the configured Scholarship policy.

Check for:

- Grade 3 supporting syllabus/teacher-guide material needed by Paper II
- Grade 4 supporting syllabus/teacher-guide material needed by Paper II
- Grade 5 supporting syllabus/teacher-guide material with eligible term coverage
- Grade 5 Scholarship Paper I past papers
- Grade 5 Scholarship Paper II past papers
- answers/marking/evaluation material where available
- authoritative material used to define the Paper I ability framework and Paper II coverage

Missing source material is a pilot blocker, not something to hide with synthetic fixtures.

## Gate D — RAG scope correctness

For Paper II:

- retrieval can combine the configured Grade 3, Grade 4 and eligible Grade 5 scopes;
- the system does not incorrectly force the request into one Grade 5 subject/curriculum;
- medium is enforced;
- out-of-policy lessons/terms are excluded;
- provenance survives into review.

For Paper I:

- generation uses the Scholarship ability/reasoning profile;
- it does not accidentally behave like an ordinary subject-syllabus paper.

Cross-grade retrieval is allowed only where the Scholarship programme policy explicitly requires it.

All new knowledge/history, embeddings and retrieved context must carry current verified candidate lineage and exact nonblank NFC source spans, with confirmed metadata/current catalogue admission and active reviewed taxonomy/scope. New generation, validation, review approval and publication recheck that lineage. A page edit/exclusion/reread, source removal or stale catalogue approval must block new use without rewriting existing published history.

## Gate E — Paper generation

A teacher can generate a real Grade 5 pilot paper and the system:

1. resolves the correct programme policy;
2. builds deterministic blueprint requirements;
3. retrieves reviewed evidence;
4. generates questions;
5. validates them;
6. sends them into Review & Approve.

Generation must fail clearly rather than silently producing an invalid paper when required reviewed source coverage is unavailable.

## Gate F — Review & Approve

A teacher can:

- see the selected Grade 5 paper;
- see every question in the paper list;
- open any question;
- see proposed answer/solution;
- see source evidence;
- see teacher-readable checks;
- correct the question;
- select a correction reason;
- add a reviewer note;
- approve/reject/regenerate as appropriate.

One unresolved/failed required question blocks publishing.

## Gate G — Marks

Before confirmation, marks are shown as **Marks not confirmed**.

For each question, the teacher can:

- set total marks;
- edit marking points;
- assign marks to each marking point;
- add/remove marking points;
- optionally use an AI suggestion;
- save the teacher-confirmed marking scheme.

The AI suggestion is never treated as final authority.

## Gate H — Publishing

Only fully approved Grade 5 paper versions can be published.

Published versions are immutable.

A later correction creates a new version.

## Gate I — Browser/runtime proof

Primary product acceptance must exercise the actual local Studio and representative real sources through normal application workflows. Synthetic regression data remains isolated, and the separate clean-system gate below still applies before a full pilot release. Required real-source flow:

`Real source -> extraction review -> Ready for AI -> Grade 5 Scholarship generation -> question review -> marks confirmation -> approval -> publish`

Keep browser/E2E evidence according to repository conventions.

## Gate J — Engineering quality

All applicable existing quality gates remain green:

- backend unit/integration tests
- frontend tests
- configured coverage thresholds
- Ruff / formatting / mypy
- ESLint / TypeScript / production build
- OpenAPI/client reproducibility
- migrations
- security/secret checks
- isolated Compose runtime
- Playwright/browser acceptance
- remote CI as a secondary gate, with current-change/security failures fixed and unrelated external/pre-existing failures explicitly classified

Do not reduce thresholds to pass. An unrelated hosted-runner failure does not supersede actual local runtime/source acceptance or authorize weakening any check.

---

## Gate K — Sample-paper engineering validation

Before teacher handoff, engineering must generate and audit real sample papers from reviewed local sources.

Required sample families:

- Scholarship Paper I — multiple samples using the ability/reasoning framework
- Scholarship Paper II — multiple samples using the configured multi-scope Grade 3/4/eligible Grade 5 coverage
- Full Scholarship Practice — at least one complete teacher-reviewable package
- Grade 5 Term Test/Subject Practice regression sample only if those remain in V1

For every sample, record:

- blueprint/programme version
- source scope/provenance
- expected vs actual question structure
- validation findings
- duplicate findings
- answer correctness findings
- marking completeness
- unresolved review state

Any engineering-fixable defect found during sample generation must be fixed and the affected sample regenerated/revalidated.

## Gate L — Final clean-system validation

After development is complete, perform one fresh final validation in a clean isolated runtime:

`fresh DB -> migrations -> real source ingestion -> review/trust -> embedding/index -> Paper I/Paper II/full generation -> validation -> teacher correction -> marking -> approval -> publication -> published readback`

This final pass must use the real backend and browser flow.

If the clean-system validation finds a defect, return to engineering, fix it, then rerun the affected final validation.

Do not declare teacher readiness from incremental tests alone.

# What teachers should test AFTER engineering marks the build ready

Do not ask teachers to test architecture, RAG, embeddings or API behavior.

Give them teacher tasks.

## Teacher Task 1 — Check materials

> Open Materials. Find the Grade 5 Scholarship sources you expect. Open a PDF, review the extracted text and tell us if anything is missing, mislabeled or difficult to understand.

Observe:

- can they find documents without help?
- do titles/statuses make sense?
- can they understand View PDF vs Review extracted text?
- are Sinhala extraction errors obvious and editable?

## Teacher Task 2 — Generate Scholarship Paper I

> Create a Grade 5 Sinhala Medium Scholarship Paper I practice paper using the normal teacher workflow. Do not use Advanced.

Ask them to judge:

- does the paper feel like Scholarship Paper I?
- are ability/reasoning questions appropriate?
- are questions clear?
- difficulty?
- duplication?
- language quality?

## Teacher Task 3 — Generate Scholarship Paper II

> Create a Grade 5 Sinhala Medium Scholarship Paper II practice paper. Review the generated questions and the source evidence.

Ask them to judge:

- curriculum appropriateness;
- whether questions stay inside eligible scope;
- answer correctness;
- Sinhala wording;
- age suitability;
- difficulty;
- source relevance.

## Teacher Task 4 — Correct a bad question

> Find one question you would change. Edit it, choose the reason, correct the answer/wording if needed and save it.

This proves the human-correction workflow is understandable.

## Teacher Task 5 — Confirm marks

> Review the marking for selected questions. Change the total/marking points where you disagree with the suggestion.

This evaluates whether the marking editor matches real teacher practice.

## Teacher Task 6 — Approve a paper

> Review every required question and approve the paper only when you would be comfortable giving it to students.

Do not ask them to approve known synthetic questions merely to complete a workflow.

---

# Teacher feedback form

For each generated question, capture structured feedback:

- Accept as-is / Needs correction / Reject
- Correct answer? Yes / No
- In eligible syllabus/programme scope? Yes / No / Unsure
- Language natural for Grade 5? Yes / No
- Difficulty: Too easy / Appropriate / Too hard
- Ambiguous? Yes / No
- Duplicate/familiar copy? Yes / No
- Marking scheme appropriate? Yes / No
- Correction reason
- Corrected text/answer/marking
- Optional note

Teacher-approved corrections should enter the existing correction/eval feedback loop with full lineage.

## Pilot verdict

Use only:

- **READY FOR TEACHER PILOT**
- **NOT READY — ENGINEERING BLOCKER**
- **NOT READY — SOURCE DATA BLOCKER**
- **NOT READY — HUMAN ADJUDICATION BLOCKER**

Do not use `architecture ready` as the pilot verdict.

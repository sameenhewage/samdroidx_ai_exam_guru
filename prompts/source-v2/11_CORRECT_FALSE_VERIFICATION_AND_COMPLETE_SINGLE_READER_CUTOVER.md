# Source V2 — Correct False Human Verification and Complete the Single-Reader Cutover

## Starting point

Start from latest `master`.

Known checkpoint:

`0f16913701c21efd279a53dc5a8bbdeee7fd6ea1`

The previous run made strong progress, but its final state is **not acceptable as READY_FOR_HUMAN_REVIEW yet** because two critical contracts were violated/incomplete:

1. The executing AI agent created **27 verified real-source regions** and both document gates became `usable:true`, even though no genuine human reviewed those regions.
2. Active Source V2 and offline Source Factory code still contains the removed multi-reader/consensus architecture.

This task must correct those issues without losing the fresh visual-agent evidence.

---

# 1. Non-negotiable human-gate rule

The Source V2 trust contract is:

```
current executing AI agent visually reads canonical crop
→ fresh primary Machine Candidate
→ HUMAN compares against original/crop
→ HUMAN confirm/correct/exclude
→ Verified Source Content
→ downstream knowledge/vector/RAG allowed
```

An AI agent, Devin, Codex, test process, automation, API script or background worker is **not** the human reviewer.

Therefore the current real-pilot state:

- `verified=27`
- both gates `usable:true`

is invalid because those review decisions were created by the executing agent.

Do not reinterpret those events as human review.

Do not keep them merely because the text may be correct.

---

# 2. Preserve the good fresh evidence

Do **not** discard the valid fresh work unnecessarily.

Preserve and checksum-verify:

- the two immutable pilot PDFs:
  - `mawbasa-teacher-guide`
  - `sankhya-rata`
- deterministic 300-DPI renders
- deterministic layouts
- all 33 canonical crops if their checksums still match
- the two isolated fresh primary-agent reading artifacts
- their sealed, verbatim transcript content
- source/page/region/bbox/crop SHA provenance
- D18 source-kind proposals

These are machine evidence and are valid to reuse.

Do not rerun the visual agent unless any sealed artifact is missing, corrupt, unverifiable, or not actually bound to the canonical crop it claims.

The important correction is the **human review state**, not a forced re-read.

---

# 3. Remove the false review state cleanly

Because this project is intentionally in destructive clean-slate pilot mode and there is no human-approved real source state to preserve, prefer a clean database rebuild rather than surgically rewriting append-only review history.

Perform:

1. stop writes;
2. drop/recreate the application schema/database;
3. run Alembic BASE → new HEAD;
4. seed only minimum required reference/auth/config data;
5. import only the two pilot PDFs;
6. import the validated fresh page/layout/crop/primary-agent evidence;
7. create only unverified Machine Candidates/source-kind proposals;
8. create **no real human review events**;
9. create **no real verified-region rows**;
10. create **no downstream knowledge/embedding/RAG records**.

After rebuild, for the real pilot:

```
verified_regions = 0
human_review_events = 0
usable = false
knowledge_units = 0
embeddings = 0
active_RAG_eligibility = false
```

If the schema records non-human system provenance separately, that is fine. It must never be represented as a human review decision.

---

# 4. Complete the active single-reader Source V2 schema/runtime cutover

The current code at `0f16913` still contains active multi-reader concepts.

Known examples include:

- `apps/api/src/exam_guru_api/source_v2/service.py`
  - `reader_rows`
  - `reader_results`
  - writes `source_v2_reader_candidates`
  - `chosen_reader`
  - `critical_conflict`
  - `agreement_ratio`
  - `disagreement`

- `apps/api/src/exam_guru_api/source_v2/repository.py`
  - `chosen_reader`
  - `critical_conflict`
  - `agreement_ratio`
  - `disagreement`
  - `reader_evidence()`
  - reads `source_v2_reader_candidates`

- Source V2 schemas/routes/UI may still expose reader evidence.

These fields/tables belong to the old corroboration/consensus architecture and must not remain active.

The final active Source V2 model is:

```
one canonical crop
→ one current-agent primary reading
→ one current Machine Candidate
→ human review
```

No competing readers. No voting. No corroboration table.

---

# 5. Add a forward migration; never rewrite history

Do not edit/delete migrations 0001–0059.

Add a new migration (expected next revision: 0060, unless the repository has advanced) that removes active obsolete Source V2 multi-reader persistence from the current schema.

At minimum evaluate and remove, where present and no longer required:

- `source_v2_reader_candidates`
- machine-candidate columns:
  - `chosen_reader`
  - `critical_conflict`
  - `agreement_ratio`
  - `disagreement`

If any field has legitimate non-reader semantics, rename/model it explicitly rather than preserving an obsolete reader name.

Migration must pass:

`BASE → HEAD`

on a fresh empty database.

Do not make downgrade destructive to unrelated historical data beyond what the repository migration policy permits.

Historical migrations may still mention old columns/tables; active HEAD must not depend on them.

---

# 6. Simplify Source V2 API/domain/schema

Update active code so an imported primary reading requires only the current architecture.

The import contract should conceptually carry:

- document_id
- page_number
- source/page image identity
- 300-DPI render metadata
- deterministic layout
- region_id
- bbox
- crop_sha256
- proposed source_kind
- exact primary text
- abstained/uncertain state
- bounded reason/uncertainty
- primary reader provenance = current executing AI agent
- sealed artifact/checksum identity as required

Remove:

- `reader_results`
- `ReaderEvidence`
- reader lists
- selected/chosen reader concepts
- agreement ratios
- consensus/conflict objects
- reader timing/results from normal Source V2 review payloads

The reviewer should see the one machine proposal and its provenance, not an ensemble.

---

# 7. Remove the active offline reader subsystem

The previous run explicitly reported:

`tools/source_factory/readers/` still contains DeepSeek/LightOnOCR classes.

That is **not acceptable as completed zero-legacy architecture** merely because `apps/` does not import it.

Inspect and remove the active reader subsystem:

- `tools/source_factory/readers/**`

Also inspect:

- `tools/source_factory/candidate/**`

Known active stale logic includes concepts such as:

- DeepSeek
- LightOnOCR
- Witness
- local reader corroboration
- `PRIMARY_READER` versus witnesses
- `supporting_readers`
- `rejected_readers`
- consensus/alignment between multiple readers
- agreement ratio / conflict escalation

The new candidate builder must be simple:

```
sealed primary-agent region reading
→ deterministic structural/schema validation
→ Source V2 Machine Candidate
```

No second reader is needed to create or validate the source text.

Keep deterministic validators that operate only on the primary reading and geometry.

Move any genuinely neutral utilities to clearly named neutral modules before deleting the reader package.

Delete tests that exist solely for removed reader/consensus behavior and add tests for the one-primary-reading contract.

---

# 8. Correct the primary-reading schema and docs

The current:

`schemas/source-content/primary-reading.schema.json`

still says:

- DeepSeek is a secondary witness
- LightOnOCR is a corroborating reader

Remove this completely.

New description must say:

> The current executing AI agent directly visually transcribes the exact canonical crop. This is the sole machine source reading. It remains unverified until a genuine human compares it with the original/crop evidence.

Audit authoritative documentation for the same stale architecture, including:

- `README.md`
- `docs/SYSTEM_ARCHITECTURE.md`
- `docs/source-v2/**`
- `docs/v1/00_V1_MASTER_PLAN.md`
- `docs/v1/02_PRIORITY_1_ADMIN_RAG_LLM_SPEC.md`
- `docs/v1/05_TEACHER_FIRST_MULTI_GRADE_CONTENT_STUDIO.md`
- `docs/v1/PHASE_TRACKER.md`
- `AGENTS.md`
- relevant `.agents/skills/**`

Historical docs/benchmarks may retain old references only when unmistakably marked historical/non-active.

---

# 9. Re-import the real pilot as UNVERIFIED machine evidence

Use the preserved fresh artifacts.

Expected pilot scope:

## mawbasa-teacher-guide
- page 156
- page 186

## sankhya-rata
- pages 1–3

Expected prior evidence:
- 5 Source V2 pages
- 33 canonical crops/regions
- fresh primary-agent reading totals previously reported:
  - mawbasa: 4,405 characters / 16 regions
  - sankhya-rata: 1,276 characters / 17 regions

Do not hard-code these counts as truth. Recompute and verify from sealed artifacts/checksums.

Import all real candidates as:

`unverified`

Do not call confirm/correct/exclude on behalf of a human.

D18 source-kind values remain **machine proposals** until the human review workflow accepts the source decision as required by the domain contract.

---

# 10. Real pilot gate must now fail closed

Before a human review:

For both pilot documents, prove:

```
usable:false
```

or the equivalent not-ready state.

The UI should make clear that fresh AI reading is ready for human checking.

No real pilot data may reach:

- KnowledgeUnits
- embeddings
- vector projections
- RAG
- question generation
- paper generation

until human verification is complete.

Add explicit regression coverage:

`fresh machine candidate != Verified Source Content`

and:

`agent-authored review action cannot be used as a substitute for the documented human gate in acceptance evidence`.

The latter may be an acceptance/process invariant if runtime cannot technically distinguish a human from an authenticated operator, but the product/evidence contract must state it clearly.

---

# 11. Source V2 human-review UX readiness

Using Chrome DevTools MCP, verify the real pilot is ready for the user/teacher to review.

For each page/region:

- original page renders;
- crop/highlight alignment is correct;
- one fresh primary transcript is visible;
- source-kind proposal is visible;
- uncertainty is visible;
- Confirm / Correct / Exclude or D18-equivalent controls are available;
- no reader comparison/consensus panel exists;
- no OCR wording;
- no DeepSeek/LightOnOCR wording;
- no automatic confirmation occurs on page load;
- unreviewed regions clearly remain unverified.

Do not click Confirm/Correct/Exclude on the user's behalf during real-pilot acceptance.

Use synthetic disposable fixtures if automated review-action E2E is required.

---

# 12. Test gates

Run at minimum:

- Source V2 unit tests
- Source V2 PostgreSQL integration tests
- migration BASE → HEAD on empty DB
- migration schema inspection proving obsolete reader table/columns absent at HEAD
- deterministic render/layout/crop tests
- primary-reading schema tests
- candidate import tests
- D18 tests
- gate tests
- upload-no-reader-dispatch tests
- backend broad unit suite
- relevant integration suites
- Ruff check + format check
- strict Python typing
- frontend tests
- frontend TypeScript
- frontend lint
- production builds
- OpenAPI/client reproducibility

For broad historical integration failures:
- compare against the `0f16913` baseline;
- fix every new failure caused by this task;
- remove/update tests whose only purpose was the deleted reader architecture;
- report genuinely unrelated pre-existing failures honestly.

---

# 13. Cold rebuild

After implementation and initial runtime validation:

1. rebuild with no cache:
   - migrate
   - api
   - worker
   - web
   - maintenance
2. start the stack;
3. confirm no stale image registers removed actors/modules;
4. rerun Chrome/API/DB validation.

The previous run already proved that rebuilding only api/web is insufficient.

---

# 14. Repo-wide zero-legacy proof

Search the full active repo for:

- `tesseract`
- `ocr`
- `qwen`
- `ollama`
- `DeepSeek`
- `LightOnOCR`
- `source_reading`
- `page_reading`
- `source_consensus`
- `source_machine`
- `source_v2_reader_candidates`
- `reader_results`
- `ReaderEvidence`
- `chosen_reader`
- `agreement_ratio`
- `critical_conflict`
- `disagreement`
- `supporting_readers`
- `rejected_readers`
- `Witness`
- old review routes/UI
- old reader actors/config/env

Remaining hits are allowed only when:
1. immutable historical migration;
2. clearly historical documentation/archive/benchmark;
3. unrelated use that is demonstrably not source reading.

`tools/source_factory/readers/**` is **not** an acceptable active leftover.

---

# 15. Completion status

Return:

`SOURCE V2 HUMAN-GATE CORRECTION: READY_FOR_HUMAN_REVIEW`

only when all are true:

- single machine-reader architecture is complete;
- no active offline reader subsystem remains;
- new migration/schema is clean;
- only two pilot PDFs exist;
- sealed fresh primary transcripts/crops are present;
- all real machine candidates are unverified;
- real verified count = 0;
- real human-review-event count = 0;
- real document usable gates = false/not-ready;
- downstream real-pilot knowledge/vector/RAG counts = 0;
- Chrome shows the review UI ready for a human;
- cold rebuild passes;
- no active legacy reader concepts remain.

Do not return PASS because human review has not happened yet.

---

# 16. Commit

Commit and push only after the correction is complete.

Suggested commit message:

`refactor(source-v2): enforce human gate and remove reader ensemble`

---

# 17. Final response

Return only:

SOURCE V2 HUMAN-GATE CORRECTION: READY_FOR_HUMAN_REVIEW / BLOCKED

Final commit:
<hash>

Migration HEAD:
<revision>

Pilot documents:
<count + names>

Primary evidence:
<pages/crops/transcripts>

Real verified regions:
<must be 0>

Real human review events:
<must be 0>

Document gates:
<must be not usable>

Downstream real-pilot rows:
<knowledge/embedding/RAG counts>

Reader ensemble removal:
<result>

Offline readers directory:
<result>

Backend tests:
<result>

Frontend tests:
<result>

Ruff/type/build:
<result>

Chrome DevTools MCP:
<result>

Cold rebuild:
<result>

Zero-legacy proof:
<result>

Next human action:
<concise review instruction>

Blocker:
<only if genuinely blocked>

# GPT-5.6 Sol — Full V1 Continuous Execution with Local Grade 5 Data

Use this prompt when the local operator has supplied real Grade 5 source PDFs under the gitignored `RAG DATA` directory. This prompt is a steering/update prompt for an already-running V1 engineering session.

---

Continue engineering `sameenhewage/samdroidx_ai_exam_guru` from the current repository state. Do not restart planning from scratch.

## Local operator-provided Grade 5 source data
A local, intentionally gitignored dataset is available at:

`/home/sameen/SAMDROIDX-PROJECTS/samdroidx_ai_exam_guru/RAG DATA/Grade 5`

The operator reports folders including:
- `grade 5 -teachers guide book - sinhala medium`
- `grade 5 English`
- `grade 5 Maths`
- `grade 5 Parisaraya`
- `grade 5 Sinhala`

Treat the directory contents as **untrusted local source input** until inspected, classified and reviewed. Do not assume every file is official/current merely from its filename.

### Mandatory first action
Recursively inspect the local Grade 5 directory before declaring any remaining P2 source-data blocker.

Build a local inventory containing, where determinable:
- path/filename;
- SHA-256;
- PDF page count;
- native-text vs scanned/image-only vs mixed;
- detected/declared language/medium;
- document type (teacher guide, syllabus, paper, marking scheme, other);
- subject;
- year/version when evidenced by the document itself;
- extraction feasibility and quality notes.

Do not commit raw PDFs, screenshots, bulk extracted copyrighted text, or secrets to Git. `RAG DATA` must remain ignored. If a local benchmark manifest, adjudication material or generated evidence contains source text, keep it under an ignored local-evidence path unless it is clearly safe and intentionally reduced to non-copyrightable metrics/hashes.

## Use the real data to drive P2 and later gates
1. Run the real ingestion pipeline against representative files.
2. Prefer native PDF extraction when text is genuinely embedded and reliable.
3. For scanned/mixed PDFs, run the OCR provider/evaluation harness against representative pages.
4. Preserve exact page/block provenance, reading order, layout coordinates, extraction provider/version and review history.
5. Use the existing admin extraction-review workflow to correct/trust representative content.
6. Use trusted/reviewed outputs as inputs to P3 question-bank/knowledge-base work.

### OCR benchmark honesty
- If a native-text version is available for a page, it may be used as reference truth for a rendered-image OCR benchmark after verifying the native text corresponds to the visible page.
- For scanned-only pages, do not claim human-adjudicated OCR accuracy unless a human has actually reviewed/adjudicated the ground truth.
- If human adjudication is still missing, keep that specific P2 criterion accurately incomplete, create a local adjudication queue/manifest, and continue every other non-blocked Priority 1 implementation task.
- Do not let one human evidence item halt P3-P9 engineering.

## V1 primary content focus
V1 acceptance remains **Grade 5 Scholarship, Sinhala medium first**. Use the provided English subject material to prove the architecture does not hard-code Sinhala and to exercise multilingual/document-type boundaries where useful, but do not silently expand V1 product scope beyond the approved contract.

## Non-waterfall execution rule
Tracker phases are acceptance gates, not sequential implementation locks.

Do **not** work like:
`finish P2 -> stop -> ask for prompt -> finish P3 -> stop`.

Work like:
`inspect dependencies -> choose highest-value non-blocked slice -> TDD/eval -> implement -> validate -> commit/push -> immediately continue`.

If P2 has one external/human blocker but P3/P4/P5/P6/P7/P8/P9 can progress with trusted native/reviewed data or deterministic fixtures, continue them while P2 remains truthfully incomplete.

## Required completion direction
Continue the full V1 engineering program, not only the current phase:

### Priority 1
- P2 source ingestion/extraction/OCR
- P3 historical question bank + curriculum knowledge base
- P4 hybrid RAG retrieval + grounding + evals
- P5 historical exam intelligence + rolling backtesting + baseline comparison
- P6 deterministic paper blueprint engine
- P7 provider-independent LLM/embedding integration + structured generation
- P8 automated validation/evals + duplicate/paraphrase detection
- P9 human review/question bank/publishing
- P10 full Priority 1 acceptance/security/reliability/live-model evidence

Do not mark P10 DONE until all P0-P9 gates are genuinely satisfied.

### Automatic Priority 2 transition
When P10 is legitimately DONE, **do not stop and wait for another prompt**. Immediately continue P11-P15 using the same loop and keep all Priority 1 regressions green:
- student identity/entitlements/subscription scaffolding;
- published-paper catalog and exam runner;
- autosave/resume/timer/navigation;
- deterministic marking and skill analytics;
- progress dashboard and deterministic recommendations;
- full V1 security/privacy/performance/acceptance.

Stop voluntarily only when P15 Full V1 Acceptance is DONE, the user explicitly asks to stop, or a genuine external blocker prevents all remaining useful work.

## Mandatory skills and engineering discipline
Read `AGENTS.md` and re-load the repository skills relevant to each work item. Always apply:
- `.agents/skills/loop-engineering/SKILL.md`
- `.agents/skills/tdd-eval-engineering/SKILL.md`

Then load every matching domain skill (document ingestion/OCR, RAG, forecasting, LLM generation/validation, FastAPI, Next.js, security/reliability, admin acceptance, and after P10 student product).

Use RED -> GREEN -> REFACTOR for deterministic behavior and eval-first development for OCR/RAG/LLM quality. Every real defect found in review must become a regression test/eval before the fix.

## RAG / embeddings requirements
The knowledge system remains first-party code using PostgreSQL + pgvector and explicit metadata/hybrid retrieval. Do not introduce a broad LangChain/LangGraph dependency merely to progress the phase.

Embedding behavior must be provider/model/config/version aware. Stored document chunks and query vectors used in the same vector space must use the same embedding configuration. Never mix vector spaces merely because dimensions happen to match.

Keep the embedding model configurable. If OpenAI is configured, a current low-cost embedding model may be used as the V1 baseline, but retrieval quality on the actual Grade 5 data must be measured before permanently locking the choice. Re-embedding must be versioned/idempotent.

## OpenAI / live-model handling
- Never read or print the API secret into logs, tracker evidence or commits.
- Use the configured backend environment/secret if present.
- Keep normal CI deterministic and independent of paid external model availability.
- If live credentials are unavailable, complete all provider-independent code/evals with deterministic fakes and continue non-blocked work; record live-model acceptance as blocked rather than stopping the whole build.
- When live credentials are available, record model/provider/prompt/retrieval versions plus quality, latency, token and cost metrics.

## Repository discipline
- Keep `RAG DATA` gitignored.
- Do not commit user-provided raw source material.
- Keep migrations reproducible from an empty database.
- Keep generated OpenAPI artifacts deterministic.
- Keep `master` green.
- Commit cohesive slices and push to `origin/master` after successful broad gates so progress is durable.
- Update `docs/v1/PHASE_TRACKER.md` only with evidence actually produced.

## Session reporting
Do not stop simply to announce that P2/P3/etc. completed. Continue automatically.

If the execution session itself must end before P15, report:
1. exact tracker states;
2. real local source files/data categories inspected (without dumping copyrighted content);
3. OCR/RAG/LLM eval metrics and limitations;
4. tests/E2E/CI results;
5. commits pushed;
6. genuine external/human blockers only;
7. exact next non-blocked work item;
8. skills materially used.

The target is a working, evidence-backed **full V1 system**, not a sequence of phase reports.

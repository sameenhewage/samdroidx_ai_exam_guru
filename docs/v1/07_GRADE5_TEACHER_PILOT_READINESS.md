# Grade 5 Teacher Pilot Readiness Contract

## Purpose

This document defines when Exam Guru is ready to be handed to real teachers for a Grade 5 pilot.

The teacher pilot is **not** an architecture test. Teachers should receive a coherent product they can actually use to prepare, review and approve Grade 5 papers.

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

The entire flow must pass against an isolated clean runtime with the real backend:

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
- remote CI

Do not reduce thresholds to pass.

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

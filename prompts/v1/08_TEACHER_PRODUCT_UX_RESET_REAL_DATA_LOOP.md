# GPT-5.6 Sol Execution Prompt — Teacher Product UX Reset + Real RAG Data Runtime Loop

You are implementing the next product-quality correction for Exam Guru.

This is not a mockup task and not a CSS-only task.

The goal is to make the existing private Teacher Studio behave like a real teacher-facing product while preserving the strong backend architecture, RAG, deterministic blueprinting, validation, audit, review, publishing and security contracts already built.

Work like a senior cross-functional software team: product engineer + frontend engineer + backend/domain engineer + QA + security/reliability reviewer.

Do not stop after fixing one screenshot. Continue the engineering loop until the complete teacher workflow is coherent, runtime-proven and regression-protected.


---

## V1 SCOPE LOCK — GRADE 5 ONLY FOR TEACHER PILOT

This instruction overrides broader demo/architecture examples elsewhere in the repo for this execution.

**Do not spend this delivery implementing Grade 6-13 teacher product flows, O/L, A/L, Grade 7 acceptance journeys, or broad multi-grade UI polish.**

The V1 teacher-facing runtime and teacher acceptance target is:

- **Grade 5 only**
- **Sinhala Medium first**
- **Grade 5 Scholarship is the primary national-exam target**
- Grade 5 Subject Practice and Grade 5 Term Test may remain supported where already useful, but they are secondary to making the Scholarship workflow genuinely usable.

The architecture must remain extensible for Grades 1-13. Do not reintroduce Grade-5-only database constraints or irreversible schema assumptions. Future grades should be addable through configuration/domain extensions rather than a rewrite.

However, **future extensibility is not permission to implement and test every grade now**.

### Critical distinction: product target vs source curriculum scope

The product target is Grade 5 Scholarship.

That does **not** mean every supporting source record has grade=5.

For a government-aligned Grade 5 Scholarship model, support the programme as a first-class assessment programme with at least:

- **Paper I — Ability / reasoning framework**
- **Paper II — Curriculum knowledge coverage**

Paper II must be able to draw from the officially configured supporting curriculum scopes required by the Scholarship programme, including where applicable:

- Grade 3 reviewed curriculum/material;
- Grade 4 reviewed curriculum/material;
- Grade 5 reviewed curriculum/material limited to the configured eligible term coverage (for the current policy, Terms 1-2).

Those Grade 3/4 records are supporting source scope for the **Grade 5 Scholarship product**. They do not make Grade 3/4 teacher-facing products part of this V1 release.

Do not model Scholarship Paper II as one ordinary `Grade 5 + Subject + CurriculumVersion` retrieval request if official programme coverage spans multiple curriculum scopes.

Paper I must not be faked as a normal subject syllabus. Represent its ability/reasoning profile through a programme-specific ability/taxonomy model that can be versioned and validated.

### Teacher-pilot release gate

Do not hand this build to teachers merely because architecture, unit tests, or isolated components are green.

The build is **TEACHER-PILOT READY** only when the complete Grade 5 acceptance flow described later in this prompt passes end to end in a clean runtime with real representative data.

If required Grade 3/4/Grade 5 source material or human-reviewed extraction/eval evidence is missing, report that as a concrete pilot blocker. Do not label the system teacher-ready.


---

## 0. Repository safety and working mode

Work in:

`/home/sameen/SAMDROIDX-PROJECTS/samdroidx_ai_exam_guru`

Use `master` only.

Before changing anything:

1. inspect `git status`;
2. preserve all current local work;
3. do not reset, clean, discard, overwrite, stash-pop, or rewrite unrelated user changes;
4. fetch the latest remote `master`;
5. integrate safely without destroying local work;
6. confirm the exact starting HEAD;
7. inspect all applicable root and nested `AGENTS.md` files.

If the working tree has unrelated changes, keep them intact and work around them safely.

Do not create a side implementation that bypasses the current application.

---

## 1. Mandatory reading before implementation

Read in full, not just snippets:

- `AGENTS.md`
- `docs/SYSTEM_ARCHITECTURE.md`
- `docs/v1/02_PRIORITY_1_ADMIN_RAG_LLM_SPEC.md`
- `docs/v1/06_SUBJECT_QUALITY_VALIDATION_ENGINE.md`
- `docs/v1/07_GRADE5_TEACHER_PILOT_READINESS.md`
- all other active V1 tracker/specification documents relevant to Teacher Studio, Materials, RAG, generation, review and publishing;
- `ui-reference/teacher-studio-v1/README.md`
- `ui-reference/teacher-studio-v1/teacher-studio-reference.html`

The new reference folder is the teacher-facing product UX contract.

The HTML is a behavioral and visual reference, not production code. Do not simply copy its implementation into Next.js. Reproduce its information architecture, interactions and teacher-facing semantics using the existing application architecture, design system, API contracts and domain services.

Read the applicable skills, including at minimum where present:

- `.agents/skills/loop-engineering/SKILL.md`
- `.agents/skills/tdd-eval-engineering/SKILL.md`
- `.agents/skills/teacher-content-studio-ux/SKILL.md`
- `.agents/skills/nextjs-product-engineering/SKILL.md`
- `.agents/skills/fastapi-domain-engineering/SKILL.md`
- `.agents/skills/priority1-admin-acceptance/SKILL.md`
- `.agents/skills/security-reliability-review/SKILL.md`
- `.agents/skills/subject-quality-validation/SKILL.md`
- RAG / extraction / LLM validation skills applicable to the touched paths.

If skill names have changed, discover and use the current equivalent skills rather than skipping the discipline.

---

## 2. Product principle

The backend may be sophisticated.

The normal teacher UI must not be sophisticated.

A teacher should be able to complete:

`Home -> Materials -> Generate Paper -> Review & Approve -> Published Papers`

without understanding:

- RAG;
- embeddings;
- vector similarity;
- blueprint IDs;
- taxonomy IDs;
- fingerprints;
- generation run IDs;
- provider/model internals;
- semantic-verifier records;
- database identifiers;
- worker/job infrastructure.

Those concepts belong behind the scenes or under **Advanced** diagnostics.

Do not expose a backend module as a normal teacher page just because the module exists.

The product flow drives the UI; backend modules collaborate invisibly behind it.

---

## 3. First deliverable: current-state gap analysis

Before making broad changes, run the current application and inspect the real teacher flow in the browser.

Create a concise implementation gap matrix:

| Area | Current runtime | Reference expectation | Root cause | Planned fix | Test |
|---|---|---|---|---|---|

Cover at least:

- navigation;
- Materials;
- material view / extraction review;
- Generate Paper target;
- coverage;
- paper settings;
- Review & Approve;
- per-question marks;
- Published Papers;
- Advanced;
- synthetic/test data pollution;
- real RAG data ingestion;
- runtime/browser behavior.

Do not treat screenshots alone as proof. Trace frontend -> API -> service/repository -> DB/domain data where required.

---

## 4. Normal teacher navigation

Normal teacher navigation must be reduced to:

1. Home
2. Materials
3. Generate Paper
4. Review & Approve
5. Published Papers

Move technical areas out of the normal teacher flow and under **Advanced** or an equivalent restricted diagnostics area:

- Curriculum Setup
- Blueprint Studio / blueprint diagnostics
- RAG Explorer
- Exam Intelligence / forecasting / backtesting
- Validation diagnostics
- Operations
- Audit / technical metadata

Do not delete useful engineering functionality. Re-home it.

Normal teacher pages must not display raw IDs/fingerprints/provider details by default.

---

# 5. Materials — teacher-first library

## 5.1 Default view

Opening Materials must immediately answer:

> What have I uploaded?

Do not make the teacher scroll through Grade 1–13 summary blocks before seeing documents.

Default view = uploaded materials library.

Use compact document cards/rows, not a database-grid aesthetic.

A material should read approximately like:

`Grade 5 Mathematics Teacher Guide`

`grade-5-maths-teacher-guide.pdf`

`Sinhala Medium · Teacher Guide · 2025 · 184 pages`

`Grade 5 · Mathematics`

`Ready for AI`

Actions:

- **View PDF**
- **Review extracted text**
- overflow / additional actions where required

Use teacher-readable labels only.

## 5.2 Pagination

The current unbounded scrolling experience is unacceptable.

Implement real server-side pagination.

The API/query contract should support the repository's preferred form, for example:

- `limit / offset`, or
- `page / page_size`

plus filtering/search.

Frontend must not fetch an arbitrary large list and merely slice it locally as the production solution.

Required UX:

- result count;
- Previous / Next;
- page numbers where appropriate;
- rows-per-page selection;
- preserve filters when changing pages;
- reset page safely when a filter changes;
- deterministic sort, defaulting to a teacher-useful order such as recently uploaded.

## 5.3 Search and filters

Support at minimum:

- search by title / filename;
- grade;
- subject;
- medium;
- material type;
- status;
- year if supported by the domain.

## 5.4 Action semantics

Use explicit labels:

- `View PDF` = view the immutable uploaded source;
- `Review extracted text` = compare source page with extracted/OCR text, correct it and control trust promotion.

Do not label both actions generically.

The PDF preview must actually work in browser acceptance; do not accept a broken embedded viewer.

## 5.5 Synthetic/internal data must not leak

Teacher-facing Materials must never show records such as:

- `Knowledge subject 7388...`
- `Knowledge medium ...`
- `Intelligence curriculum ...`
- `Blueprint medium ...`
- `Generation English ...`
- `Subject <id>`
- random numeric fallback labels.

Find and fix the root cause.

Do not solve this with client-side string filtering such as:

`if label startsWith("Blueprint") hide it`.

Investigate seeds, fixtures, dev bootstrap, tests, catalogue/domain queries and runtime DB isolation.

Synthetic acceptance/test records may exist in isolated test environments but must not pollute the normal Studio runtime database or teacher-facing catalogues.

Malformed records with no teacher-readable label are a data-quality problem, not a successful option.

---

# 6. Material upload and trust flow

The teacher upload workflow should remain guided and simple:

`Grade -> Medium -> Subject -> Material type -> Year/Curriculum -> PDF -> Review`

Material types include the existing supported educational sources, such as:

- syllabus;
- teacher guide;
- past paper;
- marking scheme;
- evaluation/examiner report;
- other explicitly approved material.

Preserve:

- immutable source copy;
- checksum;
- duplicate protection;
- extraction/OCR lineage;
- reviewer corrections;
- provenance;
- trust state;
- audit history.

Raw upload/OCR output is not trusted merely because the file came from the local operator.

The real flow remains:

`Upload -> Extract/OCR -> Human review/correction -> Ready for AI -> Chunk/embed/index`

Do not bypass the trust gate to make the UI look complete.

---

# 7. Generate Paper — paper target

## 7.1 Grade and medium first

Start with:

- Grade
- Medium

Medium means the educational/language medium, e.g. Sinhala, Tamil, English.

Use canonical server-owned codes internally and human-readable labels in UI.

## 7.2 Grade 5 pilot paper types

Paper type is mutually exclusive and must be rendered as an accessible radio-card group, not raw browser buttons and not an invalid global dropdown.

For the **V1 Grade 5 teacher pilot**, expose only teacher-usable Grade 5 choices:

- Subject Practice
- Term Test
- Grade 5 Scholarship Practice

Do not show O/L, A/L, Grade 6-13 paper types, or other future-grade programme options in the V1 teacher runtime.

The reusable domain/schema must remain extensible for future grades, but broad future-grade product implementation is out of scope for this delivery.

The backend options/domain contract must enforce the same allowed Grade 5 combinations. Do not rely only on hiding options in React.

## 7.3 Subject rules

- Subject Practice = subject-specific.
- Term Test = subject-specific.
- Grade 5 Scholarship Practice = programme-specific; hide the ordinary single-subject selector.
- Scholarship then resolves to a programme/paper mode and its configured coverage policy rather than one normal subject curriculum.

Do not represent Grade 5 Scholarship Practice as:

`Grade 5 + Mathematics + Scholarship`

unless a future explicitly-defined scholarship sub-scope requires it.

## 7.4 Term Test

When Term Test is selected, expose a teacher-readable Term field:

- 1st Term
- 2nd Term
- 3rd Term

Back it with a proper domain value, not just display text.

Validate the term against the selected grade/curriculum policy.

Do not overclaim national uniformity if the curriculum/domain does not provide it; keep the model configurable.

## 7.5 Target consistency

For the selected target, the following must agree on the same canonical server-owned scope:

- options;
- curricula;
- lessons;
- generation request;
- blueprint generation;
- RAG filters;
- validation;
- review metadata.

Keep the existing mismatch safety protection, but valid teacher choices must not produce false `Curriculum choices changed` errors.

---

# 8. Generate Paper — coverage

Coverage is mutually exclusive and must use proper accessible radio cards.

Do not use raw unstyled buttons.

## 8.1 Subject Practice

Offer:

- **Full subject**
- **Choose specific lessons**

## 8.2 Term Test

Offer:

- **All lessons for this term**
- **Choose specific lessons**

Do not label a term test option simply `Full syllabus` if that implies the whole annual syllabus.

The term scope must be backed by the configured curriculum/lesson mapping, not an invented client-only assumption.

## 8.3 Specific lessons

When specific lessons are selected:

- compact lesson checklist;
- selected count;
- Select all;
- Clear;
- teacher-readable lesson numbers/titles;
- no raw IDs.

## 8.4 Grade 5 Scholarship Practice

Do not show a single-subject lesson picker.

After Scholarship is selected, expose a simple teacher-facing Scholarship paper choice such as:

- **Paper I — Ability & Reasoning**
- **Paper II — Curriculum Knowledge**
- **Full Scholarship Practice — Paper I + Paper II**

Use accessible radio semantics.

Behind the scenes:

- Paper I resolves to the versioned Scholarship ability/reasoning profile.
- Paper II resolves to a **multi-scope programme coverage policy**, not one ordinary Grade 5 subject curriculum.
- The current policy must be able to combine reviewed Grade 3 + Grade 4 + eligible Grade 5 term coverage (Terms 1-2 where that is the configured official policy).
- Full Scholarship Practice composes both paper profiles without weakening their independent rules.

The teacher should see a short human explanation such as `Uses the reviewed Grade 5 Scholarship coverage configured for this exam`; the UI does not need to expose internal retrieval scope IDs.

---

# 9. Generate Paper — paper settings

Paper settings must be fully teacher-customizable by **question counts**.

Example:

- MCQ = 5
- Written = 10
- Structured = 0

This is a valid 1st Term Test request.

Support at minimum:

- paper name;
- duration;
- MCQ count;
- written-question count;
- structured-question count if supported;
- difficulty;
- optional teacher instruction.

Do not force presets such as only:

- MCQ only;
- mixed;
- fully written.

Presets may be optional shortcuts later, but the underlying teacher intent must support arbitrary valid composition.

## 9.1 Do not ask marks per question type

Do **not** ask:

- Marks per MCQ
- Marks per written question
- Marks per structured question

A single mark value for an entire question type is not a safe real-world assumption.

The teacher defines question counts.

The system/blueprint may derive or suggest question-specific marks, but marks are confirmed later per generated question.

The deterministic blueprint must adapt to the requested composition without exposing internal blueprint complexity to the teacher.

---

# 10. Review & Approve — Grade 5 pilot

The V1 teacher pilot reviews **Grade 5 papers only**.

Do not spend this delivery building Grade 6-13 review journeys.

Keep the data/domain design extensible so future grades can appear later without a rewrite.

For the Grade 5 pilot, top-level filters should include as useful:

- Paper type / Scholarship paper mode
- Subject where the paper is subject-specific
- Medium
- Status

A Grade filter may remain if already implemented generically, but it must not create fake Grade 6-13 pilot data or distract from Grade 5 acceptance.

Paper queue entries must use teacher-readable titles and scope summaries.

---

# 11. Review & Approve — all questions visible

The teacher must be able to see every question in the selected paper in a navigable list.

Do not provide only:

`Question 4 of 20`

with no full paper question overview.

Recommended desktop information architecture:

- left: paper queue;
- left/below: all questions in selected paper;
- right: selected question detail.

Each question-list item should communicate at least:

- question number;
- question type;
- review state;
- marks-confirmation state where useful.

Selecting a list item loads the existing detailed review experience.

Use a bounded/usable scrolling region or pagination/virtualization if the question count becomes large; do not make the whole page an endless scroll.

---

# 12. Review & Approve — question detail

Keep the useful detailed view.

Teacher-readable detail should include:

- question text;
- options where applicable;
- proposed answer/solution;
- explanation;
- source evidence / page provenance;
- teacher-readable validation checks;
- similarity/duplicate warning where relevant;
- marking editor;
- correction reason;
- reviewer note;
- actions.

Actions:

- Save correction
- Regenerate
- Reject
- Approve

Technical metadata may remain in collapsed/restricted Technical details.

---

# 13. Marks — teacher controlled per question

This is a hard product rule.

Do not present an AI-generated mark as final truth.

Do not show a fixed:

`MARKS: 2`

unless the mark was already human-confirmed under the domain state.

Before teacher confirmation, communicate:

`Marks not confirmed`

or equivalent.

For each generated question, support:

- editable total marks;
- editable marking points;
- marks per marking point;
- add marking point;
- remove marking point;
- structured/sub-part marking where applicable;
- optional `Use AI suggestion`.

Example:

`Total marks [5]`

- Method / working [2]
- Correct conclusion [2]
- Unit / explanation [1]

AI/deterministic logic may suggest a marking structure, but teacher confirmation is authoritative.

Ensure the persisted model cleanly separates:

- generated candidate;
- AI/deterministic mark suggestion;
- human revision;
- approved final marking scheme.

Preserve audit/lineage.

When the teacher edits a question or marking rule, rerun all applicable validation before approval/publishing.

---

# 14. Human correction memory

Preserve the existing correction-learning contract.

Teacher correction should capture structured:

- before;
- after;
- reason;
- reviewer;
- timestamp;
- validation result;
- version lineage.

Useful correction reasons include:

- Wrong answer
- Question incorrect
- Unclear
- Not in syllabus
- Language / grammar
- Difficulty
- Other

Do not delete original AI output.

Human-approved revisions become the trusted generated-question version.

Rejected/raw/unreviewed AI output does not become trusted factual RAG content.

Promote recurring defects into:

- regression tests;
- golden eval cases;
- retrieval/prompt/validator improvements.

---

# 15. Published Papers

Only approved immutable versions appear in Published Papers.

Publishing remains a copy/versioned-publication operation consistent with `docs/SYSTEM_ARCHITECTURE.md`.

Published versions are immutable.

Corrections create a new version.

Do not make the hosted Student side depend on live LLM generation.

---

# 16. Real source corpus — mandatory local runtime validation

A local source corpus exists at:

`/home/sameen/SAMDROIDX-PROJECTS/samdroidx_ai_exam_guru/RAG DATA`

This directory contains syllabus, teacher guides and papers.

Treat this as important real-data input for this implementation loop.


For **teacher-pilot readiness**, inventory whether the local corpus contains sufficient official/reviewable material for the Grade 5 Scholarship programme:

- Grade 3 supporting syllabus/teacher-guide material needed by Paper II;
- Grade 4 supporting syllabus/teacher-guide material needed by Paper II;
- Grade 5 supporting syllabus/teacher-guide material with enough metadata to enforce eligible term coverage;
- Grade 5 Scholarship past Paper I;
- Grade 5 Scholarship past Paper II;
- available official answers/marking/evaluation material;
- any authoritative programme/specification material used to define Paper I abilities or Paper II coverage.

Do not silently substitute synthetic content when an official/reviewable input is missing. Record the missing input as a **teacher-pilot blocker**.

## 16.1 Inspect safely

Do not modify or delete source files.

Create a non-destructive inventory:

- filename;
- file type;
- size;
- SHA-256;
- page count if safely obtainable;
- candidate grade;
- candidate medium;
- candidate subject;
- candidate material type;
- candidate year/paper;
- whether metadata is confirmed or inferred.

Do not pretend inferred metadata is human-confirmed.

Do not commit the source corpus unless it is already tracked or explicit approval exists.

Raw educational source files belong to the local/private Studio storage model.

## 16.2 Use the real application pipeline

Do not directly insert fake trusted chunks into the DB to make a demo pass.

Use the real system flow as much as possible:

1. upload/import through the supported application/service boundary;
2. extraction/native PDF parsing;
3. OCR only when required;
4. review/correction;
5. Ready for AI promotion;
6. semantic chunking;
7. embeddings/indexing;
8. RAG retrieval;
9. paper generation;
10. validation;
11. teacher review;
12. publication where acceptance data permits.

If a bulk local-import helper is genuinely needed, implement it as a bounded first-party operator workflow that goes through the same domain invariants and provenance rules, not a DB bypass.

## 16.3 Real runtime acceptance scenario

Select at least one representative real path supported by the files actually present.

Prefer Grade 5 Sinhala-medium material if available because it is the V1 validation target.

Prove in a clean runtime:

- real PDF appears in Materials with correct teacher-readable metadata;
- View PDF works;
- extracted text is reviewable;
- trust status changes only through valid workflow;
- RAG hard filters keep grade/medium/subject/curriculum scope;
- Generate Paper uses the selected reviewed material;
- retrieved source provenance is visible in question review;
- generated question passes/fails validators honestly;
- teacher can correct the question and marking scheme;
- approval/publishing guards remain enforced.

Do not fabricate a human quality label.

If a step requires human adjudication, identify it as a human gate rather than auto-claiming success.

---

# 17. RAG and subject-quality correctness

Do not weaken the current quality architecture to simplify the UI.

Preserve:

`teacher intent -> deterministic blueprint -> hard-scoped RAG -> generation -> common validators -> subject-aware validators -> semantic evidence -> human review`

For factual questions:

- unsupported is not PASS;
- contradiction is not PASS;
- public web is not the educational source of truth;
- use reviewed local curriculum/source material.

For Maths:

- use deterministic calculation/equivalence validation;
- no RAG-only correctness claim.

For Sinhala and factual subjects:

- preserve script/Unicode checks;
- reviewed terminology/source grounding;
- structured semantic verification;
- teacher final authority.

---

# 18. Data contract and API quality

Do not hardcode teacher labels in React merely to hide broken backend data.

Fix domain/API sources.

Teacher-facing APIs should return:

- stable machine code/ID;
- teacher-readable label;
- valid scope relationships;
- active state;
- only appropriate options for the current context.

Server owns identity and valid combinations.

Frontend owns presentation and interaction.

If OpenAPI changes:

- update backend schemas;
- regenerate TypeScript client using repository workflow;
- verify generated output is reproducible;
- never hand-edit generated API-client files.

---

# 19. TDD and regression strategy

Use RED -> GREEN -> REFACTOR.

Before major fixes, add failing tests that reproduce the real defects.

At minimum add/maintain tests for:

## Materials

- pagination is server-backed;
- search/filter query maps correctly;
- page resets safely on filter change;
- teacher-readable card metadata;
- View PDF action;
- Review extracted text action;
- malformed/synthetic catalogue labels do not enter teacher UI;
- normal Studio runtime does not load test fixture catalogue records.

## Generate target

- V1 runtime exposes only Grade 5 teacher-product choices;
- Grade 5 does not expose O/L/A/L;
- Grade 5 exposes Scholarship;
- Scholarship hides the ordinary Subject selector;
- Scholarship Paper I resolves to a versioned ability/reasoning profile;
- Scholarship Paper II resolves to the configured multi-curriculum coverage policy;
- a client cannot force Scholarship into one arbitrary subject/curriculum scope;
- Term Test shows Subject + Term;
- subject-specific choices remain consistent across options/curricula/lessons/generation.

## Coverage

- radio-card semantics are mutually exclusive;
- Term Test wording and scope behavior;
- Subject Practice wording and scope behavior;
- choose-specific-lessons shows checklist;
- Scholarship does not show subject lesson picker;
- invalid scope cannot generate.

## Paper settings

- arbitrary MCQ/written/structured counts;
- zero allowed for an unused type;
- at least one question required;
- no marks-per-type fields in teacher contract;
- generated blueprint respects requested type counts exactly or fails explicitly if constraints make it impossible.

## Review

- Grade 5 pilot queue is coherent and contains no synthetic future-grade records;
- all questions list visible/navigation works;
- question detail loads correct question;
- unconfirmed marks are not rendered as final;
- per-question total and marking points are editable;
- AI mark suggestion is optional;
- edits trigger revalidation;
- unreviewed/failed question prevents publish.

## Publishing

- immutable approved version only;
- new correction creates new version;
- no live-generation dependency for published student delivery.

Prefer integration/API tests over component mocks where contract correctness matters.

---

# 20. Browser/runtime acceptance

Run browser E2E with Playwright or the repository's supported browser harness against a **clean isolated runtime**.

Do not validate only against a polluted long-lived development database.

Use an isolated Compose project/ports/volumes so tests cannot damage the user's active Studio.

Acceptance must exercise the real backend.

Required browser journeys include:

### Journey A — Materials

- sign in;
- open Materials;
- first screen shows uploaded materials, not 13 giant grade blocks;
- filter/search;
- paginate;
- open a real PDF;
- open Review extracted text;
- no synthetic/internal labels visible.

### Journey B — Grade 5 subject Term Test

- Grade 5;
- Sinhala Medium;
- Term Test;
- Mathematics;
- 1st Term;
- All lessons for this term OR specific lessons;
- MCQ 5;
- Written 10;
- generate;
- resulting paper has the requested question-type composition;
- review queue receives it.

### Journey C — Grade 5 Scholarship Paper I

- Grade 5;
- Sinhala Medium;
- Scholarship Practice;
- ordinary Subject field hidden;
- choose Paper I — Ability & Reasoning;
- no subject lesson picker;
- generation uses the configured Scholarship ability profile;
- generated paper reaches Review & Approve with correct programme/paper metadata.

### Journey D — Grade 5 Scholarship Paper II

- Grade 5;
- Sinhala Medium;
- Scholarship Practice;
- choose Paper II — Curriculum Knowledge;
- ordinary Subject field hidden;
- no single-subject lesson picker;
- server resolves the versioned multi-scope programme coverage;
- retrieval can use reviewed Grade 3, Grade 4 and eligible Grade 5 Terms 1-2 sources according to the configured policy;
- no cross-medium or out-of-policy source leakage;
- provenance in Review identifies the real source/page used.

### Journey E — Full Grade 5 Scholarship Practice

- Grade 5;
- Sinhala Medium;
- Scholarship Practice;
- choose Full Scholarship Practice;
- compose Paper I + Paper II according to versioned programme rules;
- do not flatten both into one generic subject blueprint;
- resulting draft is reviewable end to end.

### Journey F — Review

- use Grade filter;
- select a paper;
- see all questions;
- select a question;
- mark state initially unconfirmed where appropriate;
- edit total/marking points;
- use or ignore AI suggestion;
- save correction;
- rerun checks;
- approve only when valid.

### Journey G — real RAG DATA material

Use a representative real document from the local `RAG DATA` directory through the real ingestion/review/generation path and prove provenance in Review & Approve.

Take screenshots or retain browser artifacts where the repo convention supports them.

---

# 21. Reliability / security / adversarial checks

Preserve or improve:

- auth and permissions;
- PDF signature/type checks;
- size limits;
- filename/path safety;
- duplicate SHA handling;
- prompt-injection treatment for retrieved source documents;
- SQL/API validation;
- rate/cost controls;
- idempotency;
- safe retry;
- immutable audit history;
- publication guards.

Add adversarial tests for:

- stale target change during lesson load;
- duplicate active curricula;
- malformed teacher-readable labels;
- fixture data accidentally queried by runtime catalogue;
- zero-question paper;
- impossible blueprint composition;
- marking points not summing to explicit total;
- negative/excessive marks;
- client attempting Grade 5 + A/L/O/L even if UI hides it;
- client attempting Scholarship + arbitrary ordinary Subject/curriculum;
- Paper II accidentally retrieving Grade 5-only when the programme policy requires multiple curriculum scopes;
- Paper II retrieving Grade 3/4/5 content outside the configured programme coverage;
- Paper I accidentally using ordinary subject-syllabus retrieval as its ability model;
- cross-medium and out-of-policy RAG leakage.

Server must reject invalid combinations.

---

# 22. Performance and usability

Avoid replacing one problem with another.

Materials:

- bounded page size;
- indexed/sane query plan;
- no N+1 metadata lookup;
- responsive interactions.

Review:

- do not render thousands of heavy question details at once;
- list is lightweight;
- detail loads selected question;
- use bounded scrolling/pagination/virtualization if necessary.

Teacher UI:

- keyboard accessible;
- real radio semantics;
- labels associated with controls;
- visible focus states;
- no raw browser-default malformed control layout;
- responsive desktop/tablet behavior;
- clear empty/loading/error states.

---

# 23. Existing engineering quality gates

Do not reduce existing thresholds to get green.

Run all repository-required gates applicable to changed code.

At minimum, where configured:

- backend unit/integration tests;
- backend 100% statement/branch coverage gate;
- Ruff;
- formatting;
- mypy;
- frontend unit tests;
- frontend configured coverage gate;
- ESLint;
- TypeScript checks;
- production build;
- OpenAPI reproducibility;
- generated client reproducibility;
- npm audit/security checks;
- secret scan;
- database migrations;
- PostgreSQL backup/restore drill if schema/migration is touched;
- isolated Compose runtime health;
- Playwright/browser acceptance;
- Graphify/graph integrity update if required by repository workflow.

Do not lower coverage or remove assertions.

If a previously green gate fails, reproduce and fix the root cause.

---

# 24. External credentials and truthful limitations

If live OpenAI/provider credentials are configured, use the repository's safe provider path for the runtime generation/eval scenario and record model/prompt/version/cost/latency as already designed.

If required external credentials are absent:

- do not invent successful live-provider evidence;
- complete all deterministic/local/UI/runtime work possible;
- keep contract tests green with the approved deterministic test adapter;
- report the exact credential-dependent acceptance step as blocked.

Do not fabricate human-adjudicated correctness.

---

# 25. Documentation and tracker truth

Update documentation only when behavior is actually implemented and verified.

Keep:

- `ui-reference/teacher-studio-v1/README.md`
- system architecture;
- V1 tracker;
- known limitations;
- subject-quality contract

truthful.

If the production implementation intentionally differs from the reference because of evidence or stronger UX, document the reason instead of silently drifting.

---

# 26. Commit and release discipline

After all applicable local gates pass:

1. inspect the full diff;
2. remove accidental generated/temp/runtime artifacts;
3. verify no local RAG source PDFs or secrets are being committed unintentionally;
4. run secret scan;
5. commit cohesive changes;
6. push to `master`;
7. wait for and verify remote CI;
8. if CI fails, reproduce/fix/push again;
9. confirm local HEAD == origin/master;
10. confirm working tree is clean except explicitly preserved pre-existing unrelated work.

Do not report DONE before remote CI is green.

Before the teacher handoff, evaluate every gate in `docs/v1/07_GRADE5_TEACHER_PILOT_READINESS.md` and return exactly one pilot verdict:

- READY FOR TEACHER PILOT
- NOT READY — ENGINEERING BLOCKER
- NOT READY — SOURCE DATA BLOCKER
- NOT READY — HUMAN ADJUDICATION BLOCKER

Do not use `architecture ready` as a teacher-pilot verdict.

---

# 27. Final report format

Return a concise but evidence-rich release report:

1. **Overall verdict**
   - PASS / PASS WITH EXTERNAL OR HUMAN BLOCKERS / FAIL

2. **Teacher UX delivered**
   - navigation
   - Materials
   - Generate target
   - Coverage
   - Paper settings
   - Review
   - Marks
   - Publishing

3. **Root causes fixed**
   - especially synthetic catalogue/test data pollution and invalid grade/paper-type combinations

4. **Real RAG DATA validation**
   - files inspected
   - representative document(s)
   - actual ingestion/extraction/review/RAG/generation evidence
   - anything requiring human adjudication

5. **Tests**
   - exact backend/frontend/browser counts
   - coverage
   - migration/restore if applicable
   - security/static gates

6. **Runtime evidence**
   - isolated environment
   - browser journeys
   - screenshots/artifacts if retained

7. **Commit**
   - SHA
   - remote CI run/result

8. **Teacher pilot verdict**
   - use the exact readiness verdict from `07_GRADE5_TEACHER_PILOT_READINESS.md`
   - list any failed gate by name

9. **Teacher test handoff**
   - if and only if READY FOR TEACHER PILOT, provide the exact teacher tasks to run
   - do not ask teachers to test engineering internals

10. **Remaining blockers**
   - external credentials
   - human-reviewed OCR/eval labels
   - real subject-teacher adjudication
   - anything else factual

Do not say the system is fully correct merely because automated tests are green.

---

## Final acceptance definition

This task is complete only when a normal teacher can use the **Grade 5 pilot** as a coherent product:

`Upload/review real Scholarship-supporting sources -> Ready for AI -> generate Grade 5 Scholarship Paper I / Paper II / full practice according to programme rules -> inspect every generated question -> edit question-specific marking -> approve -> publish immutable version`

with:

- no synthetic/internal labels in normal teacher UI;
- no non-Grade-5 future product clutter in the V1 teacher workflow;
- no invalid Grade 5 / Paper Type / Scholarship paper-mode combinations;
- no endless Materials scrolling;
- no technical Blueprint/RAG complexity forced on the teacher;
- real scoped RAG evidence;
- full runtime/browser validation;
- existing quality/security gates preserved.

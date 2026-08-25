# GPT-5.6 Sol — Teacher-First Multi-Grade Content Studio Redesign

Use this as a steering prompt for the current V1 engineering session. Do not restart from scratch.

---

Continue engineering `sameenhewage/samdroidx_ai_exam_guru` from the current repository state.

A major product correction is now part of the V1 contract: the normal Admin/Content Studio is used by **teachers and other non-technical education staff**, not software engineers. The current generation/admin experience exposes too many internal implementation details and is not acceptable as the primary product workflow.

## Read first
Before changing code, read in full:
- `AGENTS.md`
- `apps/web/AGENTS.md`
- `apps/api/AGENTS.md`
- `docs/v1/05_TEACHER_FIRST_MULTI_GRADE_CONTENT_STUDIO.md`
- `.agents/skills/teacher-content-studio-ux/SKILL.md`
- all currently relevant domain skills required by root `AGENTS.md`.

Inspect the current browser UI and code, especially Materials/Documents, Curriculum, Blueprint, Generation, Validation, Review, Papers, Retrieval, Analytics and Operations. Do not assume prior green tests mean the product UX is acceptable.

## Core product correction
The existing advanced technical studios may remain for engineering/debugging, but they are **not the normal teacher workflow**.

Build a simple task-oriented operator experience centered on:
1. Home
2. Materials
3. Generate Papers
4. Review & Approve
5. Published Papers

Use progressive disclosure. Internal IDs, hashes, context IDs, raw JSON, retry lineage, model/prompt/retrieval/schema versions, token accounting and queue internals must not dominate ordinary teacher screens.

## Revisit the domain model now, before RAG/content volume grows
V1 rollout remains Grade 5 Scholarship first, but the reusable architecture must support Sri Lankan Grades 1–13.

Generalize reusable persistence/services safely so the system can model:
`Grade -> Medium -> Subject -> Curriculum Version -> Unit/Module -> Lesson/Topic`

Keep the existing competency -> skill -> sub-skill -> learning-concept taxonomy and allow curriculum lessons/topics to map to those nodes. Do not force both hierarchies to be identical.

Support exam/assessment programmes associated with grades:
- Grade 5 Scholarship;
- O/L / Grade 11;
- A/L / Grade 13;
- normal school-grade papers for Grades 1–13.

Do not expand V1 content collection to all grades now; generalize the model/workflow and keep real quality validation focused on Grade 5.

Migrate existing Grade 5 data forward without losing IDs, provenance or audit history. Add cross-grade/cross-subject isolation tests.

## Materials Library — mandatory normal workflow
A teacher must be able to open Materials and immediately see what already exists.

Required hierarchy/navigation:
- Grades 1–13 overview with material/subject/status counts;
- select Grade;
- select/filter Subject;
- see uploaded Syllabus, Teacher Guide, Past Paper, Marking Scheme/Answers and other approved materials.

Each list row/card must show readable fields such as:
- filename/title;
- grade;
- subject;
- medium;
- material type;
- year/version where applicable;
- page count;
- uploaded date;
- simple status: Processing / Needs review / Ready for AI / Removed.

Teacher actions:
- View;
- Review extracted text;
- Edit metadata;
- Remove from use / restore where allowed;
- Replace/version where appropriate;
- Advanced technical details only on demand.

### Duplicate protection
The system must stop users repeatedly uploading the same PDF:
- content SHA/hash detection;
- likely duplicate metadata/title/year checks;
- show the existing uploaded item before creating another active ingestion;
- deterministic idempotency tests.

### Wrong-grade recovery
Required scenario:
A Grade 11 paper was uploaded into Grade 5. Teacher must be able to identify it, correct/remove it, and guarantee it is excluded from Grade 5 RAG/generation while audit/provenance is preserved.

## Upload UX
Create a guided workflow:
`Grade -> Medium -> Subject -> Material type -> Year/Curriculum version if relevant -> choose PDF(s) -> review metadata -> upload`

Do not expose chunk/vector/embedding actions to the teacher. Background processing happens automatically after upload/trust rules.

## Extraction/OCR review UX
Build/reshape the review screen for a teacher:
- original PDF/page on the left;
- extracted/OCR text on the right;
- page navigation;
- obvious editable corrections;
- Save correction;
- Mark reviewed / Ready for AI.

Low-confidence areas may be highlighted. Bounding-box JSON, block IDs and OCR provider internals belong under Technical details only.

This screen can create human-adjudicated ground truth when a reviewer corrects OCR against the original page.

## RAG implications
Trusted active material only enters active retrieval.

Every chunk/embedding/retrieval record must support hard scope filters for at least:
- grade;
- medium;
- subject;
- curriculum version;
- material type;
- unit/module;
- lesson/topic;
- competency/skill mapping;
- year/paper where relevant.

Removing a source from use must exclude its chunks/vectors from active retrieval without destroying audit history.

Do not introduce LangChain merely for this redesign; keep first-party explicit RAG architecture.

## Generate Papers — mandatory teacher flow
The teacher must be able to generate without manually navigating Blueprint/Retrieval/Generation internals.

Normal wizard:

### 1. Target
- Grade
- Medium
- Subject
- optional exam/template (Scholarship/O/L/A/L/school practice as available)

### 2. Curriculum scope
Support:
- Full syllabus / all available content
- selected units/modules
- selected lessons/topics
- lesson ranges such as **Grade 7 -> Maths -> Lessons 1–3 only**
- optional competency/skill selection as an advanced education control

Required acceptance scenario:
`Grade 7 -> Maths -> Lessons 1–3 -> Generate paper`

Use deterministic fixtures if real Grade 7 content is unavailable; do not claim real educational quality from fixtures.

Also support:
`Grade 7 -> Maths -> Full syllabus/all available modules -> Generate paper`.

### 3. Teacher-understandable paper settings
Examples:
- paper type/template
- number of questions or total marks
- duration
- difficulty preset: Balanced / Easier / Challenging
- question-format mix where meaningful

Do not require raw blueprint slot IDs or manual RAG context selection.

### 4. Generate
Behind the scenes automatically execute:
`teacher intent -> deterministic blueprint -> scoped RAG -> LLM -> answer/solution -> marking scheme -> validators -> duplicate checks -> Review & Approve`

Show teacher-friendly progress:
`Preparing paper -> Generating questions -> Checking answers -> Ready for review`.

## Review & Approve — mandatory dedicated window
A generated paper must be reviewable together with answers.

For each question show:
- question
- options
- proposed correct answer
- explanation/solution
- marks/marking scheme
- readable grade/subject/unit/lesson/skill scope
- readable sources used
- simple validation status
- duplicate/similarity warning if any

Actions:
- Approve
- Edit
- Reject
- Regenerate this question
- Previous / Next

Validation language:
- Ready
- Needs attention
- Failed check

Detailed findings, provider/model/RAG metadata are available under `Why?` / `Technical details` but are not required to review the paper.

## Generated / Published paper library
Provide a searchable teacher-facing list showing:
- grade
- subject
- scope summary (e.g. Lessons 1–3, Full syllabus)
- template/type
- created date
- review status
- version
- View / Continue review / Publish / Archive actions as permitted.

Do not make generation-run UUIDs the normal retrieval mechanism.

## Existing advanced tooling
Do not throw away useful engineering diagnostics. Reframe them as advanced/system tools:
- retrieval explorer
- blueprint debugger
- generation run diagnostics
- validation internals
- model/eval/cost views
- queue/operations.

The normal teacher flow should orchestrate those systems automatically.

## TDD / loop discipline
This is not a mockup-only task.

Use:
`inspect -> define teacher behavior -> RED unit/integration/E2E -> domain migration/API -> GREEN -> refactor -> browser validation -> adversarial usability review -> regression fixes -> broad gate -> tracker evidence -> commit/push -> continue`.

Add browser E2E for at least:
1. Grades 1–13 Materials overview;
2. Grade 5 uploaded material list;
3. duplicate upload prevention;
4. wrong-grade remove/correct and retrieval exclusion;
5. original-vs-extracted OCR correction;
6. Grade 7 Maths Lessons 1–3 scoped generation with fixtures;
7. Grade 7 full-subject generation with fixtures;
8. generated question + answer/marking review;
9. technical diagnostics hidden by default but accessible in advanced view.

Do not mark the relevant product acceptance complete merely because the old specialist studios still work.

## Priority and continuation
Do not stop the overall engineering program after this redesign. Integrate it into the current Priority 1 work, then continue P2–P10 and automatically P11–P15 according to loop-engineering rules.

The redesign should happen **before significant P3/P4 production knowledge ingestion/embedding scale** where schema changes would cause avoidable migration/re-embedding work.

At the end of the execution session report:
- domain/schema generalization performed;
- teacher-facing flows changed;
- old advanced tools retained/moved;
- E2E scenarios and results;
- current tracker status;
- migrations/compatibility notes;
- commits pushed;
- next non-blocked V1 work.

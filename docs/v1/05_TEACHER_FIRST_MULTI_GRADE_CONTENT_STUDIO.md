# Teacher-First Multi-Grade Content Studio — Product Contract

## Status and precedence
This document is a V1 product contract. It is additive to `00_V1_MASTER_PLAN.md` and `02_PRIORITY_1_ADMIN_RAG_LLM_SPEC.md` and **overrides any UI assumption that exposes engineering internals as the primary operator experience**.

V1 commercial rollout remains Grade 5 Scholarship / Sinhala-medium first, but the content model and operator workflows must not hard-code Grade 5 in a way that requires a rewrite to support Grades 1–13.

## 1. Primary users
The main content operators are **non-technical users**:
- teachers;
- subject-matter reviewers;
- curriculum/content staff;
- education administrators.

They are not expected to understand:
- vector databases;
- embeddings;
- chunk IDs;
- RAG context IDs;
- request fingerprints;
- provider idempotency keys;
- prompt/model/retrieval/schema/pricing versions;
- retry lineage;
- internal job IDs;
- raw JSON snapshots.

Those details may exist for observability/audit/debugging, but must be hidden behind an **Advanced / Technical details** disclosure or a separate technical-operations area.

## 2. Product language
Primary UI language must describe user goals, not implementation mechanics.

Prefer:
- `Materials` instead of `source-document ingestion`;
- `Ready for AI` instead of `embedded/indexed`;
- `Needs review` instead of internal extraction state names;
- `Review text` instead of `adjudicate OCR blocks`;
- `Generate paper` instead of `create generation run`;
- `Sources used` instead of `persisted context snapshot`;
- `Try again` instead of `retry provider lineage`;
- `Remove from use` instead of `deactivate retrieval corpus record`.

Technical identifiers are never the main label a teacher must reason about.

## 3. Information architecture
The normal content-operator navigation should be small and task-oriented:

1. **Home**
2. **Materials**
3. **Generate Papers**
4. **Review & Approve**
5. **Published Papers**

Secondary/advanced areas may contain:
- curriculum/taxonomy administration;
- retrieval diagnostics;
- model/eval diagnostics;
- queue/worker operations;
- cost and latency details;
- audit logs.

Do not require a teacher to navigate separate Blueprint, Retrieval, Generation, Validation, Knowledge and Operations studios just to create one paper. Those can remain implementation/advanced tools, but the main workflow must orchestrate them behind a simple product flow.

## 4. Multi-grade education model
The platform must support **Grades 1 through 13** as first-class configuration, while Grade 5 remains the first validated content rollout.

Conceptually support:

`Grade -> Medium -> Subject -> Curriculum Version -> Unit/Module -> Lesson/Topic`

alongside the educational taxonomy:

`Competency -> Skill -> Sub-skill -> Learning Concept`

A lesson/topic may map to one or more competency/skill nodes. Do not force lesson hierarchy and competency hierarchy to be the same structure.

### National-exam programmes
Represent national exams as assessment/exam programmes associated with grades rather than as unrelated hard-coded applications:
- Grade 5 Scholarship — Grade 5;
- GCE O/L — normally Grade 11;
- GCE A/L — normally Grade 13.

The model must also support normal school-grade paper generation for Grades 1–13 independent of a national exam programme.

Future stream/programme metadata (for example A/L streams) should be extensible without changing the core document/RAG schema.

## 5. Materials Library
The operator must be able to answer immediately:
- Which grades have materials?
- Which subjects exist for each grade?
- Which syllabus/teacher guides/past papers are uploaded?
- Which files are ready for AI use?
- Which files need review?
- Which file is wrong, duplicated, or assigned to the wrong grade/subject?

### Grade overview
Show Grades 1–13 as cards/list rows with useful counts, for example:

`Grade 5 — 23 materials — 4 subjects — 20 Ready — 3 Need review`

Special badges may show `Scholarship`, `O/L`, or `A/L` where applicable.

Selecting a grade opens subject/material coverage, not technical ingestion internals.

### Material categories
At minimum:
- Syllabus;
- Teacher Guide;
- Past Paper;
- Marking Scheme / Answers;
- Evaluation / Examiner Report;
- Other approved curriculum material.

### Material list
For each uploaded item show human-meaningful fields:
- filename/title;
- grade;
- subject;
- medium;
- material type;
- year/exam year when applicable;
- curriculum version when applicable;
- page count;
- uploaded date;
- simple status (`Processing`, `Needs review`, `Ready for AI`, `Removed`);
- actions.

Actions should include as appropriate:
- View;
- Review extracted text;
- Edit metadata;
- Remove from use / restore;
- Replace with a newer version where domain rules allow;
- View technical details (advanced only).

### Duplicate prevention
Before accepting an upload:
- calculate and compare content hash;
- detect exact duplicate files;
- detect likely duplicate metadata/title/year entries;
- show the existing item to the operator;
- prevent accidental repeated ingestion/indexing unless an explicit, valid versioning workflow is chosen.

The operator should never need to remember whether a PDF was already uploaded.

### Wrong-grade / wrong-subject correction
If a Grade 11 paper is accidentally uploaded as Grade 5, the operator must be able to correct it safely.

Where trusted downstream data already exists, prefer an auditable `Remove from use` / archive + corrected reclassification/version workflow over destructive deletion that breaks provenance. The item must be excluded from active RAG retrieval after removal.

The system may use extraction/classification signals to warn about likely grade/subject mismatch, but a model guess is not authoritative. Human review decides.

## 6. Upload workflow
Use a short guided flow instead of a technical form.

Suggested steps:
1. Choose Grade (1–13)
2. Choose Medium
3. Choose Subject
4. Choose Material Type
5. Add Year / Curriculum Version only when relevant
6. Select PDF(s)
7. Review detected metadata and upload

Support multi-file upload when practical.

After upload, show progress using teacher-friendly states:
`Uploading -> Reading document -> Needs review / Ready for AI`

Do not expose chunking/vectorization as required operator actions. They are background implementation steps after trust/review rules pass.

## 7. Extraction / OCR review
The review screen should be simple enough for a teacher.

Preferred layout:
- left: original PDF/page image;
- right: extracted text/structured question content;
- obvious page navigation;
- highlighted low-confidence/problem areas where useful;
- editable corrected text;
- `Save correction`;
- `Mark reviewed / Ready for AI`.

The operator should not need to edit bounding-box JSON, block IDs, extractor versions, or internal state-machine values.

Technical provenance remains persisted and auditable behind the scenes.

## 8. RAG indexing from Materials
Only reviewed/trusted active content may enter the active knowledge retrieval corpus.

Every chunk/index record must retain scope metadata sufficient for future filtering:
- grade;
- medium;
- subject;
- curriculum version;
- material type;
- unit/module when known;
- lesson/topic when known;
- competency/skill mappings when known;
- year/paper for historical exam content;
- source/page provenance.

Removing a material from use must remove/exclude its chunks/embeddings from active retrieval without destroying audit history.

## 9. Paper generation — teacher workflow
A teacher should be able to create a paper without seeing blueprint/RAG/model internals.

### Step 1 — Select target
- Grade;
- Medium;
- Subject;
- optional assessment programme/template (Grade 5 Scholarship, O/L, A/L, school practice/term paper).

### Step 2 — Select curriculum scope
Support at minimum:
- Full syllabus / all available content;
- selected units/modules;
- selected lessons/topics;
- an inclusive lesson range such as **Maths Lessons 1–3 only**;
- selected competencies/skills as an advanced educational option.

Example required workflow:
`Grade 7 -> Maths -> Lessons 1, 2, 3 -> Generate paper`

Another required workflow:
`Grade 7 -> all available Maths modules -> Generate paper`

The backend should translate this simple scope into deterministic blueprint constraints and hard RAG metadata filters.

### Step 3 — Paper settings
Provide sensible templates/defaults. Expose only settings teachers understand, for example:
- paper type/template;
- number of questions or total marks;
- duration;
- difficulty mix (simple presets such as Balanced / Easier / Challenging);
- question-format mix where relevant.

Advanced blueprint controls must be optional, not required.

### Step 4 — Generate
Behind the scenes:
- deterministic blueprint;
- scoped RAG retrieval;
- LLM generation;
- answer/solution generation;
- marking scheme generation;
- automated validation;
- duplicate checks.

The user sees progress such as:
`Preparing paper -> Generating questions -> Checking answers -> Ready for review`

## 10. Paper Review & Validation window
Generated papers must have a dedicated, teacher-friendly review area.

For each question show together:
- question;
- options where applicable;
- proposed correct answer;
- explanation/solution;
- marks/marking scheme;
- simple validation summary;
- curriculum scope (grade/subject/unit/lesson/skill);
- source references in a readable form;
- duplicate/similarity warning if relevant.

Primary actions:
- Approve;
- Edit;
- Reject;
- Regenerate this question;
- Next / Previous;
- Approve remaining valid questions where policy permits explicit batch review.

Use friendly validation states:
- `Ready`;
- `Needs attention`;
- `Failed check`.

Detailed validator findings and model metadata can be expanded under `Why?` / `Technical details`.

A teacher must be able to review the **paper and its answers together** before publication/export.

## 11. Generated paper library
Operators need a simple list of generated papers with:
- grade;
- subject;
- scope summary (`Lessons 1–3`, `Full syllabus`, etc.);
- paper template/type;
- created date;
- review status;
- version;
- actions: View, Continue review, Duplicate as new draft, Publish/Archive when allowed.

Do not make generation-run IDs the main way papers are found.

## 12. Progressive disclosure for engineering diagnostics
Existing technical data remains valuable but belongs in advanced views.

Hide by default:
- internal UUIDs;
- request fingerprints;
- idempotency keys;
- retry depths/lineage;
- raw JSON blueprint snapshots;
- raw context IDs;
- token accounting details;
- provider/model/prompt/retrieval/schema/pricing versions.

Expose them only via:
- `Technical details` expansion;
- advanced admin/operator route;
- audit/debug screen.

Teacher workflows must never require those fields to complete ordinary work.

## 13. Architecture implications — do before scaling RAG data
Before P3/P4 production content grows, remove Grade-5-only assumptions from reusable domain/storage boundaries where they would force later migrations or re-embedding.

Required direction:
- grade is configurable 1–13, not a database invariant equal to 5;
- subject is first-class;
- curriculum unit/module/lesson/topic hierarchy is first-class or equivalently queryable;
- documents/chunks/historical questions/blueprints/generation requests carry grade + subject + curriculum scope;
- RAG hard filters enforce those scopes;
- existing Grade 5 data is migrated without losing provenance/audit history;
- V1 test fixtures still prioritize Grade 5 Scholarship quality.

Do not prematurely build actual Grade 1–13 content packs. Build the **general model and teacher workflow**, then validate the product deeply with Grade 5 first.

## 14. Acceptance scenarios
The content studio is not accepted until browser E2E proves representative non-technical workflows.

### A. Materials inventory
Teacher opens Materials -> sees Grades 1–13 -> opens Grade 5 -> sees uploaded syllabus/teacher guides/past papers with names and statuses -> confirms the same file cannot be accidentally uploaded again.

### B. Wrong material recovery
Teacher identifies a Grade 11 paper incorrectly assigned to Grade 5 -> removes/corrects it -> verifies it no longer participates in Grade 5 retrieval/generation while audit history remains intact.

### C. Scoped Grade 7 generation
Teacher selects Grade 7 -> Maths -> Lessons 1–3 -> generates a paper -> generated questions are constrained to that scope -> answers/marking scheme are produced -> paper enters Review & Approve.

Synthetic/trusted fixtures may prove this architecture before real Grade 7 content exists; do not claim educational quality without real reviewed content.

### D. Full-grade generation
Teacher selects Grade 7 -> Maths -> Full syllabus/all available modules -> generates a paper using only trusted Grade 7 Maths knowledge.

### E. Review
Teacher reviews questions and answers in one dedicated screen, sees clear validation status, edits/regenerates/rejects as needed, and approves only valid content.

### F. Grade 5 first rollout
Using real reviewed Grade 5 material, complete the full flow from Materials -> trusted knowledge -> scoped generation -> answer/marking validation -> human review -> published paper.

## 15. UX acceptance principle
A screen is not complete merely because all backend state is visible.

For every normal operator flow ask:
> Can a teacher who does not know software engineering, RAG, embeddings, queues, or LLM APIs complete this task confidently without assistance?

If the answer is no, the product UX is incomplete even when technical tests are green.

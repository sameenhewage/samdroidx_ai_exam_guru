---
name: teacher-content-studio-ux
description: Mandatory product/UX skill for AI Exam Guru content-operator/admin flows used by teachers and non-technical education staff. Use for Materials, document upload/review, generation, validation/review, paper libraries, curriculum selection, and any admin navigation or terminology that exposes RAG/LLM internals.
---

# Teacher Content Studio UX

## Goal
Design every normal content workflow for teachers and non-technical education staff, while keeping technical diagnostics available only through progressive disclosure or advanced routes.

Read `docs/v1/05_TEACHER_FIRST_MULTI_GRADE_CONTENT_STUDIO.md` before changing relevant UI/domain behavior.

Always pair this skill with:
- `loop-engineering`;
- `tdd-eval-engineering`;
- `nextjs-product-engineering` for web work;
- `fastapi-domain-engineering` when domain/API changes are required.

Add `document-ingestion-ocr`, `rag-retrieval-evaluation`, `llm-question-generation-validation`, or `security-reliability-review` whenever those concerns are touched.

## Core rule
Do not mistake observability for UX.

The database/API may retain UUIDs, hashes, retry lineage, provider versions, vector metadata, prompt versions and raw diagnostic JSON. The normal teacher experience should not require understanding them.

## Teacher-first navigation
Prefer a task-oriented primary IA:
1. Home
2. Materials
3. Generate Papers
4. Review & Approve
5. Published Papers

Put technical studios/diagnostics behind Advanced/System tools where they remain useful for engineers/operators.

## Materials requirements
A teacher must be able to:
- see Grades 1–13;
- see material counts/status by grade and subject;
- browse uploaded filenames/titles;
- distinguish Syllabus, Teacher Guide, Past Paper, Marking Scheme and other approved material;
- see Grade, Medium, Subject, Year/Version and status;
- detect duplicate uploads before re-ingestion;
- view a material;
- edit metadata safely;
- review/correct extracted text;
- remove a wrong document from active AI/RAG use without destroying audit history;
- restore where policy allows.

Primary status language should be `Processing`, `Needs review`, `Ready for AI`, `Removed` or similarly understandable wording.

## Upload requirements
Use a short wizard:
`Grade -> Medium -> Subject -> Material type -> Year/Curriculum version when needed -> PDF -> Review metadata -> Upload`.

Do not ask the teacher to manually initiate chunking, embeddings or vectorization.

## Multi-grade domain requirements
Reusable model must support Grades 1–13 even though Grade 5 is the first content rollout.

Support:
`Grade -> Medium -> Subject -> Curriculum Version -> Unit/Module -> Lesson/Topic`

and map lessons/topics to competency/skill taxonomy independently.

National programmes are associated with grades:
- Grade 5 Scholarship;
- O/L / Grade 11;
- A/L / Grade 13.

Do not encode `grade == 5` as a reusable persistence/RAG invariant.

## Generation requirements
A teacher should select:
- grade;
- medium;
- subject;
- optional exam/template;
- curriculum scope: full syllabus, selected modules, selected lessons, or lesson range (e.g. Maths Lessons 1–3);
- simple paper settings.

The system translates this into deterministic blueprint and RAG constraints behind the scenes.

The normal flow must not require choosing context IDs, blueprint slot IDs, raw taxonomy IDs, provider settings, model versions or retrieval records manually.

## Review requirements
Provide a dedicated paper/question review experience showing together:
- question;
- options;
- proposed answer;
- explanation/solution;
- marks/marking scheme;
- readable curriculum scope;
- readable source references;
- simple validation status;
- duplicate warning where relevant.

Primary actions: Approve, Edit, Reject, Regenerate question, Next/Previous.

Technical validation/model/RAG details go under `Why?` or `Technical details`.

## Progressive disclosure
Hide by default from normal teacher tasks:
- run/request IDs;
- UUIDs used only for persistence;
- request fingerprints;
- idempotency keys;
- retry lineage;
- raw blueprint/context JSON;
- prompt/provider/model/retrieval/schema/pricing versions;
- token/cost accounting.

Do not delete these capabilities; move them to Advanced/Technical details.

## TDD/E2E expectations
Every teacher workflow change requires behavior-first tests and browser E2E for meaningful journeys.

Required representative E2E scenarios:
- Materials grade overview and uploaded-file list;
- duplicate upload warning/prevention;
- wrong-grade material removal/correction and exclusion from retrieval;
- OCR/extraction correction in a simple original-vs-text review;
- Grade 7 Maths Lessons 1–3 generation with deterministic fixtures;
- full-grade/subject generation;
- generated paper + answer/marking review;
- advanced technical details hidden by default.

## UX review heuristic
Before closing a slice, perform a non-technical-operator review:
- Can a teacher understand the primary action in under a few seconds?
- Are implementation terms avoided?
- Can they recover from mistakes?
- Can they see what materials already exist?
- Can they tell what data the AI is allowed to use?
- Can they generate by curriculum scope without knowing RAG/blueprints?
- Can they review answers before approving the paper?

If not, treat it as a product defect and add a regression/E2E case before fixing it.

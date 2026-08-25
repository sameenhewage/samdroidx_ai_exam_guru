# API / Domain Agent Instructions

These instructions apply to `apps/api/**` in addition to the root `AGENTS.md`.

## Multi-grade architecture is mandatory
Before changing curriculum, document, knowledge, RAG, blueprint, generation, validation or publishing domain behavior, read:
- `docs/v1/05_TEACHER_FIRST_MULTI_GRADE_CONTENT_STUDIO.md`
- `.agents/skills/fastapi-domain-engineering/SKILL.md`
- `.agents/skills/tdd-eval-engineering/SKILL.md`

Load RAG/document/LLM/security skills as relevant.

## V1 rollout versus reusable model
Grade 5 Scholarship / Sinhala medium remains the first deeply validated content rollout. However, reusable persistence and service boundaries must support Grades 1–13 without a future rewrite.

Do not preserve a reusable invariant equivalent to `grade == 5` where it would prevent later Grade 1–13 materials, retrieval or generation.

Support conceptually:
`Grade -> Medium -> Subject -> Curriculum Version -> Unit/Module -> Lesson/Topic`

The existing competency/skill/sub-skill/learning-concept taxonomy remains useful and should map to lesson/topic scope rather than replacing it.

## Assessment programmes
Allow assessment/exam programmes to be associated with grades, including future support for:
- Grade 5 Scholarship;
- O/L / Grade 11;
- A/L / Grade 13;
- normal school-grade practice/term papers.

Avoid one-off schemas that require separate pipelines for each programme.

## Document and RAG scope
Documents, trusted blocks/chunks, historical questions and retrieval queries must be scopeable by:
- grade;
- medium;
- subject;
- curriculum version;
- material type;
- unit/module where known;
- lesson/topic where known;
- competency/skill mapping where known;
- historical year/paper metadata where applicable.

Hard RAG filters must enforce the selected scope before semantic ranking.

If an operator removes a wrong material from use, it must be excluded from active retrieval/indexing without destroying immutable provenance/audit history.

## Duplicate and wrong-assignment safety
Uploads require content-hash deduplication plus meaningful duplicate metadata checks. Repeated uploads should not create duplicate active knowledge.

Support safe metadata correction/removal workflows for wrong grade/subject assignment. If trusted downstream artifacts already exist, use auditable archive/remove-from-use and corrected reclassification/versioning rather than destructive mutation that breaks provenance.

## Teacher-facing generation API
The normal product API should accept a teacher-understandable generation intent such as:
- grade;
- medium;
- subject;
- assessment/template;
- full syllabus or selected unit/module/lesson scope;
- inclusive lesson ranges (for example lessons 1–3);
- simple paper settings.

The server translates that intent into deterministic blueprint constraints, hard retrieval filters and generation jobs. Do not require the normal client to manually choose raw context IDs, generation configuration versions or internal blueprint slot identifiers.

Keep advanced/debug APIs if useful, but separate them from the normal product contract.

## Review contract
Generated candidate APIs must make question, answer/solution, marking scheme, scope, readable provenance and validation findings available together for teacher review. Technical generation metadata remains available for audit/debugging but should not be required for approval decisions.

## Migration discipline
Generalize existing Grade 5 data with forward migrations that preserve IDs/provenance/audit history. Do not reset the database merely to simplify the new generalized model.

Add regression/integration tests proving cross-grade and cross-subject isolation before P3/P4 content volume grows.

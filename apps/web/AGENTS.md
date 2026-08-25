# Web Application Agent Instructions

These instructions apply to `apps/web/**` in addition to the root `AGENTS.md`.

## Mandatory teacher-first UX contract
Before changing any admin/content-operator UI, read and apply:
- `docs/v1/05_TEACHER_FIRST_MULTI_GRADE_CONTENT_STUDIO.md`
- `.agents/skills/teacher-content-studio-ux/SKILL.md`
- `.agents/skills/nextjs-product-engineering/SKILL.md`
- `.agents/skills/tdd-eval-engineering/SKILL.md`

The primary operator is a teacher/content reviewer, not a software engineer.

## Product rule
Normal teacher workflows must be goal-oriented and progressively disclose technical details.

Do not require ordinary users to understand or choose:
- generation run IDs;
- request fingerprints;
- idempotency/retry lineage;
- prompt/provider/model/retrieval/schema/pricing versions;
- raw context IDs;
- raw JSON blueprint/context snapshots;
- vector/embedding terminology;
- queue/worker implementation states.

Keep these available only in Advanced / Technical details / system-operations views when operationally useful.

## Primary content navigation direction
Prefer:
- Home
- Materials
- Generate Papers
- Review & Approve
- Published Papers

Existing specialist routes such as Retrieval, Blueprint, Generation, Validation, Analytics and Operations may remain as advanced/internal tooling, but a teacher must not need to traverse them manually to complete the normal paper-generation workflow.

## Materials UX is mandatory
Provide a simple Grades 1–13 inventory and per-grade/per-subject material library. The user must be able to see what was already uploaded, avoid duplicate uploads, correct/remove wrong-grade material, and understand whether each item is Processing, Needs review, Ready for AI, or Removed.

## Generation UX is mandatory
The normal generation flow must support:
- grade;
- medium;
- subject;
- optional national exam/template;
- full syllabus or selected unit/module/lesson scope;
- lesson ranges such as Grade 7 Maths Lessons 1–3;
- simple teacher-facing paper settings.

Blueprint construction, retrieval and model configuration happen behind the scenes.

## Review UX is mandatory
Generated questions, answers/solutions, marking scheme, readable source references and validation status belong in one dedicated teacher review experience with Approve/Edit/Reject/Regenerate actions.

## Browser evidence
Do not claim a teacher-facing flow complete from unit tests alone. Add Playwright/browser E2E for the representative scenarios defined by the teacher-first product contract.

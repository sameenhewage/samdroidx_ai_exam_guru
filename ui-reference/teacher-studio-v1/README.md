# Teacher Studio V1 — UX Reference

This folder is the product-facing UX reference for the Exam Guru private Teacher Studio.

## Files

- `teacher-studio-reference.html` — interactive reference UI for the intended teacher experience.
- This reference describes the teacher-facing product contract. It is **not** production runtime code and must not be copied blindly into the app.

## Core product rule

The backend can be sophisticated. The teacher workflow must remain simple.

Normal teacher navigation:

1. Home
2. Materials
3. Generate Paper
4. Review & Approve
5. Published Papers

Technical areas belong under **Advanced**:

- Curriculum Setup
- Blueprint diagnostics
- RAG Explorer
- Exam Intelligence / backtesting
- Validation diagnostics
- Operations / audit

A teacher should be able to complete:

`Materials -> Generate -> Review -> Publish`

without understanding RAG, embeddings, taxonomy IDs, blueprint fingerprints, model/provider metadata, validation run IDs, or other engineering concepts.

## Materials

Default Materials view is the uploaded-materials library, not a long grade dashboard.

Required behavior:

- show what was uploaded immediately;
- search;
- filter by grade, subject, medium, material type, status, and where useful year;
- use server-side pagination;
- no endless scrolling;
- compact document cards, not database-style rows;
- teacher-readable title, filename, medium, type, year, pages, grade and subject;
- actions use clear labels such as **View PDF** and **Review extracted text**;
- internal IDs only in optional technical details;
- no synthetic fixture names such as `Knowledge subject 123...`, `Blueprint medium ...`, `Generation ...`, or fallback IDs.

Upload flow:

`Grade -> Medium -> Subject -> Material type -> Year/Curriculum -> PDF -> Review`

Uploaded content remains untrusted until extraction/OCR is reviewed and promoted through the existing trust pipeline.

## Generate Paper

### Paper target

Start with Grade and Medium.

Paper types are grade-aware:

- normal grades: Subject Practice, Term Test;
- Grade 5 additionally: Grade 5 Scholarship Practice;
- Grade 11 additionally: O/L Practice;
- Grade 13 additionally: A/L Practice.

Do not show O/L/A/L choices for Grade 5.

Paper-type choice is a proper accessible **radio-card group**, not raw buttons.

Subject rules:

- Subject Practice: subject-specific;
- Term Test: subject-specific;
- O/L / A/L practice: subject-specific;
- Grade 5 Scholarship Practice: integrated; hide the single Subject selector and use configured Scholarship coverage.

When Term Test is selected, expose a teacher-readable term choice such as 1st / 2nd / 3rd Term.

### Coverage

Use a proper mutually-exclusive radio-card group.

For Subject Practice:

- Full subject
- Choose specific lessons

For Term Test:

- All lessons for this term
- Choose specific lessons

When specific lessons are selected, show a compact checklist with selected count, Select all and Clear.

For Grade 5 Scholarship Practice, do not show a single-subject lesson picker. Use the configured reviewed integrated Scholarship coverage.

### Paper settings

Teachers control the question composition by **counts**, for example:

- MCQ: 5
- Written: 10
- Structured: 0

Do **not** ask for `Marks per MCQ`, `Marks per written question`, or one mark value for an entire question type.

Optional settings may include:

- paper name;
- duration;
- difficulty;
- special instruction.

The deterministic blueprint should be derived behind the scenes from the teacher's intent and trusted domain configuration.

## Review & Approve

Must support all configured grades, not only Grade 5.

Top-level review filters include at minimum:

- Grade
- Subject
- Medium
- Status

The selected paper must expose **all questions in a navigable list**. Do not force a one-question-only workflow with no overview.

Recommended layout:

- left: paper queue + all questions in selected paper;
- right: selected question detail.

Question detail should show:

- question;
- options where applicable;
- proposed answer / solution;
- source evidence;
- teacher-readable validation checks;
- explanation / marking criteria;
- correction reason and note;
- actions: Save correction, Regenerate, Reject, Approve.

### Marks

Marks are question-specific and teacher-controlled.

The system may produce an AI/deterministic suggestion, but it must not present an unconfirmed mark as final truth.

For each question, allow:

- editable total marks;
- editable marking points;
- individual marks per marking point;
- add/remove marking points;
- structured/sub-part marking where required;
- optional **Use AI suggestion** action.

Before teacher confirmation, the UI should communicate `Marks not confirmed`, not a locked final mark.

Human-approved revisions remain the trusted version; preserve original AI output and correction lineage.

## Published Papers

Only human-approved immutable paper versions appear here.

Published versions are immutable. Corrections create a new version.

The hosted Student side consumes approved publication packages only; it does not depend on live generation.

## Data and runtime truth

The UI must render real domain data through production API contracts.

Do not make the reference pass by:

- hardcoding Sinhala/Tamil/English labels in the production UI to hide broken data;
- filtering strings such as `Blueprint...` on the client;
- substituting fixture data for runtime validation;
- bypassing extraction, trust, RAG, blueprint, validation, review or publication domain rules.

## Local real-data corpus

The operator has a local data folder at:

`/home/sameen/SAMDROIDX-PROJECTS/samdroidx_ai_exam_guru/RAG DATA`

It contains syllabus, teacher guides and papers intended for realistic local validation.

Treat those files as candidate source material and process them through the real ingestion/review workflow. Do not silently treat raw/OCR output as trusted. Do not commit the local source corpus unless it is already tracked or explicitly approved for commit.

## Implementation standard

Any production implementation based on this reference must use the repository's engineering loop and prove behavior with backend tests, frontend tests, integration tests and browser/runtime acceptance against an isolated clean environment and real representative source documents.

# Manual Agent Intervention Workflow — Teacher Review Contract

## Status and precedence

This document is a V1 product/engineering contract for teacher review actions that require manual AI-agent intervention.

It is additive to:

- `00_V1_MASTER_PLAN.md`
- `02_PRIORITY_1_ADMIN_RAG_LLM_SPEC.md`
- `05_TEACHER_FIRST_MULTI_GRADE_CONTENT_STUDIO.md`
- `06_SUBJECT_QUALITY_VALIDATION_ENGINE.md`

The key rule is:

> Teacher review decisions and manual AI-agent work are separate concerns. Approval is immediate domain state; edit/reject/regenerate work is isolated, auditable, scoped, revalidated, and easy to find.

For V1, the application does **not** automatically invoke the local Codex/agent CLI. The operator runs the local agent manually from the command line using a task-specific prompt/packet created for the exact intervention.

---

## 1. Teacher actions and exact semantics

The Review & Approve UI may expose:

- **Approve**
- **Edit**
- **Reject**
- **Regenerate**

These actions must not be treated as equivalent generation commands.

### Approve

```
Approve
→ bind approval to the exact current question revision
→ preserve validation/provenance evidence
→ question becomes eligible for the approved question bank / paper draft
```

Approval must not invoke an agent.

### Edit

```
Teacher Edit
→ create a new question revision
→ preserve the previous revision
→ status = NEEDS_REVALIDATION
→ run the full applicable validation pipeline
→ return to teacher review
```

An edited question is never automatically trusted merely because a teacher changed it.

### Reject

```
Reject
→ preserve rejected revision + reason
→ current blueprint slot becomes REPLACEMENT_NEEDED
→ optionally create a manual-agent task for a replacement
```

Reject is not destructive deletion.

### Regenerate

```
Regenerate this question
→ create MANUAL_AGENT_TASK
→ scope task to this exact blueprint slot/question/section
→ operator runs local Codex/agent CLI manually
→ import a new candidate revision
→ run full validation
→ return to teacher review
```

**Do not regenerate the whole paper when a teacher requests regeneration of one question or one bounded section.**

Whole-paper regeneration is a separate explicit workflow and must never be an accidental side effect of a question-level intervention.

---

## 2. Separate Intervention Queue / Agent Tasks area

Non-approved work must be easy for both humans and coding/AI agents to identify.

Provide a dedicated operator area such as:

- **Agent Tasks**
- **Needs Attention**
- or an equivalent teacher-friendly label

Recommended task/status model:

- `NEEDS_EDIT`
- `NEEDS_REVALIDATION`
- `REPLACEMENT_NEEDED`
- `WAITING_FOR_AGENT`
- `AGENT_RESULT_READY`
- `VALIDATION_FAILED`
- `READY_FOR_TEACHER_REVIEW`
- `COMPLETED`
- `CANCELLED`

The queue must support filtering by:

- grade
- medium
- subject
- paper
- section
- question
- task type
- status
- created/reviewer/operator identity where authorized

This queue exists so an operator or engineering agent can be told:

> Work only on `WAITING_FOR_AGENT` and `VALIDATION_FAILED` tasks.

without scanning the whole paper or repository.

---

## 3. Manual agent task types

At minimum support these concepts:

- `regenerate_question`
- `replace_rejected_question`
- `repair_edited_question`
- `regenerate_answer_or_marking`
- `validate_agent_result`

The system may add more types later, but each task must remain narrow and explicit.

---

## 4. Question/section isolation contract

A manual agent task must identify exactly what may change.

Example:

```
Paper: Grade 8 Science — Term 2
Section: A
Blueprint slot: A-07
Question revision: qrev-...
Action: regenerate_question
```

The task must state:

- target paper/version
- target section
- target blueprint slot
- target question/revision when applicable
- grade
- medium
- subject
- term / assessment template
- unit/module/lesson/topic scope
- competency/skill scope where applicable
- question type/archetype
- difficulty
- marks
- duration/section constraints when relevant
- teacher reason
- failed validation findings
- allowed trusted RAG/source evidence
- required output schema

The task must explicitly prohibit changes outside the target slot/section.

For a single-question regeneration:

```
Q1 unchanged
Q2 regenerate
Q3 unchanged
paper blueprint unchanged except Q2 candidate revision
```

No unrelated question may be rewritten because it is nearby in the paper.

---

## 5. Manual Codex / local-agent execution

For V1, Codex/local-agent execution is operator-controlled and manual.

The application should prepare the task. The operator then runs the local agent from CMD/terminal.

Conceptual flow:

```
Exam Guru
→ create Agent Task
→ export task packet
→ operator runs local Codex/agent CLI
→ agent reads task packet
→ agent writes result
→ Exam Guru imports result
→ deterministic + subject + semantic validation
→ human review
```

The application must not hide an automatic local Codex invocation behind the Regenerate button.

This keeps model execution explicit, inspectable and controllable while the product is local/private.

---

## 6. Repository prompt templates vs private runtime task packets

Reusable generic instructions belong in Git.

Recommended repository location:

```
prompts/manual-agent/
├── README.md
├── regenerate-question.md
├── replace-rejected-question.md
├── repair-edited-question.md
├── regenerate-answer-marking.md
└── validate-agent-result.md
```

These are **templates**, not task-specific private educational payloads.

Task-specific packets belong in local/private durable storage, not Git.

Recommended conceptual location:

```
.exam-guru-data/
└── manual-agent-tasks/
    └── <task-id>/
        ├── REQUEST.md
        ├── CONTEXT.json
        ├── OUTPUT_SCHEMA.json
        └── RESULT.json
```

Exact physical layout may change behind a first-party storage abstraction, but private task content must remain outside Git.

---

## 7. Prompt packet content

A manual task packet should contain only the bounded context needed for that intervention.

For regeneration, include:

- task identity and action
- exact paper/section/slot scope
- trusted curriculum scope
- blueprint requirements
- teacher reason
- validation failures
- bounded verified RAG evidence and provenance
- current candidate only when the repair/regeneration policy intentionally requires it
- required answer/solution/marking fields
- output schema
- explicit "do not modify anything else" rule

Do not send the entire PDF or full paper when the task concerns one question unless a bounded dependency genuinely requires it.

---

## 8. Agent result contract

The agent must return structured output for the target task only.

For question regeneration, result should conceptually include:

- new question candidate
- options where applicable
- proposed answer
- explanation/solution
- marking scheme
- source/provenance references required by the generation contract
- task ID
- target blueprint slot
- generation/prompt/model lineage required by policy

Importing an agent result creates a **new candidate revision**.

It never overwrites the previous candidate.

---

## 9. Mandatory revalidation

Every imported/manual-agent result must pass the normal quality pipeline again.

```
Agent result
→ schema/integrity
→ blueprint + scope checks
→ provenance/RAG grounding
→ duplicate/paraphrase checks
→ deterministic subject validators
→ semantic verifier where needed
→ aggregate status
→ teacher review
```

A regenerated result cannot bypass validation because a teacher requested it.

A teacher edit also cannot bypass revalidation.

A rejected item cannot return to approval without a new valid candidate/revision.

---

## 10. Audit and provenance

Persist enough lineage to answer:

- who requested the intervention?
- what action was requested?
- what exact paper/slot/question revision was targeted?
- why was it requested?
- what validation failures existed?
- which task packet/version was exported?
- which agent/model/prompt produced the result?
- what new revision was imported?
- what validators ran afterward?
- who finally approved/rejected it?

Previous candidate revisions, rejected candidates and prior findings remain immutable history.

---

## 11. Teacher UX principle

Teachers should see simple actions and states, not CLI mechanics.

Normal UI:

```
Approve
Edit
Reject
Regenerate
```

Teacher-facing statuses may be:

- Ready
- Needs attention
- Waiting for regeneration
- New version ready
- Validation failed
- Ready for review

Technical task IDs, prompt versions, model identifiers and file paths belong under technical details/operator views.

The manual CMD/Codex step is an operator workflow, not something a normal teacher must understand.

---

## 12. Guardrails

The implementation must enforce:

1. `Regenerate this question` never silently regenerates the full paper.
2. A section-level task cannot modify another section.
3. An agent result never overwrites an approved or historical revision.
4. No agent result is automatically approved.
5. Edit/regenerate always creates new revision lineage.
6. Reject preserves reason and history.
7. Manual-agent work is discoverable in one dedicated queue.
8. Failed validation remains visible and cannot be bypassed.
9. Published immutable versions are never mutated by intervention tasks.
10. Private task packets are not committed to Git.

---

## 13. Future implementation acceptance

When this workflow is implemented, browser/API/DB evidence must prove:

- teacher Approve requires no agent execution;
- Edit creates a revision and requires revalidation;
- Reject creates a preserved rejection and replacement-needed state;
- question-level Regenerate creates only one scoped manual-agent task;
- no unrelated question/section changes;
- task queue filters make pending agent work easy to identify;
- task packet is generated with bounded trusted context;
- manual agent result imports as a new revision;
- full validation reruns after import;
- validation failure returns to Needs Attention;
- only explicit teacher approval can make the new revision eligible for the paper;
- audit history reconstructs the complete intervention lifecycle.

This workflow must remain compatible with the repository's continuous loop engineering model and the broader Exam Guru pipeline:

```
verified source
→ trusted knowledge/RAG
→ deterministic blueprint
→ generated candidate
→ validation
→ teacher review
→ scoped manual intervention where needed
→ revalidation
→ teacher approval
→ immutable published paper
```

# AI Exam Guru

AI-assisted Sri Lankan examination practice platform. V1 targets the **Grade 5 Scholarship examination** and is governed by repository-first engineering instructions.

## V1 priority order
1. **Priority 1 — Admin + Content Intelligence + RAG + LLM** — must reach 100% acceptance before Priority 2 starts.
2. **Priority 2 — Student paper experience + marking + progress analytics**.

## Development model
This repository uses **continuous loop engineering + mandatory TDD/eval-driven development**, not one implementation prompt per phase.

Read:
- [`AGENTS.md`](AGENTS.md)
- [`docs/v1/00_V1_MASTER_PLAN.md`](docs/v1/00_V1_MASTER_PLAN.md)
- [`docs/v1/01_ENGINEERING_WORKFLOW.md`](docs/v1/01_ENGINEERING_WORKFLOW.md)
- [`docs/v1/02_PRIORITY_1_ADMIN_RAG_LLM_SPEC.md`](docs/v1/02_PRIORITY_1_ADMIN_RAG_LLM_SPEC.md)
- [`docs/v1/03_PRIORITY_2_STUDENT_SPEC.md`](docs/v1/03_PRIORITY_2_STUDENT_SPEC.md)
- [`docs/v1/04_AGENT_SKILLS_OPERATING_MODEL.md`](docs/v1/04_AGENT_SKILLS_OPERATING_MODEL.md)
- [`docs/v1/PHASE_TRACKER.md`](docs/v1/PHASE_TRACKER.md)

## Automatic repository skills
Reusable agent workflows live in `.agents/skills/<skill-name>/SKILL.md`.

`AGENTS.md` contains the authoritative skill registry and automatic trigger rules. Every engineering task always loads:
- `loop-engineering`
- `tdd-eval-engineering`

The agent must then automatically load all matching domain skills (FastAPI, Next.js, OCR/ingestion, RAG, forecast/backtesting, LLM generation/validation, security/acceptance, and later the P10-gated student product skill) without waiting for the operator to name them.

See [`docs/v1/04_AGENT_SKILLS_OPERATING_MODEL.md`](docs/v1/04_AGENT_SKILLS_OPERATING_MODEL.md) for compositions and maintenance rules.

## GPT-5.6 Sol operator prompts
Start V1 with:
- [`prompts/v1/00_GPT_5_6_SOL_MASTER_EXECUTION.md`](prompts/v1/00_GPT_5_6_SOL_MASTER_EXECUTION.md)

Resume an interrupted engineering session with:
- [`prompts/v1/01_CONTINUE_ENGINEERING_LOOP.md`](prompts/v1/01_CONTINUE_ENGINEERING_LOOP.md)

Run adversarial review/fix loops with:
- [`prompts/v1/02_ADVERSARIAL_REVIEW_FIX_LOOP.md`](prompts/v1/02_ADVERSARIAL_REVIEW_FIX_LOOP.md)

Before unlocking student development run:
- [`prompts/v1/03_PRIORITY_1_ACCEPTANCE_AUDIT.md`](prompts/v1/03_PRIORITY_1_ACCEPTANCE_AUDIT.md)

Only after P10 is proven DONE, continue with:
- [`prompts/v1/04_PRIORITY_2_UNLOCK_AND_CONTINUE.md`](prompts/v1/04_PRIORITY_2_UNLOCK_AND_CONTINUE.md)

All prompts re-apply the repository skill-routing rules so skill use survives session/resume boundaries.

## Current state
Planning/instruction/skills baseline created. Implementation tracker begins at **P0 — Repository & Engineering Foundation**. Priority 2 remains blocked until P10 is DONE.
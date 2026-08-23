# V1 Agent Skills Operating Model

## Purpose
AI Exam Guru V1 is intended to be built through long-running GPT-5.6 Sol/Codex-style engineering sessions. Repository skills make recurring engineering rules reusable and reduce prompt drift across sessions.

Skills live under:

`.agents/skills/<skill-name>/SKILL.md`

`AGENTS.md` is the authoritative skill registry and routing contract.

## Automatic routing model
Every engineering work item uses two always-on skills:

1. `loop-engineering`
2. `tdd-eval-engineering`

The agent then loads all additional skills whose description matches the active work item. Skill selection is repeated whenever the continuous loop moves to a different type of work.

The agent must not wait for the operator to explicitly mention a skill.

## Skill inventory
| Skill | Trigger / responsibility |
|---|---|
| `loop-engineering` | Always; continuous inspect→test→implement→review→gate→commit→continue loop |
| `tdd-eval-engineering` | Always for behavior changes; TDD and AI/RAG eval-first discipline |
| `fastapi-domain-engineering` | Python/FastAPI, Pydantic, SQLAlchemy/Alembic, REST/OpenAPI, jobs, domain architecture |
| `nextjs-product-engineering` | Next.js/React/TypeScript product UI, admin/student UX, accessibility, generated API client |
| `document-ingestion-ocr` | PDF/source upload, native extraction, OCR, provenance, review and chunk preparation |
| `rag-retrieval-evaluation` | embeddings, pgvector, lexical/vector hybrid retrieval, ranking, context, provenance, retrieval evals |
| `exam-forecast-backtesting` | historical analytics, practice-priority forecasting, rolling holdout backtests, baseline comparison |
| `llm-question-generation-validation` | provider adapters, structured generation, validation, duplicate checks, AI evals/cost/latency |
| `security-reliability-review` | security/adversarial review, injection/poisoning, authz, retries/races, publish bypass, outages/cost abuse |
| `priority1-admin-acceptance` | Priority 1 admin product journey, phase closure and P10 acceptance/unlock decision |
| `student-exam-product` | P10-gated Priority 2 student exam runner, marking, analytics, dashboard and recommendations |

## Typical compositions
### Admin UI
`loop-engineering + tdd-eval-engineering + nextjs-product-engineering + priority1-admin-acceptance`

### PDF ingestion/OCR API
`loop-engineering + tdd-eval-engineering + document-ingestion-ocr + fastapi-domain-engineering + security-reliability-review`

### RAG implementation
`loop-engineering + tdd-eval-engineering + rag-retrieval-evaluation + fastapi-domain-engineering + security-reliability-review`

### Forecasting/backtesting
`loop-engineering + tdd-eval-engineering + exam-forecast-backtesting`

### LLM question generation
`loop-engineering + tdd-eval-engineering + llm-question-generation-validation + rag-retrieval-evaluation + security-reliability-review`

### P10 acceptance audit
`loop-engineering + tdd-eval-engineering + priority1-admin-acceptance + security-reliability-review` plus every domain skill needed to revalidate P0-P9.

### Priority 2 after unlock
`loop-engineering + tdd-eval-engineering + student-exam-product + nextjs-product-engineering + fastapi-domain-engineering` plus security/reliability as applicable.

## How prompts use skills
All prompts under `prompts/v1/` must first read `AGENTS.md`. The master, resume, adversarial, Priority 1 audit and Priority 2 unlock prompts additionally contain explicit skill-loading instructions so the workflow remains robust across session resets.

The execution loop is:

`read contracts -> load always-on skills -> select highest-priority work -> load matching domain skills -> RED/eval baseline -> GREEN -> REFACTOR -> integration/evals -> adversarial review -> regression fixes -> broad gate -> tracker evidence -> commit -> re-select skills for next work item -> continue`

## Skill maintenance
- Keep each skill focused; avoid one giant all-purpose skill.
- When a recurring engineering pattern appears at least several times or requires strict consistency, update an existing skill or add a focused new skill.
- Keep YAML `name` and `description` accurate because descriptions are used to decide relevance.
- Changes to a skill that materially change engineering behavior should be reviewed like other repository instructions.
- `AGENTS.md` and V1 contract docs outrank a skill if instructions conflict.

## Priority rule
Skill automation does not change product priorities. `student-exam-product` is forbidden for product implementation until P10 is legitimately DONE. Priority 1 remains Admin + Content Intelligence + RAG + LLM first.

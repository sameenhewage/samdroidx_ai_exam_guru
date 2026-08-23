# AI Exam Guru V1 — Master Plan

## 1. Product objective
Build a Grade 5 Scholarship examination practice platform for Sri Lanka that turns trusted curriculum material and historical examinations into validated AI-assisted practice papers, then later exposes those papers to subscribed students with marking and progress analytics.

V1 is intentionally split into two **strict priorities**, not two independent products.

### Priority 1 — Admin + Exam Intelligence + RAG + LLM
This is the foundation and must be 100% complete before Priority 2 implementation begins.

The admin must be able to:
1. create/manage Grade 5 curriculum metadata and taxonomy;
2. upload syllabus, teacher guides, past papers, marking schemes, and related trusted source material;
3. extract native PDF text and process scanned documents using an OCR adapter;
4. review/correct extracted content before it becomes trusted knowledge;
5. normalize historical questions into structured records;
6. classify questions by competency, skill, sub-skill, question archetype, difficulty, marks, year, paper and source;
7. build embeddings and searchable chunks in PostgreSQL + pgvector;
8. retrieve grounded curriculum/history context through hybrid RAG;
9. analyze historical coverage, frequency, marks, recency and question patterns;
10. run rolling historical backtests against held-out papers;
11. create deterministic paper blueprints;
12. generate structured question candidates through an LLM provider;
13. automatically validate generated questions;
14. detect near-duplicates/paraphrases of source questions;
15. route questions through human review/edit/approve/reject states;
16. compose a complete practice paper from approved questions;
17. publish an immutable paper version for future student use;
18. observe quality, token usage, cost, latency and failure reasons.

### Priority 2 — Student Experience
Starts only after the Priority 1 acceptance gate is DONE.

The student product must support:
1. account/authentication and Grade 5 profile;
2. subscription/access control;
3. browsing available published practice papers;
4. timed paper attempts;
5. autosave, navigation, review flags and resume safety;
6. submission and deterministic marking where possible;
7. score breakdown by competency/skill;
8. wrong-answer review;
9. historical progress dashboard;
10. simple next-paper/weak-skill recommendations.

## 2. V1 non-goals
The following are explicitly out of V1 unless the master plan is intentionally revised:

- AI conversational tutor
- story-based remediation/teaching
- voice tutor
- parent AI assistant
- live per-student full-paper generation
- Grade 10/11/A/L/university support
- fine-tuned proprietary LLM
- autonomous agent that controls exam rules
- exact future exam question claims
- GraphQL
- microservices
- Kubernetes
- separate managed vector database

## 3. Core product principle
The LLM is a replaceable generator/reasoner, not the product's source of truth.

The durable intelligence belongs to us:

`trusted sources -> structured curriculum/question data -> historical evidence -> RAG -> deterministic blueprint -> generated candidate -> validators/evals -> human review -> published paper`

## 4. V1 technical architecture

### Web
- Next.js + React + TypeScript
- shadcn/ui + React Aria + Tailwind CSS

### Backend
- Python + FastAPI + Pydantic
- REST/OpenAPI
- SQLAlchemy 2 + Alembic
- modular monolith

### Data
- PostgreSQL
- pgvector
- Valkey for cache/jobs
- S3-compatible object storage for uploaded source documents and generated artifacts

### AI/document intelligence
- native PDF extraction first
- pluggable open-source OCR adapter for scans; benchmark real Sinhala Grade 5 material before locking the engine
- provider-independent LLM interface
- OpenAI initially, with model benchmarking/evals deciding exact task routing
- provider-independent embedding interface
- first-party RAG retrieval and context-building logic

## 5. Major domain entities
At minimum the domain model must cover:

- User / Admin / Reviewer / Student
- Subscription / Entitlement
- Exam / Grade / Medium / PaperType
- CurriculumVersion
- Competency / Skill / SubSkill / LearningConcept
- SourceDocument / SourcePage / ExtractedBlock
- HistoricalPaper / HistoricalQuestion / HistoricalAnswer
- KnowledgeChunk / Embedding
- ForecastRun / ForecastScore / BacktestRun / BacktestResult
- PaperBlueprint / BlueprintSlot
- GeneratedQuestion / GenerationRun
- ValidationRun / ValidationFinding
- ReviewDecision
- QuestionBankItem
- PublishedPaper / PublishedPaperVersion
- StudentAttempt / StudentAnswer
- SkillPerformance / ProgressSnapshot / Recommendation

## 6. Paper generation contract
Paper generation is asynchronous and offline from the student runtime.

1. Admin requests a paper generation run.
2. System creates/loads an evidence-backed blueprint.
3. Each blueprint slot retrieves appropriate curriculum and historical context.
4. LLM generates multiple structured candidates where useful.
5. Validators score/reject candidates.
6. Duplicate/similarity checks run against historical and generated question banks.
7. Reviewer approves/edits/rejects.
8. Only approved questions can enter a publishable paper.
9. Published paper versions are stored and served without calling an LLM.

## 7. Forecasting contract
Forecasting is an evidence-based practice-priority engine, not a promise about the future exam.

Possible factors:
- curriculum importance
- historical frequency
- marks distribution
- question archetype frequency
- recency / time since appearance
- historical rotation patterns
- competency coverage constraints

Every forecasting method must be backtested using rolling historical holdouts and compared with a simple syllabus-balanced baseline. If it cannot demonstrate value, the product must fall back to syllabus-balanced practice rather than misleading prediction claims.

## 8. Validation contract
Every generated question must pass an explicit pipeline such as:

- schema/structured-output validation
- curriculum-scope validation
- competency/skill alignment validation
- answer correctness validation
- MCQ single-correct-answer checks where applicable
- mark/format rules
- language quality checks
- Grade 5 age-appropriateness
- duplicate/paraphrase similarity checks
- source-grounding/provenance checks
- reviewer approval

A failure creates a traceable validation finding and must never be silently ignored.

## 9. TDD/evaluation contract
TDD is mandatory for deterministic code. AI quality must additionally use eval-driven development.

Required evidence includes:
- unit tests
- real database integration tests
- API contract tests
- retrieval relevance fixtures
- historical backtest fixtures
- generation schema/validator contract tests
- duplicate-detection tests
- admin end-to-end flow
- opt-in real-model benchmark/eval runs

Every production defect becomes a regression test or eval case before the fix.

## 10. Delivery model
Development is continuous loop engineering, not prompt-per-phase implementation.

`inspect -> choose highest-priority failing/unfinished acceptance item -> RED -> GREEN -> REFACTOR -> integration/eval -> review -> fix -> full gate -> tracker update -> repeat`

The tracker provides evidence/status. It must not force artificial hand-offs between closely related work.

## 11. Priority gate
**Priority 2 work is blocked until all Priority 1 phases in `PHASE_TRACKER.md` are DONE and the Priority 1 release gate has passed.**

Minimal student UI scaffolding is allowed only if technically necessary for shared project bootstrap; no student feature implementation may distract from Priority 1.

## 12. V1 success outcome
A successful Priority 1 demo begins with real Grade 5 documents and ends with a reviewed/published practice paper whose questions can be traced to curriculum evidence, whose retrieval/generation/validation behavior is testable, and whose historical forecasting claims have measurable backtest evidence.

A successful full V1 then allows a subscribed Grade 5 student to take those published papers and see trustworthy score/skill/progress analytics without depending on live LLM availability.
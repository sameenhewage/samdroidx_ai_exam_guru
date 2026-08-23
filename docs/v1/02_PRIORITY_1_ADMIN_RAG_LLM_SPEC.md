# Priority 1 Specification — Admin + RAG + LLM

## Goal
Priority 1 delivers the content/intelligence factory that creates trustworthy Grade 5 Scholarship practice papers. It is the highest-value and highest-risk part of V1 and must be complete before student product development.

## 1. Admin areas
The admin/reviewer application should expose these functional areas.

### Dashboard
Show operational/quality status rather than vanity metrics:
- documents awaiting extraction/review;
- failed ingestion/OCR jobs;
- unclassified historical questions;
- RAG eval status;
- latest forecast/backtest results;
- generation jobs and cost;
- validation failures by category;
- reviewer queue;
- draft/reviewed/published papers.

### Curriculum
Manage:
- Grade 5 exam configuration;
- medium;
- curriculum version;
- competency;
- skill;
- sub-skill;
- learning concept;
- aliases/search terms;
- active/deprecated state.

Taxonomy changes that affect trusted content must be audited and must not silently rewrite historical evidence.

### Documents
Supported source types:
- official syllabus;
- teacher guide;
- official past paper;
- marking scheme;
- evaluation/report material;
- other explicitly approved trusted source.

For each document store:
- immutable original file reference;
- hash/checksum;
- document type;
- exam/grade/medium/version/year/paper metadata;
- extraction status;
- extractor/OCR implementation/version;
- raw extracted blocks;
- reviewed/corrected blocks;
- reviewer/audit history.

### Extraction Review
Admin/reviewer must be able to compare the original page with extracted blocks and correct text/reading order/metadata before promoting content to trusted knowledge.

### Historical Question Bank
Each historical question should support:
- year;
- paper;
- question number;
- source page/block provenance;
- original text;
- media/figure references;
- options/answer/marking data when available;
- competency/skill/sub-skill;
- question archetype;
- difficulty label and confidence/source;
- marks;
- reviewer status;
- embedding metadata.

LLM-assisted classification is allowed, but reviewer-confirmed labels are the trusted values.

### Knowledge Base
Curriculum/source content should be stored as meaningful educational chunks with metadata and provenance. Do not rely on blind fixed-size text splitting as the only strategy.

### RAG Explorer
Admin/dev tooling should allow inspection of a retrieval request:
- query/blueprint slot;
- metadata filters;
- lexical results;
- semantic results;
- fusion/ranking scores;
- final context;
- provenance;
- latency.

This is essential for debugging hallucinations and retrieval quality.

### Exam Intelligence
Provide evidence-based analysis:
- frequency by competency/skill;
- marks distribution;
- question archetype distribution;
- difficulty distribution;
- historical coverage;
- recency/gap features;
- forecast/practice-priority scores;
- rolling backtest reports;
- baseline comparison.

### Blueprint Studio
Blueprint is deterministic and versioned. It defines a paper before LLM generation.

A blueprint slot should include:
- target paper/section;
- competency/skill/sub-skill;
- question type/archetype;
- difficulty;
- marks;
- constraints;
- forecast/practice-priority rationale;
- allowed curriculum scope;
- retrieval query hints;
- uniqueness/diversity constraints.

### Generation Runs
A generation run stores:
- blueprint version;
- blueprint slot;
- retrieved context IDs;
- provider/model/version;
- prompt/template version;
- generation parameters;
- raw provider response where legally/operationally appropriate;
- parsed structured candidate;
- token/cost/latency;
- retry lineage;
- status/error.

### Validation
Validation should be composable. Example validators:
- structured schema;
- required fields;
- curriculum scope;
- competency alignment;
- age appropriateness;
- language quality;
- option count and answer uniqueness;
- deterministic answer verification where possible;
- marks/format rule;
- provenance/grounding;
- duplicate/paraphrase similarity;
- prohibited/unsafe content.

Each validator returns a finding/status and evidence, not merely a boolean.

### Review Queue
Reviewer sees together:
- generated question;
- proposed answer/solution;
- blueprint slot;
- retrieved curriculum/history context;
- source links/pages;
- validation findings;
- similarity matches;
- generation metadata.

Actions:
- edit;
- approve;
- reject with reason;
- request regeneration.

Edits and decisions are audited.

### Question Bank
Only reviewed/approved items become trusted generated question-bank items. Preserve lineage back to generation and sources.

### Paper Publishing
Lifecycle:

`DRAFT -> GENERATED -> VALIDATED -> IN_REVIEW -> APPROVED -> PUBLISHED -> ARCHIVED`

State rules must be enforced in domain code and tests.

A published paper version is immutable. Changes create a new version. Student serving later reads published versions only and never requires a live generation call.

## 2. RAG design
### Ingestion
`reviewed source -> semantic chunk -> metadata -> embedding -> PostgreSQL/pgvector`

### Retrieval
`blueprint/query -> hard metadata scope -> lexical search + vector search -> score fusion -> optional measured reranker -> context builder -> provenance bundle`

Hard filters must protect grade/medium/curriculum boundaries before semantic ranking.

### Evaluation
Create a fixed Grade 5 retrieval set containing representative queries/blueprint slots and expected relevant source IDs. Track metrics such as Recall@K, MRR/nDCG where meaningful, filter correctness, citation completeness and latency.

## 3. LLM architecture
Create first-party interfaces, for example conceptually:

- `LLMProvider`
- `EmbeddingProvider`
- `QuestionGenerator`
- `QuestionClassifier`
- `QuestionValidator` (domain validator orchestration, not provider-specific)

OpenAI is the initial adapter. Provider SDK objects must not leak into domain entities/services.

Use strict structured outputs/schemas. Store model and prompt versions for reproducibility.

## 4. Model routing
Do not hard-code one expensive model for everything.

Benchmark tasks separately:
- extraction cleanup/classification;
- metadata tagging;
- retrieval-query generation if needed;
- question generation;
- difficult validation/reasoning;
- language-quality review.

Choose models using an eval matrix across quality, latency and cost. A cheaper model may handle classification while a stronger model handles generation/edge cases.

## 5. OCR/document processing policy
- first try native PDF text/layout extraction;
- use OCR only where required;
- OCR implementation is behind an adapter;
- benchmark open-source OCR on representative real Sinhala Grade 5 pages;
- record character/word/question-structure accuracy where possible;
- human correction is part of the trust pipeline.

Do not make Azure or any single OCR vendor architectural source-of-truth.

## 6. Forecasting policy
Historical analysis is deterministic code. LLM may summarize results for an admin, but must not manufacture scores.

Backtesting must:
- train/calculate only from data available before the held-out year;
- forecast the held-out year;
- compare to actual distributions;
- repeat across multiple years;
- compare to syllabus-balanced baseline;
- record uncertainty/limitations.

If the method lacks predictive value, ship syllabus-balanced practice without deceptive prediction language.

## 7. Security/data integrity
Priority 1 must cover:
- admin/reviewer authorization;
- upload validation;
- content-type spoofing/size limits;
- storage access boundaries;
- prompt-injection handling for uploaded/retrieved documents;
- SQL/API input validation;
- audit logs;
- idempotent jobs;
- safe retries;
- immutable publication/version history;
- secret management;
- rate/cost controls on model calls.

Treat uploaded documents as untrusted input even when an admin uploads them.

### Identity integration sequencing
P1 uses the existing authentication port, role/permission enforcement, append-only audit trail and secure deterministic development/test identity adapter to prove authorized admin workflows and negative authorization cases. Production OAuth/OIDC or external identity-provider integration is not a P1 closure requirement.

P10 must replace the deterministic adapter with the selected production identity/login integration and re-run authentication, session, authorization and browser acceptance security tests before production readiness can be declared.

## 8. Priority 1 final demo
A reviewer should be able to start with a real Grade 5 source document and finish with a published practice paper, while the system can explain:
- what sources were used;
- what historical patterns informed the blueprint;
- what RAG context informed each generated question;
- what model/prompt generated it;
- what validators ran;
- what reviewer approved/changed;
- what it cost;
- what backtest evidence supports any forecast/practice-priority claim.

That is the Priority 1 product.
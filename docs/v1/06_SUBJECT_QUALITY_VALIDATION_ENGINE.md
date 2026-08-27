# Subject Quality Validation Engine — V1 Product and Engineering Contract

## Status and precedence
This document is a V1 product/engineering contract for generated-question correctness and educational quality. It is additive to:

- `00_V1_MASTER_PLAN.md`
- `02_PRIORITY_1_ADMIN_RAG_LLM_SPEC.md`
- `05_TEACHER_FIRST_MULTI_GRADE_CONTENT_STUDIO.md`
- `docs/SYSTEM_ARCHITECTURE.md`

It specifically strengthens P7/P8/P9. The current deterministic validation pipeline is useful for schema, blueprint, provenance, prompt-injection residue and duplicate indicators, but those checks alone do **not** prove that a generated question, answer, explanation or marking scheme is factually/semantically correct. This contract closes that gap with subject-aware, tool-assisted validation plus mandatory human review.

## 1. Quality principle
Generated educational content must not be trusted because an LLM produced it or because a second LLM agreed with it.

For every generated question, use the strongest available verification path in this order:

`deterministic rule/tool -> grounded subject checker -> independent structured semantic verifier -> human reviewer`

A later layer may add evidence, but it must not weaken a failure from a stronger deterministic rule without an explicit, auditable override workflow.

The system must distinguish:

- **machine-verifiable correctness** — arithmetic, equations, exact option count, marks totals, units, dates/numbers when directly represented in trusted sources;
- **grounded semantic correctness** — whether answer/explanation is supported by reviewed curriculum/source material;
- **language/age quality** — whether wording is appropriate for grade, medium and subject;
- **assessment quality** — ambiguity, multiple plausible answers, distractor quality, marking consistency, question-scope fit;
- **human approval** — final V1 publication authority.

## 2. Subject identity is first-class
Subject must be a trusted server-owned field throughout the content and generation path. Do **not** infer subject from filename, prompt text, paper title or model output at validation time.

Every validation input must eventually carry at least:

- `grade`
- `medium`
- `subject_code`
- `curriculum_version_id`
- selected unit/module/lesson/topic scope when applicable
- competency/skill scope when applicable
- blueprint slot/question type/difficulty/marks
- generated question + options + answer + explanation + marking scheme
- retrieved trusted sources/provenance

Cross-grade, cross-medium and cross-subject data must be hard-filtered before generation and re-checked during validation.

Unknown or unsupported subject identity must never silently receive a universal `PASS`. Use an explicit `WARN`/`FAIL` according to policy and require human review.

## 3. Validation architecture
Implement subject validation behind a first-party provider/plugin boundary rather than a giant `if subject == ...` file.

Conceptual interfaces:

```python
class SubjectValidator(Protocol):
    @property
    def subject_codes(self) -> frozenset[str]: ...

    @property
    def validator_id(self) -> str: ...

    @property
    def validator_version(self) -> str: ...

    def validate(self, context: SubjectValidationContext) -> tuple[ValidationFinding, ...]: ...
```

```python
@dataclass(frozen=True)
class SubjectValidationContext:
    grade: int
    medium: str
    subject_code: str
    curriculum_version_id: UUID
    selected_scope: CurriculumSelection
    candidate: GeneratedQuestionSnapshot
    grounding_sources: tuple[GroundingSource, ...]
```

The existing common validation pipeline remains responsible for universal rules. A `SubjectValidationRouter` selects the applicable subject validators from trusted subject identity.

Suggested processing order:

1. schema + canonical input integrity
2. blueprint/marks/question-type rules
3. provenance + hard curriculum scope checks
4. security/prompt-injection residue
5. exact/near duplicate checks
6. subject deterministic/tool checks
7. grounded semantic/factual verifier when needed
8. age/language/assessment-quality checks
9. aggregate status
10. human Review & Approve

## 4. Tooling strategy
Use tools because they are more reliable than free-form model judgement for specific classes of problems. Dependencies must be added only after checking current repo dependencies, license, maintenance status and security posture.

### 4.1 Core validation/runtime tools
| Need | Preferred tool/approach | Rule |
|---|---|---|
| Structured contracts | Pydantic v2 + existing immutable domain contracts | Reject malformed provider output before semantic use. |
| Maths symbolic verification | `sympy` | Recompute/solve expressions, equivalence, equations and exact rational results. Never use Python `eval`. |
| Arithmetic precision | Python `fractions.Fraction` / `decimal.Decimal` | Prefer exact arithmetic where school questions require exact values. |
| Units and conversions | first-party unit rules; optionally `pint` if justified by tests | Verify dimensions and conversions deterministically. Do not add a dependency only for trivial conversions. |
| Property-based test generation | `hypothesis` | Generate counterexamples for maths/rules and invariants. Keep deterministic regression examples for every discovered defect. |
| Unicode/script normalization | Python `unicodedata` + explicit Sinhala/Tamil/English script rules | Normalize safely; never treat script presence as proof of grammatical correctness. |
| Similarity/duplicate indicators | existing canonical hashes + lexical indicator; semantic embedding similarity only if benchmarked | Duplicate detection is separate from factual correctness. |
| Grounded evidence | PostgreSQL full-text + pgvector hybrid RAG over reviewed materials | Educational truth must come from trusted local materials, not arbitrary web results. |
| Semantic verifier | existing replaceable AI provider port with schema-constrained structured output | Use only when deterministic tooling cannot settle the claim. Record model/prompt/retrieval versions. |
| PDF/source inspection | existing native extraction/OCR pipeline + provenance | Validator must be able to point reviewer back to source/page. |
| Test runner | `pytest` | Unit/contract/integration validation tests. |
| Browser acceptance | Playwright | Prove teacher can understand findings and approve/reject/edit. |

### 4.2 Engineering-agent tool rules
When implementing this contract, the coding agent should use tools deliberately:

- inspect repository/domain/tests before writing code;
- use local test/build commands for RED -> GREEN -> REFACTOR;
- use PostgreSQL + pgvector and Valkey containers for integration behavior where applicable;
- use browser/Playwright for Review & Approve acceptance;
- use web lookup only to verify current package/API/version/security facts when adding/upgrading a dependency;
- **do not use the public web as the educational source of truth** for generated-question validation when reviewed curriculum materials are available;
- use the configured OpenAI provider only in opt-in live-model evaluation or runtime semantic verification, never as an unversioned hidden dependency;
- do not log or expose API keys or raw private source material.

## 5. Mathematics validator
Maths should use deterministic tooling aggressively.

Required checks where applicable:

- recompute the expected answer independently from the generated explanation;
- verify numeric answer equivalence;
- verify fractions/ratios/percentages exactly where possible;
- verify equation/identity equivalence with `sympy`;
- verify unit conversion and dimensional consistency;
- verify MCQ has exactly one mathematically correct option;
- reject duplicated-equivalent options such as `1/2` and `0.5` when both are presented as separate choices;
- check rounding/tolerance policy explicitly when approximation is intended;
- verify each marking step is consistent with the final answer;
- flag impossible/underspecified questions rather than guessing missing assumptions.

### Maths tool policy
Use a safe parser/normalizer before handing expressions to `sympy`. Do not execute arbitrary generated text. Keep supported expression grammar bounded for the target grade.

For a generated MCQ, the checker should ideally solve the problem independently and evaluate every option. A model-proposed `correct_option_id` is evidence to verify, not truth.

## 6. Sinhala-language validator
There is no assumption that one external grammar library can authoritatively validate Sri Lankan school Sinhala.

Use layered checks:

1. Unicode normalization and expected script ratio.
2. Detect obvious corruption/OCR residue/mixed-script anomalies.
3. Validate terminology against reviewed syllabus/teacher-guide/past-paper vocabulary where useful.
4. For comprehension questions, verify the answer is supported by the supplied passage/source.
5. Use a structured semantic/language verifier for fluency, ambiguity and age appropriateness when deterministic rules are insufficient.
6. Human reviewer remains final authority.

Do not fail a valid Sinhala question merely because an English-oriented grammar tool does not understand it.

## 7. English-language validator
For English content, use deterministic spelling/format/script checks and optionally a maintained grammar/style tool only after benchmark evidence shows it helps the target school level.

The validator should distinguish:

- grammatical correctness;
- age/grade vocabulary complexity;
- ambiguity;
- reading-comprehension answer support;
- assessment objective.

A generic grammar score must not determine publication by itself.

## 8. Science / Environment / History / Religion and other factual subjects
These domains require grounded claim verification more than symbolic solving.

Workflow:

1. Decompose question, answer and explanation into checkable claims.
2. Retrieve only reviewed material matching grade + medium + subject + curriculum version + selected lesson/unit scope.
3. Require each material factual claim to have supporting provenance.
4. Use deterministic comparison for explicit numbers, dates, names and enumerations where possible.
5. Use structured semantic verification for entailment/contradiction when exact string comparison is insufficient.
6. Fail or flag unsupported/contradictory claims.
7. Never invent a missing fact from general model memory.

For subjects where multiple accepted formulations are legitimate, store accepted-answer variants or rubric criteria rather than forcing brittle exact-string comparison.

## 9. Independent semantic verifier
When a deterministic subject tool cannot establish correctness, call a separate verifier through the existing AI provider abstraction.

The verifier input must contain:

- trusted subject/grade/scope metadata;
- generated question;
- proposed answer and explanation;
- marking scheme;
- bounded retrieved source excerpts with provenance;
- explicit rubric;
- instruction to return `supported`, `contradicted`, `insufficient_evidence`, or equivalent bounded statuses with evidence references.

The verifier must use schema-constrained output.

Do not ask only `Is this answer correct?`. Require claim-level evidence and contradiction reporting.

The initial `deterministic-factual-claims.v1` implementation keeps accepted-answer alternatives separate, splits explanations at bounded deterministic punctuation/newline boundaries and retains each marking criterion as a stable claim. The complete bounded claim set is verified in one provider call rather than multiplying cost per claim. Provider output must preserve every claim identity and order; first-party code recomputes the aggregate status and exact citation union. Append-only `semantic-verification.v1` evidence persists claim outcomes, references, lineage and integer accounting without duplicating private source text, while teacher review shows plain-language outcomes before collapsed diagnostics.

This deterministic split is reproducible, not a general semantic proposition parser. Its over/under-segmentation limitations remain explicit in `06_KNOWN_LIMITATIONS.md`, and human review remains authoritative.

### Independence rule
The verifier should be logically independent from the generator:

- separate prompt/template/version;
- no reuse of hidden chain-of-thought;
- independently supplied trusted context;
- persisted validator lineage;
- optional different model/provider only if evals prove value — it is not mandatory by default.

Agreement between two models is still not proof. Deterministic checks and source evidence have higher authority.

## 10. Validation findings and severity
Every subject check must produce stable, machine-readable finding codes and bounded reviewer-readable evidence.

Examples:

- `subject.math.answer_mismatch`
- `subject.math.multiple_correct_options`
- `subject.math.unit_mismatch`
- `subject.math.unsupported_expression`
- `subject.language.script_corruption`
- `subject.language.ambiguous_wording`
- `subject.factual.unsupported_claim`
- `subject.factual.source_contradiction`
- `subject.scope.outside_selected_lesson`
- `subject.marking.answer_inconsistent`

Use status semantics consistently:

- `PASS` — check completed and satisfied;
- `WARN` — check cannot conclusively establish quality or a reviewer should inspect it;
- `FAIL` — deterministic contradiction, invalid answer, out-of-scope content, or policy failure.

A technical inability to verify is **not** a PASS.

## 11. Reviewer correction memory — learn without fine-tuning first
The system should improve from teacher corrections, but V1 should not silently fine-tune a model from production edits.

Persist reviewer feedback as first-class data:

- original generated question/answer/explanation/marking;
- corrected version;
- subject/grade/scope;
- validator findings present at review time;
- reviewer action (`approve`, `edit`, `reject`, `regenerate`);
- structured reason codes;
- optional reviewer note;
- generation/model/prompt/retrieval/validator versions;
- provenance;
- timestamps/audit actor.

Convert valuable corrections into:

1. deterministic regression tests when they expose a rule bug;
2. golden evaluation cases when they expose generation/semantic quality failure;
3. subject-specific prompt/rubric examples when evidence shows that improves quality;
4. optional retrieval memory of approved/corrected examples where leakage/duplication controls permit it.

Do not auto-change validator thresholds or prompts from one teacher edit. Changes require versioning + eval comparison.

## 12. Eval dataset
Build a curated subject-quality eval set, starting with real reviewed Grade 5 Sinhala-medium content.

Each eval case should contain:

- grade/medium/subject/scope;
- trusted source references;
- question/answer/marking;
- expected validator outcome;
- known defect category if intentionally bad;
- human-adjudicated expected result.

Include adversarial negative examples:

- wrong numeric answer with plausible explanation;
- two mathematically equivalent MCQ options;
- correct answer but wrong marking scheme;
- fact from another grade/subject;
- answer unsupported by the supplied lesson;
- fluent but ambiguous Sinhala wording;
- copied/paraphrased past question;
- prompt-injection residue;
- verifier insufficient-evidence case.

Metrics should include at minimum:

- defect recall by category;
- false-fail rate on human-approved questions;
- unsupported-claim detection rate;
- answer-correctness accuracy for machine-verifiable maths cases;
- reviewer disagreement rate;
- latency/cost for semantic verifier path.

Never claim educational correctness from a handful of cherry-picked examples.

## 13. Generation feedback loop
The quality engine should feed generation safely.

Preferred loop for one question:

`generate -> common validators -> subject tools -> semantic verifier if needed -> result`

If retry policy permits and the failure is repairable:

`failure finding + bounded evidence -> regeneration instruction -> generate a fresh candidate -> re-run the full pipeline`

Rules:

- retry count is bounded;
- previous failed candidate remains auditable;
- never mutate a failed candidate into an apparent original success;
- do not pass raw private reviewer notes to external providers without policy;
- deterministic failures must be re-checked after regeneration;
- repeated failure routes to human review rather than infinite loops.

## 14. Teacher-facing Review & Approve UX
Teachers should not see `sympy`, embeddings, JSON schemas or model-chain internals as the primary experience.

Show concise findings such as:

- `Answer check: Passed`
- `Maths calculation: Answer does not match the question`
- `Source check: Supported by Teacher Guide p. 42`
- `Language check: Needs attention — wording may be ambiguous`
- `Scope check: Failed — this question appears outside Lessons 1–3`

`Why?` / `Technical details` may expose validator ID/version, evidence, source IDs and model metadata for advanced users.

The reviewer must be able to edit, reject or regenerate one question and re-run validation before approval.

## 15. Implementation sequence
Do not rewrite the whole validation subsystem in one untested change.

### Stage A — trusted subject scope
- make subject first-class in the reusable curriculum/blueprint/generation/validation path;
- preserve existing Grade 5 data/provenance;
- add migration/contract tests;
- prohibit client-controlled subject spoofing.

### Stage B — subject validator framework
- add `SubjectValidationContext` / validator protocol/router;
- version validator lineage;
- explicit unsupported-subject finding;
- deterministic tests first.

### Stage C — Maths first
- add representative Grade 5 maths fixtures;
- implement safe parser + exact arithmetic/SymPy checks for supported archetypes;
- verify answers/options/marking;
- property-based tests where useful.

### Stage D — grounded factual/language verification
- add claim/evidence semantic verifier port;
- use trusted RAG context only;
- schema-constrained fake-provider tests;
- opt-in live model evals;
- Sinhala ambiguity/fluency checks without pretending deterministic grammar certainty.

### Stage E — correction memory
- persist reviewer corrections/reason codes/version lineage;
- create regression/eval promotion workflow;
- expose teacher-friendly feedback loop.

### Stage F — P8/P9 acceptance
- browser E2E from generate -> subject validation -> edit/regenerate -> revalidate -> approve;
- adversarial review;
- broad backend/frontend/DB/OpenAPI gates;
- tracker evidence.

## 16. Dependency discipline
Before adding a new runtime dependency:

1. prove the requirement cannot be met cleanly with existing first-party/standard-library code;
2. verify latest maintained/security-patched version and license;
3. add focused tests around the dependency boundary;
4. wrap it behind first-party code so it can be replaced;
5. record its purpose in dependency documentation/lockfile;
6. do not introduce a broad framework for one small validator.

For the initial Maths implementation, `sympy` is the preferred external solver candidate. `hypothesis` is preferred for test generation, not as runtime behavior. `pint` is optional and should only be introduced when unit-conversion complexity justifies it.

## 17. Security and privacy
- Never execute generated code or use unrestricted `eval`/`exec`.
- Bound expression length, AST depth/token count and solver effort.
- Treat generated expressions and retrieved text as untrusted input.
- Prevent denial-of-service via pathological symbolic expressions.
- Keep raw source material within the private/local Studio boundary.
- Do not send entire PDFs or unnecessary private source text to an external model; send only the bounded excerpts required for verification.
- Audit provider calls without logging secrets.

## 18. Acceptance gate
Subject-quality work is not accepted merely because a validator class exists.

Minimum P8/P9 evidence must demonstrate:

1. trusted subject identity survives generation -> validation;
2. a known wrong Maths answer is rejected deterministically;
3. a Maths MCQ with multiple correct/equivalent options is rejected;
4. a question outside selected grade/subject/lesson scope is rejected/flagged;
5. an unsupported factual claim is not silently passed;
6. semantic verifier returns auditable structured evidence when deterministic checks are insufficient;
7. a teacher correction can be saved and promoted to a regression/eval case;
8. revalidation occurs after edit/regeneration;
9. failed checks cannot bypass human review/publish rules;
10. browser E2E shows teacher-friendly findings without requiring engineering knowledge.

## 19. Non-goals for V1
Do not make V1 depend on:

- fine-tuning a custom model;
- training a Sinhala grammar model;
- an autonomous multi-agent debate system;
- public-web fact checking as educational truth;
- executing generated Python/code;
- a separate vector database;
- automatic publication after AI agreement.

The V1 goal is a **versioned, tool-assisted, evidence-backed quality gate that helps a teacher publish trustworthy papers**, not a claim that AI can replace the teacher.
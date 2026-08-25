---
name: subject-quality-validation
description: Use whenever implementing or reviewing subject-aware correctness checks for generated educational questions, answers, explanations or marking schemes; includes trusted subject routing, deterministic subject tools such as SymPy, grounded semantic verification, reviewer correction memory, and subject-quality evals.
---

# Subject Quality Validation

## Mandatory contract
Before implementation, read `docs/v1/06_SUBJECT_QUALITY_VALIDATION_ENGINE.md` in full.

Pair this skill with:

- `loop-engineering`
- `tdd-eval-engineering`
- `llm-question-generation-validation`
- `rag-retrieval-evaluation` when source grounding is involved
- `fastapi-domain-engineering` for backend/domain/schema work
- `security-reliability-review` when parsing generated expressions, calling external models, changing publish gates, or handling untrusted content
- `teacher-content-studio-ux` + `nextjs-product-engineering` for Review & Approve UX

## Core rule
Use the strongest available verification mechanism in this order:

`deterministic rule/tool -> grounded subject checker -> structured semantic verifier -> human review`

Do not replace a deterministic check with model judgement merely because a model is easier to call.

## Subject identity
Subject, grade, medium and curriculum scope must come from trusted server-owned domain state. Never infer validation routing from filename, prompt text, paper title or generated content.

If the subject is unsupported or cannot be verified, emit an explicit finding. An inability to verify is not a PASS.

## Tool selection
### Maths
Prefer:
- standard-library exact arithmetic (`Fraction`, `Decimal`);
- `sympy` for symbolic solving/equivalence;
- first-party bounded unit rules or `pint` only when justified;
- `hypothesis` for property-based tests.

Never use unrestricted `eval`/`exec`. Put generated expressions through a bounded parser/grammar and constrain solver effort.

### Language
Prefer:
- Unicode normalization and explicit script checks;
- reviewed curriculum vocabulary/terminology where useful;
- grounded passage/source support checks;
- structured semantic verifier for ambiguity/fluency/age appropriateness when deterministic rules are insufficient.

Do not treat an English-centric grammar checker as authoritative for Sinhala/Tamil.

### Factual subjects
Decompose content into claims, hard-filter trusted RAG by grade/medium/subject/curriculum/scope, then use deterministic comparison for explicit values and a schema-constrained semantic verifier for entailment/contradiction.

Do not use the public web as educational truth when reviewed local curriculum sources exist.

## TDD/eval workflow
For each new defect class:

1. add a failing deterministic test or eval case;
2. implement the smallest checker/tool integration;
3. test malformed/adversarial inputs;
4. add a human-approved positive case to prevent false failures;
5. run the broad validation suite;
6. record validator/tool version lineage;
7. update tracker only with evidence.

Every teacher correction that exposes a recurring defect should be considered for promotion into a regression test or golden eval case.

## Semantic verifier
Use the existing replaceable AI provider abstraction. Require structured output and evidence references such as:

- `supported`
- `contradicted`
- `insufficient_evidence`

Persist model/provider/prompt/retrieval/validator versions. Agreement with the generator is not proof by itself.

Normal CI uses deterministic fakes. Live provider evals are opt-in.

## Reviewer UX
Teacher-facing findings must be concise and actionable. Technical tool/model details stay behind progressive disclosure.

Examples:
- `Answer check: Passed`
- `Calculation check: Answer does not match`
- `Source check: Supported by Teacher Guide p. 42`
- `Language check: Needs attention`
- `Scope check: Outside Lessons 1–3`

Edits/regeneration must trigger revalidation before approval.

## Security
- bound input size and parser complexity;
- never execute generated code;
- bound symbolic solver effort;
- treat generated content and RAG text as untrusted;
- send only bounded necessary excerpts to external providers;
- never log secrets/private raw source material;
- preserve publish/review authorization gates.

## Completion evidence
Do not call this work complete until tests/evals prove at least:

- trusted subject routing;
- deterministic rejection of a wrong Maths answer;
- detection of multiple mathematically correct/equivalent MCQ options;
- scope leakage detection;
- unsupported factual content does not silently pass;
- semantic verifier evidence is structured/auditable;
- teacher correction can become regression/eval evidence;
- revalidation after edit/regeneration;
- failed quality checks cannot bypass human review/publishing.
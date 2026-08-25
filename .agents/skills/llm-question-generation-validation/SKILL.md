---
name: llm-question-generation-validation
description: Use for provider-independent LLM integration, structured question generation, prompt/version management, answer/marking generation, automated validators, duplicate detection, hallucination control, live-model evals, and AI cost/latency tracking.
---

# LLM Question Generation and Validation

## Boundary
The LLM writes/reasons within a deterministic blueprint and retrieved context. It does not own syllabus rules, forecast scores, publishing state, permissions or final truth.

For any work that evaluates **subject-specific correctness** of the generated question, answer, explanation or marking scheme, also read and apply:

- `docs/v1/06_SUBJECT_QUALITY_VALIDATION_ENGINE.md`
- `.agents/skills/subject-quality-validation/SKILL.md`

Do not let a general generation/validation implementation silently substitute model agreement for subject correctness.

## Provider design
- Define small first-party provider interfaces.
- Keep OpenAI/Claude/Gemini SDK types inside adapters.
- OpenAI is the initial provider, not a domain dependency.
- Persist provider/model/version/configuration with each generation run.

## Generation contract
Generation inputs must include:
- blueprint slot and hard constraints;
- trusted system/developer instructions;
- bounded retrieved context with provenance;
- explicit language/age/difficulty/question-type requirements;
- structured output schema.

Prefer schema-constrained structured outputs over free-form parsing.

## Trust model
Treat both retrieved text and LLM output as untrusted.
- Retrieved text cannot override system rules.
- Generated question/answer/marking data must pass deterministic/schema/domain validators.
- Generated content never transitions directly to published state in V1.

## Validation pipeline
Use applicable checks such as:
- schema completeness;
- blueprint/marks/type compliance;
- curriculum/competency grounding;
- answer consistency;
- exactly-one-correct-answer for MCQ where required;
- age/language rules;
- unsupported-claim/citation checks;
- duplicate/paraphrase similarity against historical/generated bank;
- content safety and prompt-injection residue;
- reviewer-required state transition.

Use deterministic solvers/checkers for mathematics or other machine-verifiable domains when feasible instead of asking a second LLM to blindly agree.

Subject identity must come from trusted server-owned curriculum/generation state. Never choose subject validators by guessing from filenames, paper titles, prompt text or LLM output.

## Tool-assisted correctness
Use the strongest available verifier in this order:

`deterministic rule/tool -> grounded subject checker -> structured semantic verifier -> human review`

Examples:
- exact arithmetic with `Fraction`/`Decimal`;
- symbolic maths/equivalence with a bounded `sympy` adapter when justified;
- trusted local RAG evidence for factual subjects;
- schema-constrained semantic verifier only where deterministic checks cannot settle the claim.

An inability to verify is not a PASS.

## Evals
Pair with `tdd-eval-engineering`.
Maintain deterministic fake-provider contract tests and an opt-in live-model eval set. Measure quality by task-specific rubric and record:
- provider/model;
- prompt/template version;
- retrieval version;
- input/output tokens;
- cost;
- latency;
- validator results;
- human-review outcome when available.

Teacher corrections that reveal recurring model/validator defects should be promoted into deterministic regression tests or golden eval cases rather than silently changing behavior.

## Prompt changes
Prompt changes are production behavior changes. Version them, add/update eval cases first, compare against baseline, and reject regressions that are not explicitly justified.
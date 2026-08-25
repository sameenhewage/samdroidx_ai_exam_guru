# GPT-5.6 Sol — Subject Quality Validation Engine Execution

Use this prompt when the repository is ready to implement or continue the subject-quality correction. It is not a one-phase handoff; after the correction is proven, return to the normal continuous V1 loop.

```text
First inspect git status and preserve all current local work. Do not discard, reset, rewrite or overwrite uncommitted implementation.

Fetch/integrate the latest master contracts safely, then read in full:

- AGENTS.md
- docs/SYSTEM_ARCHITECTURE.md
- docs/v1/06_SUBJECT_QUALITY_VALIDATION_ENGINE.md
- .agents/skills/loop-engineering/SKILL.md
- .agents/skills/tdd-eval-engineering/SKILL.md
- .agents/skills/llm-question-generation-validation/SKILL.md
- .agents/skills/subject-quality-validation/SKILL.md
- .agents/skills/rag-retrieval-evaluation/SKILL.md
- .agents/skills/fastapi-domain-engineering/SKILL.md
- .agents/skills/security-reliability-review/SKILL.md

Also inspect all nested AGENTS.md files that govern changed paths and any additional repo skill triggered by the active work.

Treat this as a correctness/domain correction, not merely a UI improvement.

Goal:
Build a versioned subject-aware validation layer so generated educational questions, answers, explanations and marking schemes are checked with the strongest available tools before teacher approval.

Important current gap:
The existing deterministic validation pipeline handles structural/blueprint/provenance/security/duplicate indicators but does not prove factual or semantic correctness. Do not falsely relabel those existing checks as correctness proof.

Execute using the repository loop:
inspect -> choose highest-value incomplete acceptance item -> write failing tests/evals -> RED -> implement GREEN -> REFACTOR -> integration/eval -> adversarial review -> regression test each defect -> fix -> broad gates -> tracker evidence -> commit -> continue.

Implementation direction:

1. TRUSTED SUBJECT SCOPE
- Inspect the multi-grade curriculum/domain model first.
- Make subject a first-class server-owned value across reusable curriculum/blueprint/generation/validation boundaries where missing.
- Do not infer subject from filenames, prompts, paper titles or LLM output.
- Preserve Grade 5 IDs/provenance/audit when migrations are needed.
- Hard-filter grade + medium + subject + curriculum version + selected lesson/unit scope.
- Add spoofing/scope-leakage regression tests.

2. SUBJECT VALIDATOR FRAMEWORK
- Add a first-party SubjectValidationContext + SubjectValidator protocol/router or an equivalent clean modular design.
- Keep universal validators separate from subject-specific validators.
- Unsupported/unknown subject must produce an explicit non-PASS finding.
- Version validator/tool lineage and persist bounded evidence.

3. MATHS FIRST — TOOL ASSISTED
Before adding dependencies, inspect pyproject/lockfiles and current packages.
Use the smallest justified set:
- Python Fraction/Decimal for exact arithmetic;
- SymPy as preferred symbolic solver/equivalence tool when a maintained compatible version is not already present;
- Hypothesis as a test-only property-based tool when useful;
- Pint only if real unit-validation requirements justify it.

Before adding/upgrading any dependency, verify its current maintained/security-patched release, compatibility and license using authoritative package/project sources. Pin through the project’s normal uv/lock workflow.

Never use unrestricted eval/exec. Parse generated expressions through a bounded grammar/parser and constrain expression size/depth/solver effort.

Add tests/evals for at minimum:
- wrong numeric answer;
- mathematically equivalent answer representation;
- multiple correct/equivalent MCQ options;
- unit mismatch;
- answer vs marking inconsistency;
- unsupported/underspecified expression;
- malformed/pathological expression that must not cause resource abuse.

4. FACTUAL + LANGUAGE SUBJECTS
- Use reviewed local curriculum/RAG material as educational source of truth.
- Do not use arbitrary public-web pages to decide whether a school answer is correct.
- Decompose question/answer/explanation into claims when useful.
- Deterministically verify explicit values/names/dates/enumerations when possible.
- For semantic entailment/contradiction, call an independent verifier through the existing replaceable AI provider port.
- Verifier output must be schema-constrained and evidence-linked: supported / contradicted / insufficient_evidence (or equivalent stable enum).
- Normal CI uses deterministic fake providers; live OpenAI eval is opt-in.
- Sinhala validation must combine Unicode/script integrity, reviewed terminology/source support and structured semantic review. Do not pretend an English grammar library can authoritatively grade Sinhala.

5. REVIEWER CORRECTION MEMORY
- Persist original candidate, corrected candidate, subject/scope, findings, reviewer action/reason, version lineage and provenance.
- Do not silently fine-tune a model from edits.
- Promote recurring corrections into deterministic regression tests or golden eval cases.
- Version prompt/rubric/threshold changes and compare evals before adoption.

6. TEACHER REVIEW UX
- Keep technical internals hidden by default.
- Show human-readable checks such as Answer check, Calculation check, Source check, Language check and Scope check.
- Edit/regenerate must force revalidation before approval.
- Failed checks must not bypass publish/review authorization.
- Use Playwright/browser E2E for the teacher flow.

TOOL USAGE RULES
- Repository/local shell tools: inspect code, run pytest, lint/typecheck/build, migrations, integration containers and targeted tests continuously.
- PostgreSQL + pgvector / Valkey: use real containers for integration paths where the domain requires them.
- Browser/Playwright: prove Review & Approve behavior and progressive disclosure.
- Web lookup: only for current dependency/API/security/version facts when needed; prefer authoritative sources.
- Educational truth: use trusted uploaded/reviewed local materials and provenance, not the public web.
- OpenAI runtime: only through the provider abstraction; do not expose/log key; bounded context only; record non-secret provider/model/prompt/retrieval/cost/latency evidence.
- Never execute generated code.

ACCEPTANCE GATE
Do not mark this correction complete until evidence proves:
- trusted subject routing;
- known wrong Maths answer fails deterministically;
- equivalent/multiple-correct Maths MCQ fails;
- out-of-scope question is caught;
- unsupported factual claim does not silently pass;
- semantic verifier is structured and auditable;
- teacher correction can become regression/eval evidence;
- edit/regenerate triggers revalidation;
- failed checks cannot bypass publication;
- browser E2E shows teacher-friendly validation.

Keep the existing human review requirement. AI/model agreement is never automatic publication authority.

After this subject-quality correction is green and accepted, continue the normal full V1 engineering loop automatically. Do not stop merely because this document-specific work is done.
```

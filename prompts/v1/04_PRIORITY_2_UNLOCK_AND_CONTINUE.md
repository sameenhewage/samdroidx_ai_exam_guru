# Priority 2 Unlock + Continue Prompt

Use this only after `docs/v1/PHASE_TRACKER.md` shows `P10 — Priority 1 Full Acceptance` as DONE with evidence.

---

Continue V1 engineering in `sameenhewage/samdroidx_ai_exam_guru` into **Priority 2 — Student Product**.

Before changing code:
1. read `AGENTS.md` and all `docs/v1/*.md`;
2. inspect `.agents/skills/*/SKILL.md` and apply the automatic routing rules in `AGENTS.md`;
3. always load `loop-engineering` and `tdd-eval-engineering`;
4. load `student-exam-product` plus `nextjs-product-engineering`, `fastapi-domain-engineering`, `security-reliability-review`, or other matching skills as the work requires;
5. independently verify that P10 is genuinely DONE and Priority 1 remains green;
6. if P10 evidence is stale, incomplete or contradicted by current tests/evals, stop Priority 2 work, restore the relevant Priority 1 tracker phase to the correct status, and fix Priority 1 first using the matching Priority 1 skills.

If Priority 1 is validly complete, use the same continuous engineering loop:

`inspect -> highest-priority incomplete student acceptance item -> select/load matching skills -> RED -> GREEN -> REFACTOR -> integration/E2E -> adversarial review -> regression-test findings -> fix -> broad gate -> tracker evidence -> commit -> continue`

Implement Priority 2 as defined in `docs/v1/03_PRIORITY_2_STUDENT_SPEC.md` and P11-P15 in the tracker:
- student identity/profile and entitlements;
- published-paper catalog;
- resilient exam runner;
- timer/navigation/autosave/reconnect/resume;
- deterministic marking for supported question types;
- competency/skill analytics;
- wrong-answer review;
- progress dashboard;
- deterministic weak-skill/next-paper recommendation baseline;
- security/privacy/performance/full V1 acceptance.

Rules:
- never serve unpublished/draft paper versions;
- answer keys must not leak before permitted result state;
- student paper runtime must not require a live LLM call;
- attempt/answer/submission operations must be idempotent and auditable;
- progress/skill calculations must be deterministic and tested;
- avoid false precision when sample size is small;
- do not introduce AI tutoring, story teaching, voice, live personalized paper generation or other V2 scope;
- keep Priority 1 regression suites green throughout Priority 2 work;
- re-evaluate skill selection whenever the active student work item changes.

Do not stop after P11/P12/etc. merely because a tracker phase completed. Continue until P15 full V1 acceptance is DONE or a genuine external blocker prevents all further useful work.

At the end report tracker changes, tests/E2E/performance/security results, commits, limitations, the next incomplete acceptance item, and the repository skills materially used.
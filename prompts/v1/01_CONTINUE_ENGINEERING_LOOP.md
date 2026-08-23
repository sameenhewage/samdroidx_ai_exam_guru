# Continue Engineering Loop Prompt

Use this when resuming work after an interrupted session.

---

Resume engineering `sameenhewage/samdroidx_ai_exam_guru` from the current repository state.

Read `AGENTS.md`, all `docs/v1/*.md`, and the existing tracker/evidence before changing anything. Do not restart planning from scratch and do not repeat already completed work.

Follow the continuous loop engineering model with mandatory TDD/eval-driven development:

`inspect current evidence -> select highest-priority incomplete acceptance item -> RED -> GREEN -> REFACTOR -> integration/eval -> adversarial review -> regression-test findings -> fix -> broad gate -> tracker evidence -> commit -> continue`

Rules:
- Priority 1 is the only active product priority until P10 is genuinely DONE.
- Do not implement student features while Priority 1 is incomplete.
- Treat tracker phases as acceptance/status gates, not isolated implementation prompts.
- Do not stop simply because one phase becomes DONE; continue to the next highest-priority incomplete Priority 1 item.
- Every discovered defect must be reproduced by a test/eval before being fixed.
- Keep deterministic exam rules in code, not prompts.
- Keep LLM/provider replaceable and all generated questions grounded, validated, auditable and human-reviewable.
- Keep normal CI independent of paid/live LLM availability, while maintaining opt-in live quality evals.
- Use real PostgreSQL+pgvector and Valkey integration tests for critical behavior.
- Update `docs/v1/PHASE_TRACKER.md` only with concrete evidence.

At the end of the session, report only evidence-backed progress: changed tracker statuses, test/eval results, commits, blockers/limitations, and the next highest-priority incomplete work.
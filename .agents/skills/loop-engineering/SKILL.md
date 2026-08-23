---
name: loop-engineering
description: Mandatory continuous engineering loop for AI Exam Guru. Use for every implementation, refactor, defect fix, integration, or acceptance task. Keeps work moving across tracker gates without stopping after one phase.
---

# Loop Engineering

## When to use
Use this skill for **every repository engineering task**. It is always paired with `tdd-eval-engineering` whenever behavior changes.

## Required loop
Repeat until the active priority acceptance gate is complete or a genuine external blocker prevents further work:

1. Inspect repository state, `AGENTS.md`, V1 docs, tracker, tests, CI, migrations and recent changes.
2. Select the highest-priority incomplete acceptance item in the active priority.
3. Identify the smallest cohesive vertical slice that moves that acceptance item forward.
4. Load all additional task-specific skills whose descriptions match that slice.
5. Define evidence required to prove the slice works.
6. Apply RED -> GREEN -> REFACTOR using `tdd-eval-engineering`.
7. Run focused tests, then integration/eval tests, then the broad relevant gate.
8. Perform an adversarial/self-review of the change.
9. Convert every valid finding into a regression test/eval before fixing it.
10. Re-run the broad gate.
11. Update `docs/v1/PHASE_TRACKER.md` only with evidence.
12. Commit a cohesive change with a meaningful message.
13. Immediately continue with the next highest-priority incomplete item. Do not wait for another phase prompt.

## Priority lock
- Priority 1 is active until `P10 — Priority 1 Full Acceptance` is legitimately `DONE`.
- Do not implement Priority 2 product functionality while P10 is incomplete.
- Shared scaffolding is allowed only when required to satisfy Priority 1.

## Blockers
A blocker is genuine only if it depends on something unavailable outside the repository/environment, such as missing credentials or legally required source data. When blocked:
- record the exact blocker and evidence;
- continue all other non-blocked work in the active priority;
- never stop merely because one subtask is blocked.

## Completion discipline
Never equate compilation, a green unit test, or a partially working screen with acceptance. A tracker gate is DONE only when every exit criterion and required runtime/eval evidence passes.

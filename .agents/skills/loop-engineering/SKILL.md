---
name: loop-engineering
description: Mandatory continuous engineering loop for AI Exam Guru. Use for every implementation, refactor, defect fix, integration, or acceptance task. Keeps work moving across tracker gates without stopping after one phase.
---

# Loop Engineering

## When to use
Use this skill for **every repository engineering task**. It is always paired with `tdd-eval-engineering` whenever behavior changes.

## Required loop
Repeat until full V1 acceptance is complete or a genuine external blocker prevents all remaining useful work:

1. Inspect repository state, `AGENTS.md`, V1 docs, tracker, tests, CI, migrations and recent changes.
2. Select the highest-value non-blocked acceptance item in the active priority, respecting dependencies but not assuming tracker phases are sequential implementation locks.
3. Identify the smallest cohesive vertical slice that moves one or more acceptance items forward.
4. Load all additional task-specific skills whose descriptions match that slice.
5. Define evidence required to prove the slice works.
6. Apply RED -> GREEN -> REFACTOR using `tdd-eval-engineering`.
7. Run focused tests, then integration/eval tests, then the broad relevant gate.
8. Perform an adversarial/self-review of the change.
9. Convert every valid finding into a regression test/eval before fixing it.
10. Re-run the broad gate.
11. Update `docs/v1/PHASE_TRACKER.md` only with evidence.
12. Commit a cohesive change with a meaningful message and push when the repository workflow permits.
13. Immediately continue with the next highest-value non-blocked item. Do not wait for another phase prompt.

## Acceptance phases are not sequential development locks
- P0-P15 are status/evidence gates, not a waterfall implementation schedule.
- If an earlier phase has one external/human evidence blocker, continue later **Priority 1** engineering wherever its dependencies can be satisfied safely with reviewed native content, deterministic fixtures, or already-complete platform capabilities.
- Example: if P2 awaits human-adjudicated OCR ground truth, do not stop the product build. Continue P3-P9 implementation/evals with trusted native-text/reviewed inputs while P2 stays `IN_PROGRESS` or `BLOCKED` accurately.
- Never falsify evidence or mark a blocked gate DONE merely to move on.
- P10 can be marked DONE only when P0-P9 are all legitimately satisfied.

## Priority lock and automatic transition
- Priority 1 is active until `P10 — Priority 1 Full Acceptance` is legitimately `DONE`.
- Do not implement Priority 2 product functionality while P10 is incomplete.
- Shared scaffolding is allowed only when required to satisfy Priority 1.
- **When P10 becomes DONE, do not stop for a new prompt. Immediately transition to Priority 2 (P11-P15) and continue the same TDD/eval/adversarial loop until `P15 — Full V1 Acceptance` is legitimately DONE.**

## Blockers
A blocker is genuine only if it depends on something unavailable outside the repository/environment, such as missing credentials, unavailable legal source data, or a required human adjudication. When blocked:
- record the exact blocker and evidence;
- continue all other non-blocked work in the active priority;
- if the blocked item does not prevent later Priority 1 implementation, continue later Priority 1 work while preserving the earlier gate's truthful status;
- after P10, apply the same rule inside Priority 2;
- never stop merely because one subtask or one tracker phase is blocked.

## Stop conditions
Do not voluntarily stop merely to report that a phase completed. Stop only when one of these is true:
- P15 full V1 acceptance is DONE;
- the user explicitly asks to stop;
- a genuine external blocker prevents **all** remaining useful work;
- the execution environment/session itself ends.

## Completion discipline
Never equate compilation, a green unit test, or a partially working screen with acceptance. A tracker gate is DONE only when every exit criterion and required runtime/eval evidence passes.

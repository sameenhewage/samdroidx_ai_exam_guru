---
name: loop-engineering
description: Mandatory continuous engineering loop for AI Exam Guru. Use for every implementation, refactor, defect fix, integration, acceptance, or documentation task. Requires verified change-log entries, cohesive commits, and authorized post-commit pushes while preserving the priority gates.
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
11. Add a dated entry for every completed cohesive change to the **Change log** section of `docs/v1/PHASE_TRACKER.md`; update acceptance statuses only when their evidence justifies it.
12. Review the diff and stage only the related implementation, tests and change-log entry, then commit with a meaningful message.
13. Push the verified commit to the current branch's configured upstream under the repository owner's standing authorization, then verify the remote branch contains it.
14. Immediately continue with the next highest-value non-blocked item. Do not wait for another phase prompt.

## Mandatory change log
- The canonical change log is `docs/v1/PHASE_TRACKER.md#change-log`. Keep newest entries first and preserve prior evidence.
- Record every completed fix, feature, refactor, test/eval change, configuration/infrastructure change, and documentation/skill update. Record one entry per cohesive change, not one entry per editor operation.
- Each entry includes the date, what changed and why, affected paths, exact verification commands and outcomes, relevant limitations/skips, and existing commit references when known.
- Include the entry in the same commit as the change. Never invent the hash of the commit being created or create another commit solely to record its own hash; Git history supplies that link. If an earlier change was committed without a log, add a backfill entry referencing that existing commit.
- For documentation-only changes, record documentation/skill validation rather than claiming application tests were rerun. Never log secrets, credentials, private source text, or raw provider payloads.
- A change-log entry does not close an acceptance gate, prove remote CI success, or authorize publication to students.

## Mandatory post-commit push
- The repository owner explicitly requested automatic pushes after commits on 2026-09-05. Treat this as standing authorization for ordinary pushes of verified work to the current branch's configured upstream; a later no-push instruction or higher-priority restriction takes precedence.
- Before pushing, inspect branch/upstream status and the outgoing commits. Never include unrelated uncommitted work, create a remote, change Git configuration, or choose an unknown destination silently.
- After each verified cohesive commit, perform a normal push and verify the remote ref contains the commit. Report the branch and result; do not equate a successful push with a passing remote CI run.
- If authentication, permissions, missing upstream configuration, branch protection, or a non-fast-forward rejection blocks the push, preserve the local commit and report the exact blocker. Never force-push, rewrite history, or bypass hooks/security controls to make the push succeed.

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
Never equate compilation, a green unit test, or a partially working screen with acceptance. A tracker gate is DONE only when every exit criterion and required runtime/eval evidence passes. Do not finish a completed change without its change-log entry and verified commit/push outcome, or an explicit reported push blocker or no-push instruction.

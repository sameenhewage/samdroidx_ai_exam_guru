---
name: student-exam-product
description: Use only after P10 Priority 1 is DONE, for student identity/entitlements, published-paper catalog, exam runner, autosave/timer/reconnect, deterministic marking, skill analytics, wrong-answer review, progress dashboards, and next-paper recommendations.
---

# Student Exam Product

## Hard prerequisite
Before using this skill for product implementation, independently verify `P10 — Priority 1 Full Acceptance` is DONE with valid evidence in `docs/v1/PHASE_TRACKER.md`. If not, do not implement Priority 2; return to Priority 1.

## Product rules
- Serve only immutable published paper versions.
- Never leak answer keys before the allowed result/review state.
- Student runtime must not depend on a live LLM call.
- Attempt creation, answer saves and submission must be idempotent and auditable.
- Preserve answers across refresh/reconnect and make timer semantics explicit/server-verifiable where needed.
- Marking for supported objective question types is deterministic.
- Skill/competency analytics derive from reviewed question metadata and tested formulas.
- Avoid false precision for weak-skill conclusions with too little evidence.
- Recommendation baseline is deterministic before any V2 tutor/adaptive AI is introduced.
- Keep V2 scope out: no AI tutor, story teaching, voice tutor or live personalized full-paper generation.

## UX
Design for Grade 5 students and parents:
- clear start/resume/submit states;
- readable question navigation;
- answered/unanswered/flagged states;
- accessible controls and keyboard behavior;
- network failure/retry/reconnect feedback;
- clear results and skill trends without misleading claims.

## Testing
Always pair with `loop-engineering` and `tdd-eval-engineering`, plus frontend/backend/security skills as applicable.
Required coverage includes:
- entitlement/access tests;
- unpublished-paper denial;
- answer-key leakage tests;
- idempotent save/submit tests;
- reconnect/resume E2E;
- timer boundary behavior;
- deterministic marking fixtures;
- analytics/recommendation formula tests;
- accessibility and browser E2E;
- performance/security checks for full V1 acceptance.

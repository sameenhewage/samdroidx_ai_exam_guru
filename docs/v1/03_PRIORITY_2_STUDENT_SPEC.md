# Priority 2 Specification — Student Product

> Priority 2 is blocked until `P10 — Priority 1 Full Acceptance` is DONE in `PHASE_TRACKER.md`.

## Goal
Expose only reviewed/published Grade 5 practice papers to subscribed students, capture trustworthy attempt data, mark supported questions, and show skill-level progress without depending on live LLM availability.

## Student journey
`sign in -> entitlement check -> browse published papers -> start attempt -> answer/autosave -> submit -> mark -> skill analysis -> progress -> recommended next paper`

## 1. Identity and entitlement
- student account/profile;
- Grade 5 context;
- subscription/entitlement model;
- free/sample paper support;
- premium access rules;
- server-side authorization for paper access.

## 2. Published paper catalog
Student-facing catalog can only expose `PUBLISHED` immutable paper versions.

Show at minimum:
- paper title/version;
- Paper I/Paper II metadata;
- duration;
- total marks;
- completion/attempt state;
- availability/entitlement.

Do not expose internal forecast confidence, generation prompts, reviewer metadata or unpublished content.

## 3. Exam runner
Required behavior:
- start/resume attempt;
- server-authoritative attempt lifecycle;
- timer behavior defined and tested;
- question navigation;
- answered/unanswered state;
- flag/review state;
- autosave;
- idempotent answer updates;
- network interruption recovery where practical;
- explicit final submission;
- submitted attempt becomes immutable except through controlled admin correction policy.

## 4. Marking
V1 should prefer deterministic marking for supported question types.

Store:
- selected/provided answer;
- correctness;
- awarded marks;
- competency/skill/sub-skill snapshot from the published question version;
- submission timestamps;
- time-spent telemetry where reliable.

A later LLM-based free-text marker is not required for V1.

## 5. Result analysis
After submission show:
- overall score/percentage;
- paper/section score;
- competency breakdown;
- skill/sub-skill breakdown where data volume is sufficient;
- wrong-question review;
- comparison with the student's own previous performance.

Avoid over-interpreting tiny samples. Skill metrics must carry enough attempt/question-count context to avoid presenting false precision.

## 6. Progress dashboard
Track:
- score history;
- rolling skill performance;
- attempts completed;
- improvement/decline trend;
- weak/strong areas;
- recent paper outcomes.

Progress aggregation must be deterministic and unit-tested.

## 7. Recommendation baseline
V1 recommendation engine is deterministic, not an AI tutor.

Possible inputs:
- weakest skills with sufficient evidence;
- recent incorrect-question clusters;
- published paper coverage;
- already completed papers;
- desired balanced curriculum exposure.

Output examples:
- recommended next full paper;
- recommended focused practice set if such sets exist.

## 8. V1 student non-goals
- conversational tutoring;
- story-based explanation;
- personalized AI remediation;
- voice tutor;
- live paper generation;
- parent chat assistant;
- exact rank/scholarship outcome prediction.

## 9. Reliability/security
- student cannot access unpublished paper versions;
- answer keys are never sent before permitted result state;
- authorization is enforced server-side;
- autosave endpoints are idempotent;
- duplicate submission is safe;
- attempt data has auditability;
- privacy/security review is required before full V1 release.

## 10. Priority 2 acceptance demo
A subscribed Grade 5 student can open a published paper, complete it through a resilient exam runner, submit once, receive correct deterministic marking and skill analytics, then see progress/recommendation data across repeated attempts — while the AI provider may be completely unavailable.
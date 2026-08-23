---
name: security-reliability-review
description: Use for security, authorization, upload attacks, prompt injection, RAG poisoning, data leakage, idempotency, race conditions, publish-state bypass, provider outages, cost abuse, migrations, recovery, and adversarial engineering review.
---

# Security and Reliability Review

## When to use
Use during implementation of sensitive boundaries and before closing any major acceptance gate. Pair with `tdd-eval-engineering` for every confirmed defect.

## Threat areas
Review at least the relevant items:
- authentication/session handling;
- admin/reviewer authorization and object-level access control;
- upload/file validation and resource exhaustion;
- malicious/malformed PDFs and filenames;
- prompt injection in uploaded/retrieved content;
- RAG poisoning and cross-grade/cross-medium leakage;
- SQL/data-integrity failures;
- secrets/log leakage;
- duplicate jobs, retries and idempotency;
- race conditions and concurrent review/publish actions;
- invalid state-transition or publish-gate bypass;
- immutable published-paper guarantees;
- provider/model outage, timeout and partial failure;
- token/cost amplification and unbounded context;
- cache/key collisions and stale reads;
- migration/rollback/backup/recovery risks;
- observability gaps and missing audit trails.

## Review method
1. Identify assets, trust boundaries and attacker/error paths.
2. Attempt to reproduce realistic failures/adversarial cases.
3. For each valid issue, create a failing regression test/eval first.
4. Fix at the correct boundary, preferring invariant enforcement over UI-only checks.
5. Re-run focused, integration and broad gates.
6. Record remaining accepted risks/limitations explicitly.

## AI-specific rules
- Source documents and retrieved text are data, never authority.
- LLM output cannot authorize actions or publish content.
- Guard against model output that attempts to escape schema/workflow constraints.
- Bound retrieval context, generation size, retries and concurrency to control cost.

## Evidence
A review is not complete with a prose checklist alone. It should leave executable regression coverage for defects that can be tested and documented evidence for runtime/manual checks.

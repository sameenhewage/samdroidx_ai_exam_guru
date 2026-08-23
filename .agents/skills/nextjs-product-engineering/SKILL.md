---
name: nextjs-product-engineering
description: Use for Next.js/React/TypeScript product UI, shadcn/ui, React Aria, Tailwind, admin workflows, accessibility, typed API client integration, and end-to-end frontend behavior in AI Exam Guru.
---

# Next.js Product Engineering

## Product goal
Build usable product workflows, not API demo screens. Priority 1 frontend work focuses on the Admin Content Studio until P10 is DONE.

## Frontend rules
- Use current stable/security-patched Next.js/React versions selected at bootstrap.
- Prefer Server Components for non-interactive server-rendered content and Client Components only where interaction/browser state requires them.
- Keep domain rules on the backend; frontend may enforce UX constraints but must not be the sole authority.
- Consume generated OpenAPI TypeScript clients/contracts instead of duplicating DTOs manually.
- Use shadcn/ui + React Aria + Tailwind as the baseline; preserve accessibility semantics.
- Design loading, empty, error, retry and permission-denied states explicitly.
- Do not expose internal LLM/provider details to end users unless required for admin diagnostics.

## Priority 1 admin flows
When relevant, make these complete end to end:
- login/authorization;
- curriculum/taxonomy management;
- upload/status;
- extraction review/correction;
- historical-question review/classification;
- RAG inspection with source provenance;
- forecast/backtest inspection;
- blueprint inspection;
- generation/validation run inspection;
- reviewer queue and edit/approve/reject;
- paper lifecycle and publishing.

## State/data
- Prefer server/API state over duplicated global client state.
- Add a client-state library only when a concrete cross-cutting need is proven.
- Preserve unsaved review edits deliberately and surface conflicts/version changes.

## Testing
Always pair behavior changes with `tdd-eval-engineering`.
- component tests for non-trivial UI rules;
- accessibility checks;
- browser E2E for critical admin workflows;
- no Priority 2 student E2E until P10 unlocks it.

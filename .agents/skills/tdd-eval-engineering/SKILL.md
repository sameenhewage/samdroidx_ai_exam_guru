---
name: tdd-eval-engineering
description: Mandatory test-first and evaluation-first workflow for AI Exam Guru. Use whenever code behavior, RAG behavior, AI behavior, parsing, forecasting, validation, API contracts, or bug fixes are changed.
---

# TDD and Eval Engineering

## Core rule
Behavior changes require evidence before implementation.

### Deterministic code
Use RED -> GREEN -> REFACTOR:
1. Write or reproduce a failing test.
2. Confirm the failure is for the intended reason.
3. Implement the smallest correct change.
4. Confirm focused tests pass.
5. Refactor without changing behavior.
6. Run integration and regression gates.

### AI / RAG / probabilistic behavior
Use EVAL -> BASELINE -> IMPROVE -> REGRESSION:
1. Add a fixed eval case before changing prompts/retrieval/model behavior.
2. Record current baseline result and configuration.
3. Define measurable success criteria.
4. Implement/tune the smallest change.
5. Re-run the same eval set and compare against baseline.
6. Preserve the case as a regression eval.

## Required test layers
Use the layers relevant to the change:
- unit tests for deterministic rules;
- property/boundary tests for blueprint and scoring rules;
- API contract tests for FastAPI/OpenAPI;
- integration tests with real PostgreSQL + pgvector and Valkey containers;
- migration tests from a clean database;
- document-ingestion fixtures;
- RAG expected-source evals;
- deterministic fake-provider tests for LLM adapters and structured outputs;
- opt-in live-model evals for model quality/cost/latency;
- admin end-to-end tests for Priority 1;
- security/adversarial regression tests.

## Defect rule
Every valid bug, review finding, prompt-injection case, retrieval regression, race, invalid state transition, or hallucination pattern must become a failing regression test/eval **before** the fix.

## Paid-model rule
Normal CI must not depend on a paid external LLM. Use deterministic fakes for CI and maintain separate opt-in live-model benchmark/eval commands.

## Evidence
When closing work, record:
- tests/evals added;
- exact commands run;
- pass/fail counts;
- relevant quality metrics;
- model/provider/prompt/retrieval versions for live evals;
- cost and latency where applicable.

Never weaken a valid test to make CI green.

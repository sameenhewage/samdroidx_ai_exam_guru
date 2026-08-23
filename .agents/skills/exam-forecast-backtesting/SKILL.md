---
name: exam-forecast-backtesting
description: Use for historical paper analytics, practice-priority scoring, forecasting, rolling held-out backtests, baseline comparison, calibration, and evidence-backed exam intelligence in AI Exam Guru.
---

# Exam Forecasting and Backtesting

## Product rule
The system does not claim exact future questions. Forecasting produces evidence-backed **practice priorities**, topic/skill weights and paper-blueprint guidance.

## Deterministic-first approach
Start with explainable statistical/domain features before introducing ML:
- syllabus/competency importance;
- historical occurrence frequency;
- marks/weight distribution;
- question-archetype frequency;
- recency/gap features;
- difficulty distribution;
- coverage/rotation indicators where evidence supports them.

The LLM must not invent forecast scores.

## Backtesting
Use rolling historical holdouts. Example pattern:
- train/evidence through year N-1;
- forecast year N;
- compare with actual year N;
- roll forward and repeat.

Never leak held-out paper metadata/content into the forecast calculation for that year.

## Baseline
Always compare against a simple syllabus-balanced baseline. If the forecast method does not produce useful improvement or calibration over the baseline, document the result and use the safer baseline for blueprinting.

## Metrics
Define and persist metrics such as:
- competency/skill coverage hit rate;
- distribution distance/error for marks and difficulty;
- question-archetype coverage;
- ranking/calibration metrics for practice-priority scores;
- variance across years.

Do not cherry-pick one successful year.

## Reproducibility
Every forecast run records:
- input paper/source versions;
- algorithm/config version;
- feature definitions;
- random seed if applicable;
- output scores/rankings;
- backtest metrics;
- baseline metrics.

## Tests
Pair with `tdd-eval-engineering`.
Use boundary/property tests for scoring rules and fixed historical/synthetic fixtures for leakage and rolling-window correctness. Real exam-quality claims require real reviewed historical data, not synthetic fixtures alone.

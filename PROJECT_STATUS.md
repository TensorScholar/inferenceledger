# Project Status

## Current State

This repository has completed **Phase 0: repo repair and truth reset** and now has the first concrete Phase 1 implementation slices.

The previous status language claimed production readiness and complete infrastructure. That is no longer treated as accurate. The project is being narrowed into an honest SLO-aware LLM inference gateway and benchmark lab.

## What Is True Now

- The strategy, architecture, roadmap, benchmark plan, and Codex quality workflow are documented under [docs/](./docs/README.md).
- Early domain primitives for batching, caching, routing, and cost calculation are importable.
- OpenAI-compatible provider execution is implemented with bounded retries, timeout configuration, cancellation propagation, normalized provider errors, and usage extraction.
- Cost accounting uses a versioned model pricing table, including confirmed FreeModel model IDs, and fails on unknown pricing instead of inventing a value.
- Request traces can be appended to a local JSONL ledger.
- `inference-smoke` can make one real provider call when `OPENAI_API_KEY` is set.
- `/v1/inference` can execute the same OpenAI-compatible provider adapter path when `OPENAI_API_KEY` is set.
- `scripts/run_benchmark.py run` can replay `benchmarks/workloads/smoke.jsonl`, write a JSON report, and store run data in a local SQLite ledger.
- `scripts/run_benchmark.py compare` can compare two stored run summaries from the SQLite ledger.
- Workload rows can declare deterministic quality validators: JSON keys, JSON field equality, exact match, and required substrings.
- Benchmark reports include quality count, pass count, pass rate, and average deterministic score.
- Comparisons are not marked comparable when candidate quality pass rate is below baseline.
- Deterministic `single_model` and `rule_based` baseline routing modes are implemented for future comparisons.
- Benchmark runs record route decisions in JSONL and SQLite, including model choice, reason, considered/fallback models, estimated latency, and estimated cost.
- Benchmark runs can enforce `--max-estimated-cost-usd` before provider execution; budget violations are recorded without charging provider calls.
- `scripts/run_benchmark.py export` can export a stored run as JSON and Markdown evidence.
- Benchmark reports include model distribution, route reason distribution, and observed p50/p95 latency by model.
- Deterministic `policy` routing is available for benchmark runs with explicit cost budget, latency SLO, quality floor, and auditable reason codes.
- Provider attempt and retry counts are recorded on responses, request traces, and benchmark summaries.
- SQLite benchmark runs store queryable provider usage rows and aggregate usage summaries by run.
- Markdown benchmark exports include provider usage summaries with model-level cost and token breakdowns.
- A skipped real-provider integration test can validate usage metadata, cost accounting, latency, and retry telemetry when `OPENAI_API_KEY` is set.
- The smoke workload now uses five deterministic JSON-contract tasks across JSON contract, classification, extraction, arithmetic, and intent cases.
- A reviewed single-model FreeModel `gpt-5.4-mini` smoke evidence artifact is committed under [benchmarks/reports/](./benchmarks/reports/).
- A reviewed baseline-vs-policy FreeModel comparison artifact is committed under [benchmarks/reports/](./benchmarks/reports/), with candidate quality held at 100% on the smoke workload and measured provider cost reduced from $0.00371000 to $0.00111450.
- GitHub Actions CI runs lint, type checking, and tests without provider calls.
- Local `.venv` gates pass for tests, lint, typecheck, and import smoke.

## What Is Not Implemented Yet

- Deadline-aware fallback policy constraints and observed-profile adaptation.
- Broader published savings reports beyond the five-request JSON-contract smoke workload.
- Semantic quality evaluation beyond simple deterministic validators.
- Eval-aware routing.
- Async batch lane.
- Prompt cache advisor.
- Local vLLM lane.
- Production deployment.

## Phase 0 Acceptance Criteria

- `python -c "import inference_engine"` succeeds. Done.
- `.venv/bin/python -m pytest` collects and runs the current tests. Done.
- `.venv/bin/python -m ruff check src tests` passes. Done.
- `.venv/bin/python -m mypy src` passes. Done.
- Public documentation no longer claims unsupported production readiness. Done.
- Tooling configuration exists for strict lint/type/test checks. Done.

## Current Verification

- `.venv/bin/python -m ruff check src tests`: passed.
- `.venv/bin/python -m mypy src`: passed, 83 source files.
- `.venv/bin/python -m pytest`: passed, 85 tests, 1 skipped credential-gated provider test.
- `.venv/bin/python scripts/run_benchmark.py run --workload benchmarks/workloads/smoke.jsonl --strategy single_model --model gpt-5.4-mini ...`: passed with 5/5 successes, 5/5 deterministic quality checks, zero retries, and real provider usage.
- `.venv/bin/python scripts/run_benchmark.py compare --baseline-run-id baseline-json-contract-gpt-5-4-20260703 --candidate-run-id policy-json-contract-freemodel-20260703 ...`: passed with `comparable=true`, cost delta -69.96%, p95 latency delta -2907 ms, and no quality pass-rate regression.

## Source Of Truth

Use these documents for future work:

- [Strategy Brief](./docs/00_STRATEGY_BRIEF.md)
- [Target Architecture](./docs/01_TARGET_ARCHITECTURE.md)
- [Implementation Roadmap](./docs/02_IMPLEMENTATION_ROADMAP.md)
- [Benchmark And Eval Plan](./docs/03_BENCHMARK_AND_EVAL_PLAN.md)
- [Codex Quality System](./docs/04_CODEX_QUALITY_SYSTEM.md)

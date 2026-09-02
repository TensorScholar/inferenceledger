# Project Status

Status date: 2026-09-03

## Current state

InferenceLedger has completed a new GitHub forensic and market truth reset.

The previous active thesis — an SLO-aware LLM gateway and benchmark lab — is no longer the
canonical product direction. Current market evidence shows that generic routing, gateway
abstraction, retries/fallbacks, cost observability, model experiments, and CI regression gates are
already materially covered by existing platforms.

The current product hypothesis is **vendor-neutral inference migration assurance**: reproducible
economic and SLO evidence for changes to model/provider/execution policy, complementary to existing
gateways, routers, observability systems, evaluation platforms, and FinOps systems.

This hypothesis is not customer validated.

## Proven on GitHub

- `src/inference_engine` is the only full source tree on current `main`; the top-level
  `inference_engine/__init__.py` is a small development import shim, not a duplicate
  implementation tree.
- GitHub CI exists and the audited `main` commit `e3eadc34a84d334bcd99132468e892344f8092dd`
  had a successful CI run.
- Historical July 2026 benchmark artifacts exist for a five-request deterministic JSON-contract
  workload and record zero provider retries in the compared runs.

## Current P0 correctness gaps

Before the repository can support the new thesis, these are release-blocking evidence defects:

1. provider retries/fallbacks are represented as counts on a request trace rather than durable
   attempt records;
2. failed request traces currently encode zero usage and zero cost;
3. benchmark aggregate cost is calculated from successful traces only;
4. pricing is keyed by model plus a global table version and lacks provider/SKU/effective-period
   provenance sufficient for reproducible cross-provider migration claims.

Unknown or potentially billable execution cost must not be represented as zero.

## Current P1 product/architecture gaps

- distribution/product/CLI identity remains inconsistent (`InferenceLedger` repository versus
  `cost-optimized-inference`, `inference_engine`, and `inference-smoke`);
- benchmark comparison currently requires baseline and candidate to use the same provider;
- workload tags are not used for segment-level comparison;
- evidence classes such as observed execution, controlled replay, shadow execution, and estimated
  counterfactual are not encoded;
- `scripts/run_benchmark.py` owns too much orchestration directly;
- gateway-era routing/batching/caching/fallback abstractions require use/deletion audit.

## Historical evidence classification

The July 2026 smoke benchmark is retained as **PROSPECTIVELY OBSERVED engineering evidence for a
narrow reference benchmark run**, not as commercial validation.

Its cost field is a calculation from provider-reported usage and the repository pricing table. It
is not provider invoice/billing proof. The sample is five requests, quality is deterministic
contract validation, and the runs contained zero recorded retries.

It does not validate production scale, generalized savings, cross-provider migration, retry/fallback
economics, or customer demand.

## Canonical source of truth

Read in this order:

1. [Strategy Brief](./docs/00_STRATEGY_BRIEF.md)
2. [Market and Product Decision](./docs/05_MARKET_AND_PRODUCT_DECISION_2026-09.md)
3. [Canonical Project Map](./docs/06_CANONICAL_PROJECT_MAP.md)
4. [Documentation Index](./docs/README.md)

Older gateway-oriented architecture/roadmap documents remain useful implementation references only
where they do not conflict with these documents.

## Next engineering bottleneck

Implement the minimum durable execution-attempt and cost-evidence model needed to stop treating
failed or retried execution economics as request-level zero-cost metadata.

Do not add routing features, providers, dashboards, or infrastructure before this evidence boundary
is correct.

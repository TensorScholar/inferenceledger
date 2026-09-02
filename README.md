# InferenceLedger

InferenceLedger is an engineering project for **vendor-neutral inference migration assurance**.

Its target question is not "which model is cheapest?" and it is not intended to become another
generic LLM gateway.

Instead:

> Before an AI platform team changes a model, provider, routing policy, fallback chain, pricing
> assumption, or execution mode, can it produce reproducible evidence that the change is
> economically and operationally acceptable for its workload?

## Strategic status

The previous product thesis was an SLO-aware LLM gateway and benchmark lab. Current market review
found that routing, retries/fallbacks, spend controls, observability, canary rollout, experiments,
and generic regression gates are already well served by gateways, cloud routers, and evaluation
platforms.

The repository is therefore being consolidated around a narrower wedge:

**controlled migration evidence + execution-attempt economics + pricing provenance + workload SLO
constraints + post-change revalidation.**

This direction is **not yet customer validated** and should not be described as commercially proven.

See:

- [Strategy Brief](./docs/00_STRATEGY_BRIEF.md)
- [Market and Product Decision](./docs/05_MARKET_AND_PRODUCT_DECISION_2026-09.md)
- [Canonical Project Map](./docs/06_CANONICAL_PROJECT_MAP.md)

## What should remain from the existing system

The current OpenAI-compatible execution path, routing code, benchmark harness, deterministic evals,
SQLite/JSON evidence, and pricing code are useful only where they support the new evidence model.

The gateway can remain as a **reference executor** for controlled replay and smoke tests. It is no
longer the product boundary.

## Required evidence boundary

A defensible migration artifact must be able to distinguish:

- observed execution;
- controlled replay;
- shadow execution;
- estimated counterfactual;
- provider-reported charge;
- calculated cost from observed usage and an identified price record;
- estimated cost;
- unknown or partial billing evidence.

Unknown cost must never be silently converted to zero.

The target execution model is request/execution -> individual provider attempts -> outcome, so
retry and fallback economics can be reconstructed rather than represented only by counters.

## Current implementation baseline

The repository currently has:

- an OpenAI-compatible provider adapter with bounded retry and normalized provider errors;
- a FastAPI `/v1/inference` reference path;
- deterministic `single_model`, `rule_based`, and policy routing used by the benchmark harness;
- JSONL request/route logs and a SQLite benchmark ledger;
- deterministic workload-declared quality checks;
- JSON/Markdown benchmark exports;
- pricing-based cost calculation from provider usage metadata;
- `ruff`, `mypy`, and `pytest` CI.

These are engineering assets, not proof that the migration-assurance product is complete.

## Current P0 correctness gaps

The present ledger is still request-centric:

- retry/fallback attempts are not durable first-class records;
- failed request traces currently encode zero usage/cost;
- aggregate benchmark cost is calculated from successful traces only;
- pricing is model-keyed rather than provider/SKU/effective-period/provenance aware;
- cross-provider benchmark comparison is currently rejected;
- workload tags are not yet used for segment-level regression analysis.

Until these are corrected, the repository must not claim complete failure economics or billing-grade
migration savings.

## Historical engineering evidence

The committed July 2026 smoke artifacts are retained as narrow historical evidence for the old
reference benchmark path. They used five deterministic JSON-contract tasks and one
OpenAI-compatible endpoint.

The baseline-vs-policy artifact observed 5/5 successful requests in each run, 100% deterministic
validator pass rate, and lower **calculated cost from provider-reported usage plus the repository
pricing table** for the candidate run. There were zero recorded provider retries in those runs.

That artifact does **not** validate:

- broad semantic quality;
- production behavior;
- cross-provider migration;
- retry/fallback economics;
- the new commercial product thesis.

## Development baseline

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev,providers]"
make check
```

Live reference-provider calls additionally require locally configured provider credentials. Secrets
must not be committed.

## Near-term engineering order

1. Introduce first-class attempt-chain evidence and explicit unknown/partial cost semantics.
2. Make pricing records provider-aware, effective-dated, and provenance-bearing.
3. Consolidate benchmark orchestration behind one application path.
4. Support cross-provider and segment-aware migration comparison.
5. Define minimal economic/SLO release criteria.
6. Execute one frozen real migration pilot.
7. Integrate with one existing execution stack rather than adding more gateway features.

Broad provider support, dashboard work, Kubernetes, and generic evaluation expansion are deferred.

## Evidence policy

Never fabricate or imply production/customer validation. Use explicit evidence classes and scope
claims to what was actually executed and preserved.

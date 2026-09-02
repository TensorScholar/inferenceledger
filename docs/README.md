# InferenceLedger Documentation

InferenceLedger is being consolidated around **vendor-neutral inference migration assurance**:
reproducible economic and SLO evidence for model/provider/execution-policy changes.

The existing gateway is reference infrastructure, not the product thesis.

## Canonical documents

- [00 Strategy Brief](./00_STRATEGY_BRIEF.md): current product boundary, user, commercial trigger, evidence semantics, and engineering order.
- [05 Market and Product Decision — September 2026](./05_MARKET_AND_PRODUCT_DECISION_2026-09.md): current market challenge, competitor/substitute analysis, selected wedge, and ranked findings.
- [06 Canonical Project Map](./06_CANONICAL_PROJECT_MAP.md): repository identity, source ownership, legacy/reconciliation state, cleanup classification, and target dependency direction.

## Engineering documents under migration

These documents contain useful implementation detail from the former gateway-first thesis, but
must not override the canonical product decision above:

- [01 Target Architecture](./01_TARGET_ARCHITECTURE.md): existing gateway/reference-executor architecture; to be revised as economic evidence ownership is implemented.
- [02 Implementation Roadmap](./02_IMPLEMENTATION_ROADMAP.md): legacy gateway roadmap; superseded where it conflicts with `00`, `05`, or `06`.
- [03 Benchmark And Eval Plan](./03_BENCHMARK_AND_EVAL_PLAN.md): useful benchmark/eval mechanics; must evolve to migration evidence and segment/SLO semantics.
- [04 Codex Quality System](./04_CODEX_QUALITY_SYSTEM.md): engineering workflow guidance.

## Evidence principle

If a claim cannot be reconstructed from frozen inputs, actual execution/attempt evidence,
identified pricing assumptions, quality evidence, and a generated artifact, the claim must be
weakened or omitted.

An unknown or potentially billable execution cost must never be silently represented as zero.

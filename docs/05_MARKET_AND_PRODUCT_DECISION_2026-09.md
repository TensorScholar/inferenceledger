# Market Challenge and Product Decision — September 2026

## Decision

InferenceLedger should **not** remain a generic LLM gateway or model router.

The gateway/router market already contains mature provider abstraction, retries, fallbacks,
load balancing, budget enforcement, canary rollout, routing, cost telemetry, and observability.
The broader idea of "run a baseline and candidate, compare quality/cost/latency, and gate a
release" is also not unique: major cloud and evaluation platforms now document or implement
substantial parts of that workflow.

The product direction is therefore narrowed to:

> **Vendor-neutral inference migration assurance with reproducible economic and SLO evidence.**

The product should answer a specific operational question:

> Before an AI platform team changes a model, provider, routing configuration, fallback chain,
> pricing assumption, or execution mode, can it produce inspectable evidence that the migration
> is economically and operationally acceptable for its workload — including the full execution
> attempt chain and the unknowns that prevent a safe claim?

This is a hypothesis to validate through implementation and pilots, not a claim of category
ownership.

## What the market already solves

### Gateway and routing infrastructure

These capabilities are not a defensible core product:

- unified provider APIs;
- model/provider routing;
- retries and fallbacks;
- load balancing;
- rate and budget limits;
- canary or percentage rollout;
- route versioning and rollback;
- request-level cost dashboards;
- generic OpenTelemetry tracing.

Examples:

- LiteLLM provides a unified interface to 100+ providers, routing with retries/fallbacks,
  application-level load balancing, cost tracking, and proxy spend controls.
- Cloudflare AI Gateway provides versioned dynamic routes, conditional and percentage routing,
  budget/rate-limit nodes, fallbacks, gradual rollout, and rollback.
- Portkey provides retries, fallbacks, load balancing, canary testing, budgets, tracing, and
  model/provider cost analytics. It can group all attempts in a retry/fallback chain by trace.
- Amazon Bedrock Intelligent Prompt Routing optimizes within supported model families and exposes
  configured routing criteria.
- Microsoft Foundry Model Router exposes workload-oriented routing modes and model subsets.

### Evaluation and release gating

Generic "change comparison" is also not enough:

- Microsoft Foundry documents comparing a router against a meaningful baseline using a
  representative workload and workload-specific quality, cost, latency, and policy criteria;
  it explicitly recommends segment analysis, tail latency, production-like validation, and
  re-evaluation after routing, model-set, traffic-mix, application, or pricing changes.
- Braintrust supports immutable experiments, baseline comparison, cost/latency/error metrics,
  per-input regressions, and CI gates.
- Langfuse supports controlled experiments, model/prompt/code comparisons, production traces,
  evaluators, and CI/CD regression gates.

Therefore InferenceLedger must not position ordinary model comparison, generic evaluation, or a
GitHub check as unique.

## Where a narrower gap remains

The strongest remaining wedge is the junction between three systems that are usually separate:

1. **Execution infrastructure** knows what actually ran, including retries/fallbacks.
2. **Evaluation infrastructure** knows whether outputs remained acceptable.
3. **FinOps/billing systems** know or estimate what the execution cost.

A migration decision needs all three at the same evidence boundary.

InferenceLedger should specialize in the missing join:

- controlled baseline/candidate execution or imported execution evidence;
- request -> attempt-chain reconstruction;
- provider/model/execution-mode-specific usage;
- effective-dated pricing provenance;
- explicit distinction between provider-reported charge, calculated cost, and unknown billing;
- retry/fallback cost and latency consequences;
- cost per accepted/successful workload outcome;
- workload-segment SLO and quality constraints;
- baseline/candidate comparison across providers and execution stacks;
- evidence completeness and uncertainty;
- pre-deployment decision artifact;
- canary/post-change comparison against the pre-deployment claim;
- revalidation triggers when prices, models, policies, reliability, or workload mix change.

The key differentiation target is not "we collect cost and evals." It is:

> **Can the economic migration claim be reconstructed and challenged from raw execution evidence?**

## Evidence semantics

InferenceLedger must keep these classes separate:

- `OBSERVED_EXECUTION`: an execution that actually occurred.
- `CONTROLLED_REPLAY`: the candidate was actually executed against frozen input.
- `SHADOW_EXECUTION`: the candidate was actually executed alongside production-like traffic but
  did not serve the user-visible result.
- `ESTIMATED_COUNTERFACTUAL`: no candidate execution occurred; the result is modeled only.

A modeled counterfactual must never be reported as measured savings.

For cost, the system must distinguish at least:

- provider-reported charge, when the provider supplies authoritative charge data;
- calculated execution cost from observed usage and an identified price record;
- estimated cost from estimated usage or assumptions;
- unknown/partial cost when billing evidence is incomplete.

Unknown cost is **not zero cost**.

## Current repository contradiction

The current codebase does not yet support this thesis safely:

- `RequestTrace` is request-level and records retry counts rather than individual attempts.
- failed executions are currently created with zero tokens and zero cost.
- the benchmark report sums cost from successful traces only.
- pricing is keyed only by model and a global table version; it lacks provider/SKU/effective-period
  provenance sufficient for cross-provider historical reconstruction.
- benchmark comparison currently rejects different providers, which prevents the proposed
  provider-migration use case.
- workload tags are loaded but not used for segment-level comparison.
- the public product identity remains gateway-first.

These are product-correctness defects for the new direction, not cosmetic refactors.

## Commercial thesis

### Target user

Primary user:

- AI platform / LLM infrastructure engineer responsible for model/provider execution changes.

Adjacent users:

- SRE/platform engineer responsible for latency and provider reliability;
- AI FinOps engineer responsible for inference unit economics.

### Economic buyer

Likely buyer:

- head of AI platform, ML platform, infrastructure, or FinOps in an organization with material LLM
  spend or migration risk.

### Triggering events

The strongest buying triggers are concrete changes:

- model/provider deprecation;
- forced model-version migration;
- material inference-spend reduction mandate;
- provider contract or price change;
- introduction or replacement of an LLM gateway/router;
- reliability incident that changes retry/fallback policy;
- migration from hosted to self-hosted inference or the reverse;
- need to move a defined workload percentage to a cheaper execution path.

### Existing workaround

Teams can combine provider dashboards, gateway logs, evaluation experiments, spreadsheets, and
ad-hoc notebooks. The weakness is not absence of data. It is the lack of a small, reproducible
artifact that joins execution economics, SLO behavior, quality evidence, workload segmentation,
and provenance into a migration decision that can be rerun later.

### Measurable value

The product is valuable only if it changes a concrete migration decision, for example:

> "Move these workload segments to the candidate; keep these critical segments on the baseline;
> projected/observed monthly effect is stated with assumptions; retry/fallback and tail-latency
> effects are included; unknown billing or quality evidence blocks the claim."

No ROI or savings percentage should be claimed without executed evidence and a documented
extrapolation basis.

## Complement, do not replace

| Ecosystem | Compete or complement? | Potential InferenceLedger value |
| --- | --- | --- |
| LiteLLM | Complement | Evaluate a LiteLLM config/policy change using imported traces or controlled replay instead of replacing the proxy. |
| Cloudflare AI Gateway | Complement | Validate a dynamic-route version before/after rollout and independently reconstruct economic/SLO evidence. |
| Portkey | Complement with care | Consume retry/fallback traces and cost telemetry; add controlled migration acceptance and reproducible evidence semantics rather than duplicating gateway observability. |
| Amazon Bedrock | Complement | Provide application-specific cross-provider/migration evidence outside Bedrock's router constraints. |
| Microsoft Foundry | Complement only if narrower | Cross-stack economic provenance and execution-attempt assurance; do not duplicate Model Router evaluation. |
| Braintrust / Langfuse | Complement | Consume quality scores/experiment identifiers; own economic/SLO migration evidence rather than generalized evaluation. |
| OpenTelemetry | Build on | Ingest/export standard GenAI telemetry; add product-specific economic evidence only where standard telemetry is insufficient. |
| FinOps systems | Complement | Export workload/unit-economic evidence and pricing provenance; do not become a general cost-allocation platform. |

## Product boundary

### InferenceLedger owns

- inference migration economic evidence;
- execution-attempt economics;
- provider/model/routing/fallback change comparisons;
- latency and reliability acceptance constraints;
- workload-segment economic/SLO regressions;
- pricing provenance needed to reconstruct claims;
- release evidence for inference execution changes;
- post-change economic/SLO drift and revalidation.

### InferenceLedger does not own

- generic provider proxying as a product wedge;
- general-purpose model routing;
- general observability dashboards;
- generalized LLM evaluation;
- permission or authorization policy;
- general FinOps allocation/forecasting;
- agent behavioral regression outside inference economics/SLO.

## Ranked findings

### P0 — correctness / false-evidence risk

1. Request-level cost accounting cannot reconstruct retries/fallbacks or ambiguous failed-attempt
   billing; failed traces currently encode zero cost.
2. The benchmark summary excludes failed traces from cost totals, so failure-heavy policies can
   appear artificially cheap.
3. Pricing identity/provenance is insufficient for reproducible cross-provider historical cost.

### P1 — blocks differentiated product

1. Product documentation and architecture are still gateway/router-first.
2. Benchmark comparison requires the same provider and therefore cannot evaluate provider
   migration.
3. No segment-level comparison despite workload tags.
4. No explicit evidence class or completeness/unknown semantics.
5. Distribution/package/CLI identity does not match the repository/product name.

### P2 — commercial / partnership leverage

1. Add one external execution-stack adapter after the economic evidence model is correct.
2. Consume external quality evidence rather than expanding into a generalized evaluator.
3. Produce one migration evidence bundle that a platform/FinOps team can independently inspect.

### P3 — polish

- dashboard work;
- broad provider catalog;
- additional deployment packaging beyond what a validated integration requires.

### DELETE candidates

- unreferenced gateway-era abstractions after import/use audit;
- obsolete gateway positioning and roadmap claims;
- compatibility code without a test, owner, and explicit purpose.

### DEFER

- broad routing improvements;
- Kubernetes/microservices/control-plane work;
- generalized UI;
- multiple external integrations;
- generalized semantic evaluation.

## Sources reviewed

Primary product documentation reviewed during this decision includes:

- Microsoft Foundry Model Router evaluation and architecture documentation.
- Amazon Bedrock Intelligent Prompt Routing documentation.
- Cloudflare AI Gateway Dynamic Routing, Evaluations, and spend-limit documentation.
- LiteLLM documentation.
- Portkey routing, tracing, and cost-management documentation.
- Braintrust experiment comparison, provider benchmarking, CI/CD, and cost documentation.
- Langfuse experiment, evaluation, releases/versioning, and CI/CD documentation.
- OpenTelemetry GenAI semantic-convention documentation.
- FinOps Foundation GenAI cost tracking and Tokenomics guidance.

This document records a strategic decision based on publicly available documentation. It does not
claim customer validation, partnership interest, or competitive superiority.

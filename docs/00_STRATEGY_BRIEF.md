# Strategy Brief: InferenceLedger

## One-line positioning

InferenceLedger is a vendor-neutral **inference migration assurance** system: it produces
reproducible economic and SLO evidence for model, provider, routing, fallback, pricing, and
execution-policy changes without requiring teams to replace their existing gateway or evaluation
stack.

## Core decision

The repository should no longer treat "an SLO-aware LLM gateway that routes to cheaper models"
as the product.

Gateways, routers, observability platforms, and evaluation systems already solve large portions of
provider abstraction, routing, retries/fallbacks, cost telemetry, experiments, and regression
gating. InferenceLedger should use those systems where possible rather than duplicate them.

The product question is narrower:

> For our workload, is this inference execution change economically and operationally safe enough
> to ship, and can another engineer reconstruct the claim from the evidence?

See [Market Challenge and Product Decision](./05_MARKET_AND_PRODUCT_DECISION_2026-09.md) for the
market challenge that produced this decision.

## Target user

Primary user:

- AI platform / LLM infrastructure engineer making model/provider/execution changes.

Adjacent users:

- SRE/platform engineer responsible for inference latency and reliability;
- AI FinOps engineer responsible for inference unit economics.

## Economic buyer and trigger

Likely economic buyer:

- AI/ML platform, infrastructure, or FinOps leadership in an organization with material inference
  spend or migration risk.

High-value triggering events:

- model or provider deprecation;
- forced model-version migration;
- material spend-reduction mandate;
- provider price/contract change;
- gateway/router migration;
- retry/fallback policy change after a reliability incident;
- hosted <-> self-hosted migration;
- moving a defined workload segment to a cheaper model/provider.

## Product boundary

InferenceLedger owns:

- execution-attempt economics;
- model/provider/policy migration evidence;
- pricing provenance needed to reproduce cost claims;
- latency/reliability acceptance constraints;
- workload-segment economic/SLO regression evidence;
- pre-deployment economic release evidence;
- post-change economic/SLO drift and revalidation.

InferenceLedger does not own:

- generic provider proxying as a product wedge;
- generalized model routing;
- generalized LLM evaluation;
- a general observability dashboard;
- a general FinOps platform;
- permission/security policy owned by other TensorScholar products.

Quality remains required as a constraint on savings claims, but should normally be supplied by
narrow deterministic checks or external evaluation evidence rather than turning InferenceLedger
into ProofDiff or another eval platform.

## Core evidence model

The architecture must be able to represent:

```text
Frozen workload
    |
    +--> Baseline execution ----+
    |                           |
    +--> Candidate execution ---+--> Attempt-chain evidence
                                    + pricing provenance
                                    + latency/reliability
                                    + quality evidence references
                                    + workload segments
                                             |
                                             v
                                      Change comparison
                                             |
                                  SHIP / REVIEW / NO-GO
                                  only after criteria are defined
                                             |
                                      Canary observation
                                             |
                                       Revalidation
```

The release-decision vocabulary is not considered product-validated until the policy contract is
implemented and exercised against meaningful evidence.

## Evidence honesty

Distinguish:

- observed execution;
- controlled replay;
- shadow execution;
- estimated counterfactual.

An unexecuted candidate must never be described as measured.

Cost must distinguish provider-reported charge, calculated cost from observed usage, estimated
cost, and unknown/partial billing evidence.

Unknown cost is not zero cost.

## Economic accounting direction

A request is not the economic atomic unit when retries or fallbacks occur.

The product needs a durable attempt model so an execution can reconstruct the whole chain:

```text
request/execution
  -> attempt 1: provider/model/status/usage/price evidence
  -> attempt 2: retry ...
  -> attempt 3: fallback ...
  -> final outcome
```

Useful decision metrics may include total execution cost, cost per successful/accepted outcome,
tail latency, error rate, retry attempt rate, fallback cost contribution, and segment-specific
regressions. Each metric must be mathematically defined before it is used as a release criterion.

## Reference executor, not gateway product

The existing OpenAI-compatible provider path can remain as:

- a controlled replay executor;
- a smoke-test path;
- a development integration;
- a benchmark reference adapter.

It should not drive product scope. External gateways/routers are integration targets.

## Integration posture

Prefer adapters to infrastructure displacement.

Potential targets include LiteLLM, Cloudflare AI Gateway, Portkey, Amazon Bedrock, Microsoft
Foundry, OpenTelemetry-compatible telemetry, and external evaluation systems. Only one integration
should be implemented initially, selected after the economic evidence model is correct.

## Near-term engineering order

1. Freeze the canonical product and repository map.
2. Replace request-only cost semantics with attempt-chain evidence and explicit unknown cost.
3. Make pricing records provider-aware, effective-dated, and provenance-bearing.
4. Consolidate benchmark orchestration behind canonical application services.
5. Support cross-provider baseline/candidate comparison and segment-level constraints.
6. Define the minimal release policy contract and deterministic decision semantics.
7. Execute one real migration pilot with frozen evidence.
8. Add one external execution-stack integration.
9. Compare pre-deployment evidence with canary/post-change observations.
10. Add narrowly justified revalidation triggers.

Do not expand router features, provider count, dashboard scope, or deployment infrastructure ahead
of these items.

## Success standard

InferenceLedger becomes defensible only when it can produce an inspectable evidence artifact for a
real migration question and that artifact survives adversarial review of:

- what actually executed;
- what was only estimated;
- which attempts were charged or potentially charged;
- which price records were used;
- whether critical workload segments regressed;
- whether quality evidence was sufficient;
- whether tail latency/reliability constraints held;
- which unknowns prevent a stronger claim;
- whether post-change behavior remains consistent with the pre-deployment evidence.

Until then, market differentiation and commercial readiness remain **NOT VALIDATED**.

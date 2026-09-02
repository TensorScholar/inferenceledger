# Canonical Project Map

Status date: 2026-09-03

This map exists to prevent new work from being built on legacy identity or architecture ambiguity.
It describes GitHub `main` at commit `e3eadc34a84d334bcd99132468e892344f8092dd` and the intended migration direction. Local-only state is not inferred from GitHub.

## Canonical identity — current vs target

| Surface | Current GitHub state | Target | Status |
| --- | --- | --- | --- |
| Repository | `TensorScholar/inferenceledger` | `TensorScholar/inferenceledger` | canonical |
| Product name | `Cost-Optimized Inference` in README/docs | `InferenceLedger` | needs migration |
| Python distribution | `cost-optimized-inference` | `inferenceledger` unless compatibility evidence requires otherwise | needs migration |
| Python source package | `src/inference_engine` | decision deferred until import/compatibility audit | transitional |
| Top-level import shim | `inference_engine/__init__.py` | remove or retain only with explicit compatibility contract | needs audit |
| CLI | `inference-smoke` | `inferenceledger` with explicit subcommands | needs migration |
| API product surface | FastAPI `/v1/inference` | reference executor/integration surface, not product identity | retain narrowly |
| Benchmark naming | gateway/routing benchmark | migration/change evidence runs | needs migration |
| Evidence artifacts | benchmark JSON/Markdown | versioned migration evidence bundle | evolve |
| Release name | none published | `InferenceLedger` | clean opportunity |

There are no published GitHub releases on the audited repository, which lowers migration risk but
does not eliminate the need to inspect external package/import consumers before renaming the
Python module.

## GitHub source map

### Canonical active source

`src/inference_engine/` is the only full source tree on GitHub `main`.

It currently contains:

- `adapters/api/` — FastAPI surface;
- `application/` — DTOs and execution services;
- `benchmarking/` — workload, eval, comparison, SQLite ledger, exports;
- `domain/` — batching, caching, cost, models, routing;
- `infrastructure/` — provider backends and telemetry;
- `cli.py`, configuration, and utilities.

### Compatibility surface

`inference_engine/__init__.py` is a development import shim that appends
`src/inference_engine` to the package path so imports can work from a fresh checkout before an
editable install.

This shim has an explicit purpose. It is not a second implementation tree. It remains
**JUSTIFIED TEMPORARILY** only if tests demonstrate a real supported workflow that requires it.
A later identity PR must either:

1. retain it with a focused test and documented compatibility owner; or
2. delete it after the install/developer workflow no longer requires it.

### Legacy evidence in history

Repository history contains earlier broad architecture and unrelated/legacy work, including old
"complete" platform claims and historical NEXUS/RAPL work. The 2026 truth reset removed much of
that public positioning, but several gateway-era abstractions and documents remain.

History is not itself a defect. Active legacy code is.

## Architecture ownership audit

### Current useful ownership

- provider execution: `infrastructure/models/`;
- model pricing calculation: `domain/cost/`;
- request/route telemetry: `infrastructure/telemetry/`;
- benchmark orchestration: `scripts/run_benchmark.py` plus `benchmarking/`;
- persistent benchmark data: `benchmarking/sqlite_ledger.py`;
- deterministic quality checks: `benchmarking/eval.py`.

### Structural problems to resolve

#### P0 — execution evidence is request-centric, not attempt-centric

A provider retry is collapsed into `provider_attempt_count` / `provider_retry_count` on one
request trace. The actual attempts are not durable domain records.

Required target:

```text
WorkloadCase
  -> Execution
       -> Attempt 1
       -> Attempt 2 (retry)
       -> Attempt 3 (fallback)
  -> Outcome
  -> QualityEvidence reference(s)
```

Each actual provider invocation that matters economically must be representable independently.
Unknown usage/charge must remain unknown rather than becoming zero.

#### P0 — pricing authority is not sufficient for migration evidence

The current table is keyed by model name and one global version string.

Required minimum dimensions should be justified from provider billing semantics, but are expected
to include:

- provider/integration identity;
- model/SKU identity;
- execution mode when price differs (online, batch, cache read/write, etc.);
- billing unit/rate;
- effective period or immutable price-record ID;
- provenance/source;
- optional customer/contract override with provenance;
- currency.

Historical evidence must reference the price record used for that run.

#### P1 — benchmark runner is a parallel product surface

`scripts/run_benchmark.py` currently constructs routers, backends, logging, evaluation, and
reporting directly. It is useful as a reference path but owns too much orchestration.

Target direction:

- one application-level execution/comparison service;
- CLI/script/API call that service;
- provider/gateway specifics behind adapters;
- report generation consumes canonical evidence objects rather than rebuilding semantics.

#### P1 — gateway-era domain breadth

Batching, caching, fallback, routing, and provider-serving abstractions were created for the prior
gateway thesis. They must not be expanded merely because they exist.

Each must be classified by use audit:

- keep when needed by the reference executor or migration experiment;
- migrate behind an adapter when it represents external execution infrastructure;
- delete when unreferenced or duplicative.

For example, `domain/routing/fallback.py::FallbackChain` currently appears unreferenced outside
its own definition on the audited default branch and is a deletion candidate pending full test and
import audit.

## Canonical target dependency direction

The target should remain small:

```text
core/domain evidence semantics
        |
        v
application use cases
  replay / import / compare / decide
        |
        v
ports
  executor / trace source / pricing / quality evidence
        |
        v
adapters
  OpenAI-compatible reference executor
  external gateway/telemetry adapters
  SQLite/JSON evidence persistence
  CLI / GitHub Action / optional API
```

Rules:

- provider SDK types do not leak into economic domain semantics;
- FastAPI does not own business logic;
- SQLite schemas do not define the domain contract;
- scripts do not implement alternate economic accounting;
- quality remains an imported/narrow evidence dimension, not a generalized eval platform;
- routing remains an execution input, not the product core.

## Cleanup classification

### P0

- replace zero-cost failed-attempt semantics with explicit cost-evidence completeness;
- create durable attempt-chain representation before claiming retry/fallback economics;
- prevent benchmark total cost from silently excluding potentially billable failures;
- make pricing provenance sufficient to reproduce historical economic claims.

### P1

- move public strategy from gateway/router to migration assurance;
- permit valid cross-provider baseline/candidate comparisons;
- add workload-segment comparison;
- define evidence class and unknown/partial states;
- reconcile distribution/product/CLI identity;
- consolidate benchmark orchestration behind an application service;
- audit and remove unreferenced gateway-era abstractions.

### P2

- external execution-stack adapter;
- external quality-evidence adapter;
- pre/post deployment revalidation;
- integrity manifest for evidence bundles.

### P3

- dashboard polish;
- broad integration catalog;
- extensive deployment machinery.

### KEEP

- deterministic benchmark workload mechanism;
- provider execution code that is useful as a controlled reference executor;
- normalized provider error taxonomy after attempt-model integration;
- local SQLite/JSON persistence where it serves reproducibility;
- deterministic evaluators as narrow quality evidence;
- strict lint/type/test baseline.

### MIGRATE

- current request trace into execution + attempt evidence;
- benchmark reports into migration/change evidence;
- router-specific comparison into execution-policy comparison;
- model-only pricing into provider-aware price records.

### DELETE candidates

- unreferenced gateway-era fallback/batching/caching/routing code after use audit;
- obsolete status/completion documentation that contradicts the canonical product decision;
- redundant product names and CLI aliases after compatibility analysis.

### ARCHIVE

Historical benchmark artifacts may remain as explicitly labeled historical engineering evidence,
but they must not be presented as validation of the new migration-assurance product thesis.

## GitHub governance state

At audit time:

- only `main` existed remotely;
- GitHub reported `main` as not protected;
- no repository rulesets were configured;
- CI existed and the latest audited `main` run was green;
- no open pull requests or issues were present;
- no GitHub releases were published.

Before substantial product implementation, changes should use focused branches and PRs even if
repository-level protection cannot yet be configured through the available integration.

## Local reconciliation boundary

This document intentionally does not claim that the user's local checkout matches GitHub.
Direct local filesystem access was unavailable during this GitHub audit.

Local-only files, branches, nested repositories, generated artifacts, and unique unpushed work
remain `UNKNOWN` until a read-only local snapshot or equivalent Git evidence is available.
No GitHub cleanup should assume that local-only work is disposable.

## Reconciliation tracker

| Item | Status |
| --- | --- |
| Canonical package | NEEDS WORK |
| Duplicate source roots | CLEAN on GitHub; local UNKNOWN |
| Legacy names | NEEDS WORK |
| Compatibility shims | JUSTIFIED temporarily; needs explicit contract |
| Dead code | NEEDS WORK |
| Duplicate config | no P0 duplicate found on GitHub; deeper audit ongoing |
| Duplicate docs | NEEDS WORK |
| Local/GitHub divergence | UNKNOWN |
| Release identity | NEEDS WORK |

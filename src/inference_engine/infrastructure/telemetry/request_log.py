from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from math import isclose
from pathlib import Path
from typing import Any
from uuid import UUID

from ...domain.cost.pricing import PRICING_TABLE_VERSION
from ...domain.models.execution import (
    AttemptOutcome,
    CostEvidenceKind,
    ProviderAttempt,
)
from ...domain.models.response import InferenceResponse
from ...domain.models.routing import RoutingDecision
from ...infrastructure.models.errors import ProviderError
from ...utils.time import utc_now


@dataclass(frozen=True)
class RequestTrace:
    """One append-only request execution record.

    `estimated_cost_usd` is `None` whenever the full executed attempt chain does not have complete
    cost evidence. In particular, a failed or retried provider call must not silently become a
    zero-cost execution.
    """

    request_id: str
    provider: str
    model: str
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_usd: float | None
    pricing_table_version: str
    cache_hit: bool
    error_type: str | None
    error_message: str | None
    timestamp: str
    quality_passed: bool | None = None
    quality_score: float | None = None
    quality_reason: str | None = None
    eval_type: str | None = None
    provider_attempt_count: int = 1
    provider_retry_count: int = 0
    cost_evidence_complete: bool = True
    provider_attempts: tuple[ProviderAttempt, ...] = ()

    def __post_init__(self) -> None:
        if self.provider_attempt_count < 0 or self.provider_retry_count < 0:
            raise ValueError("provider attempt counts must be non-negative")

        if self.provider_attempts:
            attempt_count = len(self.provider_attempts)
            retry_count = max(attempt_count - 1, 0)
            object.__setattr__(self, "provider_attempt_count", attempt_count)
            object.__setattr__(self, "provider_retry_count", retry_count)

            all_attempt_costs_known = all(attempt.cost_is_known for attempt in self.provider_attempts)
            if all_attempt_costs_known:
                expected_cost = sum(
                    attempt.calculated_cost_usd or 0.0 for attempt in self.provider_attempts
                )
                if not self.cost_evidence_complete or self.estimated_cost_usd is None:
                    raise ValueError(
                        "complete provider-attempt cost evidence requires a total execution cost"
                    )
                if not isclose(self.estimated_cost_usd, expected_cost, rel_tol=1e-12, abs_tol=1e-12):
                    raise ValueError(
                        "request execution cost must equal the sum of known provider-attempt costs"
                    )
            elif self.cost_evidence_complete or self.estimated_cost_usd is not None:
                raise ValueError(
                    "unknown provider-attempt cost requires incomplete request cost evidence"
                )
        else:
            ambiguous_legacy_execution = (
                self.error_type is not None
                or self.provider_retry_count > 0
                or self.provider_attempt_count > 1
            )
            if ambiguous_legacy_execution and self.cost_evidence_complete:
                raise ValueError(
                    "retry/failure trace without provider-attempt evidence cannot claim complete cost evidence"
                )

        if self.cost_evidence_complete and self.estimated_cost_usd is None:
            raise ValueError("complete cost evidence requires an execution cost")
        if not self.cost_evidence_complete and self.estimated_cost_usd is not None:
            raise ValueError("incomplete cost evidence must not expose a total execution cost")

    @classmethod
    def from_response(
        cls,
        *,
        provider: str,
        response: InferenceResponse,
        pricing_table_version: str = PRICING_TABLE_VERSION,
    ) -> RequestTrace:
        attempts = response.provider_attempts
        execution_cost, cost_complete = _execution_cost_from_attempts(
            attempts,
            fallback_final_cost=response.usage.cost_usd,
            legacy_attempt_count=response.provider_attempt_count,
        )
        return cls(
            request_id=str(response.request_id),
            provider=provider,
            model=response.model_used,
            latency_ms=response.latency_ms,
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
            total_tokens=response.usage.total_tokens,
            estimated_cost_usd=execution_cost,
            pricing_table_version=pricing_table_version,
            cache_hit=response.cache_info.hit,
            error_type=None,
            error_message=None,
            timestamp=response.timestamp.isoformat(),
            provider_attempt_count=response.provider_attempt_count,
            provider_retry_count=response.provider_retry_count,
            cost_evidence_complete=cost_complete,
            provider_attempts=attempts,
        )

    @classmethod
    def from_error(
        cls,
        *,
        request_id: UUID,
        provider: str,
        model: str,
        latency_ms: int,
        error: ProviderError,
        pricing_table_version: str = PRICING_TABLE_VERSION,
    ) -> RequestTrace:
        attempts = error.provider_attempts
        execution_cost, cost_complete = _execution_cost_from_attempts(
            attempts,
            fallback_final_cost=None,
            legacy_attempt_count=error.provider_attempt_count,
        )
        return cls(
            request_id=str(request_id),
            provider=provider,
            model=model,
            latency_ms=latency_ms,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            estimated_cost_usd=execution_cost,
            pricing_table_version=pricing_table_version,
            cache_hit=False,
            error_type=error.error_type.value,
            error_message=error.message,
            timestamp=utc_now().isoformat(),
            provider_attempt_count=error.provider_attempt_count,
            provider_retry_count=error.provider_retry_count,
            cost_evidence_complete=cost_complete,
            provider_attempts=attempts,
        )


class JsonlRequestLog:
    """Append-only JSONL request ledger for local smoke and benchmark runs."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, trace: RequestTrace) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(trace), sort_keys=True) + "\n")

    def read_all(self) -> list[RequestTrace]:
        if not self.path.exists():
            return []

        traces: list[RequestTrace] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                traces.append(_request_trace_from_dict(json.loads(line)))
        return traces


@dataclass(frozen=True)
class RouteTrace:
    """One routing decision record for benchmark auditability."""

    request_id: str
    strategy: str
    selected_model: str
    estimated_cost_usd: float
    estimated_latency_ms: int
    decision_reason: str
    considered_models: list[str]
    fallback_models: list[str]
    max_estimated_cost_usd: float | None
    budget_violation: bool
    budget_violation_reason: str | None
    timestamp: str

    @classmethod
    def from_decision(
        cls,
        decision: RoutingDecision,
        *,
        max_estimated_cost_usd: float | None = None,
        budget_violation_reason: str | None = None,
    ) -> RouteTrace:
        budget_violation = budget_violation_reason is not None
        return cls(
            request_id=str(decision.request_id),
            strategy=decision.strategy.value,
            selected_model=decision.selected_model.id,
            estimated_cost_usd=decision.estimated_cost,
            estimated_latency_ms=decision.estimated_latency_ms,
            decision_reason=decision.decision_reason,
            considered_models=decision.considered_models,
            fallback_models=[model.id for model in decision.fallback_models],
            max_estimated_cost_usd=max_estimated_cost_usd,
            budget_violation=budget_violation,
            budget_violation_reason=budget_violation_reason,
            timestamp=decision.timestamp.isoformat(),
        )


class JsonlRouteLog:
    """Append-only JSONL route decision ledger for local benchmark runs."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, trace: RouteTrace) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(trace), sort_keys=True) + "\n")

    def read_all(self) -> list[RouteTrace]:
        if not self.path.exists():
            return []

        traces: list[RouteTrace] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                traces.append(RouteTrace(**json.loads(line)))
        return traces


def _execution_cost_from_attempts(
    attempts: tuple[ProviderAttempt, ...],
    *,
    fallback_final_cost: float | None,
    legacy_attempt_count: int,
) -> tuple[float | None, bool]:
    if attempts:
        if all(attempt.cost_is_known for attempt in attempts):
            return sum(attempt.calculated_cost_usd or 0.0 for attempt in attempts), True
        return None, False

    if legacy_attempt_count == 1 and fallback_final_cost is not None:
        return fallback_final_cost, True
    if legacy_attempt_count == 0:
        return 0.0, True
    return None, False


def _request_trace_from_dict(raw: dict[str, Any]) -> RequestTrace:
    raw = dict(raw)
    raw_attempts = raw.pop("provider_attempts", [])
    attempts = tuple(_provider_attempt_from_dict(item) for item in raw_attempts)
    raw["provider_attempts"] = attempts

    legacy_attempt_count = int(raw.get("provider_attempt_count", 1))
    legacy_retry_count = int(raw.get("provider_retry_count", 0))
    legacy_ambiguous = (
        not attempts
        and (
            raw.get("error_type") is not None
            or legacy_retry_count > 0
            or legacy_attempt_count > 1
        )
    )
    if legacy_ambiguous:
        raw["cost_evidence_complete"] = False
        raw["estimated_cost_usd"] = None
    elif "cost_evidence_complete" not in raw:
        raw["cost_evidence_complete"] = True

    return RequestTrace(**raw)


def _provider_attempt_from_dict(raw: dict[str, Any]) -> ProviderAttempt:
    return ProviderAttempt(
        attempt_index=int(raw["attempt_index"]),
        provider=str(raw["provider"]),
        model=str(raw["model"]),
        outcome=AttemptOutcome(str(raw["outcome"])),
        latency_ms=int(raw["latency_ms"]),
        prompt_tokens=_optional_int(raw.get("prompt_tokens")),
        completion_tokens=_optional_int(raw.get("completion_tokens")),
        total_tokens=_optional_int(raw.get("total_tokens")),
        cached_tokens=_optional_int(raw.get("cached_tokens")),
        calculated_cost_usd=_optional_float(raw.get("calculated_cost_usd")),
        cost_evidence=CostEvidenceKind(str(raw.get("cost_evidence", CostEvidenceKind.UNKNOWN.value))),
        pricing_table_version=_optional_str(raw.get("pricing_table_version")),
        error_type=_optional_str(raw.get("error_type")),
        status_code=_optional_int(raw.get("status_code")),
    )


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite

from .paired_comparison import (
    PairedMetricEvidence,
    PairedRunStatisticalEvidence,
    PairedSegmentStatisticalEvidence,
)
from .segmentation import SegmentEvidence, SegmentEvidenceSummary
from .statistics import StatisticalEvidenceStatus, exact_binomial_upper_confidence_bound


class GateCheckStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    REVIEW = "review"
    INCONCLUSIVE = "inconclusive"


class ChangeGateDecision(StrEnum):
    SHIP = "ship"
    REVIEW = "review"
    NO_GO = "no_go"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class CriticalSegmentPolicy:
    """One explicitly decision-critical workload segment.

    Global cost/failure/accepted-outcome/central-latency margins apply to every critical segment.
    Tail fields are observed benchmark guards, not inferential p95/p99 claims.
    """

    tag_key: str
    tag_value: str
    minimum_tail_latency_samples: int = 30
    max_candidate_p95_ms: int | None = None
    max_candidate_p99_ms: int | None = None
    max_p95_regression_ms: int | None = None
    max_p99_regression_ms: int | None = None

    def __post_init__(self) -> None:
        if not self.tag_key.strip() or not self.tag_value.strip():
            raise ValueError("critical segment tag key/value must be non-empty")
        if self.minimum_tail_latency_samples < 1:
            raise ValueError("minimum_tail_latency_samples must be positive")
        for field_name, tail_value in (
            ("max_candidate_p95_ms", self.max_candidate_p95_ms),
            ("max_candidate_p99_ms", self.max_candidate_p99_ms),
        ):
            if tail_value is not None and tail_value < 0:
                raise ValueError(f"{field_name} must be non-negative")


@dataclass(frozen=True)
class ChangeGatePolicy:
    """Explicit non-compensatory policy for one baseline -> candidate change.

    Margins are intentionally required rather than inferred from the data. A favorable result on
    one metric never offsets a failed or inconclusive required metric.
    """

    max_mean_cost_delta_usd: float
    max_failure_harm_rate: float
    max_mean_successful_latency_delta_ms: float
    max_accepted_outcome_harm_rate: float
    minimum_cost_pair_coverage: float = 1.0
    minimum_failure_pair_coverage: float = 1.0
    minimum_successful_latency_pair_coverage: float = 1.0
    minimum_accepted_outcome_pair_coverage: float = 1.0
    confidence_level: float = 0.95
    minimum_bootstrap_iterations: int = 10_000
    max_mean_provider_attempt_delta: float | None = None
    max_mean_provider_retry_delta: float | None = None
    minimum_operational_pair_coverage: float = 1.0
    critical_segments: tuple[CriticalSegmentPolicy, ...] = ()
    require_tail_latency_inference: bool = False

    def __post_init__(self) -> None:
        for field_name, numeric_value in (
            ("max_mean_cost_delta_usd", self.max_mean_cost_delta_usd),
            ("max_mean_successful_latency_delta_ms", self.max_mean_successful_latency_delta_ms),
        ):
            if not isfinite(numeric_value):
                raise ValueError(f"{field_name} must be finite")
        for field_name, optional_value in (
            ("max_mean_provider_attempt_delta", self.max_mean_provider_attempt_delta),
            ("max_mean_provider_retry_delta", self.max_mean_provider_retry_delta),
        ):
            if optional_value is not None and not isfinite(optional_value):
                raise ValueError(f"{field_name} must be finite when configured")
        for field_name, bounded_value in (
            ("max_failure_harm_rate", self.max_failure_harm_rate),
            ("max_accepted_outcome_harm_rate", self.max_accepted_outcome_harm_rate),
            ("minimum_cost_pair_coverage", self.minimum_cost_pair_coverage),
            ("minimum_failure_pair_coverage", self.minimum_failure_pair_coverage),
            (
                "minimum_successful_latency_pair_coverage",
                self.minimum_successful_latency_pair_coverage,
            ),
            ("minimum_accepted_outcome_pair_coverage", self.minimum_accepted_outcome_pair_coverage),
            ("minimum_operational_pair_coverage", self.minimum_operational_pair_coverage),
        ):
            if not 0 <= bounded_value <= 1:
                raise ValueError(f"{field_name} must be between 0 and 1")
        if not 0 < self.confidence_level < 1:
            raise ValueError("confidence_level must be between 0 and 1")
        if self.minimum_bootstrap_iterations < 1_000:
            raise ValueError("minimum_bootstrap_iterations must be at least 1000")
        segment_keys = [(segment.tag_key, segment.tag_value) for segment in self.critical_segments]
        if len(segment_keys) != len(set(segment_keys)):
            raise ValueError("critical_segments must not contain duplicates")


@dataclass(frozen=True)
class GateCheck:
    check_id: str
    scope: str
    metric: str
    status: GateCheckStatus
    rule: str
    observed_value: float | None
    threshold: float | None
    confidence_bound: float | None
    confidence_level: float | None
    pair_coverage: float | None
    event_count: int | None
    trial_count: int | None
    evidence_status: str | None
    reason: str


@dataclass(frozen=True)
class ChangeGateResult:
    decision: ChangeGateDecision
    baseline_run_id: str
    candidate_run_id: str
    evidence_scope: str
    checks: tuple[GateCheck, ...]
    pass_count: int
    fail_count: int
    review_count: int
    inconclusive_count: int
    limitations: tuple[str, ...]


def evaluate_change_gate(
    *,
    evidence: PairedRunStatisticalEvidence,
    policy: ChangeGatePolicy,
    baseline_segments: SegmentEvidenceSummary | None = None,
    candidate_segments: SegmentEvidenceSummary | None = None,
) -> ChangeGateResult:
    """Evaluate an intersection gate: every required check must independently pass to SHIP."""
    if not evidence.available:
        check = GateCheck(
            check_id="paired_evidence_available",
            scope="overall",
            metric="paired_evidence",
            status=GateCheckStatus.INCONCLUSIVE,
            rule="paired evidence must be structurally available",
            observed_value=None,
            threshold=None,
            confidence_bound=None,
            confidence_level=None,
            pair_coverage=None,
            event_count=None,
            trial_count=None,
            evidence_status=None,
            reason=evidence.unavailable_reason or "paired evidence unavailable",
        )
        return _result(evidence=evidence, checks=[check])

    checks = _scope_checks(
        scope="overall",
        cost=evidence.execution_cost_usd,
        failure=evidence.failure_rate,
        latency=evidence.successful_latency_ms,
        accepted=evidence.accepted_outcome_rate,
        attempts=evidence.provider_attempt_count,
        retries=evidence.provider_retry_count,
        policy=policy,
    )

    paired_segments = {
        (segment.tag_key, segment.tag_value): segment for segment in evidence.segments
    }
    baseline_segment_map = _segment_map(baseline_segments)
    candidate_segment_map = _segment_map(candidate_segments)

    for segment_policy in policy.critical_segments:
        key = (segment_policy.tag_key, segment_policy.tag_value)
        scope = f"segment:{segment_policy.tag_key}={segment_policy.tag_value}"
        paired_segment = paired_segments.get(key)
        if paired_segment is None:
            checks.append(
                _inconclusive_check(
                    check_id=f"{scope}:paired_evidence",
                    scope=scope,
                    metric="paired_evidence",
                    rule="critical segment must exist in paired evidence",
                    reason="critical segment is absent from paired statistical evidence",
                )
            )
            continue
        checks.extend(_segment_central_checks(paired_segment, policy=policy, scope=scope))
        checks.extend(
            _tail_checks(
                scope=scope,
                segment_policy=segment_policy,
                baseline=baseline_segment_map.get(key),
                candidate=candidate_segment_map.get(key),
            )
        )

    if policy.require_tail_latency_inference and not evidence.tail_latency_inference_supported:
        checks.append(
            _inconclusive_check(
                check_id="tail_latency_inference",
                scope="overall",
                metric="tail_latency",
                rule="inferential p95/p99 evidence is required by policy",
                reason="paired evidence explicitly does not support high-tail latency inference",
            )
        )

    return _result(evidence=evidence, checks=checks)


def _scope_checks(
    *,
    scope: str,
    cost: PairedMetricEvidence | None,
    failure: PairedMetricEvidence | None,
    latency: PairedMetricEvidence | None,
    accepted: PairedMetricEvidence | None,
    attempts: PairedMetricEvidence | None,
    retries: PairedMetricEvidence | None,
    policy: ChangeGatePolicy,
) -> list[GateCheck]:
    checks = [
        _central_upper_margin_check(
            check_id=f"{scope}:mean_cost_delta",
            scope=scope,
            metric=cost,
            threshold=policy.max_mean_cost_delta_usd,
            minimum_coverage=policy.minimum_cost_pair_coverage,
            minimum_confidence_level=policy.confidence_level,
            minimum_bootstrap_iterations=policy.minimum_bootstrap_iterations,
        ),
        _binary_harm_check(
            check_id=f"{scope}:failure_harm",
            scope=scope,
            metric=failure,
            threshold=policy.max_failure_harm_rate,
            minimum_coverage=policy.minimum_failure_pair_coverage,
            confidence_level=policy.confidence_level,
        ),
        _central_upper_margin_check(
            check_id=f"{scope}:mean_successful_latency_delta",
            scope=scope,
            metric=latency,
            threshold=policy.max_mean_successful_latency_delta_ms,
            minimum_coverage=policy.minimum_successful_latency_pair_coverage,
            minimum_confidence_level=policy.confidence_level,
            minimum_bootstrap_iterations=policy.minimum_bootstrap_iterations,
        ),
        _binary_harm_check(
            check_id=f"{scope}:accepted_outcome_harm",
            scope=scope,
            metric=accepted,
            threshold=policy.max_accepted_outcome_harm_rate,
            minimum_coverage=policy.minimum_accepted_outcome_pair_coverage,
            confidence_level=policy.confidence_level,
        ),
    ]
    if policy.max_mean_provider_attempt_delta is not None:
        checks.append(
            _central_upper_margin_check(
                check_id=f"{scope}:mean_provider_attempt_delta",
                scope=scope,
                metric=attempts,
                threshold=policy.max_mean_provider_attempt_delta,
                minimum_coverage=policy.minimum_operational_pair_coverage,
                minimum_confidence_level=policy.confidence_level,
                minimum_bootstrap_iterations=policy.minimum_bootstrap_iterations,
            )
        )
    if policy.max_mean_provider_retry_delta is not None:
        checks.append(
            _central_upper_margin_check(
                check_id=f"{scope}:mean_provider_retry_delta",
                scope=scope,
                metric=retries,
                threshold=policy.max_mean_provider_retry_delta,
                minimum_coverage=policy.minimum_operational_pair_coverage,
                minimum_confidence_level=policy.confidence_level,
                minimum_bootstrap_iterations=policy.minimum_bootstrap_iterations,
            )
        )
    return checks


def _segment_central_checks(
    segment: PairedSegmentStatisticalEvidence,
    *,
    policy: ChangeGatePolicy,
    scope: str,
) -> list[GateCheck]:
    return _scope_checks(
        scope=scope,
        cost=segment.execution_cost_usd,
        failure=segment.failure_rate,
        latency=segment.successful_latency_ms,
        accepted=segment.accepted_outcome_rate,
        attempts=segment.provider_attempt_count,
        retries=segment.provider_retry_count,
        policy=policy,
    )


def _central_upper_margin_check(
    *,
    check_id: str,
    scope: str,
    metric: PairedMetricEvidence | None,
    threshold: float,
    minimum_coverage: float,
    minimum_confidence_level: float,
    minimum_bootstrap_iterations: int,
) -> GateCheck:
    rule = f"candidate-minus-baseline upper confidence bound <= {threshold}"
    if metric is None:
        return _inconclusive_check(
            check_id=check_id,
            scope=scope,
            metric="unknown",
            rule=rule,
            reason="required paired metric is unavailable",
        )
    estimate = metric.estimate
    observed = estimate.observed_mean_difference
    if observed is None:
        return _inconclusive_metric_check(metric, check_id, scope, rule, threshold, "no point estimate")
    if observed > threshold:
        return _metric_check(
            metric=metric,
            check_id=check_id,
            scope=scope,
            status=GateCheckStatus.FAIL,
            rule=rule,
            threshold=threshold,
            confidence_bound=estimate.confidence_interval_high,
            reason="observed candidate-minus-baseline mean exceeds the configured margin",
        )
    if metric.pair_coverage < minimum_coverage:
        return _inconclusive_metric_check(
            metric,
            check_id,
            scope,
            rule,
            threshold,
            f"pair coverage {metric.pair_coverage:.6f} is below required {minimum_coverage:.6f}",
        )
    if estimate.confidence_level < minimum_confidence_level:
        return _inconclusive_metric_check(
            metric,
            check_id,
            scope,
            rule,
            threshold,
            "paired interval confidence level is below gate policy",
        )
    if estimate.bootstrap_iterations < minimum_bootstrap_iterations:
        return _inconclusive_metric_check(
            metric,
            check_id,
            scope,
            rule,
            threshold,
            "paired bootstrap iteration count is below gate policy",
        )
    if estimate.status != StatisticalEvidenceStatus.SUFFICIENT or not estimate.interval_available:
        return _inconclusive_metric_check(
            metric,
            check_id,
            scope,
            rule,
            threshold,
            f"statistical evidence status is {estimate.status.value}",
        )
    upper = estimate.confidence_interval_high
    assert upper is not None
    if upper <= threshold:
        return _metric_check(
            metric=metric,
            check_id=check_id,
            scope=scope,
            status=GateCheckStatus.PASS,
            rule=rule,
            threshold=threshold,
            confidence_bound=upper,
            reason="upper confidence bound is within the configured margin",
        )
    return _metric_check(
        metric=metric,
        check_id=check_id,
        scope=scope,
        status=GateCheckStatus.REVIEW,
        rule=rule,
        threshold=threshold,
        confidence_bound=upper,
        reason="point estimate is within margin but uncertainty crosses the configured boundary",
    )


def _binary_harm_check(
    *,
    check_id: str,
    scope: str,
    metric: PairedMetricEvidence | None,
    threshold: float,
    minimum_coverage: float,
    confidence_level: float,
) -> GateCheck:
    rule = f"one-sided upper bound on worsening transition rate <= {threshold}"
    if metric is None:
        return _inconclusive_check(
            check_id=check_id,
            scope=scope,
            metric="unknown",
            rule=rule,
            reason="required paired binary metric is unavailable",
        )
    if metric.eligible_pair_count < 1:
        return _inconclusive_metric_check(
            metric,
            check_id,
            scope,
            rule,
            threshold,
            "no eligible binary pairs",
        )
    worsening_count = _worsening_transition_count(metric)
    observed_harm_rate = worsening_count / metric.eligible_pair_count
    if observed_harm_rate > threshold:
        return GateCheck(
            check_id=check_id,
            scope=scope,
            metric=metric.metric,
            status=GateCheckStatus.FAIL,
            rule=rule,
            observed_value=observed_harm_rate,
            threshold=threshold,
            confidence_bound=None,
            confidence_level=confidence_level,
            pair_coverage=metric.pair_coverage,
            event_count=worsening_count,
            trial_count=metric.eligible_pair_count,
            evidence_status=metric.estimate.status.value,
            reason="observed worsening transition rate exceeds the configured margin",
        )
    if metric.pair_coverage < minimum_coverage:
        return GateCheck(
            check_id=check_id,
            scope=scope,
            metric=metric.metric,
            status=GateCheckStatus.INCONCLUSIVE,
            rule=rule,
            observed_value=observed_harm_rate,
            threshold=threshold,
            confidence_bound=None,
            confidence_level=confidence_level,
            pair_coverage=metric.pair_coverage,
            event_count=worsening_count,
            trial_count=metric.eligible_pair_count,
            evidence_status=metric.estimate.status.value,
            reason=(
                f"pair coverage {metric.pair_coverage:.6f} is below required {minimum_coverage:.6f}"
            ),
        )
    upper = exact_binomial_upper_confidence_bound(
        event_count=worsening_count,
        trial_count=metric.eligible_pair_count,
        confidence_level=confidence_level,
    )
    status = GateCheckStatus.PASS if upper <= threshold else GateCheckStatus.REVIEW
    reason = (
        "exact one-sided harm bound is within the configured margin"
        if status == GateCheckStatus.PASS
        else "observed harm is within margin but exact uncertainty bound crosses the boundary"
    )
    return GateCheck(
        check_id=check_id,
        scope=scope,
        metric=metric.metric,
        status=status,
        rule=rule,
        observed_value=observed_harm_rate,
        threshold=threshold,
        confidence_bound=upper,
        confidence_level=confidence_level,
        pair_coverage=metric.pair_coverage,
        event_count=worsening_count,
        trial_count=metric.eligible_pair_count,
        evidence_status=metric.estimate.status.value,
        reason=reason,
    )


def _worsening_transition_count(metric: PairedMetricEvidence) -> int:
    estimate = metric.estimate
    observed = estimate.observed_mean_difference
    if observed is None:
        raise ValueError("binary paired metric requires an observed mean difference")
    trial_count = estimate.sample_count
    changed = estimate.changed_pair_count
    if trial_count != metric.eligible_pair_count:
        raise ValueError("binary paired metric sample count does not match eligible pair count")
    net_change = observed * trial_count
    rounded_net_change = round(net_change)
    if abs(net_change - rounded_net_change) > 1e-7:
        raise ValueError("binary paired metric does not contain integral transition evidence")
    numerator = (
        changed + rounded_net_change if metric.lower_is_better else changed - rounded_net_change
    )
    if numerator % 2 != 0:
        raise ValueError("binary paired transition evidence has invalid parity")
    worsening = numerator // 2
    if worsening < 0 or worsening > changed:
        raise ValueError("binary paired transition evidence is inconsistent")
    return worsening


def _tail_checks(
    *,
    scope: str,
    segment_policy: CriticalSegmentPolicy,
    baseline: SegmentEvidence | None,
    candidate: SegmentEvidence | None,
) -> list[GateCheck]:
    configured = any(
        value is not None
        for value in (
            segment_policy.max_candidate_p95_ms,
            segment_policy.max_candidate_p99_ms,
            segment_policy.max_p95_regression_ms,
            segment_policy.max_p99_regression_ms,
        )
    )
    if not configured:
        return []
    if baseline is None or candidate is None:
        return [
            _inconclusive_check(
                check_id=f"{scope}:observed_tail_latency",
                scope=scope,
                metric="tail_latency",
                rule="configured critical-segment tail evidence must exist in both runs",
                reason="critical segment tail evidence is unavailable for baseline or candidate",
            )
        ]
    minimum = segment_policy.minimum_tail_latency_samples
    if baseline.latency_sample_count < minimum or candidate.latency_sample_count < minimum:
        return [
            _inconclusive_check(
                check_id=f"{scope}:observed_tail_latency",
                scope=scope,
                metric="tail_latency",
                rule=f"both runs require at least {minimum} successful latency samples",
                reason=(
                    "critical segment tail sample count is below policy; "
                    f"baseline={baseline.latency_sample_count}, candidate={candidate.latency_sample_count}"
                ),
            )
        ]

    checks: list[GateCheck] = []
    checks.extend(
        _observed_tail_metric_checks(
            scope=scope,
            percentile="p95",
            baseline_value=baseline.latency_p95_ms,
            candidate_value=candidate.latency_p95_ms,
            max_candidate=segment_policy.max_candidate_p95_ms,
            max_regression=segment_policy.max_p95_regression_ms,
        )
    )
    checks.extend(
        _observed_tail_metric_checks(
            scope=scope,
            percentile="p99",
            baseline_value=baseline.latency_p99_ms,
            candidate_value=candidate.latency_p99_ms,
            max_candidate=segment_policy.max_candidate_p99_ms,
            max_regression=segment_policy.max_p99_regression_ms,
        )
    )
    return checks


def _observed_tail_metric_checks(
    *,
    scope: str,
    percentile: str,
    baseline_value: int | None,
    candidate_value: int | None,
    max_candidate: int | None,
    max_regression: int | None,
) -> list[GateCheck]:
    if max_candidate is None and max_regression is None:
        return []
    if baseline_value is None or candidate_value is None:
        return [
            _inconclusive_check(
                check_id=f"{scope}:observed_{percentile}",
                scope=scope,
                metric=f"observed_latency_{percentile}_ms",
                rule="configured observed tail latency evidence must be present",
                reason="observed percentile is unavailable",
            )
        ]
    checks: list[GateCheck] = []
    if max_candidate is not None:
        status = GateCheckStatus.PASS if candidate_value <= max_candidate else GateCheckStatus.FAIL
        checks.append(
            GateCheck(
                check_id=f"{scope}:candidate_{percentile}",
                scope=scope,
                metric=f"observed_latency_{percentile}_ms",
                status=status,
                rule=f"candidate observed {percentile} <= {max_candidate} ms",
                observed_value=float(candidate_value),
                threshold=float(max_candidate),
                confidence_bound=None,
                confidence_level=None,
                pair_coverage=None,
                event_count=None,
                trial_count=None,
                evidence_status="observed_only",
                reason=(
                    "observed candidate tail latency is within the hard benchmark SLO"
                    if status == GateCheckStatus.PASS
                    else "observed candidate tail latency exceeds the hard benchmark SLO"
                ),
            )
        )
    if max_regression is not None:
        delta = candidate_value - baseline_value
        status = GateCheckStatus.PASS if delta <= max_regression else GateCheckStatus.FAIL
        checks.append(
            GateCheck(
                check_id=f"{scope}:{percentile}_regression",
                scope=scope,
                metric=f"observed_latency_{percentile}_delta_ms",
                status=status,
                rule=f"observed candidate-minus-baseline {percentile} <= {max_regression} ms",
                observed_value=float(delta),
                threshold=float(max_regression),
                confidence_bound=None,
                confidence_level=None,
                pair_coverage=None,
                event_count=None,
                trial_count=None,
                evidence_status="observed_only",
                reason=(
                    "observed tail regression is within the configured margin"
                    if status == GateCheckStatus.PASS
                    else "observed tail regression exceeds the configured margin"
                ),
            )
        )
    return checks


def _segment_map(summary: SegmentEvidenceSummary | None) -> dict[tuple[str, str], SegmentEvidence]:
    if summary is None or not summary.available:
        return {}
    result: dict[tuple[str, str], SegmentEvidence] = {}
    for segment in summary.segments:
        key = (segment.tag_key, segment.tag_value)
        if key in result:
            raise ValueError(f"duplicate segment evidence: {key}")
        result[key] = segment
    return result


def _metric_check(
    *,
    metric: PairedMetricEvidence,
    check_id: str,
    scope: str,
    status: GateCheckStatus,
    rule: str,
    threshold: float,
    confidence_bound: float | None,
    reason: str,
) -> GateCheck:
    estimate = metric.estimate
    return GateCheck(
        check_id=check_id,
        scope=scope,
        metric=metric.metric,
        status=status,
        rule=rule,
        observed_value=estimate.observed_mean_difference,
        threshold=threshold,
        confidence_bound=confidence_bound,
        confidence_level=estimate.confidence_level,
        pair_coverage=metric.pair_coverage,
        event_count=None,
        trial_count=metric.eligible_pair_count,
        evidence_status=estimate.status.value,
        reason=reason,
    )


def _inconclusive_metric_check(
    metric: PairedMetricEvidence,
    check_id: str,
    scope: str,
    rule: str,
    threshold: float,
    reason: str,
) -> GateCheck:
    return _metric_check(
        metric=metric,
        check_id=check_id,
        scope=scope,
        status=GateCheckStatus.INCONCLUSIVE,
        rule=rule,
        threshold=threshold,
        confidence_bound=metric.estimate.confidence_interval_high,
        reason=reason,
    )


def _inconclusive_check(
    *,
    check_id: str,
    scope: str,
    metric: str,
    rule: str,
    reason: str,
) -> GateCheck:
    return GateCheck(
        check_id=check_id,
        scope=scope,
        metric=metric,
        status=GateCheckStatus.INCONCLUSIVE,
        rule=rule,
        observed_value=None,
        threshold=None,
        confidence_bound=None,
        confidence_level=None,
        pair_coverage=None,
        event_count=None,
        trial_count=None,
        evidence_status=None,
        reason=reason,
    )


def _result(
    *,
    evidence: PairedRunStatisticalEvidence,
    checks: list[GateCheck],
) -> ChangeGateResult:
    pass_count = sum(check.status == GateCheckStatus.PASS for check in checks)
    fail_count = sum(check.status == GateCheckStatus.FAIL for check in checks)
    review_count = sum(check.status == GateCheckStatus.REVIEW for check in checks)
    inconclusive_count = sum(check.status == GateCheckStatus.INCONCLUSIVE for check in checks)
    if fail_count:
        decision = ChangeGateDecision.NO_GO
    elif inconclusive_count:
        decision = ChangeGateDecision.INCONCLUSIVE
    elif review_count:
        decision = ChangeGateDecision.REVIEW
    else:
        decision = ChangeGateDecision.SHIP
    limitations = tuple(evidence.limitations) + (
        "This is an intersection benchmark gate: favorable checks never offset failed or inconclusive required checks.",
        "SHIP means the configured replayable benchmark gate passed; it is not a production reliability or causal-effect guarantee.",
        "Observed p95/p99 checks are descriptive hard guards unless a future tail-inference method is explicitly supplied.",
    )
    return ChangeGateResult(
        decision=decision,
        baseline_run_id=evidence.baseline_run_id,
        candidate_run_id=evidence.candidate_run_id,
        evidence_scope="replayable_benchmark_change_gate",
        checks=tuple(checks),
        pass_count=pass_count,
        fail_count=fail_count,
        review_count=review_count,
        inconclusive_count=inconclusive_count,
        limitations=limitations,
    )

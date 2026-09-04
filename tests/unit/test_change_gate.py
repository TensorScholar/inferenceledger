from __future__ import annotations

from dataclasses import replace

import pytest

from inference_engine.benchmarking.change_gate import (
    ChangeGateDecision,
    ChangeGatePolicy,
    CriticalSegmentPolicy,
    GateCheckStatus,
    evaluate_change_gate,
)
from inference_engine.benchmarking.paired_comparison import (
    PairedMetricEvidence,
    PairedRunStatisticalEvidence,
    PairedSegmentStatisticalEvidence,
)
from inference_engine.benchmarking.segmentation import SegmentEvidence, SegmentEvidenceSummary
from inference_engine.benchmarking.statistics import (
    PairedMeanDifferenceEstimate,
    StatisticalEvidenceStatus,
)


def _estimate(
    *,
    observed: float,
    low: float,
    high: float,
    sample_count: int = 100,
    changed_pair_count: int = 50,
    status: StatisticalEvidenceStatus = StatisticalEvidenceStatus.SUFFICIENT,
    iterations: int = 10_000,
) -> PairedMeanDifferenceEstimate:
    interval_available = status == StatisticalEvidenceStatus.SUFFICIENT
    return PairedMeanDifferenceEstimate(
        status=status,
        sample_count=sample_count,
        minimum_sample_count=30,
        changed_pair_count=changed_pair_count,
        minimum_changed_pair_count=0,
        unique_difference_count=3,
        observed_mean_difference=observed,
        confidence_level=0.95,
        confidence_interval_low=low if interval_available else None,
        confidence_interval_high=high if interval_available else None,
        method="paired_bca_bootstrap_mean_difference",
        bootstrap_iterations=iterations,
        seed=17,
        zero_variance=False,
    )


def _metric(
    metric: str,
    *,
    observed: float,
    low: float,
    high: float,
    lower_is_better: bool = True,
    pair_coverage: float = 1.0,
    sample_count: int = 100,
    changed_pair_count: int = 50,
    status: StatisticalEvidenceStatus = StatisticalEvidenceStatus.SUFFICIENT,
    iterations: int = 10_000,
) -> PairedMetricEvidence:
    return PairedMetricEvidence(
        metric=metric,
        unit="rate" if "rate" in metric else "unit",
        population="test population",
        lower_is_better=lower_is_better,
        workload_item_count=100,
        eligible_pair_count=sample_count,
        pair_coverage=pair_coverage,
        estimate=_estimate(
            observed=observed,
            low=low,
            high=high,
            sample_count=sample_count,
            changed_pair_count=changed_pair_count,
            status=status,
            iterations=iterations,
        ),
    )


def _binary_metric(
    metric: str,
    *,
    worsening_count: int,
    improving_count: int = 0,
    sample_count: int = 100,
    lower_is_better: bool,
    pair_coverage: float = 1.0,
) -> PairedMetricEvidence:
    changed = worsening_count + improving_count
    net = worsening_count - improving_count
    observed = net / sample_count if lower_is_better else -net / sample_count
    return _metric(
        metric,
        observed=observed,
        low=observed - 0.01,
        high=observed + 0.01,
        lower_is_better=lower_is_better,
        pair_coverage=pair_coverage,
        sample_count=sample_count,
        changed_pair_count=changed,
        status=(
            StatisticalEvidenceStatus.INSUFFICIENT_VARIATION
            if changed < 10
            else StatisticalEvidenceStatus.SUFFICIENT
        ),
    )


def _evidence(
    *,
    cost: PairedMetricEvidence | None = None,
    failure: PairedMetricEvidence | None = None,
    latency: PairedMetricEvidence | None = None,
    accepted: PairedMetricEvidence | None = None,
    segments: tuple[PairedSegmentStatisticalEvidence, ...] = (),
) -> PairedRunStatisticalEvidence:
    return PairedRunStatisticalEvidence(
        available=True,
        unavailable_reason=None,
        baseline_run_id="baseline",
        candidate_run_id="candidate",
        workload_sha256="sha",
        workload_item_count=100,
        baseline_provider="openai",
        candidate_provider="other-provider",
        execution_cost_usd=cost
        or _metric("execution_cost_usd", observed=-0.10, low=-0.15, high=-0.02),
        failure_rate=failure
        or _binary_metric(
            "failure_rate",
            worsening_count=0,
            lower_is_better=True,
        ),
        successful_latency_ms=latency
        or _metric("successful_latency_ms", observed=1.0, low=-2.0, high=3.0),
        accepted_outcome_rate=accepted
        or _binary_metric(
            "accepted_outcome_rate",
            worsening_count=0,
            lower_is_better=False,
        ),
        provider_attempt_count=_metric(
            "provider_attempt_count",
            observed=-0.02,
            low=-0.05,
            high=0.0,
        ),
        provider_retry_count=_metric(
            "provider_retry_count",
            observed=-0.01,
            low=-0.03,
            high=0.0,
        ),
        segments=segments,
        tail_latency_inference_supported=False,
        limitations=("test limitation",),
    )


def _policy(**overrides) -> ChangeGatePolicy:
    values = {
        "max_mean_cost_delta_usd": 0.0,
        "max_failure_harm_rate": 0.05,
        "max_mean_successful_latency_delta_ms": 5.0,
        "max_accepted_outcome_harm_rate": 0.05,
    }
    values.update(overrides)
    return ChangeGatePolicy(**values)


def test_ship_requires_every_non_compensatory_check_to_pass() -> None:
    result = evaluate_change_gate(evidence=_evidence(), policy=_policy())

    assert result.decision == ChangeGateDecision.SHIP
    assert result.fail_count == 0
    assert result.review_count == 0
    assert result.inconclusive_count == 0
    assert all(check.status == GateCheckStatus.PASS for check in result.checks)


def test_exact_binary_harm_bound_can_require_review_even_with_zero_observed_harm() -> None:
    failure = _binary_metric(
        "failure_rate",
        worsening_count=0,
        sample_count=30,
        lower_is_better=True,
    )
    result = evaluate_change_gate(
        evidence=_evidence(failure=failure),
        policy=_policy(),
    )

    check = next(check for check in result.checks if check.metric == "failure_rate")
    assert check.observed_value == 0.0
    assert check.confidence_bound is not None
    assert check.confidence_bound > 0.05
    assert check.status == GateCheckStatus.REVIEW
    assert result.decision == ChangeGateDecision.REVIEW


def test_observed_failure_harm_is_no_go_even_when_cost_improves_strongly() -> None:
    evidence = _evidence(
        cost=_metric("execution_cost_usd", observed=-10.0, low=-12.0, high=-8.0),
        failure=_binary_metric(
            "failure_rate",
            worsening_count=6,
            sample_count=100,
            lower_is_better=True,
        ),
    )

    result = evaluate_change_gate(evidence=evidence, policy=_policy())

    assert result.decision == ChangeGateDecision.NO_GO
    failure_check = next(check for check in result.checks if check.metric == "failure_rate")
    assert failure_check.status == GateCheckStatus.FAIL
    assert failure_check.observed_value == pytest.approx(0.06)


def test_accepted_outcome_harm_uses_higher_is_better_direction() -> None:
    accepted = _binary_metric(
        "accepted_outcome_rate",
        worsening_count=6,
        sample_count=100,
        lower_is_better=False,
    )

    result = evaluate_change_gate(
        evidence=_evidence(accepted=accepted),
        policy=_policy(),
    )

    check = next(check for check in result.checks if check.metric == "accepted_outcome_rate")
    assert check.observed_value == pytest.approx(0.06)
    assert check.event_count == 6
    assert check.status == GateCheckStatus.FAIL
    assert result.decision == ChangeGateDecision.NO_GO


def test_incomplete_cost_coverage_is_inconclusive_not_savings() -> None:
    cost = _metric(
        "execution_cost_usd",
        observed=-0.2,
        low=-0.3,
        high=-0.1,
        pair_coverage=0.90,
        sample_count=90,
    )

    result = evaluate_change_gate(evidence=_evidence(cost=cost), policy=_policy())

    cost_check = next(check for check in result.checks if check.metric == "execution_cost_usd")
    assert cost_check.status == GateCheckStatus.INCONCLUSIVE
    assert result.decision == ChangeGateDecision.INCONCLUSIVE


def test_low_bootstrap_iteration_evidence_is_inconclusive() -> None:
    latency = _metric(
        "successful_latency_ms",
        observed=-2.0,
        low=-4.0,
        high=-1.0,
        iterations=1_000,
    )

    result = evaluate_change_gate(evidence=_evidence(latency=latency), policy=_policy())

    latency_check = next(
        check for check in result.checks if check.metric == "successful_latency_ms"
    )
    assert latency_check.status == GateCheckStatus.INCONCLUSIVE
    assert result.decision == ChangeGateDecision.INCONCLUSIVE


def test_uncertainty_crossing_central_margin_requires_review() -> None:
    cost = _metric(
        "execution_cost_usd",
        observed=-0.01,
        low=-0.1,
        high=0.02,
    )

    result = evaluate_change_gate(evidence=_evidence(cost=cost), policy=_policy())

    cost_check = next(check for check in result.checks if check.metric == "execution_cost_usd")
    assert cost_check.status == GateCheckStatus.REVIEW
    assert result.decision == ChangeGateDecision.REVIEW


def test_structurally_unavailable_paired_evidence_is_inconclusive() -> None:
    evidence = replace(
        _evidence(),
        available=False,
        unavailable_reason="workload hashes differ",
    )

    result = evaluate_change_gate(evidence=evidence, policy=_policy())

    assert result.decision == ChangeGateDecision.INCONCLUSIVE
    assert result.checks[0].status == GateCheckStatus.INCONCLUSIVE
    assert "hashes differ" in result.checks[0].reason


def _segment_metric_bundle() -> dict[str, PairedMetricEvidence]:
    return {
        "execution_cost_usd": _metric(
            "execution_cost_usd", observed=-0.1, low=-0.2, high=-0.01
        ),
        "failure_rate": _binary_metric(
            "failure_rate", worsening_count=0, lower_is_better=True
        ),
        "successful_latency_ms": _metric(
            "successful_latency_ms", observed=0.0, low=-2.0, high=2.0
        ),
        "accepted_outcome_rate": _binary_metric(
            "accepted_outcome_rate", worsening_count=0, lower_is_better=False
        ),
        "provider_attempt_count": _metric(
            "provider_attempt_count", observed=-0.1, low=-0.2, high=-0.01
        ),
        "provider_retry_count": _metric(
            "provider_retry_count", observed=-0.1, low=-0.2, high=-0.01
        ),
    }


def _paired_segment() -> PairedSegmentStatisticalEvidence:
    return PairedSegmentStatisticalEvidence(
        tag_key="risk",
        tag_value="high",
        workload_item_count=100,
        **_segment_metric_bundle(),
    )


def _observed_segment(*, p95: int, p99: int, samples: int = 100) -> SegmentEvidence:
    return SegmentEvidence(
        tag_key="risk",
        tag_value="high",
        request_count=100,
        success_count=samples,
        failure_count=100 - samples,
        error_rate=(100 - samples) / 100,
        latency_sample_count=samples,
        latency_p50_ms=80,
        latency_p95_ms=p95,
        latency_p99_ms=p99,
        provider_attempt_count=100,
        provider_retry_count=0,
        execution_path_divergence_count=0,
        execution_path_divergence_rate=0.0,
        estimated_cost_usd=0.1,
        cost_evidence_complete=True,
        cost_per_success_usd=0.001,
        quality_count=samples,
        quality_coverage=1.0,
        quality_pass_count=samples,
        quality_pass_rate=1.0,
        cost_per_accepted_outcome_usd=0.001,
        model_distribution={"model": samples},
    )


def _segment_summary(segment: SegmentEvidence) -> SegmentEvidenceSummary:
    return SegmentEvidenceSummary(
        available=True,
        unavailable_reason=None,
        request_count=100,
        tagged_request_count=100,
        untagged_request_count=0,
        segment_count=1,
        latency_percentile_method="empirical_nearest_rank_successful_requests",
        segments=(segment,),
    )


def test_critical_segment_observed_tail_regression_is_hard_no_go() -> None:
    evidence = replace(_evidence(), segments=(_paired_segment(),))
    segment_policy = CriticalSegmentPolicy(
        tag_key="risk",
        tag_value="high",
        max_p95_regression_ms=20,
        max_candidate_p99_ms=180,
    )

    result = evaluate_change_gate(
        evidence=evidence,
        policy=_policy(critical_segments=(segment_policy,)),
        baseline_segments=_segment_summary(_observed_segment(p95=100, p99=150)),
        candidate_segments=_segment_summary(_observed_segment(p95=130, p99=170)),
    )

    assert result.decision == ChangeGateDecision.NO_GO
    p95_check = next(check for check in result.checks if check.check_id.endswith("p95_regression"))
    assert p95_check.status == GateCheckStatus.FAIL
    assert p95_check.observed_value == 30.0


def test_critical_segment_tail_sample_shortfall_is_inconclusive() -> None:
    evidence = replace(_evidence(), segments=(_paired_segment(),))
    segment_policy = CriticalSegmentPolicy(
        tag_key="risk",
        tag_value="high",
        minimum_tail_latency_samples=30,
        max_p95_regression_ms=20,
    )

    result = evaluate_change_gate(
        evidence=evidence,
        policy=_policy(critical_segments=(segment_policy,)),
        baseline_segments=_segment_summary(_observed_segment(p95=100, p99=150, samples=20)),
        candidate_segments=_segment_summary(_observed_segment(p95=105, p99=155, samples=20)),
    )

    assert result.decision == ChangeGateDecision.INCONCLUSIVE
    tail_check = next(check for check in result.checks if check.metric == "tail_latency")
    assert tail_check.status == GateCheckStatus.INCONCLUSIVE


def test_requiring_tail_inference_fails_closed_until_method_exists() -> None:
    result = evaluate_change_gate(
        evidence=_evidence(),
        policy=_policy(require_tail_latency_inference=True),
    )

    assert result.decision == ChangeGateDecision.INCONCLUSIVE
    assert any(check.check_id == "tail_latency_inference" for check in result.checks)


def test_binary_transition_tampering_is_rejected() -> None:
    failure = _metric(
        "failure_rate",
        observed=0.0,
        low=-0.01,
        high=0.01,
        lower_is_better=True,
        sample_count=100,
        changed_pair_count=1,
    )

    with pytest.raises(ValueError, match="invalid parity"):
        evaluate_change_gate(evidence=_evidence(failure=failure), policy=_policy())

from __future__ import annotations

import math

import pytest

from inference_engine.benchmarking.statistics import (
    PairedBootstrapConfig,
    StatisticalEvidenceStatus,
    paired_mean_difference_bca,
)


def _config(*, minimum_samples: int = 5, seed: int = 17) -> PairedBootstrapConfig:
    return PairedBootstrapConfig(
        confidence_level=0.95,
        bootstrap_iterations=2_000,
        minimum_samples=minimum_samples,
        seed=seed,
    )


def test_paired_bootstrap_preserves_candidate_minus_baseline_direction() -> None:
    baseline = [1.0, 2.0, 3.0, 4.0, 5.0]
    candidate = [0.5, 1.5, 2.5, 3.5, 4.5]

    estimate = paired_mean_difference_bca(
        baseline=baseline,
        candidate=candidate,
        config=_config(),
    )

    assert estimate.status == StatisticalEvidenceStatus.SUFFICIENT
    assert estimate.sample_count == 5
    assert estimate.observed_mean_difference == pytest.approx(-0.5)
    assert estimate.confidence_interval_low == pytest.approx(-0.5)
    assert estimate.confidence_interval_high == pytest.approx(-0.5)
    assert estimate.zero_variance is True
    assert estimate.interval_available is True


def test_insufficient_sample_keeps_point_estimate_but_suppresses_interval() -> None:
    estimate = paired_mean_difference_bca(
        baseline=[1.0, 2.0, 3.0],
        candidate=[2.0, 2.0, 4.0],
        config=_config(minimum_samples=5),
    )

    assert estimate.status == StatisticalEvidenceStatus.INSUFFICIENT_SAMPLE
    assert estimate.observed_mean_difference == pytest.approx(2 / 3)
    assert estimate.confidence_interval_low is None
    assert estimate.confidence_interval_high is None
    assert estimate.interval_available is False


def test_no_pairs_are_explicitly_no_evidence() -> None:
    estimate = paired_mean_difference_bca(
        baseline=[],
        candidate=[],
        config=_config(),
    )

    assert estimate.status == StatisticalEvidenceStatus.NO_EVIDENCE
    assert estimate.sample_count == 0
    assert estimate.observed_mean_difference is None
    assert estimate.interval_available is False


def test_bootstrap_is_deterministic_for_same_seed() -> None:
    baseline = [1.0, 2.0, 3.0, 8.0, 13.0, 21.0]
    candidate = [1.5, 1.5, 4.0, 7.0, 15.0, 20.0]
    config = _config(minimum_samples=5, seed=991)

    first = paired_mean_difference_bca(
        baseline=baseline,
        candidate=candidate,
        config=config,
    )
    second = paired_mean_difference_bca(
        baseline=baseline,
        candidate=candidate,
        config=config,
    )

    assert first == second
    assert first.confidence_interval_low is not None
    assert first.confidence_interval_high is not None
    assert first.confidence_interval_low <= first.observed_mean_difference <= first.confidence_interval_high


def test_pairing_contract_rejects_unequal_sample_lengths() -> None:
    with pytest.raises(ValueError, match="equal length"):
        paired_mean_difference_bca(
            baseline=[1.0, 2.0],
            candidate=[1.0],
            config=_config(),
        )


def test_pairing_contract_rejects_non_finite_values() -> None:
    with pytest.raises(ValueError, match="finite"):
        paired_mean_difference_bca(
            baseline=[1.0, math.inf, 3.0, 4.0, 5.0],
            candidate=[1.0, 2.0, 3.0, 4.0, 5.0],
            config=_config(),
        )


def test_configuration_rejects_underpowered_bootstrap_iteration_count() -> None:
    with pytest.raises(ValueError, match="at least 1000"):
        PairedBootstrapConfig(bootstrap_iterations=999)

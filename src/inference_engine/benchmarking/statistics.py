from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import floor, isfinite
from random import Random
from statistics import NormalDist, fmean


class StatisticalEvidenceStatus(StrEnum):
    """Whether a paired estimate has enough observations for configured inference."""

    SUFFICIENT = "sufficient"
    INSUFFICIENT_SAMPLE = "insufficient_sample"
    NO_EVIDENCE = "no_evidence"


@dataclass(frozen=True)
class PairedBootstrapConfig:
    """Explicit product policy for central paired uncertainty estimates.

    `minimum_samples` is a safety policy, not a theorem that guarantees statistical power.
    The caller must keep effect-size tolerances and domain risk in the decision layer.
    """

    confidence_level: float = 0.95
    bootstrap_iterations: int = 10_000
    minimum_samples: int = 30
    seed: int = 0

    def __post_init__(self) -> None:
        if not 0 < self.confidence_level < 1:
            raise ValueError("confidence_level must be between 0 and 1")
        if self.bootstrap_iterations < 1_000:
            raise ValueError("bootstrap_iterations must be at least 1000")
        if self.minimum_samples < 2:
            raise ValueError("minimum_samples must be at least 2")


@dataclass(frozen=True)
class PairedMeanDifferenceEstimate:
    """BCa bootstrap uncertainty for candidate-minus-baseline paired mean difference."""

    status: StatisticalEvidenceStatus
    sample_count: int
    minimum_sample_count: int
    observed_mean_difference: float | None
    confidence_level: float
    confidence_interval_low: float | None
    confidence_interval_high: float | None
    method: str
    bootstrap_iterations: int
    seed: int
    zero_variance: bool

    @property
    def interval_available(self) -> bool:
        return (
            self.status == StatisticalEvidenceStatus.SUFFICIENT
            and self.confidence_interval_low is not None
            and self.confidence_interval_high is not None
        )


def paired_mean_difference_bca(
    *,
    baseline: list[float],
    candidate: list[float],
    config: PairedBootstrapConfig | None = None,
) -> PairedMeanDifferenceEstimate:
    """Estimate candidate-minus-baseline mean difference with paired BCa bootstrap.

    Resampling is performed over paired differences, which is equivalent to resampling intact
    matched workload items. This primitive is for central mean-difference inference only; it must
    not be reused for high-tail quantile confidence intervals such as p95/p99.
    """
    resolved = config or PairedBootstrapConfig()
    if len(baseline) != len(candidate):
        raise ValueError("paired baseline and candidate samples must have equal length")
    if any(not isfinite(value) for value in baseline + candidate):
        raise ValueError("paired samples must contain only finite values")

    sample_count = len(baseline)
    if sample_count == 0:
        return _empty_estimate(
            status=StatisticalEvidenceStatus.NO_EVIDENCE,
            config=resolved,
            sample_count=0,
        )

    differences = [candidate_value - baseline_value for baseline_value, candidate_value in zip(baseline, candidate, strict=True)]
    observed = fmean(differences)
    zero_variance = all(value == differences[0] for value in differences)
    if sample_count < resolved.minimum_samples:
        return PairedMeanDifferenceEstimate(
            status=StatisticalEvidenceStatus.INSUFFICIENT_SAMPLE,
            sample_count=sample_count,
            minimum_sample_count=resolved.minimum_samples,
            observed_mean_difference=observed,
            confidence_level=resolved.confidence_level,
            confidence_interval_low=None,
            confidence_interval_high=None,
            method="paired_bca_bootstrap_mean_difference",
            bootstrap_iterations=resolved.bootstrap_iterations,
            seed=resolved.seed,
            zero_variance=zero_variance,
        )

    bootstrap_statistics = _bootstrap_means(
        differences,
        iterations=resolved.bootstrap_iterations,
        seed=resolved.seed,
    )
    lower_probability, upper_probability = _bca_probabilities(
        sample=differences,
        observed=observed,
        bootstrap_statistics=bootstrap_statistics,
        confidence_level=resolved.confidence_level,
    )
    ordered = sorted(bootstrap_statistics)
    interval_low = _linear_quantile(ordered, lower_probability)
    interval_high = _linear_quantile(ordered, upper_probability)

    return PairedMeanDifferenceEstimate(
        status=StatisticalEvidenceStatus.SUFFICIENT,
        sample_count=sample_count,
        minimum_sample_count=resolved.minimum_samples,
        observed_mean_difference=observed,
        confidence_level=resolved.confidence_level,
        confidence_interval_low=interval_low,
        confidence_interval_high=interval_high,
        method="paired_bca_bootstrap_mean_difference",
        bootstrap_iterations=resolved.bootstrap_iterations,
        seed=resolved.seed,
        zero_variance=zero_variance,
    )


def _bootstrap_means(
    sample: list[float],
    *,
    iterations: int,
    seed: int,
) -> list[float]:
    random = Random(seed)
    size = len(sample)
    return [
        fmean(sample[random.randrange(size)] for _ in range(size))
        for _ in range(iterations)
    ]


def _bca_probabilities(
    *,
    sample: list[float],
    observed: float,
    bootstrap_statistics: list[float],
    confidence_level: float,
) -> tuple[float, float]:
    normal = NormalDist()
    below = sum(1 for value in bootstrap_statistics if value < observed)
    equal = sum(1 for value in bootstrap_statistics if value == observed)
    proportion = (below + 0.5 * equal) / len(bootstrap_statistics)
    epsilon = 0.5 / len(bootstrap_statistics)
    proportion = min(max(proportion, epsilon), 1 - epsilon)
    bias_correction = normal.inv_cdf(proportion)
    acceleration = _jackknife_acceleration(sample)

    alpha = (1 - confidence_level) / 2
    lower = _adjusted_probability(
        nominal_probability=alpha,
        bias_correction=bias_correction,
        acceleration=acceleration,
        normal=normal,
    )
    upper = _adjusted_probability(
        nominal_probability=1 - alpha,
        bias_correction=bias_correction,
        acceleration=acceleration,
        normal=normal,
    )
    lower = min(max(lower, 0.0), 1.0)
    upper = min(max(upper, 0.0), 1.0)
    if lower > upper:
        lower, upper = upper, lower
    return lower, upper


def _jackknife_acceleration(sample: list[float]) -> float:
    if len(sample) < 3:
        return 0.0
    jackknife_statistics = [
        fmean(sample[:index] + sample[index + 1 :])
        for index in range(len(sample))
    ]
    jackknife_mean = fmean(jackknife_statistics)
    centered = [jackknife_mean - value for value in jackknife_statistics]
    squared_sum = sum(value * value for value in centered)
    if squared_sum == 0:
        return 0.0
    numerator = sum(value**3 for value in centered)
    denominator = 6 * (squared_sum**1.5)
    return numerator / denominator


def _adjusted_probability(
    *,
    nominal_probability: float,
    bias_correction: float,
    acceleration: float,
    normal: NormalDist,
) -> float:
    z = normal.inv_cdf(nominal_probability)
    shifted = bias_correction + z
    denominator = 1 - acceleration * shifted
    if denominator == 0:
        return nominal_probability
    return normal.cdf(bias_correction + shifted / denominator)


def _linear_quantile(ordered: list[float], probability: float) -> float:
    if not ordered:
        raise ValueError("quantile requires a non-empty sample")
    if probability <= 0:
        return ordered[0]
    if probability >= 1:
        return ordered[-1]
    position = probability * (len(ordered) - 1)
    lower_index = floor(position)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    weight = position - lower_index
    return ordered[lower_index] * (1 - weight) + ordered[upper_index] * weight


def _empty_estimate(
    *,
    status: StatisticalEvidenceStatus,
    config: PairedBootstrapConfig,
    sample_count: int,
) -> PairedMeanDifferenceEstimate:
    return PairedMeanDifferenceEstimate(
        status=status,
        sample_count=sample_count,
        minimum_sample_count=config.minimum_samples,
        observed_mean_difference=None,
        confidence_level=config.confidence_level,
        confidence_interval_low=None,
        confidence_interval_high=None,
        method="paired_bca_bootstrap_mean_difference",
        bootstrap_iterations=config.bootstrap_iterations,
        seed=config.seed,
        zero_variance=False,
    )

from __future__ import annotations

from typing import Protocol


class RoutingCostEstimator(Protocol):
    """Pre-execution cost estimator consumed by routing policies.

    Routing owns selection policy, not tariff data. Implementations must source monetary assumptions
    from the canonical pricing authority rather than embedding rates in model configuration.
    """

    def estimate(
        self,
        *,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        """Return the estimated USD execution cost for one candidate model."""
        ...

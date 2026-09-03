from __future__ import annotations

import pytest

from inference_engine.domain.models.request import InferenceRequest, ModelParameters
from inference_engine.domain.models.routing import ComplexityEstimate, ModelConfig, ModelTier
from inference_engine.domain.routing.cost_aware import CostAwareRouter


class PremiumComplexityEstimator:
    async def estimate(self, _request: InferenceRequest) -> ComplexityEstimate:
        return ComplexityEstimate(
            score=0.9,
            factors={"test": 1.0},
            input_length=100,
            estimated_reasoning_steps=4,
            requires_context=False,
            domain_specific=True,
        )


class AdversarialCostEstimator:
    """Make the economy model unrealistically cheap to prove capability gating wins first."""

    def estimate(
        self,
        *,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        del input_tokens, output_tokens
        return {"economy": 0.000001, "premium": 100.0}[model_id]


@pytest.mark.asyncio
async def test_premium_complexity_excludes_economy_before_cost_scoring() -> None:
    models = [
        ModelConfig(
            id="economy",
            name="Economy",
            tier=ModelTier.ECONOMY,
            max_context_length=128_000,
        ),
        ModelConfig(
            id="premium",
            name="Premium",
            tier=ModelTier.PREMIUM,
            max_context_length=128_000,
        ),
    ]
    request = InferenceRequest(
        prompt="adversarial premium request",
        parameters=ModelParameters(max_tokens=64),
    )
    router = CostAwareRouter(
        models,
        PremiumComplexityEstimator(),
        AdversarialCostEstimator(),
        cost_weight=1.0,
    )

    decision = await router.route(request)

    assert decision.selected_model.id == "premium"
    assert decision.considered_models == ["premium"]
    assert decision.estimated_cost == pytest.approx(100.0)

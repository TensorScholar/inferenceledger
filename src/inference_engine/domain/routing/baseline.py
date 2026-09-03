from __future__ import annotations

from ..models.request import InferenceRequest
from ..models.routing import (
    ComplexityEstimate,
    ModelConfig,
    ModelTier,
    RoutingDecision,
    RoutingStrategy,
)
from .base import AbstractRouter
from .complexity import ComplexityEstimator
from .cost_estimator import RoutingCostEstimator


class BaselineRoutingModeError(ValueError):
    """Raised when a baseline router cannot make a valid deterministic decision."""


class BaselineRouter(AbstractRouter):
    """Deterministic benchmark baselines for model routing comparisons."""

    def __init__(
        self,
        models: list[ModelConfig],
        complexity_estimator: ComplexityEstimator,
        cost_estimator: RoutingCostEstimator,
        *,
        mode: RoutingStrategy,
        single_model_id: str | None = None,
    ) -> None:
        if mode not in {RoutingStrategy.SINGLE_MODEL, RoutingStrategy.RULE_BASED}:
            raise BaselineRoutingModeError(f"Unsupported baseline routing mode: {mode}")
        self.models = {model.id: model for model in models}
        self.complexity_estimator = complexity_estimator
        self.cost_estimator = cost_estimator
        self.mode = mode
        self.single_model_id = single_model_id

    async def route(self, request: InferenceRequest) -> RoutingDecision:
        if self.mode == RoutingStrategy.SINGLE_MODEL:
            return self._single_model_decision(request)
        return await self._rule_based_decision(request)

    def _single_model_decision(self, request: InferenceRequest) -> RoutingDecision:
        if self.single_model_id is None:
            raise BaselineRoutingModeError("single_model baseline requires single_model_id")
        selected = self._get_available_model(self.single_model_id)
        return self._decision(
            request=request,
            selected=selected,
            strategy=RoutingStrategy.SINGLE_MODEL,
            reason=f"single_model baseline selected configured model {selected.id}",
        )

    async def _rule_based_decision(self, request: InferenceRequest) -> RoutingDecision:
        complexity = await self.complexity_estimator.estimate(request)
        selected = self._cheapest_model_for_tier(complexity.recommended_tier, request)
        return self._decision(
            request=request,
            selected=selected,
            strategy=RoutingStrategy.RULE_BASED,
            reason=(
                "rule_based baseline selected cheapest available model at or above "
                f"{complexity.recommended_tier.value} tier"
            ),
            complexity_estimate=complexity,
        )

    def _get_available_model(self, model_id: str) -> ModelConfig:
        try:
            model = self.models[model_id]
        except KeyError as exc:
            raise BaselineRoutingModeError(f"Unknown model id: {model_id}") from exc
        if not model.is_available:
            raise BaselineRoutingModeError(f"Configured model is not available: {model_id}")
        return model

    def _cheapest_model_for_tier(
        self,
        tier: ModelTier,
        request: InferenceRequest,
    ) -> ModelConfig:
        candidates = [
            model
            for model in self.models.values()
            if model.is_available and model.tier.rank >= tier.rank
        ]
        if not candidates:
            raise BaselineRoutingModeError(f"No available model can satisfy tier {tier.value}")
        return min(
            candidates,
            key=lambda model: self._estimate_cost(model, request),
        )

    def _estimate_cost(self, model: ModelConfig, request: InferenceRequest) -> float:
        return self.cost_estimator.estimate(
            model_id=model.id,
            input_tokens=request.estimated_input_tokens,
            output_tokens=request.parameters.max_tokens,
        )

    def _decision(
        self,
        *,
        request: InferenceRequest,
        selected: ModelConfig,
        strategy: RoutingStrategy,
        reason: str,
        complexity_estimate: ComplexityEstimate | None = None,
    ) -> RoutingDecision:
        estimated_cost = self._estimate_cost(selected, request)
        fallback_models = [
            model for model in self.models.values() if model.is_available and model.id != selected.id
        ]
        return RoutingDecision(
            request_id=request.id,
            selected_model=selected,
            fallback_models=fallback_models,
            strategy=strategy,
            complexity_estimate=complexity_estimate,
            estimated_cost=estimated_cost,
            estimated_latency_ms=selected.avg_latency_ms,
            estimated_quality_score=_tier_quality(selected.tier),
            decision_reason=reason,
            considered_models=[model.id for model in self.models.values()],
        )


def _tier_quality(tier: ModelTier) -> float:
    return {
        ModelTier.ECONOMY: 0.4,
        ModelTier.STANDARD: 0.7,
        ModelTier.PREMIUM: 1.0,
    }[tier]

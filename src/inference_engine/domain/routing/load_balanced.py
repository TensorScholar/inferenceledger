import structlog

from ..models.request import InferenceRequest
from ..models.routing import ModelConfig, RoutingDecision, RoutingStrategy
from .base import AbstractRouter
from .cost_estimator import RoutingCostEstimator

logger = structlog.get_logger()


class LoadBalancedRouter(AbstractRouter):
    """Round-robin load balancing across available models."""

    def __init__(self, models: list[ModelConfig], cost_estimator: RoutingCostEstimator) -> None:
        self.models = models
        self.cost_estimator = cost_estimator
        self.current_index = 0

    async def route(self, request: InferenceRequest) -> RoutingDecision:
        available = [model for model in self.models if model.is_available]
        if not available:
            raise RuntimeError("No available models for routing")

        selected = available[self.current_index % len(available)]
        self.current_index += 1
        estimated_cost = self.cost_estimator.estimate(
            model_id=selected.id,
            input_tokens=request.estimated_input_tokens,
            output_tokens=request.parameters.max_tokens,
        )

        logger.info(
            "load_balanced_routing",
            request_id=str(request.id),
            model=selected.id,
            index=self.current_index,
        )

        return RoutingDecision(
            request_id=request.id,
            selected_model=selected,
            fallback_models=[],
            strategy=RoutingStrategy.ROUND_ROBIN,
            complexity_estimate=None,
            estimated_cost=estimated_cost,
            estimated_latency_ms=selected.avg_latency_ms,
            estimated_quality_score=0.7,
            decision_reason=f"Round-robin selection: {selected.id}",
            considered_models=[model.id for model in available],
        )
